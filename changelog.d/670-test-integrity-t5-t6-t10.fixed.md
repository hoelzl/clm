- **Three test-integrity gaps closed.** The PlantUML and DrawIO end-to-end tests
  logged the number of rendered images and asserted nothing, so they passed even
  when the converter produced no output at all; they now assert that images
  exist and carry a real PNG signature. `AssertionError` was removed from the
  `flaky` `only_rerun` lists in `test_worker_base.py` and `test_lifecycle_mock.py`
  — an intermittent race in the real claim/heartbeat loop surfaces as exactly
  that exception, so retrying on it hid the class of bug those tests exist to
  catch. PlantUML JAR discovery now happens in one place: the fallback path in
  `tests/conftest.py` pointed at the pre-PR-#239 vendored location and matched
  nothing, leaving local availability to depend on an import-time `os.environ`
  mutation in `tests/workers/plantuml/test_plantuml_converter.py`, which made
  availability collection-order-dependent under xdist. That mutation is gone and
  the surviving fallbacks name paths that exist.
