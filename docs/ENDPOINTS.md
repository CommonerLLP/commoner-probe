# Endpoint Reference

This is a public, source-facing reference for the government disclosure
endpoints that `commoner-probe` uses. It documents source contracts, not
private run notes.

## Sansad Q/A

`commoner-probe sansad` probes Lok Sabha and Rajya Sabha question records from
the public Sansad web surfaces. The two houses expose different endpoint
families and response shapes, so the manifest keeps `house` and source metadata
on every record.

Outputs:

- `manifest.jsonl` records with `kind = "qa"`
- optional PDFs under `pdfs/`
- `_runs.jsonl` audit records

Follow-up command:

```bash
commoner-probe extract-answers --out data/<corpus>
```

## Standing Committees

`commoner-probe committees` probes Lok Sabha and Rajya Sabha department-related
standing committee report listings. Report records include report type,
committee slug, dates, PDF URLs, and downloaded PDF paths when downloads are
enabled.

Outputs:

- `manifest.jsonl` records with `kind = "committee_report"`
- optional PDFs under `pdfs/`
- `_runs.jsonl` audit records

Follow-up commands:

```bash
commoner-probe extract-answers --out data/<committee-corpus>
commoner-probe atr-linkage --out data/<committee-corpus>
```

## NeVA State Assembly Portals

`commoner-probe state-assembly` probes public National e-Vidhan Application
portals. Each state deployment has a portal subdomain and CMS state code.

Outputs:

- `questions.jsonl`
- `questions_unlisted.jsonl`
- `members.jsonl`
- `papers_laid.jsonl`

Example:

```bash
commoner-probe state-assembly \
  --portal gujarat \
  --state GJ \
  --out data/gujarat-assembly \
  --assemblies 15
```

## MCA CSR

`commoner-probe mca-csr` downloads company-spend CSV exports from the MCA CDM
CSR public data page.

Source contract:

- page: `GET https://www.mcacdm.nic.in/csr-data`
- export: `POST https://www.mcacdm.nic.in/cdm/export.php`

Outputs:

- `manifest.jsonl` records with `kind = "mca_csr_company_spend"`
- one CSV per requested financial year

Example:

```bash
commoner-probe mca-csr \
  --out data/mca-csr \
  --years 2022-23,2021-22
```

## Mines DMFT

`commoner-probe mines-dmft` downloads raw Ministry of Mines and Odisha DMFT
public disclosure files. Ministry CSVs are current cumulative snapshots; they
are not year-wise files unless the source itself publishes a period field.

Default source families:

- `mines-gov-in`: Ministry of Mines static CSV snapshots
- `odisha`: Odisha DMFT JSON/report surfaces

Outputs:

- `manifest.jsonl` records with `kind = "mines_dmft_source_file"`
- source files under source-named directories

Example:

```bash
commoner-probe mines-dmft \
  --out data/mines-dmft \
  --sources mines-gov-in,odisha
```

## DMFT Evidence Bundle

`commoner-probe evidence dmft` combines executive disclosure and parliamentary
oversight into a single JSON object without flattening them into one table.

Inputs:

- a `mines-dmft` corpus
- optionally, a Sansad Q/A corpus for DMFT/PMKKKY oversight

Example:

```bash
commoner-probe evidence dmft \
  --mines-dmft-dir data/mines-dmft \
  --sansad-dir data/sansad/mines-dmft-pmkkky \
  --out data/evidence/dmft.json
```
