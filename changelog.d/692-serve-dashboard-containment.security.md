- **`clm serve` gets the same browser containment as the recordings
  dashboard.** The Monitor API has no login, so any page open in the same
  browser could read your build state, worker list and job history — and,
  since nothing validated the `Host` header, a DNS-rebinding page reached the
  app as a genuinely same-origin caller. Requests must now carry a `Host` that
  names this server, and mutating requests must come from the dashboard's own
  origin. `--allowed-host` / `--allowed-origin` opt in to a Tailscale hostname
  or a reverse proxy; a wildcard bind without one now prints a warning instead
  of silently answering `400` to every remote request.
- **`--cors-origin` no longer defaults to `*`.** The default combined `*` with
  `allow_credentials=True`, which makes Starlette echo whichever origin asked —
  strictly worse than a literal wildcard, because it legalises credentialed
  cross-origin reads. A dashboard serving its own frontend needs no CORS at
  all, so the default is now none. Passing `*` explicitly still works, without
  credentials.
- **The `/ws` stream is inside the Studio token gate.** It broadcasts
  deck-change and sync-progress events for the course being served, and
  WebSockets are exempt from CORS — so anyone who could reach the port could
  subscribe and read them, around the bearer token that guards every
  `/api/studio` route. When `--spec` is in play the token is now required
  *before* the handshake is accepted. Browsers cannot set an `Authorization`
  header on a WebSocket, so the Studio PWA presents it as the
  `clm-token.<token>` subprotocol (kept out of access logs, unlike a query
  parameter); scripts may still use `Authorization: Bearer`.
- **Channel subscriptions are restricted to `status`, `workers`, `jobs` and
  `studio`.** Any name was previously stored verbatim, which also meant a typo
  looked like a successful subscription and then went quiet forever — the
  reply now reports what was actually subscribed to.
- **A non-ASCII bearer token no longer raises from inside the auth check.**
  `secrets.compare_digest` rejects non-ASCII `str`, and headers arrive
  latin-1 decoded, so one byte above `0x7F` turned a bad token into a `500`.
