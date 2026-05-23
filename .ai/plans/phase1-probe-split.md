# Phase 1 — Probe Split: sansad-crawler becomes the canonical Layer 0

**Identity of probe:** Sousveillance infrastructure for the state's mandatory disclosure
systems — parliamentary questions, budget allocations, committee proceedings, state
assembly records. It automates the move Ambedkar made with census blue books and Browne
made with Fanon's FBI file: seizing the state's own paperwork and turning it into
evidence. (Simone Browne, *Dark Matters: On the Surveillance of Blackness*, Duke UP 2015,
item_key EG7ZPN8H.)

The library is domain-expert, not generic — it knows these portals' conventions (session
cookies, CSRF tokens, paginated JSONL APIs, PDF-wrapped answers) so callers do not have
to. Targets: sansad.in, NeVA state assembly portals, budget.gov.in, MoSPI, and others
across CommonerLLP's corpus.

**Goal:** Make sansad-crawler the single authoritative acquisition layer (`probe`).
sansad-semantic-crawler then becomes a thin analytics layer (`compose`) that imports from it.

**Constraint:** Session 1 must complete and tag `v0.3.0` before Session 2 touches
sansad-semantic-crawler. If you delete from the semantic repo before sansad-crawler is
the canonical source, the build breaks.

**Implication for HTTP client:** Because probe is a library with expertise in state
disclosure portals, it must own a production-grade HTTP client — not bare `requests.get()`. The
canonical reference is `academiaindia/scraper/fetch.py` (requests-cache, per-domain
rate-limit, exponential backoff, stale-if-error, robots.txt). Standardising on this
client is a Session 1 task (see §5a below).

---

## Session 1 — sansad-crawler (add everything missing)

Work entirely in this repo. No changes to sansad-semantic-crawler yet.

### 1. Port `bkt_no_match` + classifier log fields into `sansad.py`

The semantic fork added these 13 lines to the LS/RS crawl loop.
Apply the same diff to `sansad_crawler/sansad.py`:

```python
# In SansadCrawler.__init__ (or crawl_ls / crawl_rs signature):
classifier_mode: str = "regex",   # new param

# In crawl_ls / crawl_rs loop body, after title is fetched:
semantic = self.topic.classify(title, query)
if not semantic["matches"]:
    bkt_no_match += 1
    continue

# In the per-bucket stats dict:
bkt_no_match=bkt_no_match,
```

And in `runlog.py`, make the classifier fields present (keep optional for now so
existing corpora deserialise cleanly):

```python
classifier_mode: str = ""
classifier_config: dict[str, Any] | None = None
```

### 2. Port `crawl_composition()` + `committee_members.jsonl` into `committees.py`

sansad-semantic's `committees.py` has `crawl_composition()` at line 273 and writes
`committee_members.jsonl`. Copy that method verbatim into sansad-crawler's
`sansad_crawler/committees.py` — it only calls `self._get()` and standard stdlib.

Also add `committee_members.jsonl` to the corpus schema / stats if `compute_stats`
enumerates known output files.

### 3. Add `filter_fn` hook to `TopicProfile` in `topics.py`

This is the architectural root fix. Without it, the classify() call is hardwired in
the crawl loop and the loop cannot live in a pure acquisition layer.

```python
# topics.py — TopicProfile dataclass
from typing import Callable

@dataclasses.dataclass
class TopicProfile:
    ...
    filter_fn: Callable[[str, str], bool] | None = None  # (title, query) -> keep?
```

Call site in `sansad.py` crawl loop:

```python
if self.topic.filter_fn is not None:
    if not self.topic.filter_fn(title, query):
        bkt_no_match += 1
        continue
```

This lets sansad-semantic inject its classifier as `topic.filter_fn = classifier.matches`
without sansad-crawler ever importing from `classifiers/`.

### 4. Move `neva.py` verbatim

Copy `sansad-semantic-crawler/sansad_semantic_crawler/neva.py` into
`sansad_crawler/neva.py`.

It imports only from `.base` (BaseCrawler, now, safe_filename_segment) and
`.http_client` (make_session) — both exist in sansad-crawler already. No changes needed.

Add `NevaStateCrawler` to `__init__.py` exports and add a `crawl-neva` subcommand
to `cli.py` (mirror the structure of `crawl-committees`).

### 5a. Standardise HTTP client

Copy `academiaindia/scraper/fetch.py` into `sansad_crawler/http_client.py`, replacing
the current thin wrapper. Capabilities required for a government-site library:

- Exponential backoff with jitter (govt sites 429 without warning)
- Per-domain rate limit (configurable, default 1 req/s for sansad.in)
- requests-cache with `stale-if-error` (corpora must survive portal downtime)
- robots.txt respect (mandatory for a public-interest library)
- `User-Agent` identifying the library version

Update all call sites in `sansad.py`, `committees.py`, `neva.py` to use the new client.
Remove any remaining bare `requests.get()` calls (currently in `sansad_vacancy_scraper.py`).

This is a library-quality gate: probe cannot claim govt-site expertise with a no-cache,
no-backoff HTTP layer.

### 5b. Design decision: `extractors.py` stays in compose

`sansad_semantic_crawler/extractors.py` imports `from .classifiers.llm import LLMClassifier`.
It cannot move to Layer 0 without dragging the LLM dependency in.

**Decision: leave it in sansad-semantic-crawler.** The `CompositionExtractor` is an
analytics concern, not an acquisition concern. It belongs in `compose`.

### 6. Commit and tag

```bash
git add -A
git commit -m "feat: port bkt_no_match, crawl_composition, filter_fn hook, neva crawler (probe Layer 0 readiness)"
git tag v0.3.0
git push && git push --tags   # only when user explicitly requests push
```

---

## Session 2 — sansad-semantic-crawler (gut the forks)

Only start after `v0.3.0` is tagged in sansad-crawler.

### 1. Fix `dependencies = []` and add sansad-crawler

In `sansad-semantic-crawler/pyproject.toml`:

```toml
[project]
dependencies = [
    "sansad-crawler>=0.3.0",
    ...other real deps...
]
```

### 2. Replace forked imports

For each module that is a fork of something now in sansad-crawler:

| Delete from sansad_semantic_crawler/ | Replace with import from |
|---|---|
| `sansad.py` | `from sansad_crawler.sansad import SansadCrawler` |
| `committees.py` (crawl half) | `from sansad_crawler.committees import CommitteeCrawler` |
| `topics.py` | `from sansad_crawler.topics import TopicProfile` |
| `runlog.py` | `from sansad_crawler.runlog import RunRecord` |
| `neva.py` | `from sansad_crawler.neva import NevaStateCrawler` |
| `http_client.py` | `from sansad_crawler.http_client import make_session` |
| `base.py` | `from sansad_crawler.base import BaseCrawler` |

Keep in sansad-semantic-crawler:
- `classifiers/` (all of it — this is compose's core)
- `extractors.py` (LLM dep, belongs here)
- `committees.py` (composition analysis half, if split from crawl half)
- `answers.py`, `atr_linkage.py`, `stats.py`, `validate.py` (analytics)

### 3. Wire classifier as `filter_fn`

In the sansad-semantic CLI or wherever `TopicProfile` is constructed:

```python
from sansad_crawler.topics import TopicProfile
from .classifiers import build_classifier

topic = TopicProfile.from_json(path)
clf = build_classifier(topic.classifier_config)
topic.filter_fn = clf.matches   # inject — no circular import
```

### 4. Delete forked files

```bash
rm sansad_semantic_crawler/sansad.py
rm sansad_semantic_crawler/runlog.py
rm sansad_semantic_crawler/http_client.py
rm sansad_semantic_crawler/base.py
rm sansad_semantic_crawler/neva.py
# committees.py: keep the analytics half, delete the crawl half
```

### 5. Verify and tag

```bash
python -m pytest
python -m sansad_semantic_crawl --help   # smoke test CLI
git add -A
git commit -m "refactor: gut forked acquisition modules; depend on sansad-crawler>=0.3.0 (compose Layer 1 cleanup)"
git tag v1.2.0
```

---

## What does NOT change in these two sessions

- narcotrek: GPL barrier, own runlog, cannot merge. Schema convergence is a separate
  later task (expose `sansad_crawler.core.runlog` as a stdlib-only module for narcotrek
  to adopt).
- `extractors.py` LLM dependency: stays in compose, no changes.
- Rename from `sansad-crawler` → `probe` and `sansad-semantic-crawler` → `compose`:
  this is a separate Phase 2 naming task (PyPI rename, import rename, docs update).
  Do not do it in these two sessions.
