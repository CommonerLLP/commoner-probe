from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .topics import TopicProfile


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def clean_htmlish(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", text).strip()


def extract_pdf_text(path: Path) -> str:
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        from pdfminer.high_level import extract_text  # type: ignore

        return extract_text(str(path))
    except Exception:  # noqa: BLE001
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


