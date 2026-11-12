# Municipal Corporation of Delhi fixtures

All four files are whole response bodies from the MCD portal's menu API,
captured on 12 November 2026 (IST; the last two requests finished just after
midnight) with the probe's budget User-Agent, 2 seconds apart. Nothing is
trimmed or edited.

| File | URL |
|---|---|
| `showSubMenu_finance.json` | `https://mcdonline.nic.in/portal/showSubMenu?menuguide=914bf40f-81d3-4ae4-91c0-202a64bfa925` |
| `showSubMenu_budget_documents.json` | `https://mcdonline.nic.in/portal/showSubMenu?menuguide=1b4066c6-73ae-417b-bc62-796f9f0b7c7e` |
| `showContent_budget_documents.json` | `https://mcdonline.nic.in/portal/showContent?menuguide=1b4066c6-73ae-417b-bc62-796f9f0b7c7e` |
| `showPDFContent_budget_documents.json` | `https://mcdonline.nic.in/portal/showPDFContent?menuguide=1b4066c6-73ae-417b-bc62-796f9f0b7c7e` |

## What each hop holds

- `showSubMenu_finance.json` is the Finance menu (guide `914bf40f-...`). It
  lists four child menus: About Department, Budget Documents (guide
  `1b4066c6-73ae-417b-bc62-796f9f0b7c7e`), Downloads, and Key Contacts. Only
  Budget Documents matches the budget pattern, and the file carries no
  document links.
- `showSubMenu_budget_documents.json` is the two-byte body `{}`: the Budget
  Documents guide has no child menus.
- `showContent_budget_documents.json` carries the document table in the
  `menuName` field as an HTML string: 22 anchors, each with a relative
  `/portal/downloadFile/...pdf` href that resolves against
  `https://mcdonline.nic.in`.
- `showPDFContent_budget_documents.json` carries an empty `Downloads` table
  (`<tbody></tbody>`) and no document links.

The 22 documents equal the count from the first capture on 4 November 2026.
