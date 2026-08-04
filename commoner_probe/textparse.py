# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

#: Rasterization resolution for OCR. 300 is where a digitally-rendered page
#: stops losing conjunct detail; measured on Gujarati NeVA question PDFs.
DEFAULT_OCR_DPI = 300

#: Tesseract page-segmentation mode 6 = "a single uniform block of text",
#: which suits a one-column government answer far better than the default
#: automatic mode's attempts at column detection.
DEFAULT_OCR_PSM = "6"

#: Tesseract collapses runs of spaces by default, which destroys the column
#: geometry that a two-column Q/A layout is split on — the same geometry
#: ``pdftotext -layout`` is used for. Measured on a NeVA question page: without
#: this the header extracts as ``પ્રશ્ન જવાબ`` (max run of spaces: 0) and the
#: splitter finds no boundary; with it the gap survives at 35 spaces and the
#: split succeeds. Better characters are worth nothing if the layout they
#: arrive in cannot be parsed.
OCR_PRESERVE_SPACES = ("-c", "preserve_interword_spaces=1")


class PdfTextUnavailable(RuntimeError):
    """No PDF text backend could run. Distinct from "the PDF has no text"."""


class OcrUnavailable(RuntimeError):
    """The OCR toolchain is missing or failed. Never silently empty text.

    An empty string here is indistinguishable from "the page had no words",
    which is the silent-success failure this repo keeps having to fix.
    """


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def clean_htmlish(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", text).strip()


def ocr_pdf_text(
    path: Path,
    *,
    page: int,
    dpi: int = DEFAULT_OCR_DPI,
    lang: str = "eng",
    psm: str = DEFAULT_OCR_PSM,
    runner=subprocess.run,
) -> str:
    """Read one page of *path* by rasterizing it and running tesseract.

    The last rung of this module's extraction fallback chain, below
    ``pdftotext`` and ``pdfminer``. Reach for it when the embedded text layer
    is absent **or present but untrustworthy** — a PDF whose font subset has a
    broken ``ToUnicode`` cmap extracts confident nonsense, which is worse than
    extracting nothing.

    That second case is why this exists. Such a document is not a scan: its
    glyphs draw correctly and only the character mapping is wrong, so
    rasterizing yields a pristine image and OCR recovers what a reader sees.
    Measured on 30 Gujarati NeVA questions whose text layer failed the
    reference check, similarity of the title line to the portal's own subject
    was 0.993 median by OCR against 0.942 from the text layer, OCR better on
    28 of 30. That margin does NOT transfer to scans, where OCR is working
    from noise rather than from a clean render.

    ``lang`` must name an installed tesseract model (``guj`` for Gujarati);
    the default ``eng`` on Indic script produces garbage, which is exactly how
    an existing one-off elsewhere in the org was useless for this corpus.

    ``runner`` is injected so the toolchain can be tested without invoking it.

    Raises :class:`OcrUnavailable` if poppler or tesseract is missing or
    fails. Returns ``""`` only when the page genuinely rasterized to nothing.
    """
    with tempfile.TemporaryDirectory() as scratch:
        prefix = str(Path(scratch) / "page")
        try:
            render = runner(
                ["pdftoppm", "-r", str(dpi), "-png", "-f", str(page), "-l", str(page), str(path), prefix],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise OcrUnavailable(f"pdftoppm: {exc}") from exc

        # A malformed PDF or an out-of-range page exits nonzero and writes no
        # PNG. Returning "" there would make a tool failure indistinguishable
        # from a blank page — the caller would record neither an error nor an
        # attempt, which is the silent-success failure this exception exists
        # to prevent.
        if getattr(render, "returncode", 0) != 0:
            raise OcrUnavailable(
                f"pdftoppm exited {render.returncode} on {path.name} page {page}"
            )

        pngs = sorted(Path(scratch).glob("*.png"))
        if not pngs:
            return ""

        try:
            out = runner(
                ["tesseract", str(pngs[0]), "-", "-l", lang, "--psm", psm, *OCR_PRESERVE_SPACES],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise OcrUnavailable(f"tesseract: {exc}") from exc

    if out.returncode != 0:
        raise OcrUnavailable(f"tesseract exited {out.returncode} on {path.name} page {page}")
    stdout = out.stdout
    return stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else (stdout or "")


def ocr_toolchain_missing() -> list[str]:
    """Which OCR command-line tools are absent, so a caller can say so up front."""
    return [tool for tool in ("pdftoppm", "tesseract") if shutil.which(tool) is None]


def _pdfminer_extract(path: Path) -> str:
    """pdfminer rung, or None at module level when pdfminer is not installed.

    Bound as a module attribute so the chain can tell "pdfminer is missing"
    from "pdfminer read the file and found nothing" — the distinction the bare
    ``except Exception: return ""`` erased.
    """
    from pdfminer.high_level import extract_text  # type: ignore

    return extract_text(str(path))


try:  # pragma: no cover - import-time capability probe
    import pdfminer  # type: ignore  # noqa: F401
except ImportError:  # pragma: no cover
    _pdfminer_extract = None  # type: ignore[assignment]


def _pdf_page_count(path: Path) -> int:
    """Page count via pdfinfo, 1 if it cannot be determined."""
    try:
        out = subprocess.run(
            ["pdfinfo", str(path)], capture_output=True, text=True, timeout=30, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 1
    match = re.search(r"^Pages:\s+(\d+)", out.stdout or "", re.MULTILINE)
    return int(match.group(1)) if match else 1


def extract_pdf_text(
    path: Path,
    *,
    ocr: bool = False,
    ocr_lang: str = "eng",
    ocr_max_pages: int = 50,
) -> str:
    """Text from a PDF: pdftotext, then pdfminer, then optionally OCR.

    Returns ``""`` only when a backend ran and the document genuinely carries
    no text. If NO backend is usable — poppler absent and pdfminer not
    installed — this raises :class:`PdfTextUnavailable` rather than returning
    the same empty string, because a crawl over a thousand PDFs would
    otherwise write a thousand empty text files and report success.

    ``ocr`` wires in the last rung for documents whose text layer is absent or
    untrustworthy. It is opt-in: rasterising and running tesseract costs
    orders of magnitude more than reading an embedded text layer, and it needs
    poppler plus tesseract. When it is asked for and cannot run, the
    :class:`OcrUnavailable` it raises reaches the caller — an OCR rung that
    silently returns nothing is the failure this module already refuses.
    """
    backend_ran = False
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        backend_ran = True
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if _pdfminer_extract is not None:
        try:
            text = _pdfminer_extract(path)
            backend_ran = True
            if text and text.strip():
                return text
        except Exception:  # noqa: BLE001 - a malformed PDF is not a missing toolchain
            backend_ran = True

    if ocr:
        pages = min(_pdf_page_count(path), ocr_max_pages)
        return "\n".join(
            ocr_pdf_text(path, page=page, lang=ocr_lang)
            for page in range(1, pages + 1)
        )

    if not backend_ran:
        raise PdfTextUnavailable(
            f"no PDF text backend available for {path.name}: install poppler "
            "(pdftotext) or `pip install commoner-probe[pdf]`"
        )
    return ""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def text_path_for(out_dir: Path, rec: dict[str, Any]) -> Path:
    key = re.sub(r"[^A-Za-z0-9_.-]+", "_", rec.get("key") or rec.get("title") or "question")
    return out_dir / "text" / f"{key}.txt"


def pdf_path_for(out_dir: Path, rec: dict[str, Any]) -> Path | None:
    raw = rec.get("pdf_path")
    if not raw:
        return None
    path = out_dir / raw
    return path if path.exists() else None


def excerpt(text: str, max_len: int = 280) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


