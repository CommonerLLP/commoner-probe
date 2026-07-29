# SPDX-License-Identifier: MIT
"""Headless-browser fallback acquisition, with a shell-vs-content assertion.

REQ-0035. A **fallback**, never the default: a real browser costs orders of
magnitude more than a GET, so it is for pages the fetch layer genuinely cannot
read, not for convenience.

The point of this module is not "a browser path exists" — it is that a capture
**cannot claim success when it only got a shell**. JS-heavy portals do not fail
loudly. ``data.gov.in``, ``lokdhaba.ashoka.edu.in`` and ``myneta.info`` all
answer HTTP 200 with a well-formed HTML document for every path, including
invented ones. A status-code check records a clean acquisition of a page
containing none of the data. That silent-success class has bitten this repo
repeatedly (the RS PDF 406 stored as ``fetch_status: "ok"``, REQ-0005).

**Byte size does not separate the two, and this was measured, not assumed.**
Live, 2026-07-26:

===========================  ===========  =============
page                         HTML bytes   visible text
===========================  ===========  =============
data.gov.in/catalogs           1,000,989    1,850 chars
prsindia.org/billtrack           407,356   67,372 chars
===========================  ===========  =============

The Nuxt shell is **two and a half times larger** than the fully-rendered page,
because a ~1 MB inline ``window.__NUXT__`` payload counts as bytes but is
script, not content. So a size floor — the check REQ-0035 itself suggested —
passes the empty page and would have to fail the real one. The discriminator is
visible text *after* script/style removal, plus caller-supplied content the page
must actually contain.

Framework markers (``__NUXT__``, ``__NEXT_DATA__``, ``<app-root>``) are
reported but never disqualifying on their own: a correctly-rendered Next.js
page still carries them. They explain a failure; they do not define one.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

#: Minimum characters of visible text before a capture counts as rendered.
#: data.gov.in's shell yields 1,850 (site chrome: nav, theme switcher, footer),
#: so the floor sits above that; a real listing page clears it by an order of
#: magnitude. Override per target — this is a default, not a law.
DEFAULT_MIN_TEXT_CHARS = 4000

#: Substrings that identify a client-rendered app. Reported, not disqualifying.
SHELL_FRAMEWORK_MARKERS: tuple[tuple[str, str], ...] = (
    ("__NUXT__", "Nuxt"),
    ("data-n-head-ssr", "Nuxt"),
    ("__NEXT_DATA__", "Next.js"),
    ('id="__next"', "Next.js"),
    ("__N_SSP", "Next.js (server-side props)"),
    ("<app-root", "Angular"),
    ('id="root"', "React root element"),
)

DEFAULT_ENGINE = "chromium"
DEFAULT_TIMEOUT_MS = 45_000
#: NOT "networkidle". Government SPAs commonly hold a socket open or poll on a
#: timer, so networkidle never fires and the navigation times out with the page
#: fully rendered on screen — mospi.gov.in did exactly that (90s timeout, 2026-07-27).
#: "load" fires reliably; `settle_ms` then covers the client-side render that
#: happens after it.
DEFAULT_WAIT_UNTIL = "load"
#: Grace period after load for client-side rendering to populate the DOM.
#: Prefer `wait_for` (a selector) when a known one exists — this is the blunt
#: fallback for when it does not.
#:
#: Measured, not guessed. On mospi.gov.in, 2,500 ms captured the empty React
#: root in 4 of 5 cold trials (56 KB / 0 chars of visible text) and the full
#: page in 1 — a value that passes occasionally is worse than one that fails
#: consistently, because the occasional pass is what gets recorded as proof.
#: 6,000 ms and above returned the rendered page (192 KB / 10,897 chars) in
#: every trial. 8,000 ms buys margin on a slower day; the browser path is an
#: explicit fallback, so seconds are cheaper here than a false capture.
DEFAULT_SETTLE_MS = 8_000

_STRIPPED_ELEMENTS_RE = re.compile(
    r"(?is)<(script|style|noscript|template|svg|head)\b[^>]*>.*?</\1>"
)
#: Inline styles (and the bare `hidden` attribute) that declare an element
#: invisible. A preloaded panel or a duplicated responsive nav can carry
#: thousands of characters no reader ever sees, which lifts a shell over the
#: text floor.
_HIDDEN_STYLE_RE = re.compile(
    r"(?i)(?:display\s*:\s*none|visibility\s*:\s*hidden)"
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class BrowserUnavailable(RuntimeError):
    """Playwright, or its browser binary, is not installed."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class _VisibleTextParser(HTMLParser):
    """Collect text, skipping subtrees the markup declares invisible.

    Depth-tracking, not pattern-matching. A regex cannot do this: with a
    non-greedy body it stops at the FIRST matching close tag, so a hidden
    ``<div>`` containing a nested ``<div>`` ends early and the parent's
    remaining text survives into the output. Hidden SPA panels nest divs as a
    matter of course, so that leak is the common case rather than the exotic
    one — and enough surviving text lifts a shell over the floor and records it
    as a real capture.
    """

    #: Content-free by definition; their text is markup, not reading matter.
    SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head"})
    #: Void elements never close, so they must not push onto the depth stack.
    VOID_TAGS = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    })
    #: Phrasing content: sits inside a line, so its boundaries are NOT word
    #: boundaries. ``Mini<b>s</b>try`` is one word to a reader, and the old
    #: tag-stripping regex broke it into three by replacing every tag with a
    #: space. Everything not listed here is treated as block-level.
    INLINE_TAGS = frozenset({
        "a", "abbr", "acronym", "b", "bdi", "bdo", "big", "cite", "code",
        "data", "del", "dfn", "em", "font", "i", "ins", "kbd", "label", "mark",
        "nobr", "output", "q", "rp", "rt", "rtc", "ruby", "s", "samp", "small",
        "span", "strike", "strong", "sub", "sup", "time", "tt", "u", "var",
        "wbr",
    })
    #: Void elements that a reader sees as a break. The other void tags
    #: (``img``, ``input``, ``meta``…) sit inline and must not split a word.
    BREAK_VOID_TAGS = frozenset({"br", "hr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        #: Open tags whose subtree is being suppressed, innermost last.
        self._suppressed: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.VOID_TAGS:
            if not self._suppressed and tag in self.BREAK_VOID_TAGS:
                self.parts.append(" ")
            return
        if self._suppressed:
            self._suppressed.append(tag)
            return
        if tag in self.SKIP_TAGS or self._is_hidden(attrs):
            self._suppressed.append(tag)
            return
        self._separate(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self._suppressed:
            self._separate(tag)
            return
        # Close the innermost matching open tag. Unbalanced markup is normal on
        # the web, so an end tag with no match is ignored rather than trusted.
        for i in range(len(self._suppressed) - 1, -1, -1):
            if self._suppressed[i] == tag:
                del self._suppressed[i:]
                return

    def _separate(self, tag: str) -> None:
        """Mark a block boundary so adjacent elements do not fuse.

        ``<div>Ministry</div><div>of Finance</div>`` carries no whitespace
        between the fragments, so joining them raw yields ``Ministryof
        Finance`` — which fails ``require_text`` and files a real capture as
        ``shell_only``.
        """
        if tag not in self.INLINE_TAGS and tag not in self.VOID_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._suppressed:
            self.parts.append(data)

    @staticmethod
    def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
        for name, value in attrs:
            if name == "hidden":
                return True
            if name == "style" and value and _HIDDEN_STYLE_RE.search(value):
                return True
        return False


def visible_text(html: str) -> str:
    """Text a reader would actually see: script, style and markup removed.

    Pure function. This is the measurement the whole module turns on — an
    inline JS payload is bytes, not content, and counting it is what makes a
    shell look full. Markup-declared hidden subtrees go the same way, because a
    preloaded panel or a duplicated responsive nav is bytes a reader never sees
    and would lift a shell over the floor just as an inline payload would.

    Hidden subtrees are excluded by depth-tracking parse, not by regex, because
    a non-greedy pattern ends at the first matching close tag and leaks the rest
    of a nested hidden parent back into the count.

    It reads markup, so it catches ``hidden`` and inline ``display:none`` and
    **not** hiding done from a stylesheet class. Asking the browser for
    ``body.innerText`` would catch every case, and would move the measurement
    inside the one part of this module that cannot be unit-tested against canned
    markup. That trade was made deliberately in favour of the pure function:
    ``require_text`` is the answer when a page's real content must be proven,
    and it does not depend on this heuristic at all.
    """
    parser = _VisibleTextParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        # Never let malformed markup crash the measurement — fall back to the
        # blunt strip, which under-counts rather than over-counts.
        body = _STRIPPED_ELEMENTS_RE.sub(" ", html or "")
        return _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", body))).strip()
    return _WS_RE.sub(" ", "".join(parser.parts)).strip()


def detect_frameworks(html: str) -> tuple[str, ...]:
    """Client-side frameworks the markup advertises, de-duplicated."""
    found: list[str] = []
    for marker, name in SHELL_FRAMEWORK_MARKERS:
        if marker in (html or "") and name not in found:
            found.append(name)
    return tuple(found)


@dataclass(frozen=True)
class RenderCheck:
    """Verdict on whether a captured page contains content or only a shell."""

    rendered: bool
    reason: str
    text_chars: int
    html_bytes: int
    frameworks: tuple[str, ...] = ()
    missing_text: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "rendered": self.rendered,
            "reason": self.reason,
            "text_chars": self.text_chars,
            "html_bytes": self.html_bytes,
            "frameworks": list(self.frameworks),
            "missing_text": list(self.missing_text),
        }


def check_rendered(
    html: str,
    *,
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
    require_text: Sequence[str] = (),
) -> RenderCheck:
    """Decide whether *html* is a rendered page or an empty app shell.

    Pure function, unit-testable against canned markup. ``require_text`` is the
    strong form of the check: strings the caller knows the real page contains
    (a column header, a known row label). Use it whenever a known-good string
    exists — a text-length floor is a heuristic, a required string is a fact.
    """
    text = visible_text(html)
    frameworks = detect_frameworks(html)
    missing = tuple(s for s in require_text if s.lower() not in text.lower())
    html_bytes = len((html or "").encode("utf-8"))

    if missing:
        return RenderCheck(
            rendered=False,
            reason=f"required content absent from the rendered text: {list(missing)}",
            text_chars=len(text),
            html_bytes=html_bytes,
            frameworks=frameworks,
            missing_text=missing,
        )
    if len(text) < min_text_chars:
        detail = f" ({', '.join(frameworks)} shell)" if frameworks else ""
        return RenderCheck(
            rendered=False,
            reason=(
                f"only {len(text)} chars of visible text{detail}, below the "
                f"{min_text_chars}-char floor — {html_bytes} bytes of HTML is "
                "mostly script, not content"
            ),
            text_chars=len(text),
            html_bytes=html_bytes,
            frameworks=frameworks,
        )
    return RenderCheck(
        rendered=True,
        reason=f"{len(text)} chars of visible text",
        text_chars=len(text),
        html_bytes=html_bytes,
        frameworks=frameworks,
    )


def render_page(
    url: str,
    *,
    wait_for: str | None = None,
    engine: str = DEFAULT_ENGINE,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    user_agent: str | None = None,
    wait_until: str = DEFAULT_WAIT_UNTIL,
    settle_ms: int = DEFAULT_SETTLE_MS,
) -> tuple[str, int | None]:
    """Load *url* in a headless browser and return ``(html, http_status)``.

    ``wait_for`` is a CSS selector to await before snapshotting — the reliable
    way to let a client-rendered list populate. Without one, the capture waits
    ``settle_ms`` after ``wait_until`` instead, which is a guess and is why a
    selector is worth supplying whenever you know one.

    Raises :class:`BrowserUnavailable` when Playwright or its browser binary is
    missing, naming the install command. It never degrades to a plain fetch:
    silently returning un-rendered HTML from a function called ``render_page``
    would recreate the exact failure this module exists to prevent.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import]
    except ImportError as exc:  # pragma: no cover - import-guard branch
        raise BrowserUnavailable(
            "playwright is not installed — pip install playwright && "
            "playwright install chromium. It is an optional fallback, not a "
            "dependency of the default fetch path."
        ) from exc

    with sync_playwright() as p:
        try:
            browser_type = getattr(p, engine)
        except AttributeError as exc:
            raise ValueError(f"unknown browser engine {engine!r}") from exc
        try:
            browser = browser_type.launch(headless=True)
        except Exception as exc:  # playwright raises its own Error type
            raise BrowserUnavailable(
                f"could not launch headless {engine} — run: playwright install {engine} ({exc})"
            ) from exc
        try:
            context = browser.new_context(**({"user_agent": user_agent} if user_agent else {}))
            page = context.new_page()
            response = page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            if wait_for:
                page.wait_for_selector(wait_for, timeout=timeout_ms)
            elif settle_ms:
                page.wait_for_timeout(settle_ms)
            return page.content(), (response.status if response else None)
        finally:
            browser.close()


def _slug(url: str) -> str:
    """Filesystem-safe identity for *url*, including its query and fragment.

    Host and path alone are not an identity: ``/search?q=alpha`` and
    ``/search?q=beta`` are different pages that would share one manifest key
    and one destination file, so the second capture silently overwrites the
    first while both rows claim their own URL and hash. The query and fragment
    are folded in as a short digest rather than spelled out, because they carry
    separators, encodings and unbounded length.
    """
    parsed = urlparse(url)
    raw = f"{parsed.netloc}{parsed.path}".strip("/")
    slug = _SLUG_RE.sub("-", raw.lower()).strip("-") or "page"
    slug = slug[:80]
    variant = f"{parsed.query}#{parsed.fragment}" if (parsed.query or parsed.fragment) else ""
    if variant:
        slug = f"{slug}-{hashlib.sha256(variant.encode('utf-8')).hexdigest()[:10]}"
    return slug


@dataclass
class BrowserProbe:
    """Capture JS-rendered pages into the provenance manifest.

    Shells and rendered pages are written to **separate directories**, not just
    flagged differently in the manifest. Downstream tools glob directories; a
    shell sitting next to real captures will eventually be read as content by
    something that never opened manifest.jsonl.
    """

    out_dir: Path
    engine: str = DEFAULT_ENGINE
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    #: Injectable for tests; defaults to the real headless browser.
    renderer: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.out_dir = Path(self.out_dir)
        self.manifest = self.out_dir / "manifest.jsonl"

    def _render(self, url: str, *, wait_for: str | None) -> tuple[str, int | None]:
        if self.renderer is not None:
            return self.renderer(url, wait_for=wait_for)
        return render_page(url, wait_for=wait_for, engine=self.engine, timeout_ms=self.timeout_ms)

    def capture(
        self,
        url: str,
        *,
        wait_for: str | None = None,
        require_text: Sequence[str] = (),
        min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        now = _now_iso()
        record: dict[str, Any] = {
            "key": f"RENDERED|{_slug(url)}",
            "kind": "rendered_page",
            "record_type": "rendered_page",
            "source_family": "browser",
            "source": urlparse(url).netloc,
            "url": url,
            "acquired_via": "headless-browser",
            "render_engine": self.engine,
            "wait_for": wait_for,
            "fetched_at": now,
            "probed_at": now,
        }
        try:
            html, http_status = self._render(url, wait_for=wait_for)
        except BrowserUnavailable:
            raise
        except Exception as exc:
            record["status"] = "error"
            record["rendered"] = False
            record["error"] = f"{type(exc).__name__}: {exc}"
            record["dry_run"] = dry_run
            if not dry_run:
                self.append_manifest(record)
            return record

        check = check_rendered(html, min_text_chars=min_text_chars, require_text=require_text)
        record["http_status"] = http_status
        record.update(check.as_dict())
        record["sha256"] = hashlib.sha256(html.encode("utf-8")).hexdigest()

        # Playwright returns a response for 4xx/5xx instead of raising, so the
        # text check runs against an error page. A verbose 403 or a styled 500
        # can clear the floor and record as `downloaded`. Only a short one gets
        # caught, which is luck, not a check — the Akamai 403 that exposed this
        # happened to be 201 chars. The status code decides first.
        http_error = http_status is not None and not 200 <= http_status < 400
        if http_error:
            record["rendered"] = False
            record["error"] = f"HTTP {http_status}"
            record["reason"] = f"HTTP {http_status} — an error page, whatever its length"

        # A shell is evidence worth keeping — of the failure, not of the page.
        subdir = "rendered" if (check.rendered and not http_error) else "rendered_shells"
        dest = self.out_dir / subdir / f"{_slug(url)}.html"
        record["dest"] = str(dest)
        if http_error:
            record["status"] = "error"
        else:
            record["status"] = "downloaded" if check.rendered else "shell_only"

        # `dry_run` is a mode, not a verdict. Overwriting `status` with it hid
        # the failure the command exists to report: `render` without `--out` is
        # forced into dry-run, so a shell capture in the ordinary preview mode
        # exited 0. The verdict stays; the flag says nothing was written.
        record["dry_run"] = dry_run
        if dry_run:
            return record
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")
        self.append_manifest(record)
        return record

    def append_manifest(self, record: dict[str, Any]) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
