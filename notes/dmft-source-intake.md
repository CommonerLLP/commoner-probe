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
  - likely DMF portal; iFOREST references National DMF Portal and Odisha DMF Portal 2025 for sector allocations.
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
