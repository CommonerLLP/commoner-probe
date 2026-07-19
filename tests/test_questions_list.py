from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlparse

from commoner_probe.questions_list import QuestionsListProbe, parse_question_rows

PDF_BODY = b"%PDF-1.4 fake questions list body that is over one thousand bytes " + b"x" * 1100

LS_CATALOG = [
    {"loksabha": 18, "sessions": [{"sessionNo": 3, "dates": ["20/07/2026", "21/07/2026"]}]},
]

RS_CATALOG = [
    {"session": 271, "sittingDates": ["20/07/2026", "21/07/2026"]},
]


class FakeResponse:
    def __init__(self, payload=None, *, content: bytes | None = None, text: str | None = None, status: int = 200):
        self._payload = payload
        self.content = content if content is not None else (text or json.dumps(payload or "")).encode()
        self.text = text if text is not None else self.content.decode(errors="replace")
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "AllLoksabhaAndSessionDates" in url:
            return FakeResponse(LS_CATALOG)
        if "sessionDates" in url:
            return FakeResponse(RS_CATALOG)
        if "api_ls/question/questionListUrl" in url:
            return FakeResponse({
                "name": "Questions List",
                "date": "20/07/2026",
                "url": "https://sansad.in/getFile/dms/fetch/ls-q?source=dsp2",
            })
        if "api_ls/business/bulletin1Url" in url:
            return FakeResponse({"name": None, "date": None, "url": None, "fileType": None})
        if "api_ls/business/bulletin2Url" in url:
            return FakeResponse(text="")
        if "api_rs/business/questionUrls" in url:
            return FakeResponse([
                {
                    "name": "Starred Questions",
                    "type": "Starred",
                    "url": "https://sansad.in/getFile/UploadedFiles/Questions/QuestionsList/271/starred.pdf?source=rscms",
                    "date": "20/07/2026",
                },
                {
                    "name": "Unstarred Questions",
                    "type": "UnStarred",
                    "url": "https://sansad.in/getFile/UploadedFiles/Questions/QuestionsList/271/unstarred.pdf?source=rscms",
                    "date": "20/07/2026",
                },
            ])
        if "api_rs/business/bulletin1Url" in url:
            return FakeResponse({"name": None, "url": None, "date": None})
        if "api_rs/business/bulletin2Url" in url:
            return FakeResponse(text="")
        if "getFile" in url:
            assert kwargs.get("headers", {}).get("Accept") != "application/json"
            return FakeResponse(content=PDF_BODY)
        raise AssertionError(f"unrouted url: {url}")


def _probe(tmp_path, **kw):
    probe = QuestionsListProbe(tmp_path, sleep=0, from_date="2026-07-20", to_date="2026-07-20", **kw)
    probe.session = FakeSession()
    return probe


def test_ls_question_list_records_dict_response(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "commoner_probe.questions_list.extract_pdf_text",
        lambda path: "1  NATIONAL EDUCATION POLICY      MINISTRY OF EDUCATION\n",
    )
    records = _probe(tmp_path, house="ls").probe(download=True)

    assert len(records) == 1
    rec = records[0]
    assert rec["key"] == "QUESTION_LIST|LS18|3|2026-07-20|question_list"
    assert rec["house"] == "Lok Sabha"
    assert rec["loksabha"] == 18
    assert rec["session_no"] == 3
    assert rec["document_type"] == "question_list"
    assert rec["fetch_status"] == "downloaded"
    assert rec["sha256"] == hashlib.sha256(PDF_BODY).hexdigest()
    assert (tmp_path / rec["pdf_path"]).exists()
    assert rec["question_rows_extracted"] == 1

    rows = [json.loads(line) for line in (tmp_path / "questions_list.jsonl").read_text().splitlines()]
    assert rows[0]["qno"] == "1"
    assert rows[0]["subject"] == "NATIONAL EDUCATION POLICY"
    assert rows[0]["ministry"] == "MINISTRY OF EDUCATION"


def test_rs_question_urls_records_starred_and_unstarred(tmp_path):
    records = _probe(tmp_path, house="rs").probe(download=False)

    assert len(records) == 2
    assert {r["document_type"] for r in records} == {"question_list_starred", "question_list_unstarred"}
    assert {r["house"] for r in records} == {"Rajya Sabha"}
    assert all(r["fetch_status"] == "metadata_only" for r in records)
    assert all(r["pdf_path"] is None for r in records)
    manifest = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert manifest == records


def test_date_query_keys_match_frontend_contract(tmp_path):
    probe = _probe(tmp_path, house="ls")
    probe.probe(download=False)
    q_url = next(url for url in probe.session.calls if "questionListUrl" in url)
    query = parse_qs(urlparse(q_url).query)
    assert query == {"quesDay": ["20"], "quesMonth": ["7"], "quesYear": ["2026"], "locale": ["en"]}


def test_metadata_only_rerun_skips_until_download_rerun(tmp_path):
    assert len(_probe(tmp_path, house="ls").probe(download=False)) == 1
    assert _probe(tmp_path, house="ls").probe(download=False) == []
    downloaded = _probe(tmp_path, house="ls").probe(download=True)
    assert len(downloaded) == 1
    assert downloaded[0]["fetch_status"] == "downloaded"
    assert _probe(tmp_path, house="ls").probe(download=True) == []


def test_max_records_brakes_inside_multi_row_response(tmp_path):
    # the RS questionUrls endpoint returns starred+unstarred in one response;
    # the brake must stop after the first document, not the first endpoint
    records = _probe(tmp_path, house="rs").probe(download=False, max_records=1)
    assert len(records) == 1


def test_dry_run_persists_nothing(tmp_path):
    preview = _probe(tmp_path, house="ls").probe(download=False, dry_run=True)
    assert preview
    assert not (tmp_path / "manifest.jsonl").exists()
    # a real run after a dry run must not treat the documents as already seen
    records = _probe(tmp_path, house="ls").probe(download=False)
    assert len(records) == 1
    assert records[0]["pdf_url"] is not None


def test_parse_question_rows_is_conservative():
    rows = parse_question_rows(
        "noise\n"
        "42  PUBLIC LIBRARIES IN DISTRICTS      MINISTRY OF CULTURE\n"
        "not a structured row\n",
        house="Lok Sabha",
        sitting_date="2026-07-20",
        list_type="question_list",
        source_pdf="pdfs/q.pdf",
    )
    assert len(rows) == 1
    assert rows[0]["key"] == "QUESTION_ROW|Lok Sabha|2026-07-20|question_list|42"
    assert rows[0]["askers"] == []


def test_schema_bundled_and_validates(tmp_path):
    import pytest

    pytest.importorskip("jsonschema")
    from commoner_probe import Corpus, schemas
    from commoner_probe.validate import validate_corpus

    assert "manifest_question_list" in schemas.list_all()
    assert "question_list_row" in schemas.list_all()
    records = _probe(tmp_path, house="rs").probe(download=False)
    assert records
    assert validate_corpus(tmp_path, log=lambda _: None)
    assert len(list(Corpus(tmp_path).manifest_question_lists())) == 2


# --- section-aware parsing regressions (validated against the real 20-24 Jul
# --- 2026 lists; see REQ-0030) -----------------------------------------------

LS_COMBINED_SAMPLE = """\
                                             LOK SABHA
                               List of Questions for ORAL ANSWERS
                                     Total Number of Questions - 2

                                       Manuscript Heritage
†*1.      Shri Example Alpha:
          Shri Example Beta:
      Will the Minister of CULTURE
be pleased to state:
    (a) some starred limb?

                                       Digital University
*2.       Dr. Example Gamma:
      Will the Minister of EDUCATION
be pleased to state:
    (a) another starred limb?
                                        CORRIGENDUM 1
                          to the List of Questions for ORAL ANSWERS
            1                                Some correction text

                             List of Questions for WRITTEN ANSWERS
                                 Total Number of Questions - 3

                                       Model Libraries
1.        Smt. Example Delta:
      Will the Minister of CULTURE
be pleased to state:
    (a) a written limb?

                                       Petroleum Prices
2.        Com. Example Epsilon:
          Thiru Example Zeta:
      Will the Minister of PETROLEUM AND NATURAL GAS
be pleased to state:
    (a) a second written limb?

                                       Airport Works
†3.       Mrs Example Eta:
      Will the Minister of CIVIL AVIATION
be pleased to state:
    (a) a third written limb?
                                           LOK SABHA
                                          CORRIGENDA 1
                          to the List of Questions for WRITTEN ANSWERS
            2                                Insert Second name
"""

RS_UNSTARRED_SAMPLE = """\
                                        Rajya Sabha
                         List of Questions for WRITTEN ANSWERS
                             Total number of questions -- 3

                      Passport Ranking
553 Dr. Example Theta:
Will the Minister of EXTERNAL AFFAIRS be pleased to state:
(a) an unstarred limb?

                      Corruption in Federations
635 # Shri Example Iota:
Will the Minister of YOUTH AFFAIRS AND SPORTS be pleased to state:
(a) another limb?

                      Football Promotion
636 Dr. Example Kappa:
Will the Minister of YOUTH AFFAIRS AND SPORTS be pleased to state:
(a) a third limb?
"""


def test_ls_combined_pdf_splits_sections_and_keeps_overlapping_qnos():
    from commoner_probe.questions_list import stated_totals

    rows = parse_question_rows(
        LS_COMBINED_SAMPLE,
        house="Lok Sabha", sitting_date="2026-07-20",
        list_type="question_list", source_pdf="pdfs/q.pdf",
    )
    starred = [r for r in rows if r["list_type"] == "question_list_starred"]
    unstarred = [r for r in rows if r["list_type"] == "question_list_unstarred"]
    # qno 1 and 2 exist in BOTH numbering spaces and must both survive
    assert [r["qno"] for r in starred] == ["1", "2"]
    assert [r["qno"] for r in unstarred] == ["1", "2", "3"]
    assert len(rows) == sum(stated_totals(LS_COMBINED_SAMPLE)) == 5
    # corrigenda content must not be parsed as rows
    assert all("correction" not in (r["subject"] or "").lower() for r in rows)
    # clubbed askers and the † prefix survive
    assert starred[0]["askers"] == ["Shri Example Alpha", "Shri Example Beta"]
    assert unstarred[2]["askers"] == ["Mrs Example Eta"]
    # the starred and unstarred qno-1 rows carry distinct keys
    assert len({r["key"] for r in rows}) == 5


def test_rs_dotless_heads_hash_marker_and_com_honorific():
    rows = parse_question_rows(
        RS_UNSTARRED_SAMPLE,
        house="Rajya Sabha", sitting_date="2026-07-23",
        list_type="question_list_unstarred", source_pdf="pdfs/q.pdf",
    )
    assert [r["qno"] for r in rows] == ["553", "635", "636"]
    assert rows[0]["askers"] == ["Dr. Example Theta"]
    assert rows[1]["askers"] == ["Shri Example Iota"]
    # the inline "be pleased to state:" suffix must not leak into the ministry
    assert rows[0]["ministry"] == "EXTERNAL AFFAIRS"
    assert rows[1]["ministry"] == "YOUTH AFFAIRS AND SPORTS"


def test_stated_totals_and_corrigenda_helpers():
    from commoner_probe.questions_list import corrigenda_present, stated_totals

    assert stated_totals(LS_COMBINED_SAMPLE) == [2, 3]
    assert stated_totals(RS_UNSTARRED_SAMPLE) == [3]
    assert corrigenda_present(LS_COMBINED_SAMPLE)
    assert not corrigenda_present(RS_UNSTARRED_SAMPLE)


def test_manifest_carries_reconciliation_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "commoner_probe.questions_list.extract_pdf_text",
        lambda path: LS_COMBINED_SAMPLE,
    )
    records = _probe(tmp_path, house="ls").probe(download=True)
    rec = next(r for r in records if r["document_kind"] == "question_list")
    assert rec["question_rows_extracted"] == 5
    assert rec["question_rows_expected"] == 5
    assert rec["parse_status"] == "reconciled"
    assert rec["corrigenda_present"] is True


def test_count_mismatch_is_flagged_not_silent(tmp_path, monkeypatch):
    truncated = LS_COMBINED_SAMPLE.replace("†3.       Mrs Example Eta:", "not a head")
    monkeypatch.setattr(
        "commoner_probe.questions_list.extract_pdf_text",
        lambda path: truncated,
    )
    records = _probe(tmp_path, house="ls").probe(download=True)
    rec = next(r for r in records if r["document_kind"] == "question_list")
    assert rec["question_rows_extracted"] == 4
    assert rec["question_rows_expected"] == 5
    assert rec["parse_status"] == "count_mismatch"


def test_count_mismatch_rerun_reparses_without_duplicates(tmp_path, monkeypatch):
    truncated = LS_COMBINED_SAMPLE.replace("†3.       Mrs Example Eta:", "not a head")
    monkeypatch.setattr(
        "commoner_probe.questions_list.extract_pdf_text",
        lambda path: truncated,
    )
    first = _probe(tmp_path, house="ls").probe(download=True)
    assert next(r for r in first if r["document_kind"] == "question_list")["parse_status"] == "count_mismatch"

    # the mismatch is not terminal: a rerun with a fixed parser re-extracts
    monkeypatch.setattr(
        "commoner_probe.questions_list.extract_pdf_text",
        lambda path: LS_COMBINED_SAMPLE,
    )
    second = _probe(tmp_path, house="ls").probe(download=True)
    rec = next(r for r in second if r["document_kind"] == "question_list")
    assert rec["parse_status"] == "reconciled"

    # the failed parse's partial rows were replaced, not duplicated
    rows = [json.loads(line) for line in (tmp_path / "questions_list.jsonl").read_text().splitlines()]
    assert len(rows) == 5
    assert len({r["key"] for r in rows}) == 5

    # once reconciled, the document is terminal again
    assert _probe(tmp_path, house="ls").probe(download=True) == []


def test_replace_question_rows_clears_stale_rows_on_empty_reparse(tmp_path):
    probe = _probe(tmp_path, house="ls")
    stale = {"key": "QUESTION_ROW|Lok Sabha|2026-07-20|question_list|1", "source_pdf": "pdfs/a.pdf"}
    (tmp_path / "questions_list.jsonl").write_text(json.dumps(stale) + "\n")

    # a reparse yielding zero rows must clear the failed parse's rows, not keep them
    probe.replace_question_rows("pdfs/a.pdf", [])
    assert (tmp_path / "questions_list.jsonl").read_text() == ""

    # no file and no rows stays a no-op
    (tmp_path / "questions_list.jsonl").unlink()
    probe.replace_question_rows("pdfs/a.pdf", [])
    assert not (tmp_path / "questions_list.jsonl").exists()
