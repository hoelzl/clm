- **Nightly CI job for the `slow` test tier.** `slow` is excluded from every
  PR-CI step *and* from the local default, so ~37 tests ran nowhere at all —
  including `test_cache_equivalence.py`, the only proof that a cached notebook
  replays byte-identically to a direct execution, the worker-reuse-across-builds
  e2e tests, and all 18 real-subprocess CLI tests.
  `.github/workflows/nightly.yml` now runs `pytest -m "slow and not docker"`
  daily. A failure files a GitHub issue labelled `nightly-failure`, or comments
  on the existing open one so an outage produces one issue rather than one per
  night; `workflow_dispatch` lets the run and its failure route be exercised on
  demand.
