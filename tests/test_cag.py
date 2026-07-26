"""Tests for the CAG State Finance Accounts (Vol-II) acquisition adapter.

The listing HTML mirrors the live cag.gov.in State-Accounts "Finance Accounts"
tab (``#tab-359``, verified against Gujarat/Telangana 2026-07-23): fiscal-year
``accTrigger`` headers, each owning ``<li>`` rows whose title sits in an ``<h5>``
inside the first anchor and whose PDF is the first ``.pdf`` href. The fixture
includes the real Telangana quirk — a Vol-II whose filename is ``Volume-ll``
(lowercase L-L typo) carrying no fiscal year, so the year must come from the
header, not the URL. No network.
"""

from __future__ import annotations

import hashlib
import json

from commoner_probe.cag import (
    CAG_ACCOUNTS_STATES,
    CAGAccountsProbe,
    get_state,
    parse_finance_accounts_tab,
)

# Faithful trim of the live tab-359 markup. Two years; each year a Vol-I and a
# Vol-II; the 2023-24 Vol-II uses the year-less "Volume-ll" typo. A trailing
# tab-360 (Monthly Key Indicators) section carries a stray PDF that must NOT be
# harvested — it is outside the Finance Accounts tab.
ACCOUNTS_HTML = """
<html><body>
<div class="tabContentMain">
  <div id="tab-358" class="tabContent">
    <div class="accTrigger"> 2023 - 24</div>
    <a href="/uploads/state_accounts_report/account-report-Appropriation-2023-24-aaa.pdf"><h5>Appropriation Accounts</h5></a>
  </div>
  <div id="tab-359" class="tabContent">
    <div class="accordionMain">
      <div class="accTrigger"> 2024 - 25</div>
      <div class="accordDetail"><ul class="guidelinesList accountGuid">
        <li>
          <a href="/uploads/state_accounts_report/account-report-FA-VOL-I-2024-25-abc.pdf" target="_blank"><h5>Finance Accounts-Vol I</h5></a>
          <div class="guidelinesPdfIcons"><sub>
            <a href="https://cag.gov.in/uploads/state_accounts_report/account-report-FA-VOL-I-2024-25-abc.pdf" title="pdf" target="_blank"><img src="/x.png"/></a> (1.03 MB)
            <a href="https://cag.gov.in/uploads/state_accounts_report/account-report-FA-VOL-I-2024-25-abc.pdf" download><b>Download</b></a>
          </sub></div>
        </li>
        <li>
          <a href="/uploads/state_accounts_report/account-report-FA-VOL-II-2024-25-def.pdf" target="_blank"><h5>Finance Accounts-Vol II</h5></a>
          <div class="guidelinesPdfIcons"><sub>
            <a href="https://cag.gov.in/uploads/state_accounts_report/account-report-FA-VOL-II-2024-25-def.pdf" title="pdf"><img src="/x.png"/></a> (2.10 MB)
            <a href="https://cag.gov.in/uploads/state_accounts_report/account-report-FA-VOL-II-2024-25-def.pdf" download><b>Download</b></a>
          </sub></div>
        </li>
      </ul></div>
      <div class="accTrigger"> 2023 - 24</div>
      <div class="accordDetail"><ul class="guidelinesList accountGuid">
        <li>
          <a href="/uploads/state_accounts_report/account-report-FA-VOL-I-2023-24-ghi.pdf" target="_blank"><h5>Finance Accounts-Vol I</h5></a>
          <div class="guidelinesPdfIcons"><sub>
            <a href="https://cag.gov.in/uploads/state_accounts_report/account-report-FA-VOL-I-2023-24-ghi.pdf" download><b>Download</b></a>
          </sub></div>
        </li>
        <li>
          <a href="/uploads/state_accounts_report/account-report-Finance-Accounts-Volume-ll-jkl.pdf" target="_blank"><h5>Finance Accounts-Vol II</h5></a>
          <div class="guidelinesPdfIcons"><sub>
            <a href="https://cag.gov.in/uploads/state_accounts_report/account-report-Finance-Accounts-Volume-ll-jkl.pdf" download><b>Download</b></a>
          </sub></div>
        </li>
      </ul></div>
    </div>
  </div>
  <div id="tab-360" class="tabContent">
    <div class="accTrigger"> 2024 - 25</div>
    <a href="/uploads/state_accounts_report/account-report-Monthly-Key-Indicators-mmm.pdf"><h5>MKI</h5></a>
  </div>
</div>
</body></html>
"""

GJ = get_state("Gujarat")

VOL2_2425 = "https://cag.gov.in/uploads/state_accounts_report/account-report-FA-VOL-II-2024-25-def.pdf"
VOL2_2324 = "https://cag.gov.in/uploads/state_accounts_report/account-report-Finance-Accounts-Volume-ll-jkl.pdf"

PDF_WITH_TEXT = b"%PDF-1.7 born digital " + b"x" * 2000
NOT_A_PDF = b"<html>WAF interstitial</html>"


class FakeResponse:
    def __init__(self, *, text=None, content=None, status=200, content_type=None):
        self.text = text
        self.content = content
        self.status_code = status
        self.headers = {"Content-Type": content_type} if content_type else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, *, pdf_for_url=None):
        self.pdf_for_url = pdf_for_url or {}
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url.startswith("https://cag.gov.in/en/state-accounts-report"):
            return FakeResponse(text=ACCOUNTS_HTML)
        if url in self.pdf_for_url:
            return FakeResponse(content=self.pdf_for_url[url], content_type="application/pdf")
        raise AssertionError(f"unrouted url: {url}")


def _probe(tmp_path, **kw):
    probe = CAGAccountsProbe(tmp_path, sleep=0)
    probe.session = FakeSession(**kw)
    return probe


# --- registry ----------------------------------------------------------------


def test_registry_covers_verified_states_only():
    names = {s.name for s in CAG_ACCOUNTS_STATES}
    assert "Tamil Nadu" in names and "Telangana" in names and "Karnataka" in names
    assert len(CAG_ACCOUNTS_STATES) == 25
    # The seven documented blockers must never be in the crawlable registry.
    ids = {s.state_id for s in CAG_ACCOUNTS_STATES}
    for blocked in (86, 92, 69, 70, 366, 74, 380):
        assert blocked not in ids


def test_get_state_by_name_and_id():
    assert get_state("gujarat").state_id == 71
    assert get_state(71).name == "Gujarat"
    assert get_state("71").state_id == 71


def test_get_state_unavailable_id_explains():
    import pytest

    with pytest.raises(KeyError, match="Vol-II"):
        get_state(86)  # Rajasthan — Vol-I only


def test_get_state_unknown_raises():
    import pytest

    with pytest.raises(KeyError):
        get_state("Atlantis")


# --- parser ------------------------------------------------------------------


def test_parse_reads_year_from_header_and_classifies_volumes():
    docs = parse_finance_accounts_tab(ACCOUNTS_HTML, GJ)
    got = {(d["year"], d["volume"]) for d in docs}
    assert got == {("2024-25", "I"), ("2024-25", "II"), ("2023-24", "I"), ("2023-24", "II")}
    # The year-less "Volume-ll" file gets its year from the accTrigger header.
    vol2_2324 = next(d for d in docs if d["year"] == "2023-24" and d["volume"] == "II")
    assert vol2_2324["url"] == VOL2_2324


def test_parse_excludes_documents_outside_the_finance_accounts_tab():
    docs = parse_finance_accounts_tab(ACCOUNTS_HTML, GJ)
    urls = " ".join(d["url"] for d in docs)
    assert "Appropriation" not in urls  # tab-358, before #tab-359
    assert "Monthly-Key-Indicators" not in urls  # tab-360, after the slice


def test_parse_dedupes_the_three_anchors_per_document():
    docs = parse_finance_accounts_tab(ACCOUNTS_HTML, GJ)
    assert len(docs) == 4  # 2 years x (Vol-I + Vol-II), not 12


def test_parse_empty_when_no_finance_accounts_tab():
    assert parse_finance_accounts_tab("<html><body>nothing</body></html>", GJ) == []


# --- probe -------------------------------------------------------------------


def test_probe_vol2_download_with_provenance_and_text_layer(tmp_path, monkeypatch):
    from commoner_probe import cag as cag_mod

    monkeypatch.setattr(cag_mod, "extract_pdf_text", lambda p: "extracted " * 50)
    probe = _probe(tmp_path, pdf_for_url={VOL2_2425: PDF_WITH_TEXT, VOL2_2324: PDF_WITH_TEXT})
    records = probe.probe(GJ, volumes=["II"])
    assert [r["status"] for r in records] == ["downloaded", "downloaded"]
    by_year = {r["year"]: r for r in records}
    assert by_year["2024-25"]["volume"] == "II"
    assert by_year["2024-25"]["text_layer"] is True
    assert by_year["2024-25"]["key"] == "CAG_STATE_ACCOUNT|71|2024-25|vol-ii"
    assert by_year["2024-25"]["kind"] == "cag_state_account"
    assert by_year["2024-25"]["sha256"] == hashlib.sha256(PDF_WITH_TEXT).hexdigest()
    assert (tmp_path / "gujarat" / by_year["2024-25"]["filename"]).read_bytes() == PDF_WITH_TEXT
    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert len(manifest) == 2


def test_probe_year_and_volume_filters(tmp_path, monkeypatch):
    from commoner_probe import cag as cag_mod

    monkeypatch.setattr(cag_mod, "extract_pdf_text", lambda p: "")
    probe = _probe(tmp_path, pdf_for_url={VOL2_2324: PDF_WITH_TEXT})
    records = probe.probe(GJ, years=["2023-24"], volumes=["II"])
    assert len(records) == 1
    assert records[0]["year"] == "2023-24" and records[0]["volume"] == "II"


def test_probe_dry_run_lists_without_downloading(tmp_path):
    probe = _probe(tmp_path)
    records = probe.probe(GJ, volumes=["II"], dry_run=True)
    assert {r["year"] for r in records} == {"2024-25", "2023-24"}
    assert all(r["status"] == "dry_run" for r in records)
    assert not (tmp_path / "manifest.jsonl").exists()
    # Only the listing page was fetched; no PDF bodies.
    assert len(probe.session.calls) == 1


def test_probe_skips_existing_file(tmp_path, monkeypatch):
    from commoner_probe import cag as cag_mod

    monkeypatch.setattr(cag_mod, "extract_pdf_text", lambda p: "")
    probe = _probe(tmp_path, pdf_for_url={VOL2_2425: PDF_WITH_TEXT})
    first = probe.probe(GJ, years=["2024-25"], volumes=["II"])
    second = probe.probe(GJ, years=["2024-25"], volumes=["II"])
    assert first[0]["status"] == "downloaded"
    assert second[0]["status"] == "skipped_exists"
    assert probe.session.calls.count(VOL2_2425) == 1


def test_non_pdf_body_is_recorded_as_error_not_written(tmp_path):
    probe = _probe(tmp_path, pdf_for_url={VOL2_2425: NOT_A_PDF})
    records = probe.probe(GJ, years=["2024-25"], volumes=["II"])
    assert records[0]["status"] == "error"
    assert "sha256" not in records[0]
    assert not (tmp_path / records[0]["filename"]).exists()


def test_records_validate_against_schema(tmp_path, monkeypatch):
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        import pytest
        pytest.skip("jsonschema not installed")
    from commoner_probe import cag as cag_mod
    from commoner_probe import schemas

    monkeypatch.setattr(cag_mod, "extract_pdf_text", lambda p: "")
    schema = schemas.load("manifest_cag_state_account")
    probe = _probe(tmp_path, pdf_for_url={VOL2_2425: PDF_WITH_TEXT, VOL2_2324: PDF_WITH_TEXT})
    for record in probe.probe(GJ, volumes=["II"]):
        Draft202012Validator(schema).validate(record)


def test_schema_registered_for_validation_and_corpus(tmp_path):
    """The kind must be wired end to end, not just have a schema file.

    A schema that `validate` never selects is worse than no schema: the corpus
    silently passes validation while nothing has actually been checked.
    """
    import pytest

    pytest.importorskip("jsonschema")
    from commoner_probe import schemas
    from commoner_probe.cag import CAGAccountsProbe
    from commoner_probe.corpus import Corpus
    from commoner_probe.validate import _pick_schema_name, validate_corpus

    assert "manifest_cag_state_account" in schemas.list_all()
    assert _pick_schema_name({"kind": "cag_state_account"}) == "manifest_cag_state_account"

    probe = CAGAccountsProbe(tmp_path, sleep=0)
    record = probe._record(
        {
            "state_id": 71,
            "state": "Gujarat",
            "year": "2023-24",
            "volume": "II",
            "title": "Finance Accounts 2023-24 Volume II",
            "url": "https://cag.gov.in/uploads/gujarat-vol-2.pdf",
        },
        status="downloaded",
    )
    probe.append_manifest(record)

    assert validate_corpus(tmp_path, log=lambda _: None)

    rows = list(Corpus(tmp_path).manifest_cag_state_accounts())
    assert len(rows) == 1
    assert rows[0].state == "Gujarat"
    assert rows[0].volume == "II"
    assert rows[0].year == "2023-24"
