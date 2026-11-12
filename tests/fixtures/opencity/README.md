# OpenCity CKAN fixtures

Both files are the whole response body of a CKAN Action API `package_show`
call to `data.opencity.in`, captured on 12 November 2026 (IST) with the
probe's budget User-Agent. Nothing is trimmed or edited.

| File | URL |
|---|---|
| `package_show_gba-corporation-budgets-2026-27.json` | `https://data.opencity.in/api/3/action/package_show?id=gba-corporation-budgets-2026-27` |
| `package_show_great-chennai-corporation-finances.json` | `https://data.opencity.in/api/3/action/package_show?id=great-chennai-corporation-finances` |

The requests were 10 seconds apart, as the site's `Crawl-Delay: 10` asks.
`data.opencity.in/robots.txt` disallows `/api/`. That line is CKAN's stock
robots.txt, which keeps search engines off the Action API; the Action API is
the publisher's documented machine interface, so the probe calls it with the
robots check relaxed for `/api/3/action/` paths only.

Counts the tests assert, all equal to the numbers from the first capture on 4 November 2026:

- GBA package `gba-corporation-budgets-2026-27`: 19 resources, 14 PDF and 5
  XLSX. Organization "Greater Bengaluru Authority (GBA)", license "Other
  (Public Domain)". Resource `598ade93-0ea8-4944-9d7e-96060a49c3e1` is
  "Bengaluru Central City Corporation Budget Tables", an XLSX last modified
  `2026-06-17T04:12:04.107941`.
- Chennai package `great-chennai-corporation-finances`: 14 resources, all
  CSV. Organization "Greater Chennai Corporation (GCC)". The first resource
  is "GCC budget summary statement".
