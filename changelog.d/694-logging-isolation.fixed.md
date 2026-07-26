- Fixed the nightly flake in `test_cache_miss_falls_back_to_direct_execution`
  (#694): a three-hop cross-test poisoning chain — a config test reloaded the
  process-global `ClmConfig` singleton under a monkeypatched
  `CLM_LOGGING__LOG_LEVEL=ERROR` (monkeypatch reverts the env var, not the
  singleton), a later in-process `clm build` applied the poisoned level via
  `setup_logging` → `getLogger("clm").setLevel(ERROR)`, and every later
  `clm.*` WARNING on that xdist worker was silently swallowed. Added an
  autouse `_restore_worker_global_state` fixture in `tests/conftest.py` that
  snapshots/restores the clm logger chain and the config singleton around
  every test (pinned by `tests/test_global_state_isolation.py`), and guarded
  the three caplog-asserting tests that lacked a `caplog.set_level` guard
  (the other 35 caplog-using files already had one).
