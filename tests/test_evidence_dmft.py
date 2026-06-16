from __future__ import annotations

import csv
import json
from pathlib import Path

from commoner_probe.cli import build_parser


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def _make_mom_dmft_fixture(root: Path) -> Path:
    mom = root / "mom"
    mom.mkdir()
    _write_csv(
        mom / "DMF_Collection.csv",
        [
            [
                "Sr. No.",
                "State",
                "Amount collected in respect of Coal & Lignite",
                "Total  amount collected under DMF ",
            ],
            ["", "", "", ""],
            ["1", "Odisha", "7974.0408", "35945.386"],
            ["2", "Jharkhand", "12473.38351", "17982.8394"],
        ],
    )
    _write_jsonl(
        mom / "manifest.jsonl",
        [
            {
                "source": "mines-gov-in-dmft-static-csv",
                "filename": "DMF_Collection.csv",
                "url": "https://mines.gov.in/webportal/assets/img/DMF_Collection.csv",
                "last_modified": "Thu, 11 Jun 2026 09:15:11 GMT",
                "sha256": "abc123",
            }
        ],
    )
    return mom


def _make_sansad_fixture(root: Path) -> Path:
    sansad = root / "sansad"
    sansad.mkdir()
    _write_jsonl(
        sansad / "manifest.jsonl",
        [
            {
                "key": "LS|U|10|2024-07-25",
                "kind": "qa",
                "house": "Lok Sabha",
                "title": "District Mineral Foundation",
                "date": "2024-07-25",
                "qtype": "UNSTARRED",
                "qno": "10",
                "ministry": "MINES",
                "askers": ["MP A"],
                "source": "elibrary.sansad.in",
                "pdf_url": "https://sansad.in/getFile/qa.pdf",
            },
            {
                "key": "LS|U|11|2024-07-25",
                "kind": "qa",
                "house": "Lok Sabha",
                "title": "School meals",
                "date": "2024-07-25",
                "qtype": "UNSTARRED",
                "qno": "11",
                "ministry": "EDUCATION",
                "askers": ["MP B"],
                "source": "elibrary.sansad.in",
            },
        ],
    )
    _write_jsonl(
        sansad / "answers.jsonl",
        [
            {
                "key": "LS|U|10|2024-07-25",
                "kind": "qa_response",
                "source_pdf": "pdfs/qa.pdf",
                "extracted_at": "2024-07-26T00:00:00Z",
                "question_text": "Will the Minister state DMF collection?",
                "answer_text": "The PMKKKY details are laid on the table.",
                "confidence": 0.9,
                "extractor": "answers_regex_v1",
                "boundary_marker": "ANSWER",
            }
        ],
    )
    return sansad


def test_build_dmft_evidence_bundle_keeps_mom_and_sansad_provenance(tmp_path: Path):
    from commoner_probe.evidence import build_dmft_evidence_bundle

    mom = _make_mom_dmft_fixture(tmp_path)
    sansad = _make_sansad_fixture(tmp_path)

    bundle = build_dmft_evidence_bundle(mom_dir=mom, sansad_dir=sansad)

    executive = bundle["executive_disclosure"]
    assert executive["source"] == "mines.gov.in"
    assert executive["period_kind"] == "cumulative_snapshot"
    assert executive["data_period"] is None
    assert len(executive["records"]) == 2
    assert executive["records"][0]["source_last_modified"] == "2026-06-11T09:15:11Z"
    assert executive["records"][0]["record_type"] == "dmf_collection"
    assert executive["records"][0]["row"]["State"] == "Odisha"

    parliament = bundle["parliamentary_oversight"]
    assert parliament["source"] == "sansad.in"
    assert len(parliament["records"]) == 1
    assert parliament["records"][0]["key"] == "LS|U|10|2024-07-25"
    assert parliament["records"][0]["answers"][0]["answer_text"].startswith("The PMKKKY")


def test_evidence_dmft_cli_writes_bundle_json(tmp_path: Path):
    mom = _make_mom_dmft_fixture(tmp_path)
    sansad = _make_sansad_fixture(tmp_path)
    out = tmp_path / "bundle.json"

    parser = build_parser()
    args = parser.parse_args(
        [
            "evidence",
            "dmft",
            "--mom-dir",
            str(mom),
            "--sansad-dir",
            str(sansad),
            "--out",
            str(out),
        ]
    )
    args.func(args)

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["query"]["topic"] == "dmft"
    assert payload["executive_disclosure"]["records"][1]["row"]["State"] == "Jharkhand"
    assert payload["parliamentary_oversight"]["records"][0]["ministry"] == "MINES"
