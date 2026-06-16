# State Of Brain — commoner-probe — 2026-06-16

## Active Frame

`commoner-probe` is now the canonical Layer 0 acquisition package and local checkout. It should absorb reusable public-source acquisition patterns while refusing domain interpretation, vector search, and publication surfaces.

## Current Decision

MCA CSR raw acquisition belongs here as `commoner_probe.csr.mca`. The live source is MCA CDM, not the old placeholder route: `GET /csr-data` for the CSRF-bearing form and `POST /cdm/export.php` for CSV export.

## Tensions

- The MCA CSR export is public but guarded by a client-side captcha convention; current verification shows matching `captcha_input`/`captcha_hidden` values are accepted. Keep this monitored as a source contract, not a permanent assumption.
- The CSV is an export aggregate by company/year/state/sector/subsector/spend, not full CSR project interpretation. Domain analysis still belongs downstream.
- CSR fiscal/social interpretation is not Layer 0. If it becomes durable, it belongs in a domain analysis repo, likely `budget-crawler` or a future CSR-domain layer.

## Next Thought

Finance disclosure adapters are now the top in-repo implementation queue. The next larger architecture task is still `partial-recall` adapter registration.
