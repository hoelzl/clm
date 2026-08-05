- **CI-runnable pinned public corpus gates (#682).** The new
  [ClmTestCourse](https://github.com/hoelzl/ClmTestCourse) repo (curated and
  sanitized from the course repos, CC BY-NC-SA 4.0) is fetched at a pinned
  commit by `scripts/fetch_test_corpus.py` and asserted on by
  `tests/slides/test_public_corpus.py` — exact numbers, not ceilings: pair
  population, the full refusal-code set, parse observations, `project ∘
  parse` byte-identity, and the self-diff noise floor. CI runs it in the
  integration suite; the private full-corpus run stays available locally via
  `CLM_SYNC_CORPUS_DIR`. Course specs in the corpus make every spec-driven
  surface exercisable against it.
