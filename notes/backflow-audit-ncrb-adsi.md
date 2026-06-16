# Backflow Audit: NCRB and ADSI Data Acquisition

**Date:** 2026-06-16
**Source Repo:** `narcotrek` (and `twenty27` archive)
**Target Repo:** `commoner-probe`

## 1. Context & The Problem
During the `narcotrek` sessions, we identified missing years in the Crime in India (CII) Volume I statistics (2018, 2020, 2021, 2022). To close the statistics gaps, we manually fetched these PDFs and ran a local parser script (`scripts/extract_cii_ndps_state_panel.py`) to generate the NDPS state-year panel. 

Simultaneously, ADSI (Accidental Deaths & Suicides in India) logs are slated for mass-import. Currently, the acquisition and parsing logic for these official, recurring public-record datasets are deeply coupled with the domain-specific repositories.

## 2. Rule Violation
Per the **CommonerLLP Shared Infrastructure Rule**:
> `commoner-probe` is the default home for public-record acquisition, HTTP discipline, provenance manifests, schemas, and run logs. Do not build a repo-local public-source crawler when a reusable probe belongs there.

Leaving the CII and ADSI parsers in `narcotrek` or the `twenty27` archive violates this rule. These are canonical datasets that other domain repos (e.g., public health, caste violence, incarceration studies) will inevitably need.

## 3. The Audit Recommendation
**Move all NCRB (CII) and ADSI acquisition and parsing logic upstream to `commoner-probe`.**

### Proposed Architecture for `commoner-probe`:
*   **Probe Name**: `ncrb-probe`
*   **Scope**: Responsible for polling, downloading, and verifying the PDF/Excel artifacts for Crime in India (CII) and Accidental Deaths & Suicides in India (ADSI).
*   **Extraction Layer**: Host generic table-extraction scripts (e.g., extracting "Table 1A.5" format for state-wise crime incidence).
*   **Output**: Clean, standardized `state_year_panel.csv` artifacts stored in the `commoner-probe` data registry.

### Downstream Impact (for `narcotrek` and others):
*   Domain repos will **stop** holding raw NCRB PDFs in their local `/data/raw` directories.
*   Domain repos will **stop** maintaining bespoke PDF parsers.
*   Instead, `narcotrek` will symlink or pull the cleaned `ncrb_ndps_state_panel.csv` directly from `commoner-probe` and use it exclusively for domain-level interpretation (e.g., mapping movement ledgers against the baseline state panels).

## 4. Next Steps & Implementation
1. Copy the `scripts/extract_cii_ndps_state_panel.py` from `narcotrek` into a new `probes/ncrb/` directory in `commoner-probe`.
2. Refactor the script to be generalized (removing any `narcotrek` specific tagging or heuristics) and to use the `RunLog` HTTP discipline standard.
3. Migrate the `twenty27` raw PDF assets to `commoner-probe/data/raw/ncrb/`.
4. Delete the local parsers from `narcotrek` and update its data pipeline to ingest the upstream output.
