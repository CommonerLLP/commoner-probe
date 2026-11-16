# Tamil Nadu RERA registered-projects fixtures

These files are trimmed captures from the Tamil Nadu RERA (Real Estate
Regulatory Authority) portal at `https://rera.tn.gov.in`, taken on November 11,
2026 (Indian Standard Time). Each keeps the year `<form>`, the "Last Updated on"
line, the `<table id="example1">` header row, and a few data rows, and drops the
rest of the page. Nothing inside a kept row is edited. Rows keep their original
order and their original `S. No` values. The one edit outside the rows is the
form's `_token` value, which is `FIXTURE_TOKEN` here.

| File | Source | Kept rows (`S. No`) |
|---|---|---|
| `registered_building_tn_trimmed.html` | `GET /registered-building/tn` (current year 2026, 402 rows) | 1, 2, 3, 5, 8, 11, 18, 46, 290, 402 |
| `registered_building_tn_2025_trimmed.html` | `POST /registered-building/tn` with `year=2025` (428 rows) | 1, 10, 20, 24, 428 |
| `registered_layout_tn_trimmed.html` | `GET /registered-layout/tn` (current year 2026, 3,945 rows) | 1, 5, 31, 33, 3945 |

The 2026 building rows cover:

- A CMDA planning permission in an older shape (`C/27/2025 A to D`) and a
  Greater Chennai Corporation permit (1). The CMDA number isn't typed.
- A CMDA `OL-PP/...` number and a `CEBA/...` Corporation number (2).
- A Joint Director, T&CP (DTCP) approval with no CMDA or Corporation number (3).
- Rows with no `Project Name:` line (11 of the 402 rows), a project that is
  `Completed`, a status that links to a `.pdf`, an individual promoter
  (`public-view1/building/pindividiual/...`), coordinates that aren't decimal
  degrees, and the last row (402).

The 2025 rows cover the other registration number shape
(`TN/29/Building/0001/2025`), which sits beside `TNRERA/<n>/BLG/<n>/2025`, and a
selected `year` option. The layout rows cover Joint Director, T&CP, approvals,
which carry no CMDA or Corporation number, and a status that links to a PDF.

## The listing contract (verified November 11, 2026)

| Item | Value |
|---|---|
| Building list | `GET https://rera.tn.gov.in/registered-building/tn`. HTTP 200, 1,418,431 bytes, 402 data rows, registration dates from 2026-01-02 to 2026-11-10. |
| Layout list | `GET https://rera.tn.gov.in/registered-layout/tn`. HTTP 200, 11,127,928 bytes, 3,945 data rows, the same date range. |
| `/layout/list-project` | The plan named this as the layout list. It is an index page of year cards that links to `/registered-layout/tn` (online) and `/layout/offline/<2017..2026>`. It has no table. |
| Older year | The page has `<form action=".../registered-building/tn" method="POST">` with `<input type="hidden" name="_token">` and `<select name="year">` with options 2026, 2025, and 2024. `POST _token=<token>&year=2025` in the same session (the GET set `XSRF-TOKEN` and `tnrera_session` cookies) returned HTTP 200, 1,410,498 bytes, 428 rows, dates 2025-01-03 to 2025-12-29. Both lists have the same form. |
| Pagination | None. The page's DataTable paginates in the browser. |
| Login or CAPTCHA | None. |
| Row identifier | `Project Registration No.`, unique in every list captured (402, 428, and 3,945 rows). The number shapes are `TNRERA/<n>/BLG/<n>/<year>`, `TN/<n>/Building/<n>/<year>`, and `TNRERA/<n>/LO/<n>/<year>`. The 2026 and 2025 building lists share no number, and neither shares one with the layout list. |

### robots.txt, re-read November 11, 2026

`GET /robots.txt` answers HTTP 200 with exactly these lines:

```
User-agent: *
Disallow: /uploads/
Disallow: /*.pdf$
```

`make_session` reads robots.txt with `urllib.robotparser`, which matches path
prefixes and ignores the `*` wildcard, so it enforces `/uploads/` and permits
every PDF. The adapter refuses a path under `/uploads/` and any path that ends
in `.pdf` itself, before the request (`refuse_disallowed_url`), and never
fetches a link in a row. Where a row links to a PDF (the current-status column of
some rows, under `/public/storage/`), the URL is recorded in `fields` only.

### The "Last Updated" stamp

The page has two stamps. The footer's `Last Updated On: 10 November, 2026 | v-2.0`
is site-wide. The one beside the year form is per list: `Last Updated on
10/11/2026` for the 2026 building list and the 2026 layout list, and `29/12/2025`
for the 2025 building list. In all three it equals the newest registration date
in the list, so the stamp carries nothing the rows don't.

### Counts for the freshness rule (2026 building list)

402 rows, 123 registrations dated in the last 90 days (since August 13, 2026), 37
in the last 30 days, a newest date of November 10, 2026, and a longest gap of 14
days between two registration dates (12 days in 2025). The 2026 layout list has
a longest gap of 5 days. The check uses the building list because the layout list
is 11 MB.

## Table headers, in order

The header row has 9 `<th>` cells. These are the exact strings.

| # | Header string | Cell content |
|---|---|---|
| 0 | `S. No` | Row number in the response. |
| 1 | `Project Registration No.` | `<strong>` with the number, and `<div>` with `dated dd-mm-yyyy`. |
| 2 | `Name and Address of the Promoter` | Text: the name and the address together. |
| 3 | `Project Details and Address` | `Project Name: <name>`, a `<br>`, then a description. 11 of 402 building rows have no `Project Name:` line. |
| 4 | `Approval Details` | Free text typed by the portal's staff. |
| 5 | `Project Completion Date` | `dd.mm.yyyy`, or `Completed`. |
| 6 | `Other Details` | A `Promoter Details` link, a `Project Details` link, and `Location of Site: Latitude-<n>;<br>Longitude-<n>;`. |
| 7 | `Form-c` | A PDF icon linking to `/formcqr/<uuid>`. |
| 8 | `Current Status of the Project` | Text such as `Progress of Work as on September 2026`, sometimes with a link (a `.pdf` under `/public/storage/`), or empty. |

Every data row has 9 cells in all three lists.

## Free-text fields

- **CMDA number.** 222 of the 402 building rows mention CMDA. 212 give an
  `OL-PP/<code>/<n>/<year>` planning-permission number or a `CMDA/PP/...` letter
  number. The other 10 give a different shape (`C/27/2025 A to D`,
  `PC/HRB/07/A TO M/2025`, `Planning Clearance No PC/HRB/14 (A to I) /2025`,
  `Permit No. 14509`), which stays untyped.
- **Corporation number.** 169 rows name the Greater Chennai Corporation (or `GCC`). 162 give
  a `CEBA/<ward>/<n>/<year>` number. The other 7 give `EBA/...`, a two-digit year,
  or only planning-permission numbers (`PPA/...`). Spaces typed around slashes
  (`OL-PP/ IND/ 0028/ 2024`) are read through.
- **Coordinates.** 333 of 402 building rows have decimal degrees with at least
  three decimals inside Tamil Nadu's box (7.5 to 14.0 N, 76.0 to 81.0 E) and
  different values. The rest are typed as degrees, minutes, and seconds run
  together (`130503.6`), as the same number twice (`12.893177` and `12.893177`),
  as coordinates in another country, or with a stray dash (`Longitude--80.2`).
  Those are null, and `fields["Other Details"]` keeps the text.

## The offline year pages (not read by the adapter)

The plan expected `/building/offline/<year>` to be a token form. It isn't:
`GET /building/offline/2025` (65,308 bytes, 1 row) and `/building/offline/2020`
(938,141 bytes, 490 rows) are static pages with no form. They have 8 columns
(no `Form-c`), a `<td>` registration cell with no `<strong>`, and `Approval
Details`, `Other Details`, and status cells that are links to PDFs under
`/cms/...`, which the adapter must not fetch. Some rows have 13 or 14 cells. They
need their own parser and are left for a follow-up bead.
