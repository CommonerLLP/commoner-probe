# CSR / CPSE CSR / DMFT Source Family Map — 2026-06-16

## 1. `mca-csr`

Canonical source: MCA CDM CSR data page.

- Source page: `https://www.mcacdm.nic.in/csr-data`
- Export endpoint: `POST https://www.mcacdm.nic.in/cdm/export.php`
- Current local corpus: `data/mca-csr`
- Portal FY coverage proven locally: FY 2014-15 through FY 2023-24.
- Local corpus status: 10 CSV files, 10 manifest records, schema-valid.

Data grain:

- reporting company
- financial year
- PSU / Non-PSU
- CSR state
- CSR development sector
- CSR sub-development sector
- project amount spent in INR crore

Use this for:

- comparing CSR reporting/spending companies;
- year, sector, subsector, state, and PSU/Non-PSU comparisons;
- identifying high-spend company-sector-state patterns.

Do not claim this source compares:

- CSR consulting companies;
- implementing agencies;
- vendors;
- NGOs;
- project execution quality.

Those entities are not in the observed MCA CSV header.

## 2. `mof-dpe-csr`

Target source family: CPSE / Department of Public Enterprises CSR.

Status: official DPE document-disclosure contract proven; spend-record contract
not yet proven.

Proven source contract:

- Site: `https://www.dpe.gov.in/`
- Frontend API base: `/cms/`
- Document categories:
  `GET https://www.dpe.gov.in/cms/wp-json/taxonomy/documents_category`
- Search:
  `GET https://www.dpe.gov.in/cms/wp-json/custom/api/search?s=CSR`
- Document listing pattern:
  `GET https://www.dpe.gov.in/cms/wp-json/document/documents?...`
- Document detail pattern:
  `GET https://www.dpe.gov.in/cms/wp-json/post-page/post?id=...`
- Document slug pattern:
  `GET https://www.dpe.gov.in/cms/wp-json/post-page/documents?slug_name=...`

CSR categories observed:

- `Corporate Social Responsibility (CSR)`:
  slug `corporate-social-responsibility-csr`, term id `425`
- `CSR`: slug `csr`, term id `407`

Observed useful records:

- `central_documents` and `documents` records for CPSE CSR guidelines.
- Public PDF URLs under `acf_data.pdf.url`, e.g. DPE CSR guideline PDFs.
- Search result title/slug/date/category metadata.

Current limitation:

- This proves official DPE CPSE CSR document disclosure.
- It does not yet prove a CPSE CSR spend table, project table, consultant table,
  or implementing-agency table.

Expected use:

- CPSE-focused CSR disclosure;
- possible administrative-ministry grouping;
- possible annual CPSE survey/report sources;
- possible public-sector CSR project/disclosure records.

Open questions:

- whether the official source exposes machine-readable CPSE CSR spend records;
- whether data is year-wise from FY 2014-15 onward;
- whether fields include state, district, sector, project, spend, implementing agency, or only aggregate CPSE totals;
- whether the Public Enterprises Survey endpoint/PDF family exposes CPSE CSR
  values with enough structure to parse.

Boundary:

- This is CSR, but it is not the MCA company-wide CSR portal.
- It should not be mixed into `mca-csr` until a common cross-source entity model exists.

## 3. `mom-dmft`

Target source family: Ministry of Mines DMFT / PMKKKY plus state DMFT portals.

Status: source model established from iFOREST/CSE/local notes. Ministry of
Mines static national CSV contract is proven. Odisha state DMF portal contract
is proven and adapter-ready for district-wise data.

Priority source layers:

- Ministry of Mines / National DMF Portal summary layer.
- Odisha state/district DMFT.
- Chhattisgarh state/district DMFT.
- Jharkhand state/district DMFT.

Proven Ministry of Mines contract:

- `https://mines.gov.in/webportal/pmkkky`
- `https://mines.gov.in/webportal/content/dmf-collection`
- `https://mines.gov.in/webportal/assets/img/DMF_Collection.csv`
- `https://mines.gov.in/webportal/assets/img/Project_Fund_Status_Detail.csv`
- `https://mines.gov.in/webportal/assets/img/Sector_Wise_Project_Fund_Allocation.csv`
- `https://mines.gov.in/webportal/assets/img/State_wise_Project_Details.csv`

Local Ministry corpus:

- `data/mom-dmft/mines-gov-in/`

Ministry coverage observed:

- 23 state rows in collection/project-status/state-project CSVs, including
  Odisha, Chhattisgarh, and Jharkhand.
- 14 national sector rows in sector-wise allocation/spend CSV.
- CSV Last-Modified values from `11 Jun 2026`.

Current Ministry limitation:

- The Ministry CSVs are national/current snapshots, not FY-wise tables.
- They prove state-level collection, project status, allocation, spend, and
  national sector totals; they do not expose district/project-level rows.

Proven Odisha contract:

- Homepage: `https://dmf.odisha.gov.in`
- State summary JSON:
  `https://dmf.odisha.gov.in/assets/cron_files/state_summary_data.json`
- District summary JSON:
  `https://dmf.odisha.gov.in/assets/cron_files/district_summary_data.json`
- District page example: `https://dmf.odisha.gov.in/district/KENDUJHAR`
- State report pages:
  `/report/fund_collection_report`, `/report/allocation_report`,
  `/report/sector_wise_summary_report`
- District report pages:
  `/district/KENDUJHAR/report/fund_collection_report`,
  `/district/KENDUJHAR/report/allocation_report`,
  `/district/KENDUJHAR/report/sector_wise_summary_report`
- Proven DataTables POST:
  `https://dmf.odisha.gov.in/district/report/fund_collection_list`
- Page-discovered POST endpoints:
  `/district/report/sector_wise_summary_list`,
  `/district/publication/annual_report_list`, likely
  `/district/report/allocation_list`

Odisha fields observed:

- collection/accrual splits: coal/lignite, other than coal/lignite, minor
  minerals, bank interest, other, total
- allocation/sanction splits: high priority, other priority, common
  social/economic infrastructure, administrative, other, total
- expenditure/utilisation splits on the same priority groups
- project counts by priority class
- district-wise collection, project, allocation, estimation, and expenditure

Odisha coverage caveat:

- Report dropdowns expose FY `2015-2016` through `2027-2028`.
- Current/future FY options may be zero or in-progress.
- Homepage labels still say "As on end of June 2022" while the footer says
  last updated `27/05/2026`; capture both as source metadata instead of
  normalizing one away.

Chhattisgarh/Jharkhand status:

- State-level structured DMFT finance APIs are not yet proven.
- District NIC/S3WaaS pages can supply governance/document records but are not
  yet reliable finance-summary sources.

Priority data grain:

- district financial summaries;
- sector allocations;
- project-level works;
- implementing agencies;
- annual reports, audit reports, plans, meeting minutes, ATRs, beneficiary/affected-area lists, grievance disclosures.

Boundary:

- DMFT is not CSR. It is a statutory mining benefit-sharing trust/fund under MMDR/PMKKKY.
- It can expose implementing agencies and project execution entities, but those should be modeled as DMFT execution entities, not CSR consultants, unless a source explicitly links them to CSR.

## Product Implication

Commoner should support comparisons at the grain the source actually proves:

- `mca-csr`: compare CSR spending/reporting companies.
- `mof-dpe-csr`: compare CPSE CSR once the official source contract is proven.
- `mom-dmft`: compare district DMFT collection, allocation, expenditure, project execution, sector skew, and implementing agencies.

Cross-source comparison should come later through explicit entity resolution, not by merging source-specific fields prematurely.
