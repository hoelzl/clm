- Test suite: direct-worker integration tests no longer flake under
  `pytest -m "not docker"` on many-core machines. Real-worker suites
  (`test_lifecycle_integration`, `test_direct_integration`) and the two
  `clm build` subprocess tests now carry `serial("workerpool")`, serializing
  the worker-boot herd that could stretch boot latency past the 15 s
  registration poll under `-n auto` (measured: 48 concurrent boots ≈ 10 s).
  The two build-subprocess tests also gained `integration`, moving them out
  of the fast pre-push suite while keeping `-m "not docker"` and CI coverage.
