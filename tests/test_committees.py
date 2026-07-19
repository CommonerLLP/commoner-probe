"""Tests for the standing-committee crawler.

Coverage focuses on:

* Pure helpers (date parsing, ATR detection, presented_via derivation,
  composite key, slug resolution) — these encode the form-as-data
  decisions and are cheap to pin down.
* End-to-end ``crawl_ls`` / ``crawl_rs`` against a fake session, to
  guard the record shape, dedup-on-rerun, and the run-log entry that
  consumers will rely on.

The fake session pattern matches the existing test style in
``test_classifiers.py``: inject a SimpleNamespace-shaped object after
construction, no monkey-patching.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from commoner_probe.committees import (
    CommitteeProbe,
    _ls_presented_via,
    _report_type,
    parse_ls_date,
    parse_rs_date,
    report_key,
    resolve_committees,
)
from commoner_probe.topics import load_topic

# --------------------------------------------------------------------------- #
# Fake HTTP session                                                           #
# --------------------------------------------------------------------------- #


class FakeResponse:
    def __init__(self, payload: dict | None = None, status: int = 200):
        self._payload = payload or {}
        self.status_code = status

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int = 16384):  # not used in these tests
        yield b""


class FakeSession:
    """Returns canned responses keyed by URL substring match."""

    def __init__(self, routes: dict[str, dict]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(url)
        for needle, payload in self.routes.items():
            if needle in url:
                return FakeResponse(payload)
        raise AssertionError(f"FakeSession had no route matching: {url}")


# --------------------------------------------------------------------------- #
# Fixture builders                                                            #
# --------------------------------------------------------------------------- #


ROOT = Path(__file__).resolve().parents[1]


def _ls_record(report_no: int, **overrides) -> dict:
    rec = {
        "url": f"https://sansad.in/getFile/x/{report_no}.pdf",
        "urlH": f"https://sansad.in/getFile/h/{report_no}.pdf",
        "SubjectOfTheReport": f"Report on Demands for Grants {report_no}",
        "SubjectOfTheReportH": "हिंदी शीर्षक",
        "Loksabha": 18,
        "reportNo": report_no,
        "CommitteeName": "Finance       ",  # whitespace-padded, like the real API
        "PresentedInLS": "17-Mar-2026",
        "LaidInRS": "17-Mar-2026",
        "PresentedToSpeaker": None,
        "dateOfAdoption": None,
        "dateOfPresentation": None,
    }
    rec.update(overrides)
    return rec


def _rs_record(report_no: int, **overrides) -> dict:
    rec = {
        "subjectOfTheReport": f"{report_no}th Report on Demands for Grants",
        "subjectOfTheReportHindi": "हिंदी शीर्षक",
        "reportNo": report_no,
        "committeeName": None,  # always null in real API
        "dateOfAdoption": "18/03/2026",
        "dateOfPresentation": "18/03/2026",
        "url": f"https://sansad.in/getFile/rs/{report_no}.pdf",
        "urlHindi": f"https://sansad.in/getFile/rs_h/{report_no}.pdf",
    }
    rec.update(overrides)
    return rec


# --------------------------------------------------------------------------- #
# Pure-helper tests                                                           #
# --------------------------------------------------------------------------- #


class DateParsingTests(unittest.TestCase):
    def test_ls_date_iso(self):
        self.assertEqual(parse_ls_date("17-Mar-2026"), "2026-03-17")

    def test_ls_date_empty_returns_empty(self):
        self.assertEqual(parse_ls_date(None), "")
        self.assertEqual(parse_ls_date(""), "")

    def test_ls_date_malformed_falls_back_to_leading_slice(self):
        # Function falls back to first 10 chars when datetime.strptime fails.
        self.assertEqual(parse_ls_date("not a real date"), "not a real")

    def test_rs_date_iso(self):
        self.assertEqual(parse_rs_date("18/03/2026"), "2026-03-18")

    def test_rs_date_empty_returns_empty(self):
        self.assertEqual(parse_rs_date(None), "")

    def test_rs_date_malformed_falls_back_to_leading_slice(self):
        self.assertEqual(parse_rs_date("2026-03-18"), "2026-03-18")  # not DD/MM/YYYY


class ReportTypeTests(unittest.TestCase):
    def test_action_taken_phrase_is_atr(self):
        self.assertEqual(_report_type("Action Taken Report on the 35th Report"), "action_taken")

    def test_hyphenated_action_taken_is_atr(self):
        self.assertEqual(_report_type("Action-Taken Report on the 35th Report"), "action_taken")

    def test_dfg_title_classified_as_dfg(self):
        # v0.6.3: was "original" (binary classifier); now distinguishes
        # Demands-for-Grants reports as a first-class category.
        self.assertEqual(
            _report_type("Report on Demands for Grants 2026-27"),
            "demands_for_grants",
        )

    def test_none_title_is_other(self):
        # v0.6.3: empty/missing titles map to "other" (was "original")
        # so the absence of a classifier is visible to consumers.
        self.assertEqual(_report_type(None), "other")


class PresentedViaTests(unittest.TestCase):
    def test_both_houses(self):
        self.assertEqual(
            _ls_presented_via({"PresentedInLS": "17-Mar-2026", "LaidInRS": "17-Mar-2026"}),
            "both_houses",
        )

    def test_ls_only(self):
        self.assertEqual(
            _ls_presented_via({"PresentedInLS": "17-Mar-2026", "LaidInRS": ""}),
            "ls_only",
        )

    def test_rs_only(self):
        self.assertEqual(
            _ls_presented_via({"PresentedInLS": None, "LaidInRS": "17-Mar-2026"}),
            "rs_only",
        )

    def test_speaker_only(self):
        self.assertEqual(
            _ls_presented_via({"PresentedToSpeaker": "17-Mar-2026"}),
            "speaker_only",
        )

    def test_none(self):
        self.assertEqual(_ls_presented_via({}), "none")


class ReportKeyTests(unittest.TestCase):
    def test_ls_key_includes_lok_sabha_number(self):
        self.assertEqual(report_key("ls", "finance", 35, ls_no=18), "LS|finance|35|18")

    def test_rs_key_omits_lok_sabha_number(self):
        # Even if ls_no is supplied, RS keys must not include it.
        self.assertEqual(report_key("rs", "health", 174, ls_no=18), "RS|health|174")

    def test_handles_none_report_no(self):
        self.assertEqual(report_key("rs", "health", None), "RS|health|X")


class ResolveCommitteesTests(unittest.TestCase):
    def test_none_returns_all_sorted(self):
        ls = resolve_committees("ls", None)
        self.assertIn("finance", ls)
        self.assertIn("agriculture", ls)
        self.assertEqual(ls, sorted(ls))

    def test_subset_is_kept_in_caller_order(self):
        self.assertEqual(
            resolve_committees("ls", ["defence", "finance"]),
            ["defence", "finance"],
        )

    def test_unknown_slug_raises(self):
        with self.assertRaises(ValueError):
            resolve_committees("ls", ["does_not_exist"])

    def test_rs_aliases_resolve_to_canonical_slug(self):
        self.assertEqual(resolve_committees("rs", ["culture"]), ["transport"])
        self.assertEqual(resolve_committees("rs", ["environment"]), ["science"])

    def test_alias_plus_canonical_collapses_to_one_probe(self):
        self.assertEqual(resolve_committees("rs", ["transport", "culture"]), ["transport"])

    def test_default_all_has_no_alias_duplicates(self):
        rs = resolve_committees("rs", None)
        self.assertEqual(len(rs), len(set(rs)))
        self.assertNotIn("culture", rs)
        self.assertNotIn("environment", rs)

    def test_aliases_are_house_scoped(self):
        with self.assertRaises(ValueError):
            resolve_committees("ls", ["culture"])


# --------------------------------------------------------------------------- #
# crawl_ls / crawl_rs end-to-end with a fake session                          #
# --------------------------------------------------------------------------- #


class CrawlIntegrationTests(unittest.TestCase):
    def setUp(self):
        # Reuse the shipped libraries profile; profile content determines
        # which queries/ministries are used during crawl.
        self.topic = load_topic(ROOT / "examples" / "topics" / "libraries.json")
        self.profile_path = ROOT / "examples" / "topics" / "libraries.json"

    def _probe(self, tmp: str, routes: dict[str, dict]) -> CommitteeProbe:
        probe = CommitteeProbe(
            self.topic,
            Path(tmp),
            sleep=0,
            lok_sabha_no=18,
            topic_path=self.profile_path,
        )
        probe.session = FakeSession(routes)
        return probe

    def test_probe_ls_emits_records_with_form_as_data_fields(self):
        page1 = {
            "_metadata": {"totalPages": 1},
            "records": [
                _ls_record(35),
                _ls_record(34, PresentedInLS="17-Mar-2026", LaidInRS=None),  # ls_only
                _ls_record(33, **{"SubjectOfTheReport": "Action Taken Report on 30th"}),  # ATR
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            probe = self._probe(tmp, {"api_ls/committee": page1})
            added = probe.probe_ls(
                set(),
                committees=["finance"],
                from_date=None,
                to_date=None,
                max_records=None,
                download=False,
            )
            self.assertEqual(added, 3)
            records = [
                json.loads(line) for line in (Path(tmp) / "manifest.jsonl").read_text().splitlines()
            ]

        by_no = {r["report_no"]: r for r in records}
        self.assertEqual(by_no[35]["presented_via"], "both_houses")
        self.assertEqual(by_no[34]["presented_via"], "ls_only")
        self.assertEqual(by_no[33]["report_type"], "action_taken")
        # v0.6.3: report 35 is "Demands for Grants of the Ministry of
        # Statistics and Programme Implementation" — formerly bucketed
        # as "original", now classified as a DFG report.
        self.assertEqual(by_no[35]["report_type"], "demands_for_grants")
        for r in records:
            self.assertEqual(r["language_classified"], ["en"])
            self.assertEqual(r["kind"], "committee_report")
            self.assertEqual(r["committee_slug"], "finance")
            self.assertEqual(r["committee_name"], "Finance")  # display name, not API's padded one
            self.assertTrue(r["run_id"])  # non-empty

    def test_probe_rs_emits_records_with_rs_only_presented_via(self):
        page1 = {
            "_metadata": {"totalPages": 1},
            "records": [_rs_record(174), _rs_record(173)],
        }
        with tempfile.TemporaryDirectory() as tmp:
            probe = self._probe(tmp, {"api_rs/committee": page1})
            added = probe.probe_rs(
                set(),
                committees=["health"],
                from_date=None,
                to_date=None,
                max_records=None,
                download=False,
            )
            self.assertEqual(added, 2)
            records = [
                json.loads(line) for line in (Path(tmp) / "manifest.jsonl").read_text().splitlines()
            ]

        for r in records:
            self.assertEqual(r["presented_via"], "rs_only")
            self.assertEqual(r["committee_name"], "Health and Family Welfare")
            self.assertEqual(r["language_classified"], ["en"])
            self.assertEqual(r["date"], "2026-03-18")

    def test_dedup_on_rerun_against_seen_keys(self):
        page1 = {"_metadata": {"totalPages": 1}, "records": [_ls_record(35)]}
        with tempfile.TemporaryDirectory() as tmp:
            probe = self._probe(tmp, {"api_ls/committee": page1})
            probe.probe_ls(set(), committees=["finance"], from_date=None,
                             to_date=None, max_records=None, download=False)

            seen = probe.load_seen()
            self.assertIn("LS|finance|35|18", seen)

            # Second invocation: same payload, same key — should add nothing.
            added2 = probe.probe_ls(seen, committees=["finance"], from_date=None,
                                       to_date=None, max_records=None, download=False)
            self.assertEqual(added2, 0)

    def test_runs_jsonl_records_apparatus_with_topic_hash(self):
        page1 = {"_metadata": {"totalPages": 1}, "records": [_ls_record(35)]}
        with tempfile.TemporaryDirectory() as tmp:
            probe = self._probe(tmp, {"api_ls/committee": page1})
            probe.probe_ls(set(), committees=["finance"], from_date=None,
                             to_date=None, max_records=None, download=False)

            run_lines = (Path(tmp) / "_runs.jsonl").read_text().splitlines()
            manifest_lines = (Path(tmp) / "manifest.jsonl").read_text().splitlines()

        self.assertEqual(len(run_lines), 1)
        run = json.loads(run_lines[0])
        rec = json.loads(manifest_lines[0])

        # Categories travel with records: run_id pin, hash present.
        self.assertEqual(rec["run_id"], run["run_id"])
        self.assertTrue(run["topic_hash"].startswith("sha256:"))
        self.assertEqual(run["classifier_mode"], "")
        self.assertEqual(run["scope"]["house"], "ls")
        self.assertEqual(run["scope"]["committees"], ["finance"])
        self.assertEqual(run["added"], 1)
        self.assertEqual(run["errors"], [])


# --------------------------------------------------------------------------- #
# PDF download headers — REQ-0005 latent-406 class                            #
# --------------------------------------------------------------------------- #


class PdfDownloadHeaderTests(unittest.TestCase):
    """Binary PDF fetches must not carry the JSON Accept header (the
    REQ-0005 rsdoc.nic.in 406 class). Live check 2026-07-17: sansad.in/getFile
    currently tolerates the header (206 application/pdf both ways) — this pins
    the hardening. API calls must keep the JSON Accept header."""

    def setUp(self):
        self.topic = load_topic(ROOT / "examples" / "topics" / "libraries.json")
        self.profile_path = ROOT / "examples" / "topics" / "libraries.json"
        self.captured: list[tuple[str, dict]] = []

    def _capture_probe(self, tmp: str, routes: dict[str, dict]) -> CommitteeProbe:
        captured = self.captured

        class HeaderCaptureSession(FakeSession):
            def get(self, url, **kwargs):
                captured.append((url, kwargs.get("headers") or {}))
                return super().get(url, **kwargs)

        probe = CommitteeProbe(
            self.topic,
            Path(tmp),
            sleep=0,
            lok_sabha_no=18,
            topic_path=self.profile_path,
        )
        probe.session = HeaderCaptureSession(routes)
        return probe

    def test_ls_pdf_download_drops_json_accept_but_api_keeps_it(self):
        page1 = {"_metadata": {"totalPages": 1}, "records": [_ls_record(35)]}
        with tempfile.TemporaryDirectory() as tmp:
            probe = self._capture_probe(tmp, {"api_ls/committee": page1, "getFile": {}})
            probe.probe_ls(set(), committees=["finance"], from_date=None, to_date=None,
                           max_records=None, download=True)
        api_headers = [h for url, h in self.captured if "api_ls" in url]
        pdf_headers = [h for url, h in self.captured if "getFile" in url]
        self.assertTrue(api_headers and pdf_headers)
        for h in api_headers:
            self.assertEqual(h.get("Accept"), "application/json")
        for h in pdf_headers:
            self.assertNotIn("Accept", h)

    def test_rs_pdf_download_drops_json_accept_keeps_referer(self):
        page1 = {"_metadata": {"totalPages": 1}, "records": [_rs_record(174)]}
        with tempfile.TemporaryDirectory() as tmp:
            probe = self._capture_probe(tmp, {"api_rs/committee": page1, "getFile": {}})
            probe.probe_rs(set(), committees=["health"], from_date=None, to_date=None,
                           max_records=None, download=True)
        pdf_headers = [h for url, h in self.captured if "getFile" in url]
        self.assertTrue(pdf_headers)
        for h in pdf_headers:
            self.assertNotIn("Accept", h)
            self.assertEqual(h.get("Referer"), "https://sansad.in/rs/committees")


if __name__ == "__main__":
    unittest.main()
