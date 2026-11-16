# Karnataka RERA project listing fixture

`project_view_details_trimmed.html` is the project table from the Karnataka
RERA (Real Estate Regulatory Authority) search results at
`https://rera.karnataka.gov.in/projectViewDetails`, captured on November 11,
2026 (Indian Standard Time) with `district=Bengaluru Urban`. The fixture keeps
the `<table id="approvedTable">` element, its header row, and 20 of the 4,460
data rows. It drops the rest of the page. Nothing inside a kept row is edited.
Rows keep their original order and their original `S.No` values.

The 20 kept rows cover:

- Three approved rows with all three extension columns filled (`S.No` 27, 33,
  and 51), plus `S.No` 17 (status `TRANSFERED`), which also has all three
  filled.
- Three approved rows with all three extension columns empty (`S.No` 2, 5, 6).
- Non-approved statuses: `REJECTED` (`S.No` 3 and 2759, the latter with no
  approved-on date), `WITHDRAWN` (85), `REVOKED` (580), and `TRANSFERED` (17).
- One row with an empty project name (2759) and two with an empty approved-on
  date (3 and 2759).
- Rows without a complaints icon (3 and 2759). Every other kept row has one.
- Rows whose COVID-19 extension cell is `<start> -` with no end date (27, 33,
  51, and 337).
- An acknowledgement number in the long `ACK/KA/RERA/...` form (2240, 2759).

## The form contract (verified November 11, 2026)

| Item | Value |
|---|---|
| Form page | `GET https://rera.karnataka.gov.in/viewAllProjects`: HTTP 200, about 6.5 MB. The search form is the `<form id="loginFormID">` on this page. |
| Direct GET of the result URL | `GET https://rera.karnataka.gov.in/projectViewDetails` answers HTTP 405 with an "Error Page" body ("Requested resource is not available"). The adapter must POST. |
| Request | `POST https://rera.karnataka.gov.in/projectViewDetails`, `application/x-www-form-urlencoded`. |
| Fields | `project` (text), `firm` (text), `appNo` (text), `regNo` (text), `district` (select), `subdistrict` (select), `btn1` (submit button, value `Search`). |
| Field defaults | The four text fields are empty. `district` and `subdistrict` default to `0`, the "--Select--" option. |
| Values sent | `project=`, `firm=`, `appNo=`, `regNo=`, `district=<option value>`, `subdistrict=0`, `btn1=Search`. |
| Hidden fields | None. The form has no CSRF token. |
| Cookie or token needed | No. A POST from a fresh session, with no earlier GET, returns the full table. The server sets a `JSESSIONID` cookie on the POST response only. |
| CAPTCHA or login | None. |
| Taluk (`subdistrict`) options | Loaded by an Ajax call when you choose a district. The captures leave it at `0`, which returns the whole district. |
| `robots.txt` | `GET /robots.txt` answers 404, so no file applies. |
| `noindex` | The page head carries `<!-- <meta name="robots" content="noindex" /> -->`, commented out. On November 11, 2026 it isn't active. |
| Response size | About 6.5 MB for a district with 12 rows. The page ships about 6.5 MB of embedded script data in every response, and the table adds to it. |

### District option list

The `district` select has 31 options besides `0`. The option values have a
leading and trailing space in their label but not in the `value` attribute.
Send the `value` attribute exactly. `Bengaluru  Rural` has two spaces between
the words.

Bagalkot, Ballari, Belagavi, Bengaluru  Rural, Bengaluru Urban, Bidar,
Chamarajanagar, Chikkaballapura, Chikkamagaluru, Chitradurga, Dakshina
Kannada, Davangere, Dharwad, Gadag, Hassan, Haveri, Kalaburagi, Kodagu, Kolar,
Koppal, Mandya, Mysore, Raichur, Ramanagara, Shivamogga, Tumakuru, Udupi,
Uttara Kannada, Vijayanagara, Vijayapura, Yadgir.

## Table headers, in order

The header row has 21 `<th>` cells. These are the exact strings, including
case. Column 3 and columns 16 to 20 hold links or icons, not text. A commented
`<!--  <th>APPLIED ON</th> -->` sits between `PROJECT TYPE` and `APPROVED ON`
in the source and isn't a column.

| # | Header string | Cell content |
|---|---|---|
| 0 | `S.No` | Row number in the response. |
| 1 | `ACKNOWLEDGEMENT NO` | Application number, for example `PR/KN/170729/000003`. |
| 2 | `REGISTRATION NO` | Registration number. Empty for 288 of 4,460 Bengaluru Urban rows (applications that were never approved). |
| 3 | `View Project Details` | An `<a id="<project id>" onclick="showFileApplicationPreview(this)">` icon. The numeric `id` is the key for the `projectDetails` popup, which isn't tested. |
| 4 | `PROMOTER NAME` | Text. |
| 5 | `PROJECT NAME` | Text, followed by an empty `<p style="color:orange;">`. Empty for 1 Bengaluru Urban row. |
| 6 | `STATUS` | A `<p>` with the status (`APPROVED`, `REJECTED`, `WITHDRAWN`, `TRANSFERED`, `REVOKED`), then a second `<p>` with the authority's order text for non-approved rows. |
| 7 | `DISTRICT` | Text. |
| 8 | `TALUK` | Text. |
| 9 | `PROJECT TYPE` | `Residential/Group Housing`, `Plotted Development`, `Mixed Development`, or `Commercial`. |
| 10 | `APPROVED ON` | `dd/mm/yyyy`. Empty for 285 of 4,460 Bengaluru Urban rows. |
| 11 | `PROPOSED COMPLETION DATE` | `dd/mm/yyyy`. The current proposed date. |
| 12 | `PROPOSED COMPLETION DATE AT THE TIME OF REGISTRATION` | `dd-mm-yyyy`. Note the dashes, which differ from columns 10 and 11. |
| 13 | `COVID-19 EXTENSION DATE` | Empty, or `dd-mm-yyyy - dd-mm-yyyy` (start and new end date). 47 Bengaluru Urban rows have `dd-mm-yyyy -` with no end date. |
| 14 | `SECTION 6 EXTENSION DATE` | Empty, or `dd-mm-yyyy - dd-mm-yyyy`. |
| 15 | `FURTHER EXTENSION DATE` | Empty, or `dd-mm-yyyy - dd-mm-yyyy`. |
| 16 | `Certificate` | An `<a href="/certificate?CER_NO=...">` PDF icon, or empty. |
| 17 | `Covid Certificate` | An `<a href="/covidcertificate?CER_NO=...">` icon, or empty. |
| 18 | `Renewed Certificate` | An `<a href="/extensionCertificate?CER_NO=...">` icon, or empty. |
| 19 | `FURTHER EXTENSION ORDER` | An `<a href='/download_jc?DOC_ID=...'>` icon, or empty. |
| 20 | `COMPLAINTS / LITIGATION` | An `<a id="<id>" onclick="showFileComplaintPreview(this)">` icon, or empty. 4,174 of the 4,460 Bengaluru Urban rows have one. |

Every data row has 21 cells. Rows aren't sorted by date: Bengaluru Urban lists
the oldest registrations first, so the newest ones sit at the end of the table.

## Counts (captured November 11, 2026)

Bengaluru Urban: HTTP 200, 32,861,637 bytes, 4,460 data rows, 4,175 with an
approved-on date, and 4,095 with status `APPROVED`. The newest approved-on date
is November 7, 2026.

Registrations in the last 90 days count rows whose `APPROVED ON` date falls on
or after August 13, 2026. Task 4 uses these figures to choose the freshness
district and its `MaxAge` limit.

| District | Data rows | Newest approved-on | Registrations, last 90 days |
|---|---|---|---|
| Kodagu | 12 | September 11, 2026 | 1 |
| Yadgir | 12 | January 27, 2025 | 0 |
| Gadag | 18 | June 29, 2026 | 0 |
| Bengaluru Urban | 4,460 | November 7, 2026 | 95 |

Kodagu and Yadgir tie as the smallest (12 rows each). Gadag is third. All 31
districts were captured once, one request per second:

| District | Data rows | Newest approved-on | Last 90 days |
|---|---|---|---|
| Raichur | 19 | December 8, 2023 | 0 |
| Bidar | 22 | June 29, 2026 | 0 |
| Hassan | 22 | July 9, 2026 | 0 |
| Bagalkot | 31 | January 20, 2023 | 0 |
| Chikkamagaluru | 45 | July 4, 2026 | 0 |
| Chitradurga | 45 | July 4, 2026 | 0 |
| Koppal | 51 | October 8, 2026 | 1 |
| Chamarajanagar | 59 | June 29, 2026 | 0 |
| Ramanagara | 61 | September 4, 2026 | 1 |
| Vijayapura | 61 | October 27, 2025 | 0 |
| Vijayanagara | 72 | October 28, 2026 | 5 |
| Mandya | 87 | November 7, 2026 | 1 |
| Haveri | 90 | January 27, 2025 | 0 |
| Uttara Kannada | 115 | August 21, 2026 | 1 |
| Davangere | 181 | July 24, 2026 | 0 |
| Chikkaballapura | 188 | October 28, 2026 | 3 |
| Kolar | 202 | October 17, 2026 | 6 |
| Shivamogga | 232 | October 29, 2026 | 2 |
| Tumakuru | 234 | November 7, 2026 | 31 |
| Ballari | 243 | November 3, 2026 | 5 |
| Kalaburagi | 268 | September 23, 2026 | 2 |
| Dharwad | 278 | November 3, 2026 | 4 |
| Udupi | 280 | November 3, 2026 | 9 |
| Belagavi | 527 | October 29, 2026 | 6 |
| Dakshina Kannada | 666 | October 31, 2026 | 17 |
| Bengaluru  Rural | 726 | November 7, 2026 | 29 |
| Mysore | 799 | November 3, 2026 | 13 |

Some districts have rows with no approved-on date (for example Davangere has
181 rows but 47 dates), so "data rows" is larger than the count of dated rows.

## Differences from the plan's November 11 findings

- The plan describes the endpoint as a form POST with "other form fields
  unverified". The full field list is above.
- The plan says the page carries an active `<meta name="robots"
  content="noindex">`. In these captures the tag is inside an HTML comment.
- A bare GET of `/projectViewDetails` fails with HTTP 405. The form to read
  lives on `/viewAllProjects`.
