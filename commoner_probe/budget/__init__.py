# SPDX-License-Identifier: MIT
"""Union & state public-finance acquisition.

Consolidates the static / known-URL acquisition that previously lived in the
``budget-crawler`` repo into commoner-probe's Layer 0. The first migration
covers the pieces whose source URLs are stable and enumerable:

* **Union Budget** — Statement of Budget Estimates (SBE) "Demand for Grants"
  spreadsheets published at ``indiabudget.gov.in`` (a fixed per-fiscal-year URL
  template × demand number).
* **RBI State Finances** — the "State Finances: A Study of Budgets" publication
  page at ``rbi.org.in``, whose individual XLS/PDF documents are discovered by
  parsing the publication table.

Stateful / JavaScript-driven state portals (Gujarat ASP.NET ``__VIEWSTATE``
postback, Tamil Nadu / UP / Rajasthan ASPX) are intentionally **not** migrated
here — they need session/ViewState/browser machinery the probe HTTP layer does
not provide. They remain in ``budget-crawler`` pending a dedicated source.
"""

from .probe import (
    RBI_STATE_FINANCES_URL,
    BudgetEndpoint,
    BudgetProbe,
    parse_rbi_documents,
    union_budget_endpoints,
)

__all__ = [
    "BudgetProbe",
    "BudgetEndpoint",
    "union_budget_endpoints",
    "parse_rbi_documents",
    "RBI_STATE_FINANCES_URL",
]
