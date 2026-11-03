# MHA Budget Division fixture

`budget_division_trimmed.html` is the document list from
`https://www.mha.gov.in/finance-division-inner` (the Budget Division page),
saved on 3 November 2026. On 22 October 2026 MHA moved its Detailed Demands for
Grants volumes off `https://www.mha.gov.in/finance-division`, which now lists
only the IFD information PDF, the Output Outcome Monitoring Framework PDF, and
a link to this page. The fixture keeps the page's `views-table` block and drops
the header, menus, and footer. Nothing inside the block is edited.

The page has no `<table>` elements. Each row is a `documentRecordTitle` div
followed by a `viewBtn` link, and the old `<tr>`/`<td>` markup survives only in
HTML comments. The block lists 14 documents, all Detailed Demands for Grants
volumes (2021-22 to 2026-27). It has no Output Outcome Monitoring Framework
entry, so `tests/test_ministry_ddg.py` injects a synthetic one to test the
`demand_grant_titles_only` filter.
