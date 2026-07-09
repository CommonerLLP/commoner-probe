# Union ministry/department website platforms — a survey

This is a public, source-facing reference — the counterpart to
[`ENDPOINTS.md`](ENDPOINTS.md) for the `ministry-ddg` acquisition adapter
(`commoner_probe/ddg.py`). Every Union ministry and department hosts its own
"Detailed Demands for Grants" (DDG) on its own website, in its own site
template. There is no central index. Building the adapter's registry means
checking each ministry's site individually, and most of the value of that
work is in knowing which ones *don't* work and why — so the next agent
doesn't repeat the research. Ministries not yet checked at all are simply
absent from the tables below; being absent does not mean "blocked", it means
"not investigated yet."

Every row here was live-verified (an actual HTTP request this session), not
guessed from search results. Dates are per-check, since a site can migrate
platforms between sessions — re-verify rather than trust an old row past a
few months old.

## Registered in `MINISTRY_DDG_PORTALS` — working today

| Code | Ministry/department | Listing URL | Template | Docs | Checked |
|---|---|---|---|---|---|
| `dea` | Department of Economic Affairs (MoF) | dea.gov.in/reports-detail-demands-grants | card | 10 (2017-18→2026-27) | 2026-07-08 |
| `mha` | Ministry of Home Affairs | mha.gov.in/en/divisionofmha/finance-division | table | 32 (2012-13→2026-27, 2 vols/yr) | 2026-07-09 |
| `doe` | Department of Expenditure (MoF) | doe.gov.in/detailed-demands-for-grants | table | 7 (2006-07→2021-22) | 2026-07-09 |
| `dolr` | Department of Land Resources (Rural Dev.) | dolr.gov.in/document-category/detailed-demand-for-grants/ | table | 12 (2020-21→2026-27, some dup. accessible-version PDFs) | 2026-07-09 |
| `moefcc` | Environment, Forest and Climate Change | moef.gov.in/detailed-demand-for-grants | table | 16 (2008-09→2023-24, Hindi-only titles) | 2026-07-09 |
| `mopng` | Petroleum and Natural Gas | mopng.gov.in/en/accounts/demands-grants | table | 18 (2008-09→2026-27) | 2026-07-09 |
| `dst` | Department of Science and Technology | dst.gov.in/documents/budget | list | 10 (2017-18→2026-27) | 2026-07-09 |

**Three site templates** are supported (`commoner_probe/ddg.py` docstring has
the full markup detail): `card` (Bootstrap grid), `table` (`<tr>/<td>`,
Drupal Views or WordPress document-category), `list` (flat run of
`<a href="...pdf">` anchors, no wrapping structure). All three are plain
server-rendered HTML — no browser needed.

## Verified working, deliberately NOT registered — needs a human decision

| Code | Ministry/department | Listing URL | Template | Docs | Why held back |
|---|---|---|---|---|---|
| `steel` | Ministry of Steel | steel.gov.in/detailed-demands-for-grants | table | 4 | Server presents a **self-signed TLS certificate**. `curl` accepts it (different trust store); Python `requests`/certifi correctly rejects it. Disabling cert verification is a security decision, not a default an adapter should make silently. |
| `tribal` | Ministry of Tribal Affairs | tribal.nic.in/Finance.aspx | list | 8 of 11 (3 older editions are `.xls`, not `.pdf`, and correctly excluded) | Server sends an **incomplete certificate chain** ("unable to get local issuer certificate"). Same TLS-verification objection as Steel. |
| `wcd` | Women and Child Development | wcd.gov.in/documents/budget (+ /documents/budget-archives) | card-ish (`div.list_det_bx`, not the `dea` card shape — would need a 4th parser) | 13 across 2 pages | robots.txt is **`Disallow: /`** — a full-site crawl block. `http_client.py` has an explicit `respect_robots=False` opt-out for exactly this case, gated per registry entry, but overriding a blanket disallow is a policy call, not something to wire in unilaterally. |

## Blocked — JS-rendered SPA or WAF+AJAX (not scrapeable by plain HTTP GET)

A large and growing share of `.gov.in` ministry sites have migrated to a
shared **Next.js/Angular SPA platform** — several builds visibly share the
same `buildId`/asset-hash lineage and reference `digifootprint.gov.in`
(a common government front-end platform), strongly suggesting one vendor
serves many ministries. The server returns an essentially empty HTML shell
(`<div id="__next">`, `<app-root></app-root>`) with an empty or
routing-params-only `__NEXT_DATA__`/`__N_SSP` payload — the real document
list is fetched client-side after JavaScript executes. **No amount of
User-Agent tuning, retrying, or India-based network egress fixes this** — it
needs either a headless-browser fetch (Playwright) or reverse-engineering the
underlying JSON API each app calls, neither of which was attempted this
session (out of scope for a regex-based adapter).

| Ministry/department | Domain(s) checked | Platform | Notes |
|---|---|---|---|
| Ministry of Electronics & IT (MeitY) | meity.gov.in | Next.js (digifootprint) | Even `/sitemap.xml` returns the SPA shell, not real XML. `/_next/data/<buildId>/...json` route also falls back to HTML — no static escape hatch. |
| Ministry of Education | education.gov.in, dsel.education.gov.in | Next.js | Individual DDG PDFs are directly linkable once the URL is known; no scrapeable index. |
| Ministry of Power | powermin.gov.in | Next.js (digifootprint) | Old Drupal listing path 404s post-migration; individual `/static/uploads/...` PDFs still resolve directly if the exact URL is known. |
| Ministry of Rural Development (main dept.) | rural.gov.in | Next.js | Its own DoLR sub-department (see registered table above) is *not* migrated and works fine — platform migration is per-department, not ministry-wide. |
| Housing and Urban Affairs | mohua.gov.in | Next.js | — |
| Road Transport and Highways (MoRTH) | morth.nic.in / morth.gov.in | **Angular** (`<app-root>`) | Google's index has real per-year page titles cached (implies a prerender-for-Googlebot variant exists), but a plain GET sees nothing. |
| Health and Family Welfare (MoHFW) | mohfw.gov.in, mohfw-dohfw.gov.in | Next.js | Legacy `main.mohfw.gov.in` (pre-migration, Drupal) no longer resolves (DNS failure) — no historical fallback via the live domain; Wayback Machine untried. |
| Labour and Employment | labour.gov.in | Next.js | Individual `/static/uploads/...` PDFs directly fetchable by hash if known; static-export JSON route also falls back to HTML. |
| Ministry of Mines | mines.gov.in/webportal | **Angular** | Individually-indexable PDFs exist under `/admin/download/<hash>.pdf`; no scrapeable listing. |
| Ministry of Textiles | texmin.gov.in | Next.js (digifootprint) | `texmin.nic.in` (old domain) DNS-fails entirely. |
| Dept. of Drinking Water & Sanitation (Jal Shakti) | jalshakti-ddws.gov.in | Next.js, `__N_SSP: true` | Server-side props fetched per-request by the Node server, never reach the raw HTTP response client-side sees. |
| Agriculture & Farmers Welfare | agriwelfare.gov.in | Server HTML shell + **client-side POST AJAX** (`/en/getDemand?vacancy_type=…`, DataTables-style JSON) | Domain is also currently returning a domain-wide **403 (Google Cloud Armor/WAF)** to every path including robots.txt — compounding blocker. `agricoop.nic.in`/`.gov.in` (older domain) both dead (NXDOMAIN / DNS timeout). |
| Commerce and Industry | commerce.gov.in | React/Vite SPA (`<div id="root">`) | No SPA-fallback rewrite server-side — direct GET of a client route 404s outright, doesn't even serve the JS shell. Old WordPress-era PDF paths (`/wp-content/uploads/...`) are dead post-migration. Extracted the app's own `api/v1/budget` / `api/v1/budget-archive` calls from its JS bundle; both 404 directly (likely only reachable via an internal proxy). |

## Unreachable from this environment's network egress (not a rendering problem)

These sites are reachable in principle (real domains, respond to other
tools/paths) but this session's egress could not open a TCP connection —
distinct from the JS-rendering group above, and worth retrying from a
different network path (e.g. an India-based egress box) before concluding
anything about their content.

| Ministry/department | Domain | Symptom | Notes |
|---|---|---|---|
| Ministry of Railways | indianrailways.gov.in | Connection refused on :80 and :443 | A `railwayboard/view_section.jsp?id=...` URL and an RDSO PDF path pattern (`railwayboard/uploads/directorate/finance_budget/Budget_<YYYY-YY>_Final/...`) look promising from search-index cache, unverified live. |
| Social Justice and Empowerment | socialjustice.gov.in (IP `164.100.54.92`) | Connection refused on :443 specifically; :80 connects and redirects to :443 | Bare NIC-hosted IP, no CDN in front — looks like an ASN/geo-level rule, not a blanket gov.in issue (other CDN-fronted `.gov.in` domains connected fine in the same test). Candidate URL: `socialjustice.gov.in/common/76791`. |
| Ministry of Defence | mod.gov.in (IP `164.100.252.190`) | DNS resolves; :80 and :443 both refuse/timeout, while sibling domains (`ddpmod.gov.in`, `pib.gov.in`) connect fine | Wayback Machine's full crawl history of the domain shows no DDG-listing URL pattern (only a static grant-totals summary page, no PDFs) — may simply not exist on this domain even with connectivity. |
| Ministry of Corporate Affairs | mca.gov.in | Akamai WAF returns 403 "Access Denied" on **every** path including robots.txt, regardless of UA/headers tried | Distinct from MHA's WAF (which only blocked the URL-bearing default UA on robots.txt — a `SCHEME_FREE_USER_AGENT` fix cleared it). MCA's block looks TLS/JA3-fingerprint-based, not UA-string-based — the scheme-free-UA trick that fixed MHA does not apply. Real DDG PDFs are confirmed to exist on-domain (search-indexed titles back to 2016-17) — this is a bot-block, not "not found." |

## Already known from an earlier session (2026-07-08, `ddg.py`'s own docstring)

- **MSDE** (msde.gov.in) and **MPA** (mpa.gov.in) — both Next.js apps using
  `getServerSideProps` (`"__N_SSP":true`); same failure mode as the group
  above, discovered a day earlier before the shared-platform pattern was
  obvious.

## Headless-browser rendering was tried (2026-07-09) — does NOT cleanly fix this group

Installed Playwright + headless Chromium and live-tested it against 5 of the
13 blocked ministries. Verdict: **necessary but not sufficient** — it did not
cleanly recover a single one of the 5 tested, for two distinct reasons that
don't share a fix:

**1. A subset of ministries share an Akamai Bot Manager deployment that
blocks the headless browser itself, not just `curl`.** Confirmed by the
literal response body, not inference: MeitY, Power, and Labour (tested this
session) return the identical Akamai fingerprint —
`https://errors.edgesuite.net/<reference>` — to a real headless Chromium
navigation, same as MHA's earlier robots.txt block and MCA's blanket 403.
This is **not** a UA-string problem (the `SCHEME_FREE_USER_AGENT` fix that
cleared MHA's robots.txt block does not apply here) — it's bot/TLS
fingerprinting at the WAF layer. Checked the actual hosting signature across
both groups: the Akamai-fronted ministries (MHA, MeitY, MCA, Power, likely
Labour/Agriculture) are a distinct, shared infrastructure cluster; the
ministries that work cleanly (DST, DoLR, MoEFCC, MoPNG, Steel, DEA, DoE) all
return a bare `Server: Apache`/NIC signature with no CDN or bot-detection
layer at all. This tracks with each ministry independently procuring its own
hosting — there is no single government-wide WAF policy, only some
ministries (mostly larger/higher-profile ones) have Akamai's paid
bot-protection tier. Getting past this would mean fingerprint-evasion
techniques (stealth-patched browsers) — a categorically different, more
adversarial technique than plain scraping of a public disclosure, and
deliberately not pursued without an explicit decision to do so.

**2. For the non-WAF sites, the previously-found candidate URLs are often
stale.** Housing & Urban Affairs' candidate (`/cms/detailed-demand-for-grants.php`)
is a dead pre-migration link — the `.php` extension is the tell — and the
current site's real navigation menu has no Budget/Grants entry in the same
place at all (checked the live homepage nav, 112 links, none matched).
MoRTH's candidate URL rendered a real page (Angular prerender, correct
`<title>`) but not DDG content — same "URL moved, browser can't guess where
to" problem. **The browser renders the site fine; the specific URL doesn't
correspond to real content anymore.** This needs fresh per-site navigation
discovery on the *current* site structure, not a generic headless fetch —
i.e. real research time per ministry, the same kind this whole document
already represents, not a one-time infrastructure investment.

**Decision (2026-07-09, Commoner): stop here, document, do not pursue
per-site URL rediscovery or WAF fingerprint-evasion right now.** Revisit if
there's a specific data need that justifies the per-site research cost.

## What would actually move this forward

1. **India-based egress for the network-unreachable group.** Worth a retry
   from the india-fetch box (referenced elsewhere in org memory) before
   concluding anything about Railways, Social Justice, or Defence — this
   group is a genuinely different failure mode (connection-level, not
   rendering or WAF) and the fix is plausible with zero new tooling.
2. **An explicit policy call on the three flagged-but-working sites**
   (Steel/Tribal TLS, WCD robots.txt) — the cheapest possible wins, since the
   content is already known to be scrapeable; just needs a scoped, documented
   override decision for each.
3. **Per-site URL rediscovery**, ministry by ministry, for the non-WAF SPA
   group (Housing, MoRTH, MoHFW, Mines, Textiles, Jal Shakti, Rural
   Development's main dept., Commerce) — real research time, not
   infrastructure. Housing and MoRTH's specific failure modes are now
   documented above as a starting point.
4. **The Akamai-fronted group (MeitY, Power, Labour, MCA, and likely
   Agriculture)** is the hardest tier — fingerprint-evasion is a real
   technique with real dual-use weight to it, not something to reach for by
   default. Leave it out of scope until there's a specific reason to revisit.
