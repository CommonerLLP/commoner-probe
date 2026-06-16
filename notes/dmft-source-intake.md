# DMFT / PMKKKY Source Intake — 2026-06-16

## Local Documents Read

- iFOREST, March 2025: `District Mineral Foundation & Pradhan Mantri Khanij Kshetra Kalyan Yojana: A Decadal Assessment`.
  - Zotero full-text cache: `/Users/aakash/Zotero/storage/4DDDDD84/.zotero-ft-cache`.
  - Duplicate cache hits also exist under `BD56WRLK` and `TC8DGU5P`.
- iFOREST, September 2022: `Unlocking DMF funds to enable Clean Energy for Social Infrastructure and Livelihood in Rural Jharkhand`.
  - Zotero full-text cache: `/Users/aakash/Zotero/storage/CT6SFF5T/.zotero-ft-cache`.
- CSE, 2018: `People First: District Mineral Foundation (DMF), Status Report 2018`.
  - PDF: `/Users/aakash/Documents/mail-backup/134A8D5D-6CED-4D88-AF86-61C84E301B30/Sent Items.mbox/546C0896-1C66-4C92-A337-6D744AE2F705/Data/5/0/1/Attachments/105211/3/0.13320100_1533015489_DMF-Report2018.pdf`.
  - Extracted text during intake to `/tmp/DMF-Report2018.txt`.
- Prior CommonerLLP Gujarat DMFT work:
  - `/Users/aakash/Developer/CommonerLLP/twenty27/docs/gujarat-fra-dmft-district.md`
  - `/Users/aakash/Developer/CommonerLLP/twenty27/data/processed/gujarat_fra_dmft_current.md`

## Key DMFT Model Facts

- DMF was established through the MMDR Amendment Act, 2015, section 9B, as a benefit-sharing trust for mining-affected people and areas.
- PMKKKY was introduced in September 2015 and is implemented using DMF funds.
- iFOREST's 2025 decadal assessment treats DMFT as a district-level institution:
  - 645 DMFs across 23 states.
  - Rs 1,03,242 crore accrued over 10 years.
  - About Rs 87,957 crore sanctioned/allocated.
  - About 40% of accruals spent.
  - Odisha, Chhattisgarh, and Jharkhand account for 56% of national accruals.
  - Top 21 districts, each with at least Rs 1,000 crore accrual, account for more than 65% of DMF funds.
- The top DMF districts include key Odisha, Chhattisgarh, and Jharkhand districts:
  - Odisha: Kendujhar, Sundargarh, Angul, Jajpur, Sambalpur, Jharsuguda.
  - Chhattisgarh: Korba, Dantewada.
  - Jharkhand: Dhanbad, West Singhbhum, Chatra, Ramgarh, Bokaro.
- iFOREST's central critique:
  - no top-21 district had identified mining-affected people as DMF beneficiaries;
  - no district had published a five-year perspective plan despite the 2022 direction;
  - DMF governance is dominated by district administration and political members;
  - investments skew toward capital/infrastructure projects rather than livelihoods, skill development, and human capital.

## Adapter Implication

Do not model DMFT as one national aggregate. `commoner-probe` needs a family of Layer 0 acquisition adapters:

1. National Ministry of Mines / National DMF Portal summary records.
2. State DMFT portal adapters, first for Odisha, Chhattisgarh, and Jharkhand.
3. District-level DMFT records wherever state portals expose them.
4. Annual/audit report acquisition records for each DMF where available.

## Portal Contract Status

### Ministry of Mines / PMKKKY

Official routes are reachable, and the current Angular bundle exposes static
national DMFT CSV assets:

- `https://mines.gov.in/webportal/pmkkky`
- `https://mines.gov.in/webportal/content/dmf-collection`
- `https://mines.gov.in/webportal/assets/img/DMF_Collection.csv`
- `https://mines.gov.in/webportal/assets/img/Project_Fund_Status_Detail.csv`
- `https://mines.gov.in/webportal/assets/img/Sector_Wise_Project_Fund_Allocation.csv`
- `https://mines.gov.in/webportal/assets/img/State_wise_Project_Details.csv`

Downloaded local corpus:

- `data/mom-dmft/mines-gov-in/DMF_Collection.csv`
- `data/mom-dmft/mines-gov-in/Project_Fund_Status_Detail.csv`
- `data/mom-dmft/mines-gov-in/Sector_Wise_Project_Fund_Allocation.csv`
- `data/mom-dmft/mines-gov-in/State_wise_Project_Details.csv`

HTTP metadata observed:

- webportal bundle last modified: `Thu, 11 Jun 2026 09:15:09 GMT`
- CSV assets last modified: `Thu, 11 Jun 2026 09:15:11 GMT` to
  `Thu, 11 Jun 2026 09:15:13 GMT`

Coverage observed:

- `DMF_Collection.csv`: 23 state rows; includes Odisha, Chhattisgarh, and
  Jharkhand.
- `Project_Fund_Status_Detail.csv`: 23 state rows; includes Odisha,
  Chhattisgarh, and Jharkhand.
- `State_wise_Project_Details.csv`: 23 state rows; includes Odisha,
  Chhattisgarh, and Jharkhand.
- `Sector_Wise_Project_Fund_Allocation.csv`: 14 national sector rows.

Current limitation:

- The Ministry CSVs are national/current snapshots, not FY-wise tables.
- They prove state-level collection, project status, allocation, spend, and
  sector totals, but do not expose district/project-level details.
- Candidate national DMF hostnames checked during intake were not proven beyond
  these Ministry static assets.

### Odisha DMF

Odisha is the first adapter-ready state source.

Proven endpoints:

- homepage: `https://dmf.odisha.gov.in`
- state summary JSON:
  `https://dmf.odisha.gov.in/assets/cron_files/state_summary_data.json`
- district summary JSON:
  `https://dmf.odisha.gov.in/assets/cron_files/district_summary_data.json`
- district page example: `https://dmf.odisha.gov.in/district/KENDUJHAR`
- state report pages:
  `/report/fund_collection_report`, `/report/allocation_report`,
  `/report/sector_wise_summary_report`
- district report pages:
  `/district/KENDUJHAR/report/fund_collection_report`,
  `/district/KENDUJHAR/report/allocation_report`,
  `/district/KENDUJHAR/report/sector_wise_summary_report`
- proven DataTables POST:
  `https://dmf.odisha.gov.in/district/report/fund_collection_list`
- page-discovered POST endpoints:
  `/district/report/sector_wise_summary_list`,
  `/district/publication/annual_report_list`, likely
  `/district/report/allocation_list`

Fields observed:

- collection/accrual splits: coal/lignite, other than coal/lignite, minor
  minerals, bank interest, other, total
- allocation/sanction splits: high priority, other priority, common
  social/economic infrastructure, administrative, other, total
- expenditure/utilisation splits on the same priority groups
- project counts by priority class
- district-wise collection, project, allocation, estimation, and expenditure

Coverage caveat:

- Report dropdowns expose FY `2015-2016` through `2027-2028`.
- Current/future FY options may be zero or in-progress.
- Homepage labels still say "As on end of June 2022" while the footer says
  last updated `27/05/2026`; capture both as source metadata.

### Chhattisgarh and Jharkhand

State-level structured DMFT finance APIs were not proven during this intake.
Treat district NIC/S3WaaS pages as governance/document sources only until a
state or national structured finance endpoint is verified.

## Minimum Record Shapes To Prove

- `dmft_financial_summary`
  - state, district, financial year or as-of date
  - accrual / collection
  - allocation / sanctioned amount
  - expenditure / utilisation
  - unspent balance
  - source split where present: coal/lignite, major minerals, minor minerals
- `dmft_sector_allocation`
  - state, district, sector
  - high-priority vs other-priority classification
  - allocation and expenditure, if both are exposed
- `dmft_project`
  - state, district, project id/name
  - sector/subsector
  - location / affected area
  - sanctioned cost
  - implementing agency
  - physical progress and financial progress
  - dates and status
  - beneficiaries, if exposed
- `dmft_governance_document`
  - annual report, audit report, meeting minutes, ATRs, plans, beneficiary/affected-area lists, grievance disclosures.

## EC2 Probe Route

Local EC2 discovery found a running `news-fetch` instance:

- region: `ap-south-1`
- public IP: `13.201.62.75`
- SSH user: `ec2-user`
- local key: `~/.ssh/news-fetch-key.pem`

Use non-interactive SSH:

```bash
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -i ~/.ssh/news-fetch-key.pem ec2-user@13.201.62.75 '...'
```

This should be used for portal probing when local network access is blocked or region-sensitive.

## Source Queue

- Central / Ministry of Mines:
  - `https://mines.gov.in/webportal/pmkkky`
  - `https://mines.gov.in/webportal/content/dmf-collection`
  - National DMF Portal endpoints still need discovery.
- Odisha:
  - `https://dmf.odisha.gov.in`
  - first implementation target: static summary JSON + DataTables report POSTs.
  - priority districts: Kendujhar, Sundargarh, Angul, Jajpur, Sambalpur, Jharsuguda.
- Chhattisgarh:
  - priority districts: Korba, Dantewada.
  - state has been cited as high-transparency/high-upload to national portal.
- Jharkhand:
  - priority districts: Dhanbad, West Singhbhum, Chatra, Ramgarh, Bokaro.
  - iFOREST 2022 Jharkhand DRE report includes district-wise DMF accruals for five focus districts and sectoral spending as of February 2022.

## CSR Boundary

MCA CSR data and DMFT data must remain distinct.

- MCA CSR export compares CSR reporting/spending companies by financial year, state, sector, subsector, PSU/Non-PSU, and amount.
- It does not expose CSR consultants, vendors, or implementing agencies.
- DMFT project data may expose implementing agencies; that is a better candidate for comparing execution entities, but it is not CSR unless a source explicitly links it to CSR.
