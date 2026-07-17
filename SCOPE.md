# Scope — commoner-probe

Canonical project-control document for repo ownership and boundaries. When
documents disagree, priority order is: this file → `ARCHITECTURE.md` →
`ROADMAP.md` → `README.md` → code comments. Reconcile the documents before
implementing, not after.

## Owns

Public-record acquisition from official disclosure portals, with
provenance, for downstream consumers:

- **Source adapters** (one module per source family): parliamentary
  questions (sansad.in LS/RS, including tabled papers via eLibrary),
  standing-committee reports, floor-debate transcripts, bills, NeVA
  state-assembly portals, India Code statutory instruments, legacy DSpace
  portals, MCA and DPE CSR disclosures, mines/DMFT disclosures, Union
  Budget + RBI source files, ministry DDG series, DoE Pay & Allowances
  reports, MoSPI eSankhyiki statistics, Lok Sabha attendance, ADR/MyNeta
  affidavits, academic faculty-recruitment advertisements.
- **Shared HTTP discipline**: `http_client.py` and `url_safety.py` —
  SSRF guard, robots.txt, per-domain rate limiting, retry/backoff,
  optional cache, User-Agent policy.
- **Provenance and contracts**: `manifest.jsonl` records, `_runs.jsonl`
  run logs, JSON schemas (`schemas/`), the `validate` command, sha256 on
  downloaded files.
- **Deterministic text extraction** that is part of the acquisition
  contract: `extract-answers` (Q/A pairs, ATR responses, DFG
  recommendations, typed vacancy and outsourcing rows), `extract-debates`,
  NeVA Gujarati splitting and glyph repair.
- **The `Corpus` read API** over emitted records.

## Does not own

- Classification, topic modelling, discourse analysis, dossier generation
  — downstream domain repos.
- Semantic search, embeddings, chunking, retrieval tooling —
  `partial-recall`.
- Budget/fiscal row-level parsing (object-head lines, scheme tables) —
  `public-finance`.
- OCR of scanned documents. Scanned or flattened PDFs are *recorded*
  (`text_layer: false`, NeVA `quality: low`) so an OCR pass can be
  scheduled; no OCR machinery exists in this repo.
- Cross-source joins, identity resolution, or interpretation beyond the
  documented record schemas.

## Boundary rules for new code

- A new source arrives as a new adapter module + manifest schema + CLI
  subcommand. Registries grow one live-verified entry at a time, never a
  guessed batch.
- Never build against a guessed endpoint: verify the source contract live
  first, or mark the adapter unverified.
- Reusable HTTP behaviour belongs in `http_client.py`, not in adapters.
- If a capability would work identically for any corpus, it belongs in
  `partial-recall`; if it interprets records for one domain, it belongs in
  that domain's repo, not here.
