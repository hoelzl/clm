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
  *before* the handshake is accepted — for the **whole endpoint**, so a script
  that polled `/ws` for job status needs the token too once a spec is served.
  Browsers cannot set an `Authorization`
  header on a WebSocket, so the Studio PWA presents it as the
  `clm-token.<token>` subprotocol (kept out of access logs, unlike a query
  parameter); scripts may still use `Authorization: Bearer`.
- **Channel subscriptions are restricted to `status`, `workers`, `jobs` and
  `studio`.** Any name was previously stored verbatim, which also meant a typo
  looked like a successful subscription and then went quiet forever — the
  reply now reports what was actually subscribed to.
- **`--allowed-origin` now actually authorizes a browser** (affects
  `clm recordings serve` too). The origin guard consulted the operator's
  allowlist only in its `Origin` fallback, which is unreachable whenever
  `Sec-Fetch-Site` is present — i.e. for every current browser. Naming an
  origin therefore bought a successful CORS preflight and a `403` on the
  request behind it. The allowlist is now checked first — which makes the flag
  a full exemption from the origin check, so the docs now say so. A value that
  is not a valid origin (a missing `https://`, say) is reported at startup
  instead of being dropped in silence, and an `Origin` carrying userinfo or an
  embedded tab/newline — forms no browser emits, and which parse to a
  different host than they read as — is refused rather than normalized.
- **A non-ASCII bearer token no longer raises from inside the auth check.**
  `secrets.compare_digest` rejects non-ASCII `str`, and headers arrive
  latin-1 decoded, so one byte above `0x7F` turned a bad token into a `500`.
