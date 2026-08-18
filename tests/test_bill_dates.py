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

    def test_canonical_iso_8601_is_read_not_rejected(self):
        """The shape a modernising endpoint moves TO. A field named for ISO
        must not raise on ISO."""
        assert _date("2025-12-20T00:00:00Z") == "2025-12-20"
        assert _date("2025-12-20T00:00:00") == "2025-12-20"
        assert _date("2025-12-20T05:30:00+05:30") == "2025-12-20"

    def test_the_reader_does_not_depend_on_the_interpreter(self):
        """`fromisoformat` accepts a different set of strings on every Python
        this package supports. Basic form parsed on 3.11 and raised on 3.10, so
        two machines walking the same source wrote different records."""
        with pytest.raises(UnreadableDate):
            _date("20251220")

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

    def test_the_bad_record_keeps_everything_it_could_read(self, tmp_path):
        """The failure is one FIELD. Discarding the record over it throws away
        the bill's name, ministry, status and every file URL."""
        bad = {"billNumber": "2", "billYear": "2025", "billName": "Second",
               "ministryName": "Law and Justice", "status": "Passed",
               "billIntroducedDate": "2026-08-10 00:00:00.0",
               "billAssentedDate": "last Tuesday"}
        row = self._probe(tmp_path, [bad]).probe()[0]
        assert row["fetch_status"] == "parse_error"
        assert row["assent_date"] is None
        assert row["bill_name"] == "Second"
        assert row["ministry"] == "Law and Justice"
        assert row["introduced_date"] == "2026-08-10"
        assert "assent_date" in row["error"], "the error must name the field"

    def test_every_failed_field_is_named_not_the_first_two(self, tmp_path):
        """The case this exists for is a source-wide shape change, where all six
        fail at once. One long explanation per field filled the 500-character
        budget after two, and the other four failures went unnamed."""
        bad = {"billNumber": "3", "billYear": "2025",
               "billIntroducedDate": "last Tuesday",
               "billPassedInLSDate": "last Tuesday",
               "billPassedInRSDate": "last Tuesday",
               "referredToCommitteeDate": "last Tuesday",
               "reportPresentedDate": "last Tuesday",
               "billAssentedDate": "last Tuesday"}
        row = self._probe(tmp_path, [bad]).probe()[0]
        assert row["unreadable_fields"] == [
            "assent_date", "introduced_date", "passed_ls_date", "passed_rs_date",
            "referred_to_committee_date", "report_presented_date"]
        for field in row["unreadable_fields"]:
            assert field in row["error"], field

    def test_the_brake_holds_when_the_dates_are_unreadable(self, tmp_path):
        """`--max-records 1` exists to keep a smoke test off the whole
        catalogue. A parse failure used to skip the counter, so a shape change
        in one date field walked all ten thousand records."""
        rows = self._probe(tmp_path, [
            {"billNumber": str(n), "billYear": "2025",
             "billAssentedDate": "last Tuesday"} for n in range(50)
        ]).probe(max_records=1)
        assert len(rows) == 1

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

    def test_the_replaced_row_is_gone_not_merely_outnumbered(self, tmp_path):
        """Every reader of the manifest streams every line, `Corpus
        .manifest_bills()` included. A corrected record appended BESIDE the
        wrong one serves both, doubles the catalogue, and still hands the
        `DD/MM/YYYY` value to whoever reads it."""
        from commoner_probe import Corpus

        raw = {"billNumber": "9", "billYear": "2025", "billName": "A Bill",
               "billAssentedDate": "20/12/2025"}
        legacy = self._probe(tmp_path, [raw])._record(raw, "ls")
        legacy["assent_date"] = "20/12/2025"
        self._probe(tmp_path, [raw]).append_manifest(legacy)

        self._probe(tmp_path, [raw]).probe()
        read = [r.assent_date for r in Corpus(tmp_path / "out").manifest_bills()]
        assert read == ["2025-12-20"], read

    def test_an_interrupted_repair_is_repaired_by_the_next_run(self, tmp_path):
        """The repair walks ten thousand records at half a second each. Kill it
        in that window and both rows are on disk. Resume then reads the NEWEST
        row, finds the record unchanged, and rewrites nothing — so a rule that
        only dropped what THIS run replaced left the duplicate forever."""
        from commoner_probe import Corpus

        raw = {"billNumber": "9", "billYear": "2025", "billName": "A Bill",
               "billAssentedDate": "20/12/2025"}
        probe = self._probe(tmp_path, [raw])
        legacy = probe._record(raw, "ls")
        legacy["assent_date"] = "20/12/2025"
        probe.append_manifest(legacy)
        probe.append_manifest(probe._record(raw, "ls"))   # the run dies here

        self._probe(tmp_path, [raw]).probe()
        read = [r.assent_date for r in Corpus(tmp_path / "out").manifest_bills()]
        assert read == ["2025-12-20"], read

    def test_a_line_that_is_not_an_object_does_not_stop_the_run(self, tmp_path):
        """`json.loads(line).get(...)` raises AttributeError on `null`, which
        JSONDecodeError does not cover."""
        raw = {"billNumber": "9", "billYear": "2025", "billAssentedDate": "20/12/2025"}
        probe = self._probe(tmp_path, [raw])
        probe.append_manifest({"key": "BILL|ls|2025|1", "kind": "bill_record"})
        with probe.manifest.open("a", encoding="utf-8") as f:
            f.write("null\n123\n[]\n")
        assert self._probe(tmp_path, [raw]).probe()[0]["assent_date"] == "2025-12-20"

    def test_an_unchanged_record_is_not_written_again(self, tmp_path):
        raw = {"billNumber": "9", "billYear": "2025", "billName": "A Bill",
               "billAssentedDate": "20/12/2025"}
        self._probe(tmp_path, [raw]).probe()
        again = self._probe(tmp_path, [raw]).probe()
        assert again == [], "a second run must not re-append what it already holds"


class TestAPlanningRunChangesNothing:
    """From Codex on `d1896ca`. `compact()` ran after every probe, dry runs
    included, and the state it rewrites is exactly the one an interrupted
    repair leaves. A run whose purpose is to say what WOULD happen must not
    edit the corpus it is reporting on."""

    def _probe(self, tmp_path, records):
        from commoner_probe.bill_catalog_api import BillsProbe

        probe = BillsProbe(tmp_path / "out", sleep=0, houses=["ls"])
        probe.bills_all = lambda house: iter(records)
        return probe

    def test_a_dry_run_leaves_an_interrupted_repair_alone(self, tmp_path):
        raw = {"billNumber": "9", "billYear": "2025", "billName": "A Bill",
               "billAssentedDate": "20/12/2025"}
        probe = self._probe(tmp_path, [raw])
        legacy = probe._record(raw, "ls")
        legacy["assent_date"] = "20/12/2025"
        probe.append_manifest(legacy)
        probe.append_manifest(probe._record(raw, "ls"))
        before = probe.manifest.read_text(encoding="utf-8")

        self._probe(tmp_path, [raw]).probe(dry_run=True)
        assert probe.manifest.read_text(encoding="utf-8") == before

    def test_a_real_run_still_compacts(self, tmp_path):
        """The guard must not lose the case compaction exists for."""
        from commoner_probe import Corpus

        raw = {"billNumber": "9", "billYear": "2025", "billName": "A Bill",
               "billAssentedDate": "20/12/2025"}
        probe = self._probe(tmp_path, [raw])
        legacy = probe._record(raw, "ls")
        legacy["assent_date"] = "20/12/2025"
        probe.append_manifest(legacy)
        probe.append_manifest(probe._record(raw, "ls"))

        self._probe(tmp_path, [raw]).probe()
        assert [r.assent_date for r in Corpus(tmp_path / "out").manifest_bills()] == ["2025-12-20"]


class TestTheWholeTimestampIsChecked:
    """From Codex on `d1896ca`. The pattern checked that the time half had the
    SHAPE of a time and never that it was one, so `2025-12-20T99:99:99Z` passed
    and the record shipped `ok`. A reader that exists to expose unreadable
    source shapes cannot accept an impossible one."""

    @pytest.mark.parametrize("text", [
        "2025-12-20T99:99:99Z",
        "2025-12-20T24:00:00Z",
        "2025-12-20T00:60:00Z",
        "2025-12-20T00:00:00+05:99",
        "2025-12-20T00:00:00+99:00",
    ])
    def test_an_impossible_time_raises(self, text):
        with pytest.raises(UnreadableDate):
            _date(text)

    @pytest.mark.parametrize("text", [
        "2025-12-20T00:00:00Z",
        "2025-12-20T23:59:59.123+05:30",
        "2025-12-20T05:30",
        "2025-12-20T23:59:60Z",
    ])
    def test_a_real_time_still_reads(self, text):
        assert _date(text) == "2025-12-20"


class TestCompactionTouchesOnlyItsOwnRows:
    """From Codex on `dcfa881`. Compaction indexed every keyed object in the
    file. `prs_bill_track` appends a row per status change under one key on
    purpose — Pending then Passed is the history it exists to hold — so a bills
    probe pointed at a shared corpus would delete it and report success."""

    def _probe(self, tmp_path, records):
        from commoner_probe.bill_catalog_api import BillsProbe

        probe = BillsProbe(tmp_path / "out", sleep=0, houses=["ls"])
        probe.bills_all = lambda house: iter(records)
        return probe

    def test_another_adapter_s_history_survives(self, tmp_path):
        import json

        raw = {"billNumber": "9", "billYear": "2025", "billName": "A Bill",
               "billAssentedDate": "20/12/2025"}
        probe = self._probe(tmp_path, [raw])
        for status in ("Pending", "Passed"):
            probe.append_manifest({"key": "PRS|forest-bill", "kind": "prs_bill_track",
                                   "source": "prsindia.org", "bill_status": status})
        legacy = probe._record(raw, "ls")
        legacy["assent_date"] = "20/12/2025"
        probe.append_manifest(legacy)

        self._probe(tmp_path, [raw]).probe()
        rows = [json.loads(x) for x in
                (tmp_path / "out" / "manifest.jsonl").read_text().splitlines()]
        tracked = [r["bill_status"] for r in rows if r.get("kind") == "prs_bill_track"]
        assert tracked == ["Pending", "Passed"], tracked
        assert [r["assent_date"] for r in rows if r.get("kind") == "bill_record"] == ["2025-12-20"]


class TestAnImpossibleCalendarDateStaysOneField:
    """A P1 from Codex that merged unfixed on #142. The dispatcher read one
    comment of a two-comment review."""

    def test_an_impossible_day_raises_unreadable_not_value_error(self):
        with pytest.raises(UnreadableDate):
            _date("2025-02-30T00:00:00Z", "assent_date")

    def test_the_house_survives_an_impossible_day(self, tmp_path):
        """`_record()` catches `UnreadableDate` alone, so a bare `ValueError`
        reached the house handler, wrote one `fetch_error`, and abandoned every
        later bill in that house."""
        from commoner_probe.bill_catalog_api import BillsProbe

        good = {"billNumber": "1", "billYear": "2025", "billName": "Good Bill",
                "billIntroducedDate": "2025-01-02 00:00:00.0"}
        bad = {"billNumber": "2", "billYear": "2025", "billName": "Bad Bill",
               "billIntroducedDate": "2025-02-30T00:00:00Z"}
        probe = BillsProbe(tmp_path / "out", sleep=0, houses=["ls"])
        probe.bills_all = lambda house: iter([bad, good])

        rows = probe.probe()
        assert [r["fetch_status"] for r in rows] == ["parse_error", "ok"], rows
        assert rows[0]["bill_name"] == "Bad Bill"
        assert rows[0]["introduced_date"] is None
