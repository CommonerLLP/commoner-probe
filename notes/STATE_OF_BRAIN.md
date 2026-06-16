# State Of Brain — commoner-probe — 2026-06-16 (toolchain + SSRF)

Prior state archived at
`notes/state-of-brains/state-of-brain-2026-06-16-bd-toolchain-ssrf.md`.

## Active Frame

`commoner-probe` is Layer 0 acquisition for public disclosure sources. It can
bundle evidence across sources, but must keep unlike source families separate
and preserve provenance. It is also the single SSRF-guard surface for all
outbound HTTP — that guard is security-load-bearing, not housekeeping.

## Current Decisions

- MCA CSR acquisition is live and stable for schema/CLI: `commoner-probe mca-csr`.
- DMFT/PMKKKY uses the public name `mines-dmft`; covers Ministry of Mines
  national static CSVs + Odisha DMF endpoints. Raw acquisition, not parsed facts.
- `evidence dmft` shows executive disclosure and Sansad oversight side by side,
  never merged.
- **SSRF guard has NO name-allowlist bypass.** A host-allowlist early-return is a
  DNS-rebinding hole; every host (gov domains included) must clear the
  resolved-IP policy. See `commoner_probe/url_safety.py` docstring.

## Source Boundaries

- Ministry of Mines CSVs are cumulative/current snapshots with
  `source_last_modified`; not FY-wise data.
- Odisha DMF exposes richer state/district JSON/report surfaces; first
  district-wise implementation target.
- Sansad Q/A answers are legislative oversight records, not a replacement for
  ministry-published source data.
- MCA CSR compares reporting/spending companies, not consultants/vendors/NGOs.

## Open Tensions

- Chhattisgarh & Jharkhand structured DMFT finance endpoints unproven; district
  NIC/S3WaaS pages may only support document/governance acquisition.

## Toolchain (resolved 2026-06-16)

- Run tests/lint via `.venv/bin/...` (Python 3.14.5, jsonschema 4.26.0, ruff
  0.15.16). Full suite is 305 passed / 1 skipped, no deselection. The old
  "jsonschema/ruff missing" caveat was a wrong-interpreter (system python3.13)
  artifact.
- `bd` (beads) installed via Homebrew 1.0.5; embedded dolt DB rebuilt from
  `.beads/issues.jsonl` (43 issues, all closed). On a fresh clone, rebuild with
  `bd init --non-interactive --prefix sansad-crawler` then `bd import .beads/issues.jsonl`.
- This workspace is a multi-agent fleet (gemini/codex/agy/claude/Hermes). Treat
  `AGENTS.md`, `.codex/`, `.agents/skills/` as load-bearing, not Claude-only cruft.

## Next Thought

Resume the source work: live `mines-dmft` acquisition into `data/mines-dmft`,
Sansad `mines_dmft_pmkkky` crawl, real `evidence dmft` bundle, then parsed DMFT
record streams.
