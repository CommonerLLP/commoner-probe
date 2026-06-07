# State Of Brain — commoner-probe — 2026-06-07

## Active Frame

`commoner-probe` is now the canonical Layer 0 acquisition package and local checkout. It should absorb reusable public-source acquisition patterns while refusing domain interpretation, vector search, and publication surfaces.

## Current Decision

MCA CSR raw acquisition belongs here as `commoner_probe.csr.mca`. The old `csr-crawler` repo is archived and should not remain a canonical acquisition repo.

## Tensions

- The MCA CSR export endpoint is still a placeholder. The adapter exists to preserve and test the acquisition shape, not to claim live portal access.
- No schema is locked yet for MCA CSR manifest rows. Schema lock should wait until live endpoint behavior and source fields are verified.
- CSR fiscal/social interpretation is not Layer 0. If it becomes durable, it belongs in a domain analysis repo, likely `budget-crawler` or a future CSR-domain layer.

## Next Thought

Do not expand the adapter into a CLI or schema until the portal contract is known. The next larger architecture task is `partial-recall` adapter registration.
