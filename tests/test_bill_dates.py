"""Every date this adapter emits is ISO, or it raises.

The bills endpoint serves TWO date formats in one record. Five fields arrive as
`YYYY-MM-DD HH:MM:SS.0`, and `billAssentedDate` alone arrives as `DD/MM/YYYY`.
The reader kept the first ten characters, so the assent date travelled through
unchanged under a field name that implies ISO.

Measured against the live catalogue on 2026-08-17 by the filer: 10,093 records,
3,576 carrying an assent date, and all 3,576 parse under `%d/%m/%Y`. Zero fail.
So the second format is a source convention, not corruption.

What it produced: a count of bills assented between 2024-08-17 and 2026-08-17
compared the string against ISO bounds and reported **0 bills in two years**. The
true figure is 53. No exception, no empty field, a clean-looking run. It was
caught by `actYear` in the same record — 14, 33 and 12 Acts for those years, and
a period with 59 Acts cannot hold zero assents.

No network.
"""

from __future__ import annotations

import pytest

from commoner_probe.bill_catalog_api import UnreadableDate, _date


class TestBothFormatsTheEndpointServes:
    def test_the_timestamp_form_becomes_iso(self):
        assert _date("2026-08-06 00:00:00.0") == "2026-08-06"

    def test_the_slashed_form_becomes_iso(self):
        """A real assent value, from the filer's reproduction."""
        assert _date("06/08/2026") == "2026-08-06"

    def test_a_bare_iso_date_survives(self):
        assert _date("2025-12-16") == "2025-12-16"

    def test_the_two_fields_of_one_record_now_agree(self):
        """`assent_date: "20/12/2025"` sat beside `passed_ls_date: "2025-12-16"`,
        and nothing told the caller."""
        assert _date("20/12/2025") == "2025-12-20"
        assert _date("2025-12-16 00:00:00.0") == "2025-12-16"


class TestAnUnreadableValueRaises:
    def test_it_names_the_value(self):
        with pytest.raises(UnreadableDate) as excinfo:
            _date("last Tuesday")
        assert "last Tuesday" in str(excinfo.value)

    def test_it_never_returns_a_truncated_string(self):
        """The old reader returned `value[:10]` for anything, which is how a
        format nobody had seen became a field nobody could compare."""
        with pytest.raises(UnreadableDate):
            _date("6 August 2026")

    def test_an_impossible_date_raises(self):
        with pytest.raises(UnreadableDate):
            _date("32/13/2026")

    def test_an_absent_value_is_none_not_an_error(self):
        """A bill with no assent date is a real state, and the commonest one."""
        assert _date(None) is None
        assert _date("") is None
        assert _date("   ") is None

    def test_a_non_string_value_raises(self):
        """An epoch number under a date field is a shape change in the source.
        Reading it as an absent date reports "this bill was never assented"."""
        with pytest.raises(UnreadableDate):
            _date(20251216)
        with pytest.raises(UnreadableDate):
            _date({"date": "2025-12-16"})


class TestTheRecordThatCarriesBoth:
    def test_every_date_field_of_one_record_is_iso(self):
        from commoner_probe.bill_catalog_api import BillsProbe

        raw = {
            "billIntroducedDate": "2026-08-10 00:00:00.0",
            "billPassedInLSDate": "2025-12-16 00:00:00.0",
            "billPassedInRSDate": "2025-12-17 00:00:00.0",
            "referredToCommitteeDate": "2025-11-02 00:00:00.0",
            "reportPresentedDate": "2025-12-01 00:00:00.0",
            "billAssentedDate": "20/12/2025",
            "billNumber": "42", "billYear": "2025", "billName": "A Bill",
        }
        record = BillsProbe.__dict__["_record"](
            object.__new__(BillsProbe), raw, "ls")
        dates = {k: v for k, v in record.items() if k.endswith("_date") and v}
        assert dates["assent_date"] == "2025-12-20"
        for field, value in dates.items():
            assert len(value) == 10 and value[4] == "-" and value[7] == "-", field


class TestOneBadDateDoesNotZeroTheHouse:
    """The crawl caught exceptions per HOUSE, so a single unreadable date would
    abort the walk and record one `fetch_error` — discarding every good record
    after it. This repo's own invariant is that one bad unit degrades a result
    and never empties it."""

    def _probe(self, tmp_path, records):
        from commoner_probe.bill_catalog_api import BillsProbe

        probe = BillsProbe(tmp_path / "out", sleep=0, houses=["ls"])
        probe.bills_all = lambda house: iter(records)
        return probe

    def test_the_good_records_survive_a_bad_one(self, tmp_path):
        good = {"billNumber": "1", "billYear": "2025", "billName": "First",
                "billIntroducedDate": "2026-08-10 00:00:00.0"}
        bad = {"billNumber": "2", "billYear": "2025", "billName": "Second",
               "billAssentedDate": "last Tuesday"}
        third = {"billNumber": "3", "billYear": "2025", "billName": "Third",
                 "billAssentedDate": "20/12/2025"}
        rows = self._probe(tmp_path, [good, bad, third]).probe()
        statuses = [r.get("fetch_status") for r in rows]
        assert statuses.count("ok") == 2, "the bad record must not take the others"
        assert "parse_error" in statuses
        bad_row = next(r for r in rows if r.get("fetch_status") == "parse_error")
        assert "last Tuesday" in (bad_row.get("error") or "")

    def test_two_bad_records_are_two_rows(self, tmp_path):
        """Both used to be written under `BILL|ls|_parse_error`, so a
        key-indexed consumer saw one failure where there are two."""
        first = {"billNumber": "7", "billYear": "2025",
                 "billAssentedDate": "last Tuesday"}
        second = {"billNumber": "8", "billYear": "2025",
                  "billAssentedDate": "6 August 2026"}
        rows = self._probe(tmp_path, [first, second]).probe()
        failed = [r for r in rows if r.get("fetch_status") == "parse_error"]
        assert len({r["key"] for r in failed}) == 2, failed


class TestTheFixReachesTheRecordsAlreadyOnDisk:
    """The changelog tells the operator to re-run the probe over the same
    directory. A key-only resume made that instruction do nothing: every bill
    was already `seen`, so the wrong dates stayed and the run looked clean."""

    def _probe(self, tmp_path, records):
        from commoner_probe.bill_catalog_api import BillsProbe

        probe = BillsProbe(tmp_path / "out", sleep=0, houses=["ls"])
        probe.bills_all = lambda house: iter(records)
        return probe

    def test_a_corrected_record_is_written_again(self, tmp_path):
        import json

        raw = {"billNumber": "9", "billYear": "2025", "billName": "A Bill",
               "billAssentedDate": "20/12/2025"}
        legacy = self._probe(tmp_path, [raw])._record(raw, "ls")
        legacy["assent_date"] = "20/12/2025"          # what the old reader wrote
        self._probe(tmp_path, [raw]).append_manifest(legacy)

        rows = self._probe(tmp_path, [raw]).probe()
        assert [r["assent_date"] for r in rows] == ["2025-12-20"]

        written = [json.loads(x) for x in
                   (tmp_path / "out" / "manifest.jsonl").read_text().splitlines()]
        assert written[-1]["assent_date"] == "2025-12-20"

    def test_an_unchanged_record_is_not_written_again(self, tmp_path):
        raw = {"billNumber": "9", "billYear": "2025", "billName": "A Bill",
               "billAssentedDate": "20/12/2025"}
        self._probe(tmp_path, [raw]).probe()
        again = self._probe(tmp_path, [raw]).probe()
        assert again == [], "a second run must not re-append what it already holds"
