from __future__ import annotations

import csv
import json
from pathlib import Path

from commoner_probe.corpus import Corpus
from commoner_probe.csr.compare import (
    aggregate_by_company,
    compare_year_over_year,
    get_consistent_reporters,
    top_spenders,
)

def _setup_mock_corpus(tmp_path: Path):
    manifest = tmp_path / "manifest.jsonl"
    
    # Year 1
    dest1 = tmp_path / "mca_csr_company_spend_2020-21.csv"
    with open(dest1, "w", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Company Name", "Financial Year", "PSU/Non-PSU", "CSR State",
            "CSR Development Sector", "CSR Sub Development Sector",
            "Project Amount Spent (In INR Cr.)"
        ])
        writer.writerow(["Company A", "FY 2020-21", "Non-PSU", "Gujarat", "Education", "Education", "1.5"])
        writer.writerow(["Company B", "FY 2020-21", "PSU", "Delhi", "Health", "Health", "10.0"])
        writer.writerow(["Company A", "FY 2020-21", "Non-PSU", "Maharashtra", "Education", "Education", "2.0"])
    
    # Year 2
    dest2 = tmp_path / "mca_csr_company_spend_2021-22.csv"
    with open(dest2, "w", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Company Name", "Financial Year", "PSU/Non-PSU", "CSR State",
            "CSR Development Sector", "CSR Sub Development Sector",
            "Project Amount Spent (In INR Cr.)"
        ])
        writer.writerow(["Company A", "FY 2021-22", "Non-PSU", "Gujarat", "Education", "Education", "4.0"])
        writer.writerow(["Company C", "FY 2021-22", "Non-PSU", "Karnataka", "Environment", "Environment", "5.0"])
        
    records = [
        {
            "key": "MCA_CSR|FY 2020-21",
            "kind": "mca_csr_company_spend",
            "record_type": "mca_csr_company_spend",
            "year": "2020-21",
            "financial_year": "FY 2020-21",
            "filename": "mca_csr_company_spend_2020-21.csv",
            "dest": str(dest1),
            "source_page": "https://www.mcacdm.nic.in/csr-data",
            "url": "https://www.mcacdm.nic.in/cdm/export.php",
            "status": "downloaded",
            "timestamp_utc": "2026-06-16T16:19:02+00:00",
            "probed_at": "2026-06-16T16:19:02+00:00",
        },
        {
            "key": "MCA_CSR|FY 2021-22",
            "kind": "mca_csr_company_spend",
            "record_type": "mca_csr_company_spend",
            "year": "2021-22",
            "financial_year": "FY 2021-22",
            "filename": "mca_csr_company_spend_2021-22.csv",
            "dest": str(dest2),
            "source_page": "https://www.mcacdm.nic.in/csr-data",
            "url": "https://www.mcacdm.nic.in/cdm/export.php",
            "status": "downloaded",
            "timestamp_utc": "2026-06-16T16:19:02+00:00",
            "probed_at": "2026-06-16T16:19:02+00:00",
        }
    ]
    with open(manifest, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
            
    return Corpus(tmp_path)


def test_aggregate_by_company(tmp_path):
    corpus = _setup_mock_corpus(tmp_path)
    agg = aggregate_by_company(corpus)
    
    assert "Company A" in agg
    assert agg["Company A"]["FY 2020-21"] == 3.5  # 1.5 + 2.0
    assert agg["Company A"]["FY 2021-22"] == 4.0
    
    assert "Company B" in agg
    assert agg["Company B"]["FY 2020-21"] == 10.0
    
    assert "Company C" in agg
    assert agg["Company C"]["FY 2021-22"] == 5.0


def test_get_consistent_reporters(tmp_path):
    corpus = _setup_mock_corpus(tmp_path)
    # Company A reported in both years
    reporters = get_consistent_reporters(corpus, min_years=2)
    assert reporters == {"Company A"}
    
    # All reported at least once
    all_reporters = get_consistent_reporters(corpus, min_years=1)
    assert all_reporters == {"Company A", "Company B", "Company C"}


def test_compare_year_over_year(tmp_path):
    corpus = _setup_mock_corpus(tmp_path)
    yoy_a = compare_year_over_year(corpus, "Company A")
    assert yoy_a == {"FY 2020-21": 3.5, "FY 2021-22": 4.0}
    
    yoy_unknown = compare_year_over_year(corpus, "Unknown Company")
    assert yoy_unknown == {}


def test_top_spenders(tmp_path):
    corpus = _setup_mock_corpus(tmp_path)
    top = top_spenders(corpus, top_n=2)
    
    # B: 10.0
    # A: 7.5
    # C: 5.0
    
    assert len(top) == 2
    assert top[0] == ("Company B", 10.0)
    assert top[1] == ("Company A", 7.5)
