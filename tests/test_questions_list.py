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
