# DST Detailed Demands for Grants fixtures

DST rebuilt www.dst.gov.in as a Next.js app that reads its document lists from
a WordPress JSON API under `https://www.dst.gov.in/cms/wp-json/`. Both files
were saved on 18 October 2026.

* `orders_and_notices_trimmed.json` is the response of
  `document/documents?document_category=orders-and-notices&limit=100&page=1`.
  The live response has 39 posts. This copy keeps two of them, unedited: an
  unrelated office memorandum and the post with `post_name` "budget", which
  holds the DDG series as 18 attachment IDs and one Link entry. The envelope
  fields (`total_items`, `total_pages`, `current_page`) are the live values.
* `budget_files.json` is the response of `post-page/post?id=<the 18 IDs,
  comma-separated>`. It is complete and unedited. Each post is a
  `central_documents` entry with a direct `acf_data.pdf.url`.

Neither file is hand-edited apart from dropping posts and re-indenting the
JSON.
