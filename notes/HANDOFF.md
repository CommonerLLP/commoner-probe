# Handoff — commoner-probe — 2026-06-16

## What Changed This Session

- Verified the live MCA CDM CSR export contract:
  - source page: `https://www.mcacdm.nic.in/csr-data`
  - export endpoint: `POST https://www.mcacdm.nic.in/cdm/export.php`
  - verified response: `text/csv`, `Content-Disposition: attachment; filename="CSR_Report_2026-06-16.csv"`
  - verified FY 2022-23 CSV header: `Company Name`, `Financial Year`, `PSU/Non-PSU`, `CSR State`, `CSR Development Sector`, `CSR Sub Development Sector`, `Project Amount Spent (In INR Cr.)`
- Updated `commoner_probe.csr.mca` from the old placeholder `CSR_Excel_Export` route to the live `csr-data` / `cdm/export.php` contract.
- Added `manifest_mca_csr` schema, validation routing, `ManifestMcaCsrRecord`, and `Corpus.manifest_mca_csr()`.
- Added `commoner-probe mca-csr --years ... --out ...` with `--dry-run`.

## What Is Next

- Build finance document-disclosure adapters from SevenT4 Ahmedabad and Delhi work.
- File/track the NCRB/ADSI backflow audit once `bd` is available again.
- Next org-wide gate remains in `partial-recall`: external adapter registry/plugin mechanism.

## Verification

- `pytest tests/test_csr_mca.py -q` -> 7 passed.
- `pytest tests/test_schemas.py -q` -> 33 passed.
