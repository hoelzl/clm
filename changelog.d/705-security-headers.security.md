- **`clm serve` now sends a Content-Security-Policy and related security
  headers on every response** (except FastAPI's `/docs` and `/redoc`, which
  need CDN assets and inline script). The policy forbids inline script and
  event handlers (`script-src 'self'`) and confines `fetch`/XHR/WebSocket to
  the server's own origin (`connect-src 'self'`), so a miss in any
  HTML-sanitizing layer — the Studio's tier-2 preview sanitizer or the
  client-side markdown renderer — can no longer execute script or exfiltrate
  the Studio's bearer token; it degrades to a defacement bug. Inline styles
  and off-origin images remain allowed deliberately (the sanitizer governs
  CSS; the image rule is the documented tier-1 parity decision). The
  recordings dashboard is not covered yet — its base template embeds an
  inline `<script>` by design — and `SecurityHeadersMiddleware` documents why.
