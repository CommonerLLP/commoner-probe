# Handoff — commoner-probe — 2026-06-16 (toolchain + SSRF session)

Prior handoff archived at `notes/handoffs/handoff-2026-06-16-bd-toolchain-ssrf.md`
(the mines-DMFT acquisition session). This session was toolchain repair + a
security fix, not source work.

## What Changed This Session

- **Fixed the SSRF guard** (`commoner_probe/url_safety.py`, commit `e440f46`,
  pushed): removed the `elibrary.sansad.in` entry in `WHITELISTED_DOMAINS` and
  its unconditional early-return, which bypassed the resolved-IP checks (a
  DNS-rebinding hole). The domain resolves to a public NIC IP (164.100.85.146)
  and passes the normal policy anyway, so the allowlist bought nothing. Added
  hostname normalization (`.lower().rstrip(".")`) and `tests/test_url_safety.py`
  (10 network-free mocked-`getaddrinfo` tests, incl. a regression proving the
  formerly-allowlisted host is rejected if it resolves private). Flagged by the
  push-time security sweep.
- **Corrected the false "jsonschema/ruff missing" caveat** (commit `3a827ff`,
  pushed) across `docs/STATUS.md`, `notes/STATE_OF_BRAIN.md`, `notes/HANDOFF.md`.
  The repo venv (`.venv`, Python 3.14.5) HAS jsonschema 4.26.0 + ruff 0.15.16;
  the prior caveat came from running a bare system `python3.13`. Removed one
  unused `pathlib.Path` import ruff flagged in `examples/usage.py`.
- **Installed `bd` (beads)** via Homebrew 1.0.5 and rebuilt its embedded dolt DB
  from `.beads/issues.jsonl` (`bd init` + `bd import` → 43 issues, all closed).
  `bd ready`/`bd list`/`bd stats` now work. `bd init` auto-committed `267014f`
  (agent-surface wiring: `.codex/`, `AGENTS.md`, `.agents/skills/beads/`,
  regenerated CLAUDE.md). **Decision: keep `267014f` as-is** — it correctly wires
  every agent in the fleet (gemini/codex/agy/claude/Hermes), not Claude-only.

## Verification

- `.venv/bin/python -m pytest` → **305 passed, 1 skipped** (no deselection).
- `.venv/bin/ruff check .` → clean.
- Branch `feat/mca-csr-adapter` pushed and in sync with origin.

## Concurrency Note

Another agent was live in this repo this session (created untracked
`examples/topics/narcotics_substance.json`). All my commits were staged by
explicit path, never `git add -A`, so that file was never captured. It remains
untracked and belongs to the other session — do not assume it's yours.

## What Is Next

- Run `commoner-probe mines-dmft --out data/mines-dmft --sources mines-gov-in,odisha`
  live, then the Sansad `mines_dmft_pmkkky` crawl → `extract-answers` → `evidence dmft`.
- Add parsed record streams: `dmft_financial_summary`, `dmft_sector_summary`,
  `dmft_project`, `dmft_governance_document`.
- Build MCA CSR comparison utilities over the 10-year MCA corpus (boundary:
  reporting/spending companies, not consultants/implementing agencies).
- Continue source discovery for Chhattisgarh & Jharkhand structured DMFT finance.
- Always run tests/lint via `.venv/bin/...`, never bare system python.
