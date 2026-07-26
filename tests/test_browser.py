"""Tests for the headless-browser fallback and its shell-vs-content check.

The numbers in ``REAL_SHELL_*`` / ``REAL_PAGE_*`` are measured, not invented —
plain GETs on 2026-07-26 of ``data.gov.in/catalogs`` (a Nuxt shell) and
``prsindia.org/billtrack`` (server-rendered). They are the reason this module
does not use a byte-size floor: the empty page is 2.5x larger than the full one.

No browser is launched here; ``BrowserProbe`` takes an injectable renderer.
"""

from __future__ import annotations

import json

import pytest

from commoner_probe import corpus as corpus_mod
from commoner_probe import validate as validate_mod
from commoner_probe.browser import (
    BrowserProbe,
    BrowserUnavailable,
    check_rendered,
    detect_frameworks,
    visible_text,
)

# Measured live 2026-07-26 with a plain urllib GET.
REAL_SHELL_HTML_BYTES = 1_000_989
REAL_SHELL_TEXT_CHARS = 1_850
REAL_PAGE_HTML_BYTES = 407_356
REAL_PAGE_TEXT_CHARS = 67_372

# A Nuxt shell in miniature: site chrome in the markup, a fat inline payload,
# and none of the actual catalogue.
SHELL_HTML = (
    '<html data-n-head-ssr><head><title>Catalog | Open Government Data</title></head>'
    "<body><div id='__nuxt'><nav>Skip to Main Content A Digital India Initiative "
    "Choose your theme Select default-theme Select blue-theme</nav></div>"
    "<script>window.__NUXT__=(function(a,b){return {data:[" + ("0," * 20000) + "]}})()</script>"
    "</body></html>"
)

RENDERED_HTML = (
    "<html><head><title>Bills Track</title><style>.x{color:red}</style></head><body>"
    "<h1>Bills Track</h1>" + "<div class='views-row'>The Finance Bill, 2026 — Passed</div>" * 400
    + "</body></html>"
)


def test_visible_text_excludes_script_and_style_payloads():
    text = visible_text(SHELL_HTML)
    assert "Digital India Initiative" in text
    assert "__NUXT__" not in text
    assert "window" not in text
    assert len(text) < 200


def test_the_measured_trap_byte_size_ranks_the_pages_backwards():
    """The finding that shaped this module, asserted as a regression guard.

    Live measurements rank the empty page *above* the full one by bytes and
    below it by visible text. The fixtures reproduce that inversion, and the
    check has to follow the text.
    """
    assert REAL_SHELL_HTML_BYTES > REAL_PAGE_HTML_BYTES
    assert REAL_SHELL_TEXT_CHARS < REAL_PAGE_TEXT_CHARS

    shell, page = check_rendered(SHELL_HTML), check_rendered(RENDERED_HTML)
    assert shell.html_bytes > page.html_bytes  # bytes say the shell is richer
    assert shell.text_chars < page.text_chars  # text says otherwise
    assert (shell.rendered, page.rendered) == (False, True)


def test_shell_is_not_rendered_and_says_why():
    check = check_rendered(SHELL_HTML)
    assert check.rendered is False
    assert "visible text" in check.reason
    assert "Nuxt" in check.reason
    assert check.html_bytes > 30_000
    assert check.text_chars < 200


def test_rendered_page_passes():
    check = check_rendered(RENDERED_HTML)
    assert check.rendered is True
    assert check.text_chars > 4000


def test_framework_markers_alone_do_not_fail_a_rendered_page():
    """A correctly-rendered Next.js page still carries __NEXT_DATA__."""
    html = RENDERED_HTML.replace("<body>", '<body><div id="__next">') + '<script id="__NEXT_DATA__">{}</script>'
    check = check_rendered(html)
    assert check.rendered is True
    assert "Next.js" in " ".join(check.frameworks)


def test_require_text_is_the_strong_check():
    check = check_rendered(RENDERED_HTML, require_text=["Finance Bill", "Ordinance"])
    assert check.rendered is False
    assert check.missing_text == ("Ordinance",)
    assert "required content absent" in check.reason


def test_require_text_beats_a_long_but_wrong_page():
    """A 200 with lots of text is still the wrong page — an error page, say."""
    error_page = "<html><body>" + "We are sorry, the service is unavailable. " * 300 + "</body></html>"
    assert check_rendered(error_page).rendered is True  # length alone: passes
    assert check_rendered(error_page, require_text=["Bills Track"]).rendered is False


def test_detect_frameworks_deduplicates():
    assert detect_frameworks('<html data-n-head-ssr><script>window.__NUXT__=1</script>') == ("Nuxt",)
    assert detect_frameworks("<html><body>plain</body></html>") == ()


# --- probe ------------------------------------------------------------------

def fake_renderer(html, status=200):
    def _render(url, *, wait_for=None):
        return html, status
    return _render


def test_capture_of_a_shell_is_never_recorded_as_success(tmp_path):
    probe = BrowserProbe(tmp_path, renderer=fake_renderer(SHELL_HTML))
    record = probe.capture("https://www.data.gov.in/catalogs")
    assert record["status"] == "shell_only"
    assert record["rendered"] is False
    assert record["http_status"] == 200
    # ...and the shell must not land where real captures are globbed from.
    assert "rendered_shells" in record["dest"]
    assert not (tmp_path / "rendered").exists()


def test_capture_of_a_real_page_is_recorded_as_downloaded(tmp_path):
    probe = BrowserProbe(tmp_path, renderer=fake_renderer(RENDERED_HTML))
    record = probe.capture("https://prsindia.org/billtrack")
    assert record["status"] == "downloaded"
    assert record["rendered"] is True
    assert record["dest"].endswith("rendered/prsindia-org-billtrack.html")
    assert len(record["sha256"]) == 64


def test_capture_writes_one_manifest_row(tmp_path):
    probe = BrowserProbe(tmp_path, renderer=fake_renderer(RENDERED_HTML))
    probe.capture("https://prsindia.org/billtrack")
    lines = (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["kind"] == "rendered_page"


def test_dry_run_writes_nothing(tmp_path):
    probe = BrowserProbe(tmp_path, renderer=fake_renderer(RENDERED_HTML))
    record = probe.capture("https://prsindia.org/billtrack", dry_run=True)
    assert record["status"] == "dry_run"
    assert not (tmp_path / "manifest.jsonl").exists()
    assert not (tmp_path / "rendered").exists()


def test_render_failure_is_recorded_as_error(tmp_path):
    def boom(url, *, wait_for=None):
        raise TimeoutError("navigation timeout")

    probe = BrowserProbe(tmp_path, renderer=boom)
    record = probe.capture("https://example.gov.in/x")
    assert record["status"] == "error"
    assert record["rendered"] is False
    assert "navigation timeout" in record["error"]


def test_missing_browser_propagates_rather_than_degrading(tmp_path):
    """A missing browser must not quietly become a plain-fetch result."""
    def unavailable(url, *, wait_for=None):
        raise BrowserUnavailable("playwright is not installed")

    probe = BrowserProbe(tmp_path, renderer=unavailable)
    with pytest.raises(BrowserUnavailable):
        probe.capture("https://example.gov.in/x")


def test_rendered_page_kind_is_registered_for_validation_and_corpus(tmp_path):
    probe = BrowserProbe(tmp_path, renderer=fake_renderer(RENDERED_HTML))
    good = probe.capture("https://prsindia.org/billtrack")
    probe.renderer = fake_renderer(SHELL_HTML)
    shell = probe.capture("https://www.data.gov.in/catalogs")

    assert validate_mod._pick_schema_name(good) == "manifest_rendered_page"

    jsonschema = pytest.importorskip("jsonschema")
    from commoner_probe import schemas as sc

    validator = jsonschema.Draft202012Validator(sc.load("manifest_rendered_page"))
    assert list(validator.iter_errors(good)) == []
    assert list(validator.iter_errors(shell)) == []

    # The schema itself must reject the lie, not merely the code path.
    lying = dict(shell, status="downloaded")
    assert list(validator.iter_errors(lying)) != []

    streamed = list(corpus_mod.Corpus(tmp_path).manifest_rendered_pages())
    assert [r.status for r in streamed] == ["downloaded", "shell_only"]
    assert "manifest_rendered_pages" in corpus_mod.Corpus._STREAM_MAP
