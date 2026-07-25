- **Worker API is no longer an open service.** In Docker mode CLM starts a
  small REST API for containers to reach the job queue. It bound `0.0.0.0`
  with no authentication on any route, so on every Docker-mode build it was
  reachable from the whole LAN — and, because its handlers ignored content
  type, from any web page open in the developer's browser. It now binds
  `127.0.0.1` plus (on Linux) the Docker bridge gateways, and every route
  requires an `Authorization: Bearer` token generated per build and injected
  into worker containers. Binding wider is an explicit opt-in
  (`CLM_WORKER_API_HOST`) that also requires a pinned `CLM_API_TOKEN`; CLM
  refuses to start otherwise. See "Worker API (Docker mode)" in
  `docs/user-guide/configuration.md`.
- **The executed-notebook cache no longer stores or transmits pickles.**
  Payloads are nbformat JSON in both `clm_cache.db` and on the Worker API
  wire, so a cache entry can no longer execute code when it is read. Existing
  pickle entries are discarded the first time the cache is opened and
  regenerate on the next build.
- **Upgrade note for Docker mode**: worker images must be rebuilt
  (`clm docker build`). An image from before this change presents no token and
  its jobs fail with `Worker API rejected the token (401)`.
