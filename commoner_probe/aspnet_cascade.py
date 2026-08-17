# SPDX-License-Identifier: MIT
"""Walking a chain of dependent dropdowns on an ASP.NET WebForms report page.

**The shape this handles.** A server-rendered WebForms page (mid-2000s
procurement, still in production across Indian government MIS estates) whose
report form is a chain of `<select>` controls: choosing one repopulates the
next, each choice is a full postback, and the page's `__VIEWSTATE` carries the
position. Reaching the leaf level means walking the chain, and the row count at
the bottom is the whole reason to do it.

`aspnet` holds the per-request rules — hidden fields, the registered-value
requirement, AJAX detection, write-button refusal. This module holds the part
that spans requests: the chain walk, its resumption, and its recovery.

**Three things decide whether such a walk finishes:**

1. **One level per postback.** Setting two levels in one POST returns HTTP 200
   and a complete page whose second list is EMPTY. It looks exactly like a
   working request that found no data.
2. **Session state expires and retry cannot fix it.** After a few hundred
   postbacks every request answers HTTP 500 until the session is rebuilt.
   Backoff and jitter make it worse, because the fault is in the state, not the
   transport. The walk therefore treats a failure as "reseat": new session,
   re-select the path, continue.
3. **One dead branch must not end the crawl.** A branch that fails even after a
   reseat is skipped and deliberately left OUT of the resume set, so a later
   pass retries it rather than recording it as empty.

**Configure it with a control map, not a subclass.** `controls` names the form
control behind each level, `levels` fixes their order. `BIHAR_ICDS_ANGANWADI`
below is one worked instance, kept as data.
"""

from __future__ import annotations

import html
import re
from typing import Any, Callable, Iterable, Iterator

from .http_client import make_session


class CascadeCrawler:
    """A live session against one chain of dependent dropdowns.

    Holds the page between posts, because the server derives the next list from
    the `__VIEWSTATE` of the last response rather than from the submitted
    values alone.
    """

    def __init__(self, report_url: str, controls: dict[str, str], *,
                 session: Any | None = None, rate_limit_sec: float = 1.0,
                 user_agent: str | None = None,
                 session_factory: Callable[[], Any] | None = None) -> None:
        """`user_agent` is exposed because some deployments answer HTTP 500 to
        this package's own identifier and 200 to a browser string. See failure
        mode 10 in `aspnet`. Overriding it is a deliberate act, so there is no
        default: a caller decides, and records the decision where it is made.

        `session_factory` builds a REPLACEMENT session on reseat, and passing
        one is the caller SAYING that a rebuild is theirs to define. So it wins
        whenever it is given, including alongside an injected `session`: a
        caller whose session carries a login supplies a factory that can
        re-establish it.

        With a `session` and NO factory, the session is never replaced. This
        class did not build that session, and discarding a caller's
        authentication is not its call. A crawl in that state cannot recover
        from an expired session, and that is the caller's trade to make.
        """
        self.report_url = report_url
        self.controls = controls
        self._factory = session_factory or (
            None if session is not None
            else lambda: make_session(rate_limit_sec=rate_limit_sec,
                                      user_agent=user_agent))
        self.session = session or self._factory()
        self._selected: dict[str, str] = {}
        self.page = self._get()

    def _get(self) -> str:
        return self.session.get(self.report_url).content.decode("utf8", "replace")

    def _hidden(self, name: str) -> str:
        m = re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(name), self.page)
        return m.group(1) if m else ""

    def _selects(self) -> dict[str, list[tuple[str, str]]]:
        out = {}
        for m in re.finditer(r'<select[^>]*name="([^"]+)".*?</select>', self.page, re.S):
            out[m.group(1)] = re.findall(
                r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', m.group(0))
        return out

    def options(self, level: str) -> list[tuple[str, str]]:
        """Selectable (value, label) pairs at `level`, minus the placeholder.

        `--Select--` is dropped for a caller choosing what to crawl. It is NOT
        dropped when building a POST body: it is a registered value there, and
        omitting it is what produces the 500.
        """
        return [(v, html.unescape(t).strip())
                for v, t in self._selects().get(self.controls[level], [])
                if v not in ("", "0")]

    def _form(self, target: str) -> dict[str, str]:
        form = {
            "__EVENTTARGET": target, "__EVENTARGUMENT": "", "__LASTFOCUS": "",
            "__VIEWSTATE": self._hidden("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": self._hidden("__VIEWSTATEGENERATOR"),
            "__VIEWSTATEENCRYPTED": "",
            "__EVENTVALIDATION": self._hidden("__EVENTVALIDATION"),
        }
        # Never "" — the server answers 500 for a value it did not register.
        # The placeholder itself is usually fine: it carries value="0" on these
        # pages, which IS registered. Only a genuinely empty value is unsafe.
        for name, opts in self._selects().items():
            registered = [v for v, _ in opts if v != ""]
            form[name] = registered[0] if registered else ""
        form.update(self._selected)
        return form

    def select(self, level: str, value: str) -> None:
        """Post one level and keep the resulting page state."""
        name = self.controls[level]
        form = self._form(name)
        form[name] = value
        self._selected[name] = value
        resp = self.session.post(self.report_url, data=form,
                                 headers={"Referer": self.report_url})
        resp.raise_for_status()
        self.page = resp.content.decode("utf8", "replace")

    def reset(self) -> None:
        """Replace the session, drop every selection, and refetch.

        The stale state lives in the session cookie, so refetching with the
        SAME session returns the same HTTP 500 and the crawl never recovers.
        A session the caller injected is kept: replacing it would discard an
        authentication this class did not create.
        """
        self._selected.clear()
        if self._factory is not None:
            self.session = self._factory()
        self.page = self._get()

    def reseat(self, path: Iterable[tuple[str, str]]) -> None:
        """Rebuild the session and re-select `path` to resume where it stopped."""
        self.reset()
        for level, value in path:
            self.select(level, value)


def walk(crawler: CascadeCrawler, levels: list[str], *,
         only: dict[str, list[str]] | None = None,
         skip: set[str] | None = None) -> Iterator[dict]:
    """Every leaf row under `levels`, in order, as `{level: label, level_code: value}`.

    The last entry in `levels` is the leaf: its options are read, never posted,
    because nothing depends on them. Every earlier level is posted one at a
    time.

    `only` restricts a level to named values. `skip` holds the `_key` of
    branches already collected, so an interrupted crawl resumes without
    refetching; the level above the leaf is the unit of resumption, because it
    is the one that actually returns rows.
    """
    if len(levels) < 2:
        raise ValueError("a cascade needs at least one level to post and one to read")
    only, skip = only or {}, (skip or set())
    *walked, leaf = levels

    def descend(depth: int, path: list[tuple[str, str]], row: dict) -> Iterator[dict]:
        level = walked[depth]
        for value, label in crawler.options(level):
            if only.get(level) and value not in only[level]:
                continue
            here = path + [(level, value)]
            key = "|".join(v for _, v in here)
            last = depth == len(walked) - 1
            if last and key in skip:
                continue
            try:
                crawler.select(level, value)
            except Exception:  # noqa: BLE001 - stale session, not a transport fault
                try:
                    crawler.reseat(here)
                except Exception:  # noqa: BLE001
                    # One dead branch is not worth abandoning the crawl. It stays
                    # OUT of `skip`, so a later pass retries it instead of
                    # recording it as empty.
                    continue
            below = {**row, level: label, f"{level}_code": value}
            if last:
                for leaf_value, leaf_label in crawler.options(leaf):
                    yield {**below, leaf: leaf_label, f"{leaf}_code": leaf_value,
                           "_key": key}
            else:
                yield from descend(depth + 1, here, below)

    yield from descend(0, [], {})


def parse_labelled_code(label: str, pattern: re.Pattern[str],
                        fields: tuple[str, ...]) -> dict[str, str]:
    """Split a dropdown label whose code is embedded in the display text.

    Returns the raw label under the first field and empty strings elsewhere
    when the pattern does not match, rather than dropping the row: an
    unparseable label is still a real record.
    """
    m = pattern.match(label)
    if not m:
        return {fields[0]: label.strip(), **{f: "" for f in fields[1:]}}
    return {f: m.group(i + 1).strip() for i, f in enumerate(fields)}


# --- one worked instance, kept as data --------------------------------------
#
# Bihar's ICDS directorate runs this application as "Aangan"
# (`icdsaangan.bihar.gov.in`; the companion inspection app ships as "Aangan
# Bihar", `bih.nic.drishti`). It is the only instance of it this repo knows of:
# NIC builds a separate system per state under a separate name — Rajasthan's is
# Raj-Poshan — so another state is new work, not a new URL here.
#
# Why it is worth walking: the Centre's Poshan Tracker publishes 787 districts
# and refuses everything under them, so block, sector and individual-centre
# figures exist only on state systems. This one serves ~38 districts, 545
# projects (blocks), their sectors and every Anganwadi centre, without a login.
#
# The host publishes no robots.txt (404, which fails open), so no override is
# needed. Verified 2026-08-08: Araria enumerated to 2,806 centres, matching the
# 2,806 in the application's own DstWiseTotAWC report — two independent paths
# through the same system agreeing.

BIHAR_ICDS_ANGANWADI = {
    "report_url": ("https://icdsaangan.bihar.gov.in/AanganMandey/eAccount/"
                   "MonthWiseStatusReport.aspx"),
    "controls": {
        "fy": "ctl00$MainContent$ddlFY",
        "district": "ctl00$MainContent$ddlDistrict",
        "project": "ctl00$MainContent$ddlProject",
        "sector": "ctl00$MainContent$ddlSector",
        "awc": "ctl00$MainContent$ddlAWC",
    },
    "levels": ["district", "project", "sector", "awc"],
}

# Report pages served as plain GETs. Nine are keyed by district AND project —
# block level — and are the fastest route to aggregate indicators without
# walking the cascade at all.
BIHAR_ICDS_REPORTS = (
    "DstWiseTotAWC1", "DstWiseNotInspectedAWC", "DstWiseVoucherDetails",
    "DstProjWisePendingHororariumReasonCount", "DstWisePendingSalaryCount",
    "LSAccountStatus", "SSMonthWisePaymentStatus",
    "InstReport/DstWisePendingVerifiedAttendance",
    "InstReport/DstWisePendingVerifiedAttendanceLS",
)

#: `BALUAA MOTI TIKKAR-10209010101-S01`. Centre names carry hyphens and digits
#: of their own, so the code is anchored to the END rather than taken as the
#: second field.
AWC_LABEL = re.compile(r"(?P<name>.*)-(?P<code>\d{6,})-(?P<sector>\S+)$")
AWC_FIELDS = ("awc_name", "awc_code", "awc_sector")


def parse_awc_label(label: str) -> dict[str, str]:
    """Split an Anganwadi centre label into name, code and sector."""
    return parse_labelled_code(label, AWC_LABEL, AWC_FIELDS)


def crawler_for(preset: dict, **kw: Any) -> CascadeCrawler:
    """A crawler for a preset such as `BIHAR_ICDS_ANGANWADI`."""
    return CascadeCrawler(preset["report_url"], preset["controls"], **kw)

