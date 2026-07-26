- **The Studio cell preview no longer runs arbitrary Python.**
  `POST /api/studio/deck/render-cell` rendered its request body through a plain
  `jinja2.Environment`, so a template could read `__class__` and walk out of
  the template namespace from there — server-side template injection, verified
  against the real function. It now renders in a `SandboxedEnvironment`. The
  module advertised itself as the "no-execution" render tier while doing this;
  the claim is now accurate about what it means (no *kernel*, not no code).
  Legitimate previews are unaffected — the bundled header macros for all five
  shipped languages render unchanged.
- **The Studio API no longer accepts `?token=`.** A URL is the worst place for
  a credential that does not expire: it reaches uvicorn's access log, any
  proxy's, browser history, and the `Referer` of outbound links. Only
  `Authorization: Bearer` is accepted now (and, on `/ws`, the `clm-token.…`
  subprotocol). Nothing needed the query form — the pairing deep link targets
  the unauthenticated `/studio/` static mount and is read by the frontend.
- **The QR pairing URL moved the token into the fragment**
  (`/studio/#token=…`). Browsers never send a fragment to the server, so
  pairing itself no longer writes the token into a log. The QR is reprinted on
  every launch, so there is nothing to migrate.
- **The Studio frontend escapes quotes.** `esc()` handled `&`, `<` and `>` but
  not `"`, and its output is interpolated into `<a href="…">` before reaching
  `innerHTML` — so a markdown link target could close the attribute and add an
  event handler. Link targets are also restricted to `http`, `https`, `mailto`
  and relative URLs, with control characters stripped before the scheme is
  judged (browsers strip them when resolving, so `java&#9;script:` would
  otherwise slip past and then execute).
