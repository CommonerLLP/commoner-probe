# MoF/DPE CPSE CSR Source Intake — 2026-06-16

## Status

Official DPE document-disclosure contract is proven. A CPSE CSR spend/project
contract is not yet proven.

This means `mof-dpe-csr` can start as a CPSE CSR document-disclosure source
family, but should not claim spend comparison until a spend-bearing source is
verified.

## Proven Contract

- Site: `https://www.dpe.gov.in/`
- Frontend API base discovered in the site bundle: `/cms/`
- CMS API root exposed via response headers:
  `https://cms-dpe.digifootprint.gov.in/wp-json/`
- Public same-origin API route used by the site:
  `https://www.dpe.gov.in/cms/wp-json/...`

Useful endpoints:

- Document categories:
  `GET https://www.dpe.gov.in/cms/wp-json/taxonomy/documents_category`
- Search:
  `GET https://www.dpe.gov.in/cms/wp-json/custom/api/search?s=CSR`
- Document listing:
  `GET https://www.dpe.gov.in/cms/wp-json/document/documents?...`
- Document detail:
  `GET https://www.dpe.gov.in/cms/wp-json/post-page/post?id=...`
- Document by slug:
  `GET https://www.dpe.gov.in/cms/wp-json/post-page/documents?slug_name=...`

Observed CSR categories:

- `Corporate Social Responsibility (CSR)`, slug
  `corporate-social-responsibility-csr`, term id `425`
- `CSR`, slug `csr`, term id `407`

Observed record families:

- `documents`
- `central_documents`
- `photos_post`
- `our_division`

Observed useful CSR document records include:

- DPE CPSE CSR guidelines.
- CSR expenditure alignment guidelines for national priorities.
- CSR workshops and CSRMS-related public notices.
- Public PDF URLs under `acf_data.pdf.url` on `central_documents` records.

## Adapter Boundary

Initial adapter scope should be document acquisition only:

- source manifest records for DPE CSR documents;
- document metadata: title, slug, post type, status, publish date, source
  categories, file date, language, PDF URL, PDF id, PDF filename, PDF size;
- archived/current status as a source field, not a filter.

Do not model this as spend data yet. The observed DPE JSON records do not expose
company-year-state-sector spend rows, project rows, consultants, vendors, or
implementing agencies.

## Next Verification

Before adding schema or CLI for `mof-dpe-csr`, verify whether either of these
official source families exposes spend-bearing CPSE CSR records:

1. DPE CSRMS public pages or APIs, if any are public.
2. Public Enterprises Survey records, especially PE Survey Report categories and
   PDFs, for CPSE-wise CSR fields.

If spend data is only inside PDFs, keep the adapter as document acquisition and
put extraction in a later parser stage.
