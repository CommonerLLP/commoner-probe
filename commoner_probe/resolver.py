# SPDX-License-Identifier: AGPL-3.0-or-later
"""Name + context -> entity_id resolver.

Single function: ``Resolver.resolve(name, context, kind_hint) -> ResolutionResult``.

The resolver is the chokepoint between unstructured text (a name as it
appears in a question record, a witness list, a committee composition)
and the structured entity store. Every consumer that needs to attach
an ``entity_id`` to a record goes through this function.

Behaviour on unresolvable names (option (a) from PLAN_v0.5.0_SCOPE.md):
ambiguous lookups return ``status="ambiguous"`` with a candidates list
and ``entity_id=None``. Callers stamp the null on the record and proceed.
Manual review later. **No placeholder entities** — orphan records that
ossify are worse than honest nulls.

Bureaucrat resolution is deferred in v0.5.0: ``kind_hint="bureaucrat"``
returns ``status="deferred"`` immediately. The schema and call site are
in place; the implementation lands in v0.6.0 with DoPT data piping.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from .entities import EntityStore, MpMembership, Person

ResolutionStatus = Literal["resolved", "ambiguous", "unknown", "deferred"]


@dataclass
class ResolutionResult:
    entity_id: str | None
    confidence: float  # 0.0 = unknown/ambiguous; 1.0 = single unambiguous match
    status: ResolutionStatus
    name_as_recorded: str = ""
    candidates: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _membership_score(m: MpMembership, context: dict) -> float:
    """How well does an MP membership match the context? 0..1."""
    score = 0.0
    matched = 0
    total = 0
    if "house" in context and context["house"]:
        total += 1
        if m.house and m.house.lower() == str(context["house"]).lower():
            matched += 1
    if "party" in context and context["party"]:
        total += 1
        ctx_party = str(context["party"]).strip().lower()
        if m.party and m.party.strip().lower() == ctx_party:
            matched += 1
        elif m.party_name and ctx_party in m.party_name.lower():
            matched += 1
    if "state" in context and context["state"]:
        total += 1
        if m.state and m.state.strip().lower() == str(context["state"]).strip().lower():
            matched += 1
    if total == 0:
        return 0.0
    score = matched / total
    return score


class Resolver:
    """Wraps an ``EntityStore`` with name+context lookup.

    The resolver does not mutate the store. Adding people to the store
    is the loader's job (e.g., ``MPRoster.populate``); the resolver
    only reads.
    """

    def __init__(self, store: EntityStore):
        self.store = store

    def resolve(
        self,
        name: str,
        context: dict | None = None,
        kind_hint: str | None = None,
    ) -> ResolutionResult:
        """Map a free-text name to an entity_id with confidence.

        ``context`` may include any of: ``date``, ``house``, ``party``,
        ``state``, ``ministry``, ``designation``. Unused keys are ignored.

        ``kind_hint`` may be: ``"mp"``, ``"minister"``, ``"bureaucrat"``,
        or None (auto). Bureaucrat resolution is deferred in v0.5.0.
        """
        name = (name or "").strip()
        if not name:
            return ResolutionResult(None, 0.0, "unknown", name_as_recorded=name)

        if kind_hint == "bureaucrat":
            return ResolutionResult(
                None, 0.0, "deferred", name_as_recorded=name,
                candidates=[{"reason": "bureaucrat resolution deferred to v0.6.0"}],
            )

        candidates = self.store.find_by_name(name)
        if not candidates:
            return ResolutionResult(None, 0.0, "unknown", name_as_recorded=name)

        if len(candidates) == 1:
            person = candidates[0]
            return ResolutionResult(
                person.entity_id, 1.0, "resolved",
                name_as_recorded=name,
                candidates=[self._candidate_summary(person, 1.0)],
            )

        # Multiple candidates — try to disambiguate by context.
        if context:
            ranked = self._rank_by_context(candidates, context)
            if ranked:
                top_score, top_person = ranked[0]
                runner_up_score = ranked[1][0] if len(ranked) > 1 else 0.0
                # Resolve only when the top candidate is confidently better
                # than the runner-up. Conservative thresholds.
                if top_score >= 0.66 and (top_score - runner_up_score) >= 0.34:
                    return ResolutionResult(
                        top_person.entity_id, top_score, "resolved",
                        name_as_recorded=name,
                        candidates=[self._candidate_summary(p, s) for s, p in ranked],
                    )
                # Otherwise: ambiguous, return all ranked candidates.
                return ResolutionResult(
                    None, 0.0, "ambiguous", name_as_recorded=name,
                    candidates=[self._candidate_summary(p, s) for s, p in ranked],
                )

        # No context to disambiguate with.
        return ResolutionResult(
            None, 0.0, "ambiguous", name_as_recorded=name,
            candidates=[self._candidate_summary(p, 0.0) for p in candidates],
        )

    # ---- Internal ----

    def _rank_by_context(
        self, candidates: list[Person], context: dict
    ) -> list[tuple[float, Person]]:
        """Score each candidate against context using their MP memberships
        (and, in future, ministerial / bureaucratic role data). Returns a
        descending-score list of (score, person)."""
        ranked: list[tuple[float, Person]] = []
        for person in candidates:
            best = 0.0
            for m in self.store.memberships_for(person.entity_id):
                s = _membership_score(m, context)
                if s > best:
                    best = s
            ranked.append((best, person))
        ranked.sort(key=lambda x: x[0], reverse=True)
        return ranked

    @staticmethod
    def _candidate_summary(person: Person, score: float) -> dict:
        return {
            "entity_id": person.entity_id,
            "canonical_name": person.canonical_name,
            "primary_kind": person.primary_kind,
            "score": round(score, 3),
        }
