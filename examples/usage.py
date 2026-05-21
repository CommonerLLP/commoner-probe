"""Quickstart: loading and exploring a sansad-crawler corpus.

Run from the repo root against the bundled smoke corpus:

    python examples/usage.py examples/corpora/committees-smoke

Or against your own crawled corpus:

    python examples/usage.py data/libraries
"""

from __future__ import annotations

import sys
from pathlib import Path

from sansad_crawler import Corpus


def main(corpus_dir: str) -> None:
    c = Corpus(corpus_dir)
    print(f"Corpus: {c}\n")

    # --- 1. Iterate committee reports ---
    print("Committee reports (first 3):")
    for i, r in enumerate(c.manifest_committee_reports()):
        if i >= 3:
            break
        print(f"  [{r.house}] {r.committee_slug} | {r.report_type} | {r.date}")
        print(f"    {r.title[:80]}")
    print()

    # --- 2. Iterate Q/A records ---
    qa_count = sum(1 for _ in c.manifest_qa())
    print(f"Q/A records: {qa_count}")
    print()

    # --- 3. join_qa: manifest + extracted answers ---
    print("Q/A pairs with extracted answers (first 3):")
    shown = 0
    for pair in c.join_qa():
        if shown >= 3:
            break
        print(f"  key={pair.manifest.key}  answers={len(pair.answers)}")
        if pair.answers:
            q = pair.answers[0].question_text or ""
            print(f"    Q: {q[:80]}")
        shown += 1
    if shown == 0:
        print("  (no Q/A records in this corpus)")
    print()

    # --- 4. join_atr_chain: ATR -> original report ---
    print("ATR chains (first 3):")
    shown = 0
    for chain in c.join_atr_chain():
        if shown >= 3:
            break
        original_key = chain.original.key if chain.original else "(not resolved)"
        print(
            f"  ATR: {chain.atr.key}\n"
            f"    -> original: {original_key}\n"
            f"    atr_answers: {len(chain.atr_answers)}  "
            f"orig_observations: {len(chain.original_observations)}"
        )
        shown += 1
    if shown == 0:
        print("  (no ATR records in this corpus)")
    print()

    # --- 5. to_dataframe (pandas optional) ---
    try:
        df = c.to_dataframe("manifest_committee_reports")
        print(f"DataFrame: {len(df)} rows x {len(df.columns)} columns")
        print(f"  columns: {list(df.columns[:6])} ...")
    except ImportError as exc:
        print(f"(to_dataframe skipped: {exc})")
    print()

    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <corpus_dir>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
