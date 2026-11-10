# Haryana RERA registered-projects fixtures

These files are the registered-projects table from the Haryana RERA (Real Estate
Regulatory Authority) portal at `https://haryanarera.gov.in`, captured on
November 11, 2026 (Indian Standard Time). Each keeps the
`<table id="compliant_hearing">` element, its header row, and a few of the data
rows, and drops the rest of the page. Nothing inside a kept row is edited. Rows
keep their original order and their original `Serial No.` values.

| File | Source | Kept rows (`Serial No.`) |
|---|---|---|
| `registered_projects_2_trimmed.html` | `GET /admincontrol/registered_projects/2` (Gurugram authority, 1,086 rows) | 1, 2, 3, 4, 5, 7, 8, 13, 20, 36, 68, 120 |
| `registered_projects_1_trimmed.html` | `GET /admincontrol/registered_projects/1` (Panchkula authority, 1,132 rows) | 1, 5, 8, 35, 71 |

The Gurugram rows cover:

- A plain certificate with a certificate corrigendum and an OC/CC/PCC entry (1).
- A lapsed project (2, 4, 5, 68, 120), where `Lapsed Project` sits in the
  certificate cell.
- `DATED-30.09.2019`, a hyphen where the other rows have a space (7).
- A commented-out `<span>` with an image inside the certificate cell (8).
- A certificate that another one supersedes, with a note in parentheses (13).
- `Project ID` of `NA` and `Details of Project(Form A-H)` of `N/A` (20, 120).
- Every quarterly-progress variant: a link (1, 2, 3, 8, 13, 68), `QPR not
  Uploaded` (4, 5, 7, 20, 120), and `NA` (36).
- A certificate number with no date, only a supersession note, and the
  `01/01/1970` placeholder for `Registration Up-to` (120).

The Panchkula rows cover a certificate number that is itself a portal code with
no date (`HRERA-PKL-AMB-67-2018`, row 5), a location that wraps mid-word
(`H<br>ARYANA`, row 1), a project name with runs of spaces (row 1), and
Faridabad (row 35).

## The listing contract (verified November 11, 2026)

| Item | Value |
|---|---|
| Request | `GET https://haryanarera.gov.in/admincontrol/registered_projects/<n>`. No parameters, no form. |
| `/2` | Gurugram authority: 1,086 data rows, all with `Project District` of `GURUGRAM`. HTTP 200, 2,664,385 bytes. |
| `/1` | Panchkula authority: 1,132 data rows across 21 other districts (Faridabad 216, Sonipat 165, Jhajjar 146, Karnal 97, Rewari 76, Rohtak 70, Panipat 66, Panchkula 46, Kurukshetra 36, Hisar 33, Palwal 33, Ambala 30, Jind 28, Fatehabad 25, Yamunanagar 22, Sirsa 17, Nuh 11, Kaithal 10, Bhiwani 3, Charki Dadri 1, Mahendragarh 1). HTTP 200, 2,606,912 bytes. |
| Pagination | None. The page's DataTable paginates in the browser, and every row is in the HTML. |
| Cookie, token, CAPTCHA, or login | None. A fresh session gets the full table. |
| `robots.txt` | `GET /robots.txt` answers 404, so no file applies. |
| Row identifier | `Project ID` isn't one: it is `NA` for 37 Gurugram rows and 15 Panchkula rows. The registration certificate number is unique across both listings (2,218 rows), so the adapter keys on it. |

The plan assumed `/2` was Gurugram and `/1` was Panchkula. `/2` is Gurugram. `/1`
is the Panchkula authority's listing, which covers every other Haryana district,
not just Panchkula.

## Table headers, in order

The header row has 14 `<th>` cells. These are the exact strings, including case
and punctuation. A commented `<th>View Proceedings</th>` follows the last one in
the source and isn't a column.

| # | Header string | Cell content |
|---|---|---|
| 0 | `Serial No.` | Row number in the response. |
| 1 | `Registration Certificate Number` | The certificate number and its date, for example `GGM/415/147/2020/31 DATED 09.10.2020`. Variants are below. |
| 2 | `Project ID` | A link to `/view_project/searchprojectDetail/<n>` with text such as `RERA-GRG-741-2020`, or the text `NA` with no link. |
| 3 | `Project Name` | Text. |
| 4 | `Builder` | Text. |
| 5 | `Project Location` | Text, with `<br>` where the portal wraps a long value after about 30 characters, sometimes in the middle of a word (`G<br>URUGRAM`). |
| 6 | `Project District` | Text, in capitals. |
| 7 | `Registered With` | `HRERA` (820 Gurugram rows, 989 Panchkula rows) or `INTERIM RERA` (266 and 143). |
| 8 | `Details of Project(Form A-H)` | A PDF icon link to `/view_project/project_preview_open/<n>`, or `N/A` (37 Gurugram rows, 15 Panchkula rows). |
| 9 | `Registration Up-to` | `dd/mm/yyyy`. The placeholder `01/01/1970` (the Unix epoch) in 4 Gurugram rows and 3 Panchkula rows. |
| 10 | `View Certificate` | A PDF icon link to `/view_project/view_certificate/<base64>`. For a project with a corrigendum, a `View Corrigendum` link follows, an `onclick="view_corrigendums(<n>,'C')"` with no URL. |
| 11 | `View Quarterly Progress` | A `View` link whose `onclick` calls `window.open('<url>', ...)` (911 Gurugram rows, 846 Panchkula rows), the red text `QPR not Uploaded` (156 and 198), or `NA` (19 and 88). |
| 12 | `Monitoring Orders` | A `View Monitoring Cases` link that opens `/assistancecontrol/view_monitoring_cases/<base64>` (658 Gurugram rows, 325 Panchkula rows), or `--`. |
| 13 | `View OC/CC/PCC` | A `View OC/CC/PCC` link, `onclick="view_corrigendums(<n>,'OC')"` with no URL (389 Gurugram rows, 162 Panchkula rows), or empty. |

Every data row has 14 cells. Rows are sorted by project name, not by date.

### The certificate cell

The cell holds the number, then `DATED`, then a `dd.mm.yyyy` date. These shapes
all occur:

- `GGM/415/147/2020/31 DATED 09.10.2020`, and the older `15 OF 2018 DATED
  19.01.2018`.
- `DATED-30.09.2019`, `DATED 17.8.2017` (a one-digit month), and two spaces
  before `DATED`.
- A number with a parenthesis in it: `160(A) OF 2017`, `GGM/441/173/2021/08(A)`.
- A supersession note in parentheses (26 Gurugram rows): `(SUPERSEDED BY ...)` or
  `(SUPERSEDES ...)`. Two rows have the note and no `DATED`: `384 OF 2017` and
  `116 OF 2017`.
- One row prints a sentence: `RC NO. GGM/938/670/2025/41 DATED 01.05.2025 IS
  HEREBY SUPERSEDED BY RC NO. ...`.
- No date at all, for 1,063 of the 1,132 Panchkula rows
  (`HRERA-PKL-AMB-812-2025`).
- A link around a blinking `<span>` with the text `Lapsed Project` (205 Gurugram
  rows, 288 Panchkula rows).
- A commented-out `<span>` that holds a "project completed" image. It isn't
  rendered.

## Counts (captured November 11, 2026)

Gurugram: 1,086 rows, 1,084 with a certificate date, newest November 2, 2026.
The plan expected about 1,095 rows. The live count is 1,086. 12 certificates are
dated in the last 90 days (since August 13, 2026), and the longest gap between
two certificate dates since January 2025 is 25 days.

The freshness entry uses the Gurugram listing because the Panchkula listing
prints no certificate date for 1,063 of its 1,132 rows.

## The quarterly-progress page (opened once, not fetched by the adapter)

`View Quarterly Progress` opens `/assistancecontrol/print_quarterly_schedules/<a>/<b>/<c>`.
The three segments are base64: `<a>` is the authority listing (`Mg==` is `2` in
every Gurugram link, `MQ==` is `1` in every Panchkula link), `<b>` is the
project's number from its `Project ID`, and `<c>` is the project's year. For the
first Gurugram row, `RERA-GRG-741-2020`, the URL ends `Mg==/NzQx/MjAyMA==`
(`2`, `741`, `2020`).

One request to that URL on November 11, 2026 answered HTTP 200, 50,028 bytes of HTML,
no login, no CAPTCHA. It is an index of quarters, not a report:

- A heading `View Quarterly Progress` and a table with the columns `Quarter No`,
  `Quarter Start Date`, `Quarter End Date`, and `View Details`.
- 23 rows for this project, from `1 of 2021` (`01-Jan-2021` to `31-Mar-2021`)
  to `3 of 2026`.
- Each `View Details` cell is a `<form method="post"
  action="https://haryanarera.gov.in/view_project/quaterly_schedule_preview_open">`
  with hidden inputs `quarter_no`, `quarter_year`, `reracode_project` (`2`),
  `temp_project_id` (`741`), and `temp_project_year` (`2020`), and a `View`
  submit button.

The report behind each quarter is a POST that nobody has opened, so its format is
unverified. The adapter records the index URL in `fields["View Quarterly Progress"]`
and never fetches it (decision 6 of the epic plan). Fetching the reports is a
separate follow-up.

## Other links in a row, never fetched

`Details of Project(Form A-H)`, `View Certificate`, and `Monitoring Orders` are
PDF or page links. The adapter records them and never follows them. The
`view_corrigendums(<n>, ...)` calls on `View Certificate` and `View OC/CC/PCC`
are page scripts, not URLs. The adapter stores `<n>` as `Corrigendum ID` and
`OC/CC/PCC ID`.
