# GNCTD Finance Department fixtures

Captured on 12 November 2026 (IST) with the probe's budget User-Agent, 2
seconds apart. `finance.delhi.gov.in/robots.txt` answers 403 to every
User-Agent and the pages answer 200, so the probe relaxes the robots check
for this one host.

| File | URL |
|---|---|
| `budget_glance_trimmed.html` | `https://finance.delhi.gov.in/finance/budget-glance` |
| `receipts_budget_trimmed.html` | `https://finance.delhi.gov.in/finance/receipts-budget` |
| `budget_speech_trimmed.html` | `https://finance.delhi.gov.in/finance/budget-speech` |
| `gender_budget_trimmed.html` | `https://finance.delhi.gov.in/finance/gender-budget` |
| `ddg_index_trimmed.html` | `https://finance.delhi.gov.in/finance/detailed-demands-grants-0` |
| `ddg_2026-27_trimmed.html` | `https://finance.delhi.gov.in/finance/detailed-demands-grants-2026-27` |

## Kept element

Every fixture keeps the Drupal main-content block,
`<div id="block-dit-finance-content" class="block block-system
block-system-main-block">`, extracted by balanced `<div>` matching, and drops
the header, menus, and footer. Nothing inside the block is edited. The file
list pages keep the block's `view-generic-page-files` listing and, where the
page has one, its pager. The index page keeps the block's body text, which
holds a table of links to the per-year detail pages.

## Counts

| File | Count | Raw capture | Fixture |
|---|---|---|---|
| `budget_glance_trimmed.html` | `generic_multiple_files` | 20 | 20 |
| `receipts_budget_trimmed.html` | `generic_multiple_files` | 20 | 20 |
| `budget_speech_trimmed.html` | `generic_multiple_files` | 20 | 20 |
| `gender_budget_trimmed.html` | `generic_multiple_files` | 17 | 17 |
| `ddg_2026-27_trimmed.html` | `generic_multiple_files` | 20 | 20 |
| `ddg_index_trimmed.html` | distinct `detailed-demands-grants-20YY-YY(-voa)` | 22 | 22 |

All six equal the numbers from the first capture on 4 November 2026. A raw `.pdf` href count is one
higher than `generic_multiple_files` on the raw pages (21 against 20, and 18
against 17 for gender) because the site header links an unrelated
`i4c_handbook_dos_and_don_t_final.pdf`; the fixture drops it.

The Budget Speech page lists `Budget Speech Hindi 2026-26` (a malformed title)
with the file `budget_speech_2026-27_hindi.pdf`. The 2026-27 detail page lists
its volumes under names such as `d-1_8.pdf`, `d7.pdf`, and `111.pdf`, which
sevent4's parser drops.

## Pagination

Three pages have a Drupal pager with a second page: `budget-glance`,
`budget-speech`, and `detailed-demands-grants-2026-27`. These fixtures hold
page 1 only (the page the URL returns with no `?page=` parameter), which is
what the counts above describe. On 12 November 2026, `?page=1` returned 1
more document on `budget-glance` (`budget_at_a_glance_2006-07.pdf`), 3 or
more on `budget-speech` (for example `budget_speech_2011-12_1.pdf`), and 16
more on `detailed-demands-grants-2026-27`. These second pages are not
captured and no test asserts them. `receipts-budget` and `gender-budget` have
no pager.
