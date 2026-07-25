- **Every autouse env-neutraliser in `tests/conftest.py` now names the test that
  still covers the real production value**, and a new
  `test_pool_size_cap_interaction.py` closes the widest of the gaps they left:
  the worker pool-size clamp firing during real managed-worker startup. The
  neutralisers pin the heartbeat slow-write threshold, the pool-size caps and
  the `CLM_*_DB_PATH` defaults suite-wide — each justified in isolation, but
  together they made the production defaults invisible, and nothing outside the
  clamp's own unit tests had ever seen it engage.
