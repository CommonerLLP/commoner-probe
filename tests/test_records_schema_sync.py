"""Every typed record must carry the fields its schema declares.

Two representations of the same record exist: the JSON Schema that validates
what is written, and the dataclass that reads it back. They are maintained by
hand, in different files, and nothing connected them — so a field added to a
schema could go missing from the reader indefinitely.

Measured before this guard existed: one gap in 38 pairs. `rendered_page`'s
`dry_run` was absent from `ManifestRenderedPageRecord`, so a consumer reading
that corpus through `Corpus` could not tell a preview run from a real capture —
a distinction the schema's own description calls load-bearing.

One gap is not an argument for deleting the typed layer. It is an argument for
a check, which is what this is.
"""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

import pytest

from commoner_probe import records as R
from commoner_probe import schemas as sc
from commoner_probe.validate import _schema_for_answers_kind, manifest_schema_by_kind

CORPUS_SRC = Path(__file__).resolve().parents[1] / "commoner_probe" / "corpus.py"


def _pairs() -> dict[str, str]:
    """kind -> dataclass name, read from the stream methods that yield them."""
    src = CORPUS_SRC.read_text(encoding="utf-8")
    return dict(re.findall(r'kind"\)\s*==\s*"([a-z_0-9]+)".*?\n\s+yield (\w+)\.from_dict', src, re.S))


def _schema_for(kind: str) -> str | None:
    """The schema behind a kind: manifest map, answers map, or same-named file."""
    return (
        manifest_schema_by_kind().get(kind)
        or _schema_for_answers_kind(kind)
        or (kind if kind in sc.list_all() else None)
    )


def test_the_pairing_finds_the_records_to_check():
    """Guard the guard: a regex that matched nothing would pass everything."""
    pairs = _pairs()
    assert len(pairs) > 30, f"only {len(pairs)} kind/record pairs found — the parse broke"
    assert sum(1 for k in pairs if _schema_for(k)) > 25, "too few pairs resolved to a schema"


@pytest.mark.parametrize("kind,record_name", sorted(_pairs().items()))
def test_the_record_carries_every_field_its_schema_declares(kind, record_name):
    schema_name = _schema_for(kind)
    if schema_name is None:
        pytest.skip(f"no schema resolves for kind {kind!r}")
    declared = set((sc.load(schema_name).get("properties") or {}).keys())
    carried = {f.name for f in fields(getattr(R, record_name))}
    missing = sorted(declared - carried)
    assert not missing, (
        f"{record_name} is missing {len(missing)} field(s) that "
        f"{schema_name} declares: {', '.join(missing)}. A field written to the "
        "corpus but absent from the reader is invisible to every consumer."
    )
