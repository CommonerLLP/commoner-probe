# NADA fixtures — provenance

All captured from `https://microdata.gov.in/NADA` on 2026-07-31 while designing
the adapter. Recorded here so a later session can tell a real capture from a
hand-written one.

| file | provenance |
|---|---|
| `study_1.json` | verbatim `GET /index.php/api/catalog/DDI-IND-MOSPI-NSSO-68Rnd-Sch1.0-July2011-June2012`, 22,378 bytes, unedited |
| `search_nss.json` | verbatim `GET /index.php/api/catalog/search?ps=2&sk=NSS` (`found: 129` of `total: 187`), unedited |
| `related_materials_1.html` | `GET /index.php/catalog/1/related-materials`, **trimmed**: reduced to the `<div class="resources">` block, then to the first two resources per `<legend>`. Markup within a kept resource is untouched |
| `related_materials_150.html` | `GET /index.php/catalog/150/related-materials`, trimmed the same way |
| `study_numeric_id_error.json` | **hand-written** from the response body observed verbatim for `GET /index.php/api/catalog/1` (HTTP 400, 46 bytes). Not a saved capture |
| `collections_trimmed.json` | **hand-written**, keeping the two entries (ASI, ECO) observed verbatim from `GET /index.php/api/catalog/collections` and the real `total: 9`. The full 9-entry body was not saved. Not a saved capture |

## Why two files are hand-written

`microdata.gov.in` stopped answering on every path at about 12:35 on 2026-07-31
— connections open and then time out, while `censusindia.gov.in/nada` (the same
NADA software on a different host) kept answering in ~2s. The two files above
could not be re-fetched, so they were written from response bodies observed
verbatim earlier in the same session. Replace them with real captures when the
host returns.

## What these fixtures do NOT prove

They pin the parser against markup and payloads as they stood on 2026-07-31.
They say nothing about whether the source still behaves this way. The live
verification in `docs/superpowers/plans/2026-07-31-nada-adapter.md` (Task 8) is
what checks that, and it has not been run.
