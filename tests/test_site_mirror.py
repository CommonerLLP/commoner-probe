"""Tests for the site mirror.

No network. A fake session serves canned bodies per URL and counts the
requests, because "did the resume refetch this" is a request question and a
stopwatch cannot answer it.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from commoner_probe.site_mirror import SiteMirror, verify_manifest

HOME = b"""<html><head><title>Education for all</title></head><body>
<p>Nine years of writing on school data.</p>
<a href="/about/">About</a>
<a href="/posts/udise-2024/">UDISE 2024</a>
<a href="https://elsewhere.example/other">off host</a>
<a href="/wp-admin/edit.php">admin</a>
<a href="/files/report.pdf">the report</a>
</body></html>"""

ABOUT = b"<html><head><title>About</title></head><body>Who writes here.</body></html>"
POST = b"""<html><head><title>UDISE 2024</title></head><body>
<script>var x = 'not text';</script>
<p>The enrolment figure fell.</p>
<a href="/about/">About</a>
</body></html>"""
PDF = b"%PDF-1.4 " + b"x" * 2000


class _Response:
    def __init__(self, body: bytes, status: int = 200, content_type: str = "text/html"):
        self.content = body
        self.status_code = status
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(body))}

    def iter_content(self, chunk_size: int = 65536):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i:i + chunk_size]


class _Site:
    """Serves one host. A path mapped to None answers 404."""

    def __init__(self, pages: dict[str, bytes | None]):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        path = url.split("example.test", 1)[-1]
        if path not in self.pages:
            return _Response(b"", 404)
        body = self.pages[path]
        if body is None:
            return _Response(b"", 404)
        ctype = "application/pdf" if path.endswith(".pdf") else "text/html"
        return _Response(body, 200, ctype)


PAGES = {
    "/": HOME,
    "/about/": ABOUT,
    "/posts/udise-2024/": POST,
    "/files/report.pdf": PDF,
}


def _mirror(tmp_path, site, **kwargs):
    mirror = SiteMirror("https://example.test/", tmp_path, rate_limit_sec=0, **kwargs)
    mirror.session = site
    return mirror


def _manifest(tmp_path) -> list[dict]:
    path = tmp_path / "manifest.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── the walk ──────────────────────────────────────────────────────────────


def test_the_walk_saves_every_page_and_document_on_the_host(tmp_path):
    stats = _mirror(tmp_path, _Site(PAGES)).run()

    assert stats["html"] == 3, stats
    assert stats["doc"] == 1, stats
    assert (tmp_path / "mirror" / "index.html").exists()
    assert (tmp_path / "mirror" / "about" / "index.html").exists()
    assert (tmp_path / "mirror" / "files" / "report.pdf").exists()


def test_the_walk_stays_on_the_host_and_skips_the_admin_paths(tmp_path):
    site = _Site(PAGES)
    _mirror(tmp_path, site).run()

    assert not [u for u in site.calls if "elsewhere.example" in u]
    assert not [u for u in site.calls if "wp-admin" in u]


def test_a_page_is_never_fetched_twice_in_one_walk(tmp_path):
    """Two pages link to /about/. The frontier is a set, not a list."""
    site = _Site(PAGES)
    _mirror(tmp_path, site).run()

    assert len([u for u in site.calls if u.endswith("/about/")]) == 1


# ── the three artefacts ───────────────────────────────────────────────────


def test_the_manifest_carries_a_sha256_and_a_url_for_every_file(tmp_path):
    _mirror(tmp_path, _Site(PAGES)).run()
    rows = [r for r in _manifest(tmp_path) if r["kind"] == "mirrored_file"]

    assert len(rows) == 4
    for row in rows:
        data = (tmp_path / row["path"]).read_bytes()
        assert row["sha256"] == hashlib.sha256(data).hexdigest()
        assert row["bytes"] == len(data)
        assert row["url"].startswith("https://example.test/")


def test_the_index_names_every_page_and_no_document(tmp_path):
    _mirror(tmp_path, _Site(PAGES)).run()
    index = (tmp_path / "INDEX.md").read_text()

    assert "Education for all" in index
    assert "About" in index
    assert "UDISE 2024" in index
    assert "report.pdf" not in index, "a PDF is not a page"


def test_the_index_excerpt_skips_script_text(tmp_path):
    """A script body is not page text, and 120 characters of JavaScript tells
    the reader nothing about the page."""
    _mirror(tmp_path, _Site(PAGES)).run()
    row = next(r for r in _manifest(tmp_path) if r["title"] == "UDISE 2024")

    assert "not text" not in row["excerpt"]
    assert "enrolment figure fell" in row["excerpt"]


def test_the_text_manifest_matches_the_jsonl(tmp_path):
    _mirror(tmp_path, _Site(PAGES)).run()
    rows = {r["path"]: r for r in _manifest(tmp_path) if r["kind"] == "mirrored_file"}
    lines = [ln for ln in (tmp_path / "MANIFEST.txt").read_text().splitlines() if ln.strip()]

    assert len(lines) == len(rows)
    for line in lines:
        sha, path, size, url = line.split("  ")
        assert rows[path]["sha256"] == sha
        assert rows[path]["bytes"] == int(size)
        assert rows[path]["url"] == url


# ── the defect this upstreaming exists to fix ─────────────────────────────


def test_a_killed_walk_leaves_a_manifest_and_an_index(tmp_path):
    """The crawler this replaces wrote both files after the walk finished. A
    run killed at its deadline left 540 saved pages with neither, and the next
    reader had to select pages by grepping directory names.

    The walk here stops on its second fetch, the way a signal stops it.
    """
    site = _Site(PAGES)
    mirror = _mirror(tmp_path, site)
    real_fetch = mirror.fetch

    def fetch_then_stop(url):
        real_fetch(url)
        if mirror.stats["html"] >= 2:
            mirror._request_stop(15, None)

    mirror.fetch = fetch_then_stop
    mirror.run()

    assert mirror.stats["html"] == 2, "the stop did not take effect"
    rows = _manifest(tmp_path)
    assert len(rows) == 3, "two pages and the document between them"
    assert (tmp_path / "MANIFEST.txt").read_text().count("\n") == 3
    assert (tmp_path / "INDEX.md").read_text().count("\n") == 2, "the PDF is not a page"


def test_what_the_walk_never_reached_is_written_down(tmp_path):
    site = _Site(PAGES)
    mirror = _mirror(tmp_path, site, deadline_sec=None)
    mirror.run(max_pages=1)

    unfetched = (tmp_path / "UNFETCHED.txt").read_text()
    assert "https://example.test/about/" in unfetched


def test_a_404_is_recorded_and_does_not_end_the_walk(tmp_path):
    site = _Site({**PAGES, "/about/": None})
    stats = _mirror(tmp_path, site).run()

    assert stats["fail"] == 1
    assert stats["html"] == 2, "the other pages still saved"
    assert "https://example.test/about/\t404" in (tmp_path / "FAILURES.txt").read_text()


# ── resume ────────────────────────────────────────────────────────────────


def test_a_resume_does_not_refetch_what_the_manifest_vouches_for(tmp_path):
    _mirror(tmp_path, _Site(PAGES)).run()

    second = _Site(PAGES)
    stats = _mirror(tmp_path, second).run()

    refetched = [u for u in second.calls if u.split("example.test")[-1] in PAGES]
    assert refetched == [], "the resume refetched a file already held"
    assert stats["held"] == 4
    assert stats["html"] == 0


def test_a_row_stops_vouching_when_its_file_is_gone(tmp_path):
    """The row describes a file. Deleting the file must not leave a mirror
    that reports holding it."""
    _mirror(tmp_path, _Site(PAGES)).run()
    (tmp_path / "mirror" / "about" / "index.html").unlink()

    second = _Site(PAGES)
    _mirror(tmp_path, second).run()

    assert [u for u in second.calls if u.endswith("/about/")]


# ── verification ──────────────────────────────────────────────────────────


def test_the_manifest_verifies_against_the_files_on_disk(tmp_path):
    _mirror(tmp_path, _Site(PAGES)).run()
    assert verify_manifest(tmp_path) == []


def test_verification_names_a_file_that_changed_and_one_that_went(tmp_path):
    _mirror(tmp_path, _Site(PAGES)).run()
    (tmp_path / "mirror" / "about" / "index.html").write_bytes(b"tampered")
    (tmp_path / "mirror" / "files" / "report.pdf").unlink()

    problems = verify_manifest(tmp_path)
    assert any("about" in p and "sha256 differs" in p for p in problems)
    assert any("report.pdf" in p and "missing" in p for p in problems)


def test_the_mirror_record_validates(tmp_path):
    import jsonschema

    from commoner_probe import schemas

    _mirror(tmp_path, _Site(PAGES)).run()
    for row in _manifest(tmp_path):
        jsonschema.validate(row, schemas.load("manifest_mirrored_file"))


# ── the destination path ──────────────────────────────────────────────────


def test_a_traversal_in_the_source_path_cannot_escape_the_mirror(tmp_path):
    mirror = _mirror(tmp_path, _Site(PAGES))
    dest = mirror.destination("https://example.test/../../etc/passwd")

    assert tmp_path in dest.parents


def test_two_query_strings_are_two_files(tmp_path):
    mirror = _mirror(tmp_path, _Site(PAGES))

    assert (mirror.destination("https://example.test/p?id=1")
            != mirror.destination("https://example.test/p?id=2"))


@pytest.mark.parametrize("url", ["ftp://example.test/x", "not-a-url-at-all://"])
def test_a_non_http_base_is_refused(tmp_path, url):
    with pytest.raises(ValueError):
        SiteMirror(url, tmp_path)


def test_a_resume_reaches_a_page_the_first_run_never_fetched(tmp_path):
    """A resume that merely skips a held page never learns what that page
    links to. The frontier then collapses to the seeds, and everything the
    first run had not yet reached stays unreached — a mirror that reports
    completing and holds three of four pages."""
    first = _Site(PAGES)
    _mirror(tmp_path, first).run(max_pages=2)
    assert not (tmp_path / "mirror" / "posts").exists()

    second = _Site(PAGES)
    _mirror(tmp_path, second).run()

    assert (tmp_path / "mirror" / "posts" / "udise-2024" / "index.html").exists()
    assert len([r for r in _manifest(tmp_path) if r["kind"] == "mirrored_file"]) == 4


def test_a_resume_rebuilds_the_frontier_from_a_held_sitemap(tmp_path):
    """A live resume against a 4,163-URL site rebuilt a frontier of 99 while
    the sitemaps sat on disk unread. A sitemap is where most of the frontier
    comes from, so a resume that skips it forgets nearly the whole site."""
    sitemap = (b"<urlset><url><loc>https://example.test/posts/udise-2024/</loc></url>"
               b"<url><loc>https://example.test/about/</loc></url></urlset>")
    pages = {"/": HOME, "/sitemap_index.xml": sitemap}

    first = _Site(pages)
    _mirror(tmp_path, first).run(max_pages=2)

    second = _Site({**pages, "/about/": ABOUT, "/posts/udise-2024/": POST})
    mirror = _mirror(tmp_path, second)
    mirror.run(max_pages=0)

    queued = {url for _, _, url in mirror._frontier}
    assert "https://example.test/posts/udise-2024/" in queued
    assert "https://example.test/about/" in queued


def test_the_url_the_brake_stopped_on_is_still_unfetched(tmp_path):
    """A brake that drops the URL it fired on writes an UNFETCHED list missing
    exactly one entry, and the next run never learns of it."""
    site = _Site(PAGES)
    mirror = _mirror(tmp_path, site)
    mirror.run(max_pages=1)

    fetched = {r["url"] for r in _manifest(tmp_path)}
    unfetched = set((tmp_path / "UNFETCHED.txt").read_text().split())
    assert fetched | unfetched >= {"https://example.test/",
                                   "https://example.test/about/",
                                   "https://example.test/posts/udise-2024/",
                                   "https://example.test/files/report.pdf"}
