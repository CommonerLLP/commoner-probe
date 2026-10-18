# MHA finance division fixture

`finance_division_trimmed.html` is the document list from
`https://www.mha.gov.in/finance-division`, saved on 18 October 2026 after MHA
rebuilt its site. It keeps the page's `views-table` block and drops the header,
menus, and footer. Nothing inside the block is edited.

The page has no `<table>` elements. Each row is a `documentRecordTitle` div
followed by a `viewBtn` link, and the old `<tr>`/`<td>` markup survives only in
HTML comments. The block lists 15 documents with a fiscal year: 14 Detailed
Demands for Grants volumes (2021-22 to 2026-27) and one Output Outcome
Monitoring Framework (`OOMF2026-27_11022026.pdf`, titled "Budget Division")
that comes first.
