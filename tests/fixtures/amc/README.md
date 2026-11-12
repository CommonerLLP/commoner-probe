# Ahmedabad Municipal Corporation fixtures

Captured on 12 November 2026 (IST) with the probe's budget User-Agent.

| File | URL |
|---|---|
| `budget_trimmed.html` | `https://ahmedabadcity.gov.in/SP/Budget` |
| `balance_sheet_trimmed.html` | `https://ahmedabadcity.gov.in/SP/BalanceSheet` |

## Kept element

Both pages hold their documents in the `<div class="pagecontent
new_page_content">` block, inside a `col-xs-12` wrapper. The page markup is
badly nested (editor-pasted `<div>` and `<h5>` wrappers with inline styles),
so each fixture keeps the contiguous run of lines from the opening
`pagecontent` line through the line that closes the block, and the header,
navigation, footer, and scripts are dropped. Nothing inside the kept lines is
edited. Div tags balance in both files.

- `budget_trimmed.html`: lines 639 to 662 of the capture (46 `<div>` opens, 46
  closes).
- `balance_sheet_trimmed.html`: lines 639 to 703 of the capture (66 and 66).

## Counts

Counted with `grep -o -i 'href="[^"]*\(\.pdf\|ViewFile\)[^"]*"' <file> | wc -l`
on the raw capture and on the fixture; the two numbers are equal.

| File | Raw capture | Fixture |
|---|---|---|
| `budget_trimmed.html` | 27 | 27 |
| `balance_sheet_trimmed.html` | 38 | 38 |

The Budget page lists 27 anchors and every one carries a fiscal year. The Balance Sheet page lists 38 `.pdf` or
`ViewFile` anchors. 34 of them contain `balance sheet`, `audit report`, or
`account`, and 27 of those carry a fiscal year (`20YY-YY`, `31-03-YYYY`, or
two-digit `YY-YY`, `YY_YY`, `YY/YY` with the end year one above the start). The 7 that carry no fiscal year are
the 2005-06 schedule files, whose names use `31_03_06`. A throwaway
script that applies those year rules gives 27 for each page; no repo parser
produced these counts.

The Budget page has two anchors for fiscal year 2023-24: a
`/Uploads/Budget/AMC Budget 2023-24.pdf` file and a `ViewFile?TYPE=
FileRepository,2203` link, with the same label, so tests must keep both.
