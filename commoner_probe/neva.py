"""NeVA (National e-Vidhan Application) state assembly scraper.

Scrapes: Questions (listed + unlisted), Members, Papers to be Laid.
Tested on: Gujarat (gujarat.neva.gov.in, state_code=GJ).

Output layout::

    <out_dir>/
        probe.log
        questions.jsonl        — one record per listed question
        questions_unlisted.jsonl
        members.jsonl
        papers_laid.jsonl
        pdfs/
            questions/
            papers_laid/
"""
from __future__ import annotations

import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path

from .base import BaseProbe, now, safe_filename_segment

NEVA_UA = "commoner-probe/0.3.0 (research)"
CMS_BASE = "https://cms.neva.gov.in"


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

class _TableParser(HTMLParser):
    """Parse the first <table> in an HTML fragment into rows + per-cell hrefs."""

    def __init__(self) -> None:
        super().__init__()
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._rows: list[list[str]] = []
        self._hrefs: list[list[list[str]]] = []
        self._cur_row: list[str] = []
        self._cur_row_hrefs: list[list[str]] = []
        self._cur_cell: list[str] = []
        self._cur_cell_hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        d = dict(attrs)
        if tag == "table" and not self._in_table:
            self._in_table = True
            return
        if not self._in_table:
            return
        if tag == "tr":
            self._in_row = True
            self._cur_row = []
            self._cur_row_hrefs = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cur_cell = []
            self._cur_cell_hrefs = []
        elif tag == "a" and self._in_cell:
            href = d.get("href", "")
            if href:
                self._cur_cell_hrefs.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._in_cell:
            self._cur_row.append("".join(self._cur_cell).strip())
            self._cur_row_hrefs.append(list(self._cur_cell_hrefs))
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            self._rows.append(self._cur_row)
            self._hrefs.append(self._cur_row_hrefs)
            self._in_row = False
        elif tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cur_cell.append(data)

    @property
    def rows(self) -> list[list[str]]:
        return self._rows

    @property
    def row_hrefs(self) -> list[list[list[str]]]:
        return self._hrefs


def _parse_table(html: str) -> tuple[list[list[str]], list[list[list[str]]]]:
    p = _TableParser()
    p.feed(html)
    return p.rows, p.row_hrefs


def _split_member_cell(cell: str) -> tuple[str, str]:
    """'Name (Constituency)' -> (name, constituency). Falls back to (cell, '')."""
    m = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", cell.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return cell.strip(), ""


def _collect_pdf_hrefs(row_hrefs: list[list[str]]) -> list[str]:
    return [
        h
        for cell_hrefs in row_hrefs
        for h in cell_hrefs
        if "cms.neva.gov.in" in h or h.lower().endswith(".pdf")
    ]


# ---------------------------------------------------------------------------
# Member HTML parser
# ---------------------------------------------------------------------------

def _parse_members_html(
    html: str, state_code: str, portal_code: str, assembly_no: int
) -> list[dict]:
    """Extract member records from FetchMembersList HTML.

    Each member is an ``<a class="card" href="/Member/Details/{id}...">`` block
    containing an <img>, <h3> (name), <h6> (party), and a <table> with
    constituency + DOB rows, plus <li> items for phone/email.
    """
    # Split on card anchors
    card_pattern = re.compile(
        r'<a\s[^>]*class="[^"]*card[^"]*"[^>]*href="([^"]*)"',
        re.DOTALL,
    )
    # Find start positions and IDs
    card_starts: list[tuple[int, int, str]] = []
    for m in card_pattern.finditer(html):
        href = m.group(1)
        id_m = re.search(r"/Member/Details/(\d+)", href)
        if id_m:
            card_starts.append((m.start(), int(id_m.group(1)), href))

    records: list[dict] = []
    for i, (start, mid, href) in enumerate(card_starts):
        # Slice card HTML up to next card start (or end of html)
        end = card_starts[i + 1][0] if i + 1 < len(card_starts) else len(html)
        chunk = html[start:end]

        def _first(pattern: str, flags: int = re.DOTALL) -> str:
            mm = re.search(pattern, chunk, flags)
            return mm.group(1).strip() if mm else ""

        # Photo
        photo = _first(r'<img\s[^>]*src="([^"]+)"')

        # Name: inside <h3>, strip inner <span> tags
        h3_block = _first(r"<h3>(.*?)</h3>")
        name = re.sub(r"<[^>]+>", "", h3_block).strip()

        # Party: inside <h6>
        h6_block = _first(r"<h6>(.*?)</h6>")
        party = re.sub(r"<[^>]+>", "", h6_block).strip()

        # Constituency: second <td> after "મતવિસ્તારનું"
        const_m = re.search(
            r"મતવિસ્તારનું[^<]*</td>\s*<td>(.*?)</td>", chunk, re.DOTALL
        )
        constituency = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", const_m.group(1))).strip() if const_m else ""

        # DOB: second <td> after "જન્મ તારીખ"
        dob_m = re.search(
            r"જન્મ તારીખ[^<]*</td>\s*<td>(.*?)</td>", chunk, re.DOTALL
        )
        dob = re.sub(r"<[^>]+>", "", dob_m.group(1)).strip() if dob_m else ""

        # Phone: text after fa-phone icon
        phone_m = re.search(r"fa-phone[^<]*</i>\s*([0-9+\s\-]{7,15})", chunk)
        phone = phone_m.group(1).strip() if phone_m else ""

        # Email: text after fa-envelope icon (obfuscated as [at] [dot])
        email_m = re.search(r"fa-envelope[^<]*</i>([^<]{5,80})", chunk)
        email_raw = email_m.group(1).strip() if email_m else ""
        email = email_raw.replace("[at]", "@").replace("[dot]", ".").strip()

        rec: dict = {
            "key": f"{state_code}|member|{mid}",
            "record_type": "member",
            "source": "neva",
            "state_code": state_code,
            "portal_code": portal_code,
            "assembly_no": assembly_no,
            "member_id": mid,
            "name": name,
            "party": party,
            "constituency": constituency,
            "dob": dob,
            "phone": phone,
            "email": email,
            "photo_url": photo,
            "probed_at": now(),
        }
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------

class StateAssemblyCrawler(BaseProbe):
    """Crawl one NeVA state assembly portal.

    Args:
        portal_code: subdomain prefix — e.g. ``"gujarat"``
        state_code:  CMS two-letter code — e.g. ``"GJ"``
        out_dir:     output directory (created if absent)
        sleep:       seconds between requests (≥ 0.5 recommended)
    """

    def __init__(
        self,
        portal_code: str,
        state_code: str,
        out_dir: Path,
        *,
        sleep: float = 0.5,
    ) -> None:
        super().__init__(None, Path(out_dir), sleep=sleep)
        self.portal_code = portal_code
        self.state_code = state_code
        self._base = f"https://{portal_code}.neva.gov.in"
        self.session.headers.update({"User-Agent": NEVA_UA})
        self.questions_path = self.out_dir / "questions.jsonl"
        self.unlisted_path = self.out_dir / "questions_unlisted.jsonl"
        self.members_path = self.out_dir / "members.jsonl"
        self.papers_path = self.out_dir / "papers_laid.jsonl"

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def bootstrap(self) -> None:
        """GET homepage to obtain ASP.NET_SessionId cookie."""
        self.session.get(self._base + "/", timeout=15)
        time.sleep(self.sleep)

    def _get(self, path: str, params: dict | None = None) -> str:
        url = self._base + path
        r = self.session.get(url, params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} {url} params={params}")
        time.sleep(self.sleep)
        return r.text

    def download_pdf(self, url: str, dest: Path) -> bool:
        if dest.exists() and dest.stat().st_size > 1000:
            return True
        dest.parent.mkdir(parents=True, exist_ok=True)
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = CMS_BASE + url
        try:
            r = self.session.get(url, timeout=60)
            if r.status_code != 200:
                self.log(f"Warning: PDF {r.status_code} {url}")
                return False
            with dest.open("wb") as f:
                for chunk in r.iter_content(chunk_size=16384):
                    f.write(chunk)
            time.sleep(self.sleep)
            return dest.exists() and dest.stat().st_size > 1000
        except Exception as exc:
            self.log(f"Warning: PDF download failed {url}: {exc}")
            return False

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def get_sessions(self, assembly_no: int) -> list[dict]:
        """Sessions for an assembly number (ordered latest-first by NeVA)."""
        text = self._get("/Home/AssemblyChange", {"AssemblyId": assembly_no})
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        return [d for d in data if isinstance(d, dict) and d.get("SessionCode")]

    def get_dates(self, assembly_no: int, session_code: int) -> list[dict]:
        """Sitting dates for a session. Skips the placeholder (DateId=0) entry."""
        text = self._get(
            "/Home/SessionChangeNew",
            {"AssemblyId": assembly_no, "SessionId": session_code},
        )
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        return [d for d in data if isinstance(d, dict) and d.get("SessionDateId")]

    # ------------------------------------------------------------------
    # Questions
    # ------------------------------------------------------------------

    def fetch_questions_for_date(
        self,
        assembly_no: int,
        session_code: int,
        date_id: int,
        seen: set[str],
    ) -> list[dict]:
        html = self._get(
            "/Questions/FetchDataListQuestionBySearch",
            {"AssId": assembly_no, "SessId": session_code, "SessDateId": date_id},
        )
        rows, hrefs = _parse_table(html)
        records: list[dict] = []
        for row, row_href in zip(rows, hrefs):
            if len(row) < 2:
                continue
            q_no = row[1].strip().lstrip("*").strip()
            if not q_no.isdigit():
                continue
            key = f"{self.state_code}|q|{assembly_no}|{session_code}|{date_id}|{q_no}"
            if key in seen:
                continue
            member_name, constituency = _split_member_cell(row[6] if len(row) > 6 else "")
            rec: dict = {
                "key": key,
                "record_type": "question",
                "source": "neva",
                "state_code": self.state_code,
                "portal_code": self.portal_code,
                "assembly_no": assembly_no,
                "session_no": session_code,
                "session_date_id": date_id,
                "question_number": q_no,
                "subject": row[2] if len(row) > 2 else "",
                "question_text": row[3] if len(row) > 3 else "",
                "ministry": row[4] if len(row) > 4 else "",
                "member_name": member_name,
                "constituency": constituency,
                "pdf_urls": _collect_pdf_hrefs(row_href),
                "pdf_path": None,
                "probed_at": now(),
            }
            records.append(rec)
            seen.add(key)
        return records

    def fetch_unlisted_questions(
        self,
        assembly_no: int,
        session_code: int,
        seen: set[str],
    ) -> list[dict]:
        html = self._get(
            "/Questions/FetchUnListQuestions",
            {"AssId": assembly_no, "SessId": session_code},
        )
        rows, hrefs = _parse_table(html)
        records: list[dict] = []
        for row, row_href in zip(rows, hrefs):
            if len(row) < 2:
                continue
            q_no = row[1].strip().lstrip("*").strip()
            if not q_no.isdigit():
                continue
            key = f"{self.state_code}|q_unlist|{assembly_no}|{session_code}|{q_no}"
            if key in seen:
                continue
            member_name, constituency = _split_member_cell(row[6] if len(row) > 6 else "")
            rec: dict = {
                "key": key,
                "record_type": "question_unlisted",
                "source": "neva",
                "state_code": self.state_code,
                "portal_code": self.portal_code,
                "assembly_no": assembly_no,
                "session_no": session_code,
                "session_date_id": None,
                "question_number": q_no,
                "subject": row[2] if len(row) > 2 else "",
                "question_text": row[3] if len(row) > 3 else "",
                "ministry": row[4] if len(row) > 4 else "",
                "member_name": member_name,
                "constituency": constituency,
                "pdf_urls": _collect_pdf_hrefs(row_href),
                "pdf_path": None,
                "probed_at": now(),
            }
            records.append(rec)
            seen.add(key)
        return records

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    def fetch_members(self, assembly_no: int) -> list[dict]:
        html = self._get(
            "/ContactDirectory/FetchMembersList",
            {"AssemblyId": assembly_no},
        )
        return _parse_members_html(html, self.state_code, self.portal_code, assembly_no)

    def fetch_member_detail(self, member_id: int, assembly_no: int) -> dict:
        """Fetch individual member profile page for richer field extraction."""
        try:
            html = self._get(f"/Member/Details/{member_id}")
        except RuntimeError:
            return {}
        # Extract fields from detail page via simple regex patterns
        def _field(pattern: str) -> str:
            m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            return m.group(1).strip() if m else ""

        name = _field(r"<h[12][^>]*>\s*([^<]{3,80})\s*</h[12]>")
        party = _field(r"Party[^:]*:\s*<[^>]*>([^<]+)<")
        dob = _field(r"(?:Date of Birth|DOB|Born)[^:]*:\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{4})")
        constituency = _field(r"Constituency[^:]*:\s*<[^>]*>([^<]+)<")
        mobile = _field(r"Mobile[^:]*:\s*(\+?[0-9\s\-]{8,15})")
        email_m = re.search(r"[\w.+-]+@[\w.-]+\.\w+", html)
        email = email_m.group(0) if email_m else ""
        return {
            "name_detail": name,
            "party_detail": party,
            "dob": dob,
            "constituency_detail": constituency,
            "mobile": mobile,
            "email": email,
        }

    # ------------------------------------------------------------------
    # Papers to be laid
    # ------------------------------------------------------------------

    def fetch_papers_laid(
        self,
        assembly_no: int,
        session_code: int,
        date_id: int,
        seen: set[str],
    ) -> list[dict]:
        html = self._get(
            "/Business/FetchDataListPapersToBeLaidBySearch",
            {"AssId": assembly_no, "SessId": session_code, "SessDateId": date_id},
        )
        rows, hrefs = _parse_table(html)
        records: list[dict] = []
        for seq, (row, row_href) in enumerate(zip(rows, hrefs)):
            if not row or not any(row):
                continue
            serial = row[0].strip()
            # Skip header rows — both ASCII and Gujarati variants
            if serial.lower() in ("s.no", "sr.no", "#", "no.", "sno") or serial in ("ક્રમ નં", "ક્રમ"):
                continue
            title = row[1].strip() if len(row) > 1 else row[0].strip()
            if not title:
                continue
            key = f"{self.state_code}|paper|{assembly_no}|{session_code}|{date_id}|{seq}"
            if key in seen:
                continue
            pdf_urls = _collect_pdf_hrefs(row_href)
            rec: dict = {
                "key": key,
                "record_type": "paper_laid",
                "source": "neva",
                "state_code": self.state_code,
                "portal_code": self.portal_code,
                "assembly_no": assembly_no,
                "session_no": session_code,
                "session_date_id": date_id,
                "serial_no": serial,
                "title": title,
                "ministry": row[2].strip() if len(row) > 2 else "",
                "pdf_urls": pdf_urls,
                "pdf_path": None,
                "probed_at": now(),
            }
            records.append(rec)
            seen.add(key)
        return records

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def run(
        self,
        assembly_nos: list[int],
        *,
        download: bool = True,
        fetch_member_details: bool = True,
        sessions_limit: int | None = None,
    ) -> dict:
        """Run the full crawl for the given assembly numbers.

        Args:
            assembly_nos: list of assembly numbers to crawl (e.g. [15])
            download: download PDFs when URLs are found
            fetch_member_details: enrich members with individual profile pages
            sessions_limit: stop after this many sessions per assembly (smoke-test)

        Returns a summary dict with record counts.
        """
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.bootstrap()
        self.log(
            f"start portal={self.portal_code} state={self.state_code} "
            f"assemblies={assembly_nos} download={download}"
        )

        q_seen = self._load_jsonl_keys(self.questions_path)
        u_seen = self._load_jsonl_keys(self.unlisted_path)
        p_seen = self._load_jsonl_keys(self.papers_path)
        q_added = u_added = p_added = 0

        for asm in assembly_nos:
            sessions = self.get_sessions(asm)
            self.log(f"assembly={asm} sessions_found={len(sessions)}")
            if sessions_limit:
                sessions = sessions[:sessions_limit]

            for sess in sessions:
                s_code = sess["SessionCode"]
                s_name = sess.get("SessionName", "")
                dates = self.get_dates(asm, s_code)
                self.log(f"  session={s_code} ({s_name}) dates={len(dates)}")

                for d in dates:
                    date_id = d["SessionDateId"]

                    # Questions
                    try:
                        recs = self.fetch_questions_for_date(asm, s_code, date_id, q_seen)
                    except RuntimeError as exc:
                        self.log(f"Warning: skipping date {date_id} session={s_code}: {exc}")
                        recs = []
                    for rec in recs:
                        if download and rec["pdf_urls"]:
                            fname = safe_filename_segment(
                                f"{self.state_code}_{asm}_{s_code}_{date_id}_{rec['question_number']}.pdf"
                            )
                            dest = self.pdf_dir / "questions" / fname
                            if self.download_pdf(rec["pdf_urls"][0], dest):
                                rec["pdf_path"] = str(dest.relative_to(self.out_dir))
                        self._append_jsonl(self.questions_path, rec)
                        q_added += 1

                    # Papers to be laid
                    try:
                        p_recs = self.fetch_papers_laid(asm, s_code, date_id, p_seen)
                    except RuntimeError as exc:
                        self.log(f"Warning: skipping papers date {date_id} session={s_code}: {exc}")
                        p_recs = []
                    for seq, rec in enumerate(p_recs):
                        if download and rec["pdf_urls"]:
                            fname = safe_filename_segment(
                                f"{self.state_code}_{asm}_{s_code}_{date_id}_paper_{seq}.pdf"
                            )
                            dest = self.pdf_dir / "papers_laid" / fname
                            if self.download_pdf(rec["pdf_urls"][0], dest):
                                rec["pdf_path"] = str(dest.relative_to(self.out_dir))
                        self._append_jsonl(self.papers_path, rec)
                        p_added += 1

                # Unlisted questions (per-session, not per-date)
                u_recs = self.fetch_unlisted_questions(asm, s_code, u_seen)
                for rec in u_recs:
                    self._append_jsonl(self.unlisted_path, rec)
                    u_added += 1
                if u_recs:
                    self.log(f"    unlisted={len(u_recs)}")

        # Members — crawl once for the latest assembly
        m_seen = self._load_jsonl_keys(self.members_path)
        if not m_seen:
            asm = assembly_nos[0]
            members = self.fetch_members(asm)
            self.log(f"members_parsed={len(members)}")
            if fetch_member_details:
                for rec in members:
                    if rec["key"] not in m_seen:
                        detail = self.fetch_member_detail(rec["member_id"], asm)
                        rec.update(detail)
                        self._append_jsonl(self.members_path, rec)
                        m_seen.add(rec["key"])
            else:
                for rec in members:
                    if rec["key"] not in m_seen:
                        self._append_jsonl(self.members_path, rec)
                        m_seen.add(rec["key"])

        if download:
            q_retried, p_retried = self._retry_missing_pdfs()
        else:
            q_retried = p_retried = 0

        summary = {
            "questions_added": q_added,
            "questions_unlisted_added": u_added,
            "papers_laid_added": p_added,
            "questions_pdfs_retried": q_retried,
            "papers_pdfs_retried": p_retried,
            "members_total": len(m_seen),
        }
        self.log(f"DONE {summary}")
        return summary

    def _retry_missing_pdfs(self) -> tuple[int, int]:
        """Download PDFs for records that have pdf_urls but no pdf_path, then patch the JSONL."""
        q_fixed = self._retry_pdfs_for(
            self.questions_path,
            lambda rec, fname: self.pdf_dir / "questions" / fname,
            lambda rec: (
                f"{self.state_code}_{rec['assembly_no']}_{rec['session_no']}"
                f"_{rec['session_date_id']}_{rec['question_number']}.pdf"
            ),
        )
        p_fixed = self._retry_pdfs_for(
            self.papers_path,
            lambda rec, fname: self.pdf_dir / "papers_laid" / fname,
            lambda rec: safe_filename_segment(rec["pdf_urls"][0].split("/")[-1]),
        )
        return q_fixed, p_fixed

    def _retry_pdfs_for(self, jsonl_path: Path, dest_fn, fname_fn) -> int:
        if not jsonl_path.exists():
            return 0
        records = []
        with jsonl_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

        fixed = 0
        for rec in records:
            if rec.get("pdf_path") or not rec.get("pdf_urls"):
                continue
            try:
                fname = safe_filename_segment(fname_fn(rec))
                dest = dest_fn(rec, fname)
                if self.download_pdf(rec["pdf_urls"][0], dest):
                    rec["pdf_path"] = str(dest.relative_to(self.out_dir))
                    fixed += 1
            except Exception as exc:
                self.log(f"Warning: retry PDF failed for {rec.get('key')}: {exc}")

        if fixed:
            tmp = jsonl_path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            tmp.replace(jsonl_path)
            self.log(f"  pdf_retry: patched {fixed} records in {jsonl_path.name}")

        return fixed
