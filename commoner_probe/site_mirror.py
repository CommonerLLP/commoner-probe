"""Mirror one host to disk with a sha256 manifest and a readable page index.

The org mirrors a source site whenever the source is one author's body of work
rather than an API. A crawler written for one such site is written again for
the next one, so the walking, the saving and the two output files live here.

**Three artefacts, and the reason each exists.**

``manifest.jsonl`` is the provenance record and the resume state. One
``mirrored_file`` row per saved file, carrying its sha256, its byte count and
the URL it came from. ``MANIFEST.txt`` is the same facts in the org's
staging-manifest format, ``sha256  path  bytes  url``. ``INDEX.md`` names each
page, because 540 saved pages under slug directory names are not searchable by
a human or by an agent.

**All three append as the walk runs.** The crawler this module replaces wrote
its manifest and its index after the walk finished. A run killed at its
deadline on 2026-08-19 left 540 saved pages with neither, and the reader who
came next had to select pages by grepping directory names. A partial mirror
with a partial manifest is usable. A partial mirror with neither is not.

The session comes from :func:`commoner_probe.http_client.make_session`, so the
walk inherits the SSRF guard, the robots check, the rate limit, the 5xx and 429
backoff and the honest User-Agent. This module adds no HTTP policy of its own.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
import signal
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse, urlunparse

from .http_client import challenge_reason, make_session

MANIFEST_KIND = "mirrored_file"

#: Saved and parsed for links. Saved as bytes. Never fetched.
DOC_EXT = {".pdf", ".xls", ".xlsx", ".csv", ".doc", ".docx", ".ppt", ".pptx",
           ".zip", ".txt", ".xml"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif"}
SKIP_EXT = {".css", ".js", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3",
            ".webm", ".m4a"}

#: A WordPress site serves each page under several URLs that hold no new text.
#: Fetching them costs the rate limit and returns the page already saved.
SKIP_PAT = re.compile(
    r"/wp-admin/|/wp-json/|/wp-login|/feed/?$|/comment-page-|/cdn-cgi/|"
    r"[?&](replytocom|share=|print=|add-to-cart)|/author/|/tag/|/page/\d+/?$"
)

#: Priorities. The frontier is a heap, so a listing page is walked before the
#: leaves it names and a mirror stopped early holds the structure.
P_SITEMAP, P_LISTED, P_DOC, P_PAGE, P_IMAGE = 0, 1, 2, 3, 5

IMG_MAX = 2 * 1024 * 1024
BODY_MAX = 400 * 1024 * 1024


class _PageParser(HTMLParser):
    """Title, first text and outbound links from one HTML page.

    The stdlib parser, so ``mirror`` runs on a bare install. This package
    declares no required dependencies, and a mirror is the acquisition
    primitive other work stands on — it must not be the one command a fresh
    checkout cannot run.
    """

    _SKIP_TEXT = {"script", "style", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.links: list[tuple[str, str]] = []
        self._text: list[str] = []
        self._in_title = False
        self._muted = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag in self._SKIP_TEXT:
            self._muted += 1
        href = attr.get("href") if tag in ("a", "link") else attr.get("src")
        if href:
            self.links.append((tag, href))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in self._SKIP_TEXT and self._muted:
            self._muted -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._muted and len(self._text) < 400:
            self._text.append(data)

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._text)).strip()


class SiteMirror:
    """Walk one host, save what it serves, and record what was saved."""

    def __init__(self, base_url: str, out_dir: Path, *, rate_limit_sec: float = 2.0,
                 deadline_sec: float | None = None, image_max: int = IMG_MAX,
                 log=None) -> None:
        parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"mirror needs an http(s) URL, got {base_url!r}")
        self.host = parsed.netloc.lower()
        if not self.host:
            raise ValueError(f"mirror needs a host, got {base_url!r}")
        self.scheme = parsed.scheme
        self.base = urlunparse((self.scheme, self.host, parsed.path or "/", "", "", ""))
        self.out_dir = Path(out_dir)
        self.mirror_dir = self.out_dir / "mirror"
        self.manifest = self.out_dir / "manifest.jsonl"
        self.image_max = image_max
        self.deadline = time.monotonic() + deadline_sec if deadline_sec else None
        self.session = make_session(rate_limit_sec=rate_limit_sec)
        self._log = log
        self.stats = {"html": 0, "doc": 0, "img": 0, "fail": 0, "skip": 0,
                      "bytes": 0, "held": 0}
        self.failures: list[tuple[str, str]] = []
        self._queued: set[str] = set()
        self._frontier: list[tuple[int, int, str]] = []
        self._counter = 0
        self._stop = False

    # ── logging ──────────────────────────────────────────────────────────

    def log(self, msg: str) -> None:
        line = f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}"
        if self._log:
            self._log(line)
        with (self.out_dir / "run.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ── URL handling ─────────────────────────────────────────────────────

    def normalise(self, url: str) -> str:
        """The canonical on-host form of ``url``, or "" when it is off-limits.

        The fragment goes, because two URLs differing only by anchor are one
        page. `www.` is folded, because a site links to itself both ways and a
        mirror that treats them as two hosts saves everything twice.
        """
        parsed = urlparse(urljoin(self.base, url))
        if parsed.scheme not in ("http", "https"):
            return ""
        host = parsed.netloc.lower()
        if host.removeprefix("www.") != self.host.removeprefix("www."):
            return ""
        return urlunparse((self.scheme, self.host, parsed.path, "", parsed.query, ""))

    @staticmethod
    def extension(url: str) -> str:
        return Path(unquote(urlparse(url).path)).suffix.lower()

    def push(self, url: str, priority: int) -> None:
        url = self.normalise(url)
        if not url or url in self._queued or SKIP_PAT.search(url):
            return
        if self.extension(url) in SKIP_EXT:
            return
        self._queued.add(url)
        self._counter += 1
        heapq.heappush(self._frontier, (priority, self._counter, url))

    def destination(self, url: str) -> Path:
        """Where one URL lands under ``mirror/``.

        A query string becomes a suffix rather than a directory, so
        ``?p=1`` and ``?p=2`` are two files and neither is a parent of the
        other. Each segment is sanitised and capped, because a source path can
        carry anything and a mirror must not write outside its own tree.
        """
        parsed = urlparse(url)
        path = unquote(parsed.path).lstrip("/")
        if not path or path.endswith("/"):
            path += "index.html"
        elif not Path(path).suffix:
            path += "/index.html"
        if parsed.query:
            path += "__" + hashlib.sha1(parsed.query.encode()).hexdigest()[:8]
        parts = [re.sub(r"[^A-Za-z0-9._+~-]", "_", part)[:120]
                 for part in path.split("/") if part not in ("", ".", "..")]
        return self.mirror_dir.joinpath(*parts)

    # ── the record ───────────────────────────────────────────────────────

    def load_seen(self) -> dict[str, dict]:
        """Every URL the manifest already vouches for, mapped to its row.

        A row vouches for a URL only while its file is still there. The row
        describes a file, so it stops being evidence when the file stops
        existing, and a resume that trusted the row alone would report a
        mirror it does not hold.
        """
        seen: dict[str, dict] = {}
        if not self.manifest.exists():
            return seen
        with self.manifest.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (isinstance(rec, dict) and rec.get("kind") == MANIFEST_KIND
                        and rec.get("url") and rec.get("path")
                        and (self.out_dir / rec["path"]).exists()):
                    seen[rec["url"]] = rec
        return seen

    def _record(self, url: str, dest: Path, data: bytes, content_type: str,
                title: str | None, excerpt: str | None) -> dict:
        rel = dest.relative_to(self.out_dir).as_posix()
        return {
            "key": f"MIRROR|{self.host}|{rel}",
            "kind": MANIFEST_KIND,
            "record_type": MANIFEST_KIND,
            "source": self.host,
            "url": url,
            "path": rel,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_type": content_type or None,
            "title": title,
            "excerpt": excerpt,
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _append(self, record: dict) -> None:
        """Append one file's row to all three artefacts, immediately.

        Immediately is the whole point. The manifest and the index are written
        as the walk runs so that a kill, a deadline or a crash leaves a mirror
        somebody can read.
        """
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with self.manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        with (self.out_dir / "MANIFEST.txt").open("a", encoding="utf-8") as f:
            f.write(f"{record['sha256']}  {record['path']}  {record['bytes']}  "
                    f"{record['url']}\n")
        if record["title"] is not None:
            with (self.out_dir / "INDEX.md").open("a", encoding="utf-8") as f:
                f.write(f"- `{record['path']}` — {record['title']} — "
                        f"{record['excerpt']}\n")

    def _sort_derived(self) -> None:
        """Sort the two text artefacts by path, in place.

        They are appended in fetch order so nothing is lost to a kill, and
        sorted at every exit this process controls. A SIGKILL leaves them
        unsorted and complete, which is the right way round.
        """
        for name, key in (("MANIFEST.txt", 1), ("INDEX.md", 0)):
            path = self.out_dir / name
            if not path.exists():
                continue
            rows = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            rows.sort(key=lambda ln: ln.split("  ")[key] if key else ln)
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    # ── fetching ─────────────────────────────────────────────────────────

    def _handle_html(self, url: str, body: bytes, dest: Path) -> tuple[str, str]:
        parser = _PageParser()
        try:
            parser.feed(body.decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001 - a malformed page is not a crash
            self.log(f"PARSE {type(exc).__name__} {url}")
            return "(unparsed)", ""
        for tag, href in parser.links:
            if href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
                continue
            found = self.normalise(href)
            if not found:
                continue
            ext = self.extension(found)
            if ext in DOC_EXT:
                self.push(found, P_DOC)
            elif ext in IMG_EXT:
                self.push(found, P_IMAGE)
            elif tag == "a":
                self.push(found, P_PAGE)
        return parser.title.strip() or "(no title)", parser.text[:120]

    def _relink(self, record: dict) -> None:
        """Re-read a held page from disk for the links it names.

        A resume that merely skips a held page never learns what that page
        links to, so the frontier collapses to the seeds and every page the
        first run had not yet reached stays unreached. Reading the saved file
        costs no request and rebuilds the frontier exactly as the first walk
        built it.

        The sitemaps matter most, and they are why this reads XML as well as
        HTML. A live resume against a 4,163-URL site rebuilt a frontier of 99
        while the sitemaps sat on disk unread.
        """
        content_type = record.get("content_type") or ""
        path = self.out_dir / record["path"]
        try:
            body = path.read_bytes()
        except OSError:  # pragma: no cover - load_seen already checked it exists
            return
        if content_type.startswith(("text/html", "application/xhtml")):
            self._handle_html(record["url"], body, path)
        elif "xml" in content_type and "sitemap" in record["url"]:
            self._push_sitemap_locations(body)

    def fetch(self, url: str) -> None:
        is_image = self.extension(url) in IMG_EXT
        try:
            response = self.session.get(url, timeout=60, stream=True, allow_redirects=True)
        except PermissionError:
            self.stats["skip"] += 1
            self.failures.append((url, "robots-disallow"))
            self.log(f"ROBOTS {url}")
            return
        except Exception as exc:  # noqa: BLE001 - one bad URL never ends a walk
            self.stats["fail"] += 1
            self.failures.append((url, f"error:{type(exc).__name__}"))
            self.log(f"ERROR {type(exc).__name__} {url}")
            return
        if response.status_code != 200:
            self.stats["fail"] += 1
            self.failures.append((url, str(response.status_code)))
            self.log(f"{response.status_code} {url}")
            return
        reason = challenge_reason(response)
        if reason:
            self.stats["fail"] += 1
            self.failures.append((url, f"challenge:{reason}"))
            self.log(f"CHALLENGE {reason} {url}")
            return

        cap = self.image_max if is_image else BODY_MAX
        chunks, total = [], 0
        for chunk in response.iter_content(65536):
            chunks.append(chunk)
            total += len(chunk)
            if total > cap:
                self.stats["skip"] += 1
                self.log(f"SKIP over {cap}B {url}")
                return
        body = b"".join(chunks)
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()

        dest = self.destination(url)
        title = excerpt = None
        if content_type.startswith(("text/html", "application/xhtml")):
            if not dest.name.endswith((".html", ".xml", ".txt")):
                dest = dest.with_name(dest.name + ".html")
            self.stats["html"] += 1
        elif is_image:
            self.stats["img"] += 1
        else:
            self.stats["doc"] += 1

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        self.stats["bytes"] += len(body)
        if content_type.startswith(("text/html", "application/xhtml")):
            title, excerpt = self._handle_html(url, body, dest)
        self._append(self._record(url, dest, body, content_type, title, excerpt))
        self.log(f"200 {len(body):>9}B {content_type or '?'} {url}")

    def _walk_sitemap(self, url: str) -> None:
        try:
            response = self.session.get(url, timeout=60)
        except Exception as exc:  # noqa: BLE001
            self.failures.append((url, f"error:{type(exc).__name__}"))
            return
        if response.status_code != 200:
            self.failures.append((url, str(response.status_code)))
            return
        body = response.content
        dest = self.destination(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        self._append(self._record(url, dest, body, "application/xml", None, None))
        self._push_sitemap_locations(body)
        self.log(f"200 sitemap {url}")

    def _push_sitemap_locations(self, body: bytes) -> None:
        for loc in re.findall(rb"<loc>([^<]+)</loc>", body):
            found = loc.decode("utf-8", "replace").strip()
            self.push(found, P_SITEMAP if found.endswith(".xml") else P_LISTED)

    # ── the run ──────────────────────────────────────────────────────────

    def run(self, *, max_pages: int | None = None) -> dict:
        """Walk the host until the frontier empties, the deadline passes or a
        signal arrives. Returns the stats."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        held = self.load_seen()
        self.log(f"START host={self.host} held={len(held)} "
                 f"deadline={'none' if self.deadline is None else 'set'}")
        previous = self._install_handlers()
        try:
            self.push(self.base, P_SITEMAP)
            for name in ("sitemap_index", "post-sitemap", "page-sitemap",
                         "news-sitemap", "video-sitemap", "faq-sitemap"):
                self.push(urljoin(self.base, f"/{name}.xml"), P_SITEMAP)
            fetched = 0
            while self._frontier and not self._stop:
                if self.deadline is not None and time.monotonic() >= self.deadline:
                    self.log("DEADLINE reached")
                    break
                _, _, url = heapq.heappop(self._frontier)
                if url in held:
                    self.stats["held"] += 1
                    self._relink(held[url])
                    continue
                # Counted AFTER the held check, so `--max-pages` bounds the
                # requests a run makes rather than the URLs it steps over. A
                # resume otherwise spends its whole brake on files it holds and
                # fetches nothing.
                if max_pages is not None and fetched >= max_pages:
                    self.log(f"max_pages {max_pages} reached — frontier left unwalked")
                    # Put it back, or the URL the brake stopped on is the one
                    # URL missing from UNFETCHED.txt. `push` refuses a URL it
                    # has already queued, so the seen-set entry goes first.
                    self._queued.discard(url)
                    self.push(url, 0)
                    break
                fetched += 1
                if self.extension(url) == ".xml" and "sitemap" in url:
                    self._walk_sitemap(url)
                else:
                    self.fetch(url)
        finally:
            self._restore_handlers(previous)
            self._finish()
        return dict(self.stats)

    def _finish(self) -> None:
        self._sort_derived()
        (self.out_dir / "UNFETCHED.txt").write_text(
            "# URLs still queued when the run ended\n"
            + "\n".join(sorted(url for _, _, url in self._frontier)) + "\n",
            encoding="utf-8")
        (self.out_dir / "FAILURES.txt").write_text(
            "# url\tstatus\n"
            + "\n".join(f"{url}\t{status}" for url, status in self.failures) + "\n",
            encoding="utf-8")
        self.log(f"DONE {self.stats} queue_remaining={len(self._frontier)}")

    def _install_handlers(self) -> dict:
        """Ask the walk to stop at the next URL, on SIGTERM and SIGINT.

        The handler sets a flag rather than writing files. A signal can arrive
        inside the append that is already keeping the artefacts current, and a
        second writer there would corrupt the line being written.

        A handler can only be installed on the main thread. A caller running
        the mirror in a worker gets the deadline and the frontier check, which
        is the same stop with a coarser trigger.
        """
        previous: dict = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                previous[sig] = signal.signal(sig, self._request_stop)
            except (ValueError, OSError):  # pragma: no cover - not the main thread
                pass
        return previous

    def _restore_handlers(self, previous: dict) -> None:
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):  # pragma: no cover
                pass

    def _request_stop(self, signum, frame) -> None:  # noqa: ARG002
        self._stop = True
        self.log(f"SIGNAL {signal.Signals(signum).name} — stopping after this URL")


def verify_manifest(out_dir: Path) -> list[str]:
    """Re-hash every file the manifest names. Returns the disagreements.

    An acquisition whose manifest nobody can check is an acquisition nobody can
    cite. Each returned line names one path and what is wrong with it.
    """
    out_dir = Path(out_dir)
    manifest = out_dir / "manifest.jsonl"
    problems: list[str] = []
    if not manifest.exists():
        return [f"{manifest} does not exist"]
    with manifest.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("kind") != MANIFEST_KIND:
                continue
            path = out_dir / rec.get("path", "")
            if not path.exists():
                problems.append(f"{rec.get('path')}: missing")
                continue
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != rec.get("sha256"):
                problems.append(f"{rec.get('path')}: sha256 differs")
            elif len(data) != rec.get("bytes"):
                problems.append(f"{rec.get('path')}: byte count differs")
    return problems
