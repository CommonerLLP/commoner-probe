# SPDX-License-Identifier: MIT
"""Extract speeches from Lok Sabha debate PDFs."""

import json
import re
from pathlib import Path
from typing import Callable

from .textparse import extract_pdf_text

# A speaker name block looks like "SHRI ADHIR RANJAN CHOWDHURY:" or "*SHRI N. K. PREMACHANDRAN:"
# It may also include a constituency in parens, e.g., "SHRI RAHUL GANDHI (WAYANAD):"
# Or it could be "HON. SPEAKER:"
SPEAKER_PATTERN = re.compile(
    r"^\s*(?:\*?\s*)?"
    r"("
    r"(?:SHRI|SHRIMATI|DR\.|KUMARI|PROF\.|HON\.|SECRETARY GENERAL|MR\.)\s+"
    r"[A-Z\.\s\']+"
    r"(?:\([A-Za-z\s\-]+\))?"
    r"):\s*(.*)",
    re.MULTILINE
)

def _clean_speech(text: str) -> str:
    # Remove page numbers, date stamps, and form feeds
    text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d{2}\.\d{2}\.\d{4}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r".*?\n", "", text, flags=re.MULTILINE) # remove form feed and following line
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def parse_debate_text(text: str) -> list[dict]:
    """Parse raw debate text into individual speeches."""
    speeches = []

    # We find all speaker matches
    matches = list(SPEAKER_PATTERN.finditer(text))
    if not matches:
        return speeches

    for i, match in enumerate(matches):
        speaker_name = match.group(1).strip()
        speech_start = match.end(1) + 1 # +1 to skip colon

        # Speech ends where the next speaker begins, or end of file
        speech_end = matches[i+1].start() if i + 1 < len(matches) else len(text)

        speech_text = text[speech_start:speech_end].strip()
        cleaned = _clean_speech(speech_text)
        if cleaned:
            speeches.append({
                "speaker": speaker_name,
                "text": cleaned
            })

    return speeches

def extract_debates(out_dir: Path, *, refresh: bool = False, log_fn: Callable[[str], None] = print) -> None:
    manifest_path = out_dir / "manifest.jsonl"
    speeches_path = out_dir / "speeches.jsonl"

    if not manifest_path.exists():
        log_fn(f"No manifest found at {manifest_path}")
        return

    seen = set()
    if not refresh and speeches_path.exists():
        with speeches_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    seen.add(rec.get("source_key"))
                except json.JSONDecodeError:
                    pass

    added = 0
    with manifest_path.open("r", encoding="utf-8") as f_in, \
         speeches_path.open("a" if not refresh else "w", encoding="utf-8") as f_out:
        for line in f_in:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            key = rec.get("key")
            pdf_path_rel = rec.get("pdf_path")
            if not key or not pdf_path_rel:
                continue

            if key in seen:
                continue

            pdf_path = out_dir / pdf_path_rel
            if not pdf_path.exists():
                continue

            try:
                text = extract_pdf_text(pdf_path)
                speeches = parse_debate_text(text)
            except Exception as e:
                log_fn(f"Error parsing {pdf_path}: {e}")
                continue

            for i, sp in enumerate(speeches):
                out_rec = {
                    "speech_id": f"{key}|{i}",
                    "source_key": key,
                    "date": rec.get("date"),
                    "loksabha": rec.get("loksabha"),
                    "session_no": rec.get("session_no"),
                    "speaker": sp["speaker"],
                    "text": sp["text"]
                }
                f_out.write(json.dumps(out_rec, ensure_ascii=False) + "\n")
                added += 1

            log_fn(f"Extracted {len(speeches)} speeches from {key}")

    log_fn(f"Done. Added {added} speeches to {speeches_path}")

