"""Offline contract tests for the Koha public REST API adapter."""

from __future__ import annotations

import json
import os
from urllib.parse import parse_qs, urlparse

import pytest

from commoner_probe.koha import KohaProbe


def _item(item_id: int, biblio_id: int | None = None) -> dict:
    biblio_id = biblio_id or item_id
    return {
        "item_id": item_id,
        "biblio_id": biblio_id,
        "external_id": f"B{item_id:04d}",
        "callnumber": f"C {item_id}",
        "home_library_id": "MAIN",
        "withdrawn": 0,
        "lost_status": 0,
        "biblio": {
            "biblio_id": biblio_id,
            "title": f"Held title {biblio_id}",
            "publication_year": None,
        },
    }


class FakeResponse:
    def __init__(self, payload, *, status: int = 200, total: int | None = None):
        self.payload = payload
        self.status_code = status
        self.headers = {} if total is None else {"X-Total-Count": str(total)}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, pages, *, total=0, totals=None, marc=None):
        self.pages = pages
        self.total = total
        self.totals = totals or {}
        self.marc = marc or {}
        self.calls: list[tuple[str, int]] = []

    def get(self, url, **kwargs):
        parsed = urlparse(url)
        if parsed.path.endswith("/public/items"):
            page = int(parse_qs(parsed.query)["_page"][0])
            self.calls.append(("page", page))
            value = self.pages.get(page, [])
            if isinstance(value, BaseException):
                raise value
            if isinstance(value, tuple):
                status, payload = value
            else:
                status, payload = 200, value
            return FakeResponse(
                payload,
                status=status,
                total=self.totals.get(page, self.total),
            )
        biblio_id = int(parsed.path.rsplit("/", 1)[-1])
        self.calls.append(("marc", biblio_id))
        value = self.marc[biblio_id]
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, tuple):
            status, payload = value
        else:
            status, payload = 200, value
        return FakeResponse(payload, status=status)


def _probe(tmp_path, session, *, per_page=2):
    return KohaProbe(
        tmp_path,
        base_url="https://library.example.gov.in",
        portal_name="example-library",
        per_page=per_page,
        sleep=0,
        session=session,
        log=lambda _: None,
    )


def _manifest(tmp_path):
    path = tmp_path / "manifest.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_pagination_terminates_on_short_page_and_emits_each_item_once(tmp_path):
    session = FakeSession(
        {1: [_item(1), _item(2)], 2: [_item(3), _item(4)], 3: [_item(5)]},
        total=100,
    )
    result = _probe(tmp_path, session).probe()

    assert session.calls == [("page", 1), ("page", 2), ("page", 3)]
    assert result.items_added == 5
    assert [row["item_id"] for row in _manifest(tmp_path)] == [1, 2, 3, 4, 5]
    assert all(row["kind"] == "koha_item" for row in result.records)


def test_page_resume_fetches_only_the_unfinished_page(tmp_path):
    first = FakeSession(
        {1: [_item(1), _item(2)], 2: [_item(3), _item(4)], 3: KeyboardInterrupt()},
        total=5,
    )
    with pytest.raises(KeyboardInterrupt):
        _probe(tmp_path, first).probe()

    second = FakeSession(
        {1: [_item(1), _item(2)], 2: [_item(3), _item(4)], 3: [_item(5)]},
        total=5,
    )
    result = _probe(tmp_path, second).probe()

    assert second.calls == [("page", 3)]
    assert result.items_added == 1
    assert len({row["key"] for row in _manifest(tmp_path)}) == 5


def test_failed_page_is_reported_and_does_not_abort_later_pages(tmp_path):
    session = FakeSession(
        {1: [_item(1), _item(2)], 2: (500, {}), 3: []},
        total=3,
        totals={1: 3, 3: 4},
    )
    result = _probe(tmp_path, session).probe()

    assert session.calls == [("page", 1), ("page", 2), ("page", 3)]
    assert result.failed_units[0][0] == "page:2"
    assert result.total_changed
    log = (tmp_path / "probe.log").read_text(encoding="utf-8")
    assert "page 2 failed" in log
    assert "held-item total changed from 3 to 4" in log

    retry = FakeSession({2: [_item(3)]}, total=3)
    recovered = _probe(tmp_path, retry).probe()
    assert retry.calls == [("page", 2)]
    assert recovered.items_added == 1
    assert recovered.failed_units == []


def test_max_records_stops_mid_page_without_duplicate_on_resume(tmp_path):
    pages = {1: [_item(1), _item(2)], 2: [_item(3)]}
    first = _probe(tmp_path, FakeSession(pages, total=3)).probe(max_records=1)
    assert first.truncated
    assert [row["item_id"] for row in _manifest(tmp_path)] == [1]

    second = _probe(tmp_path, FakeSession(pages, total=3)).probe()
    assert second.items_added == 2
    assert [row["item_id"] for row in _manifest(tmp_path)] == [1, 2, 3]
    assert "TRUNCATED" in (tmp_path / "probe.log").read_text(encoding="utf-8")


def test_marc_upgrade_skips_enumeration_and_counts_holdings(tmp_path):
    pages = {1: [_item(1, 10), _item(2, 10)], 2: [_item(3, 20)]}
    _probe(tmp_path, FakeSession(pages, total=3)).probe()

    marc = {
        10: {"leader": "leader-10", "fields": [{"245": {"subfields": [{"a": "Ten"}]}}]},
        20: {"leader": "leader-20", "fields": []},
    }
    session = FakeSession({}, total=3, marc=marc)
    result = _probe(tmp_path, session).probe(marc=True)

    assert session.calls == [("marc", 10), ("marc", 20)]
    assert result.biblios_added == 2
    biblios = [row for row in _manifest(tmp_path) if row["kind"] == "koha_biblio"]
    assert {row["biblio_id"]: row["holdings_count"] for row in biblios} == {10: 2, 20: 1}
    assert biblios[0]["marc"] == marc[10]


def test_marc_404_is_reported_and_other_biblios_continue(tmp_path):
    pages = {1: [_item(1, 10), _item(2, 20)] , 2: []}
    _probe(tmp_path, FakeSession(pages, total=2)).probe()
    session = FakeSession(
        {},
        marc={10: {"leader": "ok", "fields": []}, 20: (404, {})},
    )

    result = _probe(tmp_path, session).probe(marc=True)

    assert result.biblios_added == 1
    assert result.failed_units[0][0] == "biblio:20"
    assert any(row["biblio_id"] == 10 for row in result.records)


def test_marc_resume_fetches_only_unfinished_biblio(tmp_path):
    pages = {1: [_item(1, 10), _item(2, 20)], 2: []}
    _probe(tmp_path, FakeSession(pages, total=2)).probe()
    first = FakeSession(
        {},
        marc={10: {"leader": "ten", "fields": []}, 20: KeyboardInterrupt()},
    )
    with pytest.raises(KeyboardInterrupt):
        _probe(tmp_path, first).probe(marc=True)

    second = FakeSession({}, marc={20: {"leader": "twenty", "fields": []}})
    result = _probe(tmp_path, second).probe(marc=True)

    assert second.calls == [("marc", 20)]
    assert result.biblios_added == 1
    assert len([row for row in _manifest(tmp_path) if row["kind"] == "koha_biblio"]) == 2


def test_dry_run_print_shape_writes_nothing(tmp_path):
    rows = [_item(number) for number in range(1, 7)]
    result = _probe(tmp_path, FakeSession({1: rows}, total=6), per_page=10).probe(dry_run=True)

    assert result.dry_run
    assert result.held_items_total_first == 6
    assert result.derived_pages == 1
    assert len(result.records) == 5
    assert list(tmp_path.iterdir()) == []


def test_emit_callback_streams_rows_without_retaining_them(tmp_path):
    emitted = []
    session = FakeSession({1: [_item(1), _item(2)], 2: []}, total=2)
    probe = KohaProbe(
        tmp_path,
        base_url="https://library.example.gov.in",
        portal_name="example-library",
        per_page=2,
        sleep=0,
        session=session,
        log=lambda _: None,
        emit=emitted.append,
    )

    result = probe.probe()

    assert [row["item_id"] for row in emitted] == [1, 2]
    assert result.records == []
    assert result.items_added == 2


def test_schema_and_corpus_readers_cover_both_kinds(tmp_path):
    from commoner_probe import Corpus
    from commoner_probe.validate import validate_corpus

    pages = {1: [_item(1, 10)], 2: []}
    _probe(tmp_path, FakeSession(pages, total=1)).probe()
    _probe(
        tmp_path,
        FakeSession({}, marc={10: {"leader": "leader", "fields": []}}),
    ).probe(marc=True)

    assert validate_corpus(tmp_path, log=lambda _: None)
    corpus = Corpus(tmp_path)
    assert [row.item_id for row in corpus.manifest_koha_items()] == [1]
    assert [row.biblio_id for row in corpus.manifest_koha_biblios()] == [10]


def test_cli_contract_defaults_and_caps_page_size():
    from commoner_probe.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "koha",
        "--out", "out",
        "--base-url", "https://library.example.gov.in",
        "--portal-name", "example-library",
        "--embed", "holds",
    ])
    assert args.per_page == 1000
    assert args.embed == ["biblio", "holds"]
    with pytest.raises(SystemExit):
        parser.parse_args([
            "koha",
            "--out", "out",
            "--base-url", "https://library.example.gov.in",
            "--portal-name", "example-library",
            "--per-page", "1001",
        ])


@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("COMMONER_LIVE_TESTS") != "1",
    reason="set COMMONER_LIVE_TESTS=1 to run live source checks",
)
def test_live_niti_dry_run_contract(tmp_path):
    probe = KohaProbe(
        tmp_path / "must-not-be-created",
        base_url="https://library.niti.gov.in",
        portal_name="niti-aayog",
        sleep=0,
        log=lambda _: None,
    )
    result = probe.probe(dry_run=True)

    assert result.held_items_total_first is not None
    assert result.held_items_total_first > 0
    assert result.records
    assert result.records[0]["biblio"]["title"]
    assert not (tmp_path / "must-not-be-created").exists()
