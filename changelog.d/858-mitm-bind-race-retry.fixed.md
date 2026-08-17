- Hardened worker startup and the HTTP-replay proxy against races that
  surfaced as flaky builds on busy machines
  (`docs/claude/design/test-flakiness-root-causes.md` §11):
  `cleanup_stale_workers` no longer deletes another build's seconds-old
  `'created'` pre-registration (which killed that build's booting worker with
  "Worker N does not exist in database" whenever two builds share one jobs
  DB — the age-guarded stuck-created pass remains the only reaper of
  pre-registrations); a booting worker now polls its activation rendezvous
  for up to 10s instead of dying on the first missed read; and
  `MitmproxyManager.start()` retries a lost listen-port bind race on a fresh
  port (3 attempts) instead of failing the build, while config/dependency
  failures still surface immediately and a caller-pinned port is never
  retried. Also raised the contention-sensitive test-suite subprocess
  budgets and made scrubbed-env e2e builds use per-test databases.
