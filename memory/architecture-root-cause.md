---
name: architecture-root-cause
description: why the sansad-crawler / sansad-semantic-crawler fork exists and what the single prerequisite fix is
metadata:
  type: project
---

The sansad-crawler / sansad-semantic-crawler fork exists because the `classify()` call is
hardwired in the crawl loop inside `sansad.py`. The loop must therefore live wherever the
classifiers live — in the analytics repo. The result is 15 forked modules in
sansad-semantic-crawler that drift out of sync with sansad-crawler.

**The prerequisite fix:** Add `filter_fn: Callable | None = None` to `TopicProfile` in
`topics.py`. The crawl loop calls it as `self.topic.filter_fn(title, query)` if not None.
sansad-semantic injects `clf.matches` as the callback at startup. No circular imports.

**Why:** Until this hook exists, the crawl loop cannot live in a pure acquisition layer.
This is step 3 of Session 1 in `.ai/plans/phase1-probe-split.md`.

**How to apply:** Any work on the sansad.py crawl loop or TopicProfile must preserve
this hook pattern — do not hardwire classifier calls into the loop.
