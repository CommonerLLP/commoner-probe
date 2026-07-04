# NeVA High-Value Targets Analysis

Based on the coverage probe of NeVA portals (`data/neva-coverage/neva-coverage.jsonl`), we have identified the highest value targets for state assembly and council crawling. 

Targets were ranked based on the volume of accessible data, specifically looking at:
- `questions_sample`: Number of questions retrieved
- `papers_sample`: Number of papers/documents retrieved
- `dates_found`: Volume of searchable session dates
- `members_count`: Verification of accessible member rosters

## Tier 1: Rich Data Availability
These portals yielded significant samples of both Questions and Papers, demonstrating that their documents are readily accessible for scraping.

| State | Chamber | Portal Code | Questions Sample | Papers Sample | Dates Found | Members Count |
|-------|---------|-------------|------------------|---------------|-------------|---------------|
| Gujarat | Assembly | `gujarat` | 137 | 24 | 27 | 181 |
| Mizoram | Assembly | `mizo` | 20 | 19 | 17 | 40 |
| Himachal Pradesh | Assembly | `hpvs` | 19 | 13 | 16 | 68 |
| Arunachal Pradesh | Assembly | `arla` | 25 | 11 | 1 | 59 |
| Bihar | Council | `blc` | 29 | 5 | 20 | 67 |

## Tier 2: Partial Data Availability
These portals yielded either Questions or Papers, but not heavily both. They are solid secondary targets.

| State | Chamber | Portal Code | Questions Sample | Papers Sample | Dates Found | Members Count |
|-------|---------|-------------|------------------|---------------|-------------|---------------|
| Rajasthan | Assembly | `raj` | 22 | 0 | 24 | 200 |
| Meghalaya | Assembly | `mgla` | 28 | 0 | 10 | 60 |
| Sikkim | Assembly | `sikkim` | 0 | 28 | 1 | 32 |
| Manipur | Assembly | `manipur` | 2 | 4 | 10 | 58 |
| Nagaland | Assembly | `nagaland` | 0 | 7 | 7 | 60 |
| Tripura | Assembly | `tripura` | 0 | 4 | 8 | 60 |

## Tier 3: High Session/Date Volume (Missing Document Samples)
These portals have a high number of dates available but returned 0 sample documents. They are high-potential but might require parser adaptations to fetch documents.

| State | Chamber | Portal Code | Questions Sample | Papers Sample | Dates Found | Members Count |
|-------|---------|-------------|------------------|---------------|-------------|---------------|
| Jharkhand | Assembly | `jhla` | 0 | 0 | 41 | 81 |
| Jammu and Kashmir | Assembly | `jkla` | 0 | 0 | 23 | 90 |
| Assam | Assembly | `asla` | 0 | 0 | 16 | 126 |
| Andhra Pradesh | Council | `apc` | 0 | 0 | 16 | 58 |

## Recommendation
Start crawler development targeting **Gujarat**, **Mizoram**, and **Himachal Pradesh** assemblies, as they provide the richest source of verified documents.
