# SPDX-License-Identifier: MIT
"""Generic parser for IIT-style rolling-advertisement listing pages.

Targets institutions that publish a single PDF "Areas of Specialization" (+ an
optional "Eligibility Criteria" PDF) linked from a listing page (IIT Bombay,
Delhi, Madras). Discovers the most recent rolling-ad PDF, splits it into per-unit
blocks, and emits one ad per academic unit.

Probe-native port of academiaindia/scraper/parsers/iit_rolling.py: PDF download
goes through the injected probe ``Fetcher``; Pydantic JobAd output is rewritten
to plain dicts via make_ad. Requires a Fetcher (returns [] in --no-download mode).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional
from urllib.parse import urljoin

from ..ad_factory import make_ad
from ..pdf_text import (
    UnitBlock,
    extract_text,
    extract_text_flow,
    find_category_breakdown,
    find_deadline,
    find_general_eligibility,
    find_publications,
    find_reservation_note,
    split_into_units,
    split_into_units_flow,
)

SITE_HINTS = {
    "iitb.ac.in": {
        "areas_pdf": [re.compile(r"areas?\s+of\s+specia(li[zs]ation)?", re.I)],
        "eligibility_pdf": [re.compile(r"eligibility\s+criteria", re.I)],
        "apply_url": "https://portal.iitb.ac.in/FR/index.php/FAC/FR26/user/account",
        "info_url_keep_path": True,
    },
    "iitd.ac.in": {
        "areas_pdf": [re.compile(r"AP-?\d", re.I), re.compile(r"PROF-?\d", re.I)],
        "eligibility_pdf": [],
        "apply_url": "https://ecampus.iitd.ac.in/IITDFR-0/login",
        "info_url_keep_path": True,
    },
    "iitm.ac.in": {
        "areas_pdf": [
            re.compile(r"area_and_qualification", re.I),
            re.compile(r"advertisement[_ -]?ra", re.I),
        ],
        "eligibility_pdf": [],
        "apply_url": "https://facapp.iitm.ac.in/2026ra",
        "info_url_keep_path": True,
        "use_flow_excerpts": True,
        "human_pdf_url": "https://facapp.iitm.ac.in/img/Advertisement_RA-2026.pdf",
    },
}

EMIT_KEYWORDS = re.compile(
    r"\b(humanit|social\s+science|sociolog|anthropolog|policy|design|"
    r"educational?\s+tech|technology\s+alternatives|rural|media|"
    r"learning\s+sciences|cultural|liberal\s+arts|develop)",
    re.I,
)
SUBAREA_RE = re.compile(
    r"(?:^|\n)\s*(?P<name>[A-Z][A-Za-z][A-Za-z\- ]{2,45}?):\s+", re.MULTILINE,
)
SPLIT_UNITS_RE = re.compile(
    r"\b(humanit\w*|social\s+science|liberal\s+arts|interdisciplina\w*\s+studies)", re.I,
)


def _find_pdfs(soup, base_url: str, patterns: list[re.Pattern]) -> list[str]:
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.lower().endswith(".pdf") and ".pdf" not in href.lower():
            continue
        haystack = f"{href} {a.get_text(' ', strip=True)}"
        if any(p.search(haystack) for p in patterns):
            out.append(urljoin(base_url, href))
    seen, dedup = set(), []
    for u in out:
        if u in seen:
            continue
        seen.add(u)
        dedup.append(u)
    return dedup


def _site_key(url: str) -> Optional[str]:
    for k in SITE_HINTS:
        if k in url:
            return k
    return None


def _try_parse_html_deadline(html: str) -> Optional[date]:
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    m = re.search(r"(?:Application\s+)?Last Date[^.]{0,80}?(\d{1,2}/\d{1,2}/\d{4})", plain, re.I)
    if not m:
        m = re.search(r"on or before\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", plain, re.I)
        if m:
            try:
                return datetime.strptime(m.group(1), "%B %d, %Y").date()
            except ValueError:
                return None
        return None
    try:
        d, mo, y = m.group(1).split("/")
        return date(int(y), int(mo), int(d))
    except Exception:
        return None


def _parse_pdf_deadline(text: str) -> Optional[date]:
    raw = find_deadline(text)
    if not raw:
        return None
    raw = raw.strip().rstrip(".")
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def _split_subareas(unit: UnitBlock) -> list[tuple[str, str]]:
    text = unit.text
    matches = list(SUBAREA_RE.finditer(text))
    if len(matches) < 2:
        return []
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        name = m.group("name").strip()
        if re.search(r"\b(eligibility|publication|qualification|note|annexure|page)\b", name, re.I):
            continue
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if len(body) < 40:
            continue
        out.append((name, body))
    return out


def _classify_line(line: str, col3_start: int, col4_start: int | None, tolerance: int = 15) -> tuple[str, str]:
    if not line.strip():
        return "", ""
    gaps = list(re.finditer(r"\s{3,}", line))
    col3_gap: re.Match | None = None
    col4_gap: re.Match | None = None
    for g in gaps:
        edge = g.end()
        d3 = abs(edge - col3_start)
        d4 = abs(edge - col4_start) if col4_start is not None else 1e9
        if d4 <= tolerance and d4 < d3:
            if col4_gap is None or d4 < abs(col4_gap.end() - col4_start):
                col4_gap = g
        elif d3 <= tolerance:
            if col3_gap is None or d3 < abs(col3_gap.end() - col3_start):
                col3_gap = g
    if col3_gap is not None and col4_gap is not None:
        return line[col3_gap.end():col4_gap.start()].strip(), line[col4_gap.end():].strip()
    if col3_gap is not None:
        return line[col3_gap.end():].strip(), ""
    if col4_gap is not None:
        areas = line[:col4_gap.start()].strip()
        criteria = line[col4_gap.end():].strip()
        if (len(line) - len(line.lstrip())) < col3_start - tolerance:
            areas = ""
        return areas, criteria
    content_start = len(line) - len(line.lstrip())
    text_only = line.strip()
    if col4_start is not None and content_start >= col4_start - tolerance:
        return "", text_only
    if content_start >= col3_start - tolerance:
        return text_only, ""
    return "", ""


def _extract_columns(unit: UnitBlock) -> tuple[str, str]:
    lines = unit.text.split("\n")
    header_idx = -1
    header_line = ""
    for idx, line in enumerate(lines[:3]):
        m = re.match(r"^\s*(\d+)\b", line)
        if m and int(m.group(1)) == unit.unit_num:
            header_idx = idx
            header_line = line
            break
    if header_idx < 0:
        return "", ""

    cells: list[tuple[int, str]] = []
    pos = 0
    for piece in re.split(r"(\s{2,})", header_line):
        if not piece:
            continue
        if piece.strip():
            cells.append((pos, piece))
        pos += len(piece)
    if len(cells) < 3:
        return "", ""

    name_cell_pos, name_cell_text = cells[1]
    if len(name_cell_text) > 40:
        m = re.search(r":\s+(?=[A-Z])|:$", name_cell_text)
        if m:
            split_at = m.end()
            unit_part = name_cell_text[:split_at].rstrip()
            areas_part = name_cell_text[split_at:].lstrip()
            new_pos = name_cell_pos + len(unit_part) + (len(name_cell_text) - len(name_cell_text.rstrip()))
            cells = (
                [cells[0], (name_cell_pos, unit_part)]
                + ([(new_pos, areas_part)] if areas_part else [])
                + cells[2:]
            )

    col3_start = cells[2][0]
    col4_start = cells[-1][0] if len(cells) >= 4 else None
    if col4_start is not None and col4_start - col3_start < 10:
        col4_start = None

    areas_lines: list[str] = []
    criteria_lines: list[str] = []
    for line in lines:
        if not line.strip():
            areas_lines.append("")
            criteria_lines.append("")
            continue
        a, c = _classify_line(line, col3_start, col4_start)
        if a:
            areas_lines.append(a)
        if c:
            criteria_lines.append(c)

    areas_text = "\n".join(areas_lines).strip()
    criteria_text = "\n".join(criteria_lines).strip()
    if areas_text and not criteria_text:
        first = areas_text.lstrip()[:60].lower()
        criteria_starters = (
            "academic background", "publication record", "publications:",
            "publications and ph", "other:", "other additional",
        )
        if any(first.startswith(s) for s in criteria_starters):
            return "", ""
    return areas_text, criteria_text


def _short_excerpt(unit: UnitBlock, max_chars: int = 3500) -> str:
    areas, criteria = _extract_columns(unit)
    if areas and criteria:
        joined = areas + "\n\n" + criteria
        if len(joined) > max_chars:
            joined = joined[:max_chars].rsplit(" ", 1)[0] + "…"
        return joined
    if areas and not criteria:
        if len(areas) > max_chars:
            areas = areas[:max_chars].rsplit(" ", 1)[0] + "…"
        return areas

    name_words = set(unit.unit_name.split())
    out: list[str] = []
    first_line = True
    for line in unit.text.splitlines():
        cells = [c for c in re.split(r"\s{2,}", line) if c]
        if not cells:
            continue
        if first_line:
            kept: list[str] = []
            dropped_num = False
            dropped_name = False
            for c in cells:
                if not dropped_num and c.strip() == str(unit.unit_num):
                    dropped_num = True
                    continue
                if not dropped_name and c.split() and all(w in name_words for w in c.split()):
                    dropped_name = True
                    continue
                kept.append(c)
            out.append(" ".join(kept))
            first_line = False
        else:
            kept = []
            dropping = True
            for c in cells:
                if dropping and c.split() and all(w in name_words for w in c.split()):
                    continue
                dropping = False
                kept.append(c)
            out.append(" ".join(kept))
    joined = re.sub(r"\s+", " ", " ".join(out)).strip()
    if len(joined) > max_chars:
        joined = joined[:max_chars].rsplit(" ", 1)[0] + "…"
    return joined


def _stable_id(institution_id: str, ad_number: str, unit_num: int, unit_name: str) -> str:
    import hashlib

    m = hashlib.sha256()
    for p in (institution_id, ad_number, str(unit_num), unit_name):
        m.update(p.encode("utf-8"))
        m.update(b"\x00")
    return m.hexdigest()[:16]


def parse(html: str, url: str, fetched_at: Any, pdf: Any = None) -> list[dict]:
    site = _site_key(url)
    if not site:
        return []
    hints = SITE_HINTS[site]
    if pdf is None:
        return []  # this parser is PDF-based; needs a Fetcher to download

    if url.lower().endswith(".pdf") or not html:
        areas_url = url
        elig_url = None
    else:
        from bs4 import BeautifulSoup  # lazy: bs4 is the `academia` extra

        soup = BeautifulSoup(html, "html.parser")
        areas_url = None
        for pat in hints["areas_pdf"]:
            urls = _find_pdfs(soup, url, [pat])
            if urls:
                areas_url = urls[0]
                break
        if not areas_url:
            return []
        elig_urls = _find_pdfs(soup, url, hints["eligibility_pdf"]) if hints.get("eligibility_pdf") else []
        elig_url = elig_urls[0] if elig_urls else None

    areas_path = pdf.download(areas_url)
    if not areas_path:
        return []
    areas_text = extract_text(areas_path)
    if not areas_text:
        return []

    elig_text: Optional[str] = None
    if elig_url:
        ep = pdf.download(elig_url)
        if ep:
            elig_text = extract_text(ep)

    blocks = split_into_units(areas_text)
    elig_blocks_by_num: dict[int, UnitBlock] = {}
    if elig_text:
        for b in split_into_units(elig_text):
            elig_blocks_by_num[b.unit_num] = b

    flow_excerpts: dict[str, str] = {}
    if hints.get("use_flow_excerpts") and blocks:
        flow_text = extract_text_flow(areas_path)
        if flow_text:
            flow_excerpts = split_into_units_flow(flow_text, [b.unit_name for b in blocks])

    reservation_note = find_reservation_note(areas_text)
    general_eligibility = find_general_eligibility(areas_text)
    closing = _try_parse_html_deadline(html) or _parse_pdf_deadline(areas_text)
    adno_m = re.search(
        r"Advertisement No\.?\s*([A-Z0-9./\-]+)|Rolling Advertisement No\.?\s*([A-Z0-9./\-]+)",
        html + "\n" + areas_text, re.I,
    )
    ad_number = (adno_m.group(1) or adno_m.group(2)) if adno_m else None
    institution_id = "__placeholder__"
    public_pdf_url = hints.get("human_pdf_url") or areas_url

    def _make_ad(unit_num, key, title, department, discipline, excerpt, publications,
                 elig_extract, category_breakdown_dict=None) -> dict:
        d = make_ad(
            id=_stable_id(institution_id, ad_number or "rolling", unit_num, key),
            title=title,
            original_url=public_pdf_url,
            snapshot_fetched_at=fetched_at,
            ad_number=ad_number,
            department=department,
            discipline=discipline,
            post_type="Faculty",
            contract_status="TenureTrack",
            category_breakdown=category_breakdown_dict or None,
            closing_date=closing,
            parse_confidence=0.7,
            raw_text_excerpt=excerpt,
            apply_url=hints["apply_url"],
            info_url=url,
            publications_required=publications,
            unit_eligibility=elig_extract,
            annexure_pdf_url=(areas_url if (hints.get("human_pdf_url") and areas_url != public_pdf_url) else None),
            pdf_parsed=True,
        )
        d["reservation_note"] = reservation_note
        d["general_eligibility"] = general_eligibility
        return d

    out: list[dict] = []
    for b in blocks:
        elig_block = elig_blocks_by_num.get(b.unit_num)
        publications = find_publications(elig_block.text) if elig_block else find_publications(b.text)
        elig_extract = (
            re.sub(r"\s+", " ", elig_block.text.splitlines()[1:][0:30][0]).strip()
            if (elig_block and len(elig_block.text.splitlines()) > 1)
            else None
        )
        unit_breakdown = (
            find_category_breakdown(elig_block.text) if elig_block else find_category_breakdown(b.text)
        )

        sub_ads_emitted = False
        if SPLIT_UNITS_RE.search(b.unit_name):
            subareas = _split_subareas(b)
            if subareas:
                for sub_name, sub_text in subareas:
                    sub_excerpt = re.sub(r"\s+", " ", sub_text).strip()
                    if len(sub_excerpt) > 3500:
                        sub_excerpt = sub_excerpt[:3500].rsplit(" ", 1)[0] + "…"
                    out.append(_make_ad(
                        unit_num=b.unit_num,
                        key=f"{b.unit_name}:{sub_name}",
                        title=f"Faculty — {b.unit_name} — {sub_name}",
                        department=b.unit_name,
                        discipline=sub_name,
                        excerpt=sub_excerpt,
                        publications=publications,
                        elig_extract=elig_extract,
                        category_breakdown_dict=unit_breakdown,
                    ))
                sub_ads_emitted = True

        if not sub_ads_emitted:
            flow = flow_excerpts.get(b.unit_name)
            if flow and len(flow.strip()) > 80:
                excerpt = re.sub(r"\s+", " ", flow).strip()
                if len(excerpt) > 3500:
                    excerpt = excerpt[:3500].rsplit(" ", 1)[0] + "…"
            elif hints.get("use_flow_excerpts"):
                excerpt = (
                    "Per-department specialization areas are listed in the institutional "
                    "annexure (linked as Original PDF). Automated extraction of this PDF's "
                    "multi-column layout produces unreliable text — please read the source directly."
                )
            else:
                excerpt = _short_excerpt(b)
            out.append(_make_ad(
                unit_num=b.unit_num,
                key=b.unit_name,
                title=f"Faculty — {b.unit_name}",
                department=b.unit_name,
                discipline=b.unit_name,
                excerpt=excerpt,
                publications=publications,
                elig_extract=elig_extract,
                category_breakdown_dict=unit_breakdown,
            ))
    return out
