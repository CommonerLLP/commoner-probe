# NeVA `low`-quality text: design pass

**Status:** design input, not a canonical doc. Subordinate to `SCOPE.md`.
**Date:** 2026-07-28
**Decision needed from Commoner:** one, at the end — where the OCR helper lives.

## Summary

The `low`-quality NeVA backlog is real, but two of the three explanations it
has carried are wrong. This pass falsifies both against the corpus and measures
the one that works.

| Claim | Verdict |
|---|---|
| The documents are scans and need OCR to be readable at all | **False.** 30/30 sampled carry a Gujarati Unicode text layer |
| Substring-level glyph repair can recover them | **False.** 0.9% recovery, measured |
| OCR of the rendered page beats the embedded text layer | **True.** Wins on 28/30, median similarity 0.993 vs 0.942 |

The conclusion is the same instrument the original ask named — OCR — for
entirely different reasons, on a different subset, and with a different
justification. Both matter, because the wrong reason produces the wrong scope.

## What `low` actually means

`repair_text` (`commoner_probe/neva_text.py:129-156`) returns `low` when the
portal's `subject` string is not found in the extracted text and no glyph map
can be derived from it. Its docstring calls such a document an "OCR-fallback
**candidate**". Downstream that hedge was dropped and the label was read as
"scan".

Measured on the corpus at
`sansad-semantic-crawler/data/neva/gujarat/pdfs/questions` (6,375 PDFs on disk,
`questions.jsonl` = 6,384 records):

- n=30 random: **30/30 have a text layer**, 569–30,819 chars, 236–8,485
  Gujarati codepoints. Zero image-only, zero latin-garbage.
- n=120 re-classified through `repair_text`: 5 `clean`, 1 `repaired`, 113
  `low` — ≈95%, consistent with the reported 5,972/6,384 (93.5%).

So the count was right and the diagnosis was not.

## The real defect: a partially shifted font-subset cmap

`GJ_15_8_3781_10.pdf`, labelled `low`, extracts as legible Gujarati with a
minority of wrong codepoints:

| extracted | correct |
|---|---|
| `િંશોધન` | `સંશોધન` |
| `વર્ચ` | `વર્ષ` |
| `સ્થિસ્તએ` | `સ્થિતિએ` |
| `ણિજ્ઞાન` | `વિજ્ઞાન` |
| `માાં` | `માં` |

Median similarity between the garbled title line and the clean portal subject
is **0.93** — the text is ~93% correct, not garbage.

## Why char-map repair fails, and why substring repair fails too

`derive_glyph_repair` keeps only 1:1 same-length replacements and applies them
doc-wide via `str.maketrans`. An opcode census over 237 `low` documents
(reference aligned against its best-matching line) shows why that misses:

| opcode shape | count |
|---|---|
| delete | 331 |
| insert | 308 |
| replace 1→1 | 192 |
| replace 1→2 | 83 |
| replace 2→3 | 71 |
| replace 1→3 | 36 |
| replace 2→1 | 29 |

Top pairs are conjunct-level: `'જ '`→`'લ્લ'` (56×), `'િ'`→`'જ'` (50×),
`''`→`'ર્'` (31×). Only 192 of 733 ops are expressible as a character map.

**The obvious fix does not work.** A prototype extending derivation to
substring rules of any shape, applied longest-first and gated on recovering the
reference, was measured on 110 `low` documents:

- recovers the candidate line it was derived from: **1/110 (0.9%)**
- recovers the reference anywhere in the document: **1/110 (0.9%)**

The failure is instructive. From
`હોસ્પપટલો` vs `હોસ્પિટલો` the aligner learns `પ → િ`, which is correct at that
one position and destroys every legitimate `પ` in the document. From a spurious
space it learns `' ' → ''` and deletes all whitespace. **The corruption is
position-dependent, not a consistent glyph permutation**, so no doc-wide
substitution — character or substring — can express it. The existing `low`
classification is correct and conservative, and the module is behaving as
designed. This line of attack should be closed, not refined.

## What works: OCR of the rendered page

These are **digitally-rendered PDFs whose glyphs draw correctly** — only the
`ToUnicode` cmap is wrong. Rasterizing produces a pristine, noise-free image,
and OCR reads what a human reads. This is categorically different from OCR of a
scan, which is what "OCR would degrade good text" assumes.

`pdftoppm -r 300 -png` then `tesseract -l guj --psm 6`, same document as above:

```
રાજયમાં ભૂ-રાસાયણિક સંશોધન બાબત
*15/8/912 શ્રીમતી સંગીતાબેન પાટીલ (લીંબાયત): માનનીય વિજ્ઞાન અને પ્રોદ્યોગિકી મંત્રીશ્રી …
(૧) તા.૩૧/૧૨/૨૦૨૫ની સ્થિતિએ (૧) હા,
```

Every position the text layer corrupts — `સ્થિતિએ`, `વિજ્ઞાન`, `પ્રોદ્યોગિકી`,
`મંત્રીશ્રી`, `સંશોધન` — is correct.

Head-to-head on **n=30 `low` documents**, similarity of the best-matching title
line to the portal subject:

| | median | mean | ≥0.98 |
|---|---|---|---|
| embedded text layer | 0.942 | 0.898 | 5/30 |
| OCR (300dpi, `guj`) | **0.993** | **0.986** | **25/30** |

Per-document: **OCR better on 28, text layer better on 1, tie on 1.**

Strict verbatim subject recovery is 11/25 (44%) on a separate sample. The gap
between 44% verbatim and 0.993 similarity is mostly the portal's own metadata —
several `subject` values are truncated mid-word or differ from the printed
title — so verbatim recovery understates OCR quality and should not be the
acceptance metric.

**Cost:** 1.1 s/page measured. Page 1 of all 5,972 `low` documents, 8-way
parallel, is ~0.2 h. Full documents scale by mean page count; this is hours,
not days, and needs no GPU.

## Scope this implies

- Apply OCR to the **`low` subset only**. `clean` and `repaired` documents keep
  their text layer; OCR is not an improvement there and would cost provenance
  clarity.
- Keep both texts. The embedded layer and the OCR layer disagree in known ways;
  a consumer auditing a quotation needs to see which one a claim came from.
- `quality` gains a value for OCR-derived text rather than reusing `repaired`,
  which means something specific and provable.
- Do **not** treat OCR output as verbatim-quotable without a spot check. 0.993
  is not 1.0, and this corpus feeds litigation-adjacent work.

## The decision

The org's capability registry assigns generic text/PDF extraction for retrieval
to `partial-recall`, with an exception where source structure requires a
domain-repo implementation. This case sits on that line:

- The **mechanism** (rasterize a page, run tesseract, return text) is generic.
  Three implementations already exist in the org — `sevent4`'s
  `adapters/budget_ocr.py` (the cleanest: parameterized `lang`/`dpi`, 61 lines),
  plus two one-offs in `narcotrek`. That duplication is the argument for a
  shared home.
- The **trigger** (a broken-cmap Gujarati assembly PDF whose `low` label comes
  from a portal-metadata mismatch) is entirely source-structural, and NeVA
  acquisition is registered to `commoner-probe`.

Recommendation: the generic rasterize-and-OCR helper goes to `partial-recall`
and both `sevent4` and `narcotrek` collapse onto it; the NeVA-specific policy
— which documents qualify, what `quality` becomes, how both texts are carried —
stays in `commoner_probe/neva_text.py`. That needs Commoner's ruling before any
code is written, because it is a cross-repo boundary move.

Note also that `narcotrek/scripts/surgical_ocr_dopo.py`, named in the original
ask as the thing to base this on, is the wrong reference regardless: it runs
tesseract with no `-l` flag (English on Gujarati script), hardcodes DOPO table
regexes and pages 50–120, and its input host `bprd.nic.in` is unreachable from
two continents.

## Reproduction

Measurements in this document were produced this session against the corpus and
the PDFs, not inferred. The scripts are scratch, not committed; each is a
few dozen lines over `questions.jsonl` plus `pdftotext` / `pdftoppm` /
`tesseract`, seeded (`random.seed(7)`, `101`, `23`) so the samples are
reproducible.
