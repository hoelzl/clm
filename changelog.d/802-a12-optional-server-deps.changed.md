- **BREAKING (install-time)**: `docker`, `fastapi`, `uvicorn`, and `watchdog`
  are no longer core dependencies (#802 A12) — a bare install covers
  Direct-mode builds only. Docker worker mode needs the new `[docker]` extra
  (SDK + host-side Worker API server), `clm build --watch` the new `[watch]`
  extra, and the `[web]` / `[recordings]` extras now carry their own server
  stacks. Every affected entry point fails fast with a message naming the
  missing extra; `[all]` includes the new extras. `DummyBackend` moved out of
  the shipped package into `tests/`. See `clm info migration`.
