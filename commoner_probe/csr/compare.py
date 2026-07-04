# SPDX-License-Identifier: MIT
"""Comparison utilities over the MCA CDM CSR corpus.

Provides functions to aggregate and compare spending of reporting companies
across multiple financial years.
"""

import csv
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, Set, Tuple

from commoner_probe.corpus import Corpus
from commoner_probe.records import ManifestMcaCsrRecord


@dataclass
class CompanySpendRecord:
    """A flattened CSR spending record for a single company in a single year."""

    company_name: str
    financial_year: str
    psu_status: str
    state: str
    sector: str
    sub_sector: str
    amount_spent: float


def iter_company_spend(corpus: Corpus) -> Iterable[CompanySpendRecord]:
    """Iterate over all CSR spending records across the MCA CSR corpus."""
    for record in corpus.manifest_mca_csr():
        if not hasattr(record, "dest") or not record.dest:
            continue
        try:
            with open(record.dest, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    amt_str = row.get("Project Amount Spent (In INR Cr.)", "0").strip()
                    try:
                        amt = float(amt_str) if amt_str else 0.0
                    except ValueError:
                        amt = 0.0

                    yield CompanySpendRecord(
                        company_name=row.get("Company Name", "").strip(),
                        financial_year=row.get("Financial Year", "").strip(),
                        psu_status=row.get("PSU/Non-PSU", "").strip(),
                        state=row.get("CSR State", "").strip(),
                        sector=row.get("CSR Development Sector", "").strip(),
                        sub_sector=row.get("CSR Sub Development Sector", "").strip(),
                        amount_spent=amt,
                    )
        except FileNotFoundError:
            continue


def aggregate_by_company(corpus: Corpus) -> Dict[str, Dict[str, float]]:
    """Aggregate spending by company and financial year.

    Returns a dict mapping company_name to a dict of {financial_year: amount_spent}.
    """
    agg = defaultdict(lambda: defaultdict(float))
    for record in iter_company_spend(corpus):
        if record.company_name:
            agg[record.company_name][record.financial_year] += record.amount_spent
    # Convert inner defaultdicts to regular dicts
    return {comp: dict(years) for comp, years in agg.items()}


def get_consistent_reporters(corpus: Corpus, min_years: int = 3) -> Set[str]:
    """Get companies that have reported CSR spending for at least `min_years` years.

    Useful for finding consistent spenders over the 10-year corpus.
    """
    agg = aggregate_by_company(corpus)
    return {comp for comp, years in agg.items() if len(years) >= min_years}


def compare_year_over_year(corpus: Corpus, company_name: str) -> Dict[str, float]:
    """Get the year-over-year spending for a specific company."""
    agg = aggregate_by_company(corpus)
    return agg.get(company_name, {})


def top_spenders(corpus: Corpus, top_n: int = 10) -> list[Tuple[str, float]]:
    """Get the top `top_n` spending companies across all years combined."""
    agg = aggregate_by_company(corpus)
    total_spend = {
        comp: sum(years.values())
        for comp, years in agg.items()
    }
    return sorted(total_spend.items(), key=lambda x: x[1], reverse=True)[:top_n]
