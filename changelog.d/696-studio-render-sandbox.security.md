- **The Studio cell preview no longer runs arbitrary Python.**
  `POST /api/studio/deck/render-cell` rendered its request body through a plain
  `jinja2.Environment`, so a template could read `__class__` and walk out of
  the template namespace from there — server-side template injection, verified
  against the real function. It now renders in an
  `ImmutableSandboxedEnvironment`. The
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
  pairing no longer writes the token into uvicorn's access log, a proxy's, or
  an outbound `Referer`. Browser *history* still records it — a fragment is
  stored there like a query string — so that half is unchanged. The QR is
  reprinted on every launch, so there is nothing to migrate.
- **The Studio frontend escapes quotes.** `esc()` handled `&`, `<` and `>` but
  not `"`, and its output is interpolated into `<a href="…">` before reaching
  `innerHTML` — so a markdown link target could close the attribute and add an
  event handler. Link targets are also restricted to `http`, `https`, `mailto`
  and relative URLs, with control characters stripped before the scheme is
  judged (browsers strip them when resolving, so `java&#9;script:` would
  otherwise slip past and then execute).
- **The cell preview is bounded in size and no longer runs on the event
  loop.** Blocking attribute traversal says nothing about how big a render
  gets: `{{ "A" * 200000000 }}` allocated 200 MB in about a second, and the
  route rendered inline, so one request from any token holder stalled every
  other request and the disk watcher. Sequence repetition and `+` are now
  refused before they allocate, the rendered output is bounded *as it
  accumulates* (a 500-iteration loop of individually-legal emits went from a
  100 MB peak to 1.1 MB), `~` is redirected at compile time via
  `code_generator_class` (a doubling payload went from ~1.2 GB to 5.2 MB), and
  the render runs in a worker thread. Each needs a different hook because
  Jinja routes the three differently — in particular `~` compiles to a
  `Concat` node that neither `intercepted_binops` nor `environment.concat` can
  see. **This is not a claim that the preview is DoS-proof**: nothing bounds
  *iteration*, so nested loops still burn CPU without allocating. A client
  holding the Studio token can still stall the process — accepted, because the
  token is the trust boundary and that client can already rewrite any deck.
- **The Studio service worker cached the app shell forever.** `sw.js` only
  re-installs when its own bytes change and `activate` only drops caches whose
  *name* differs, but the shell handler was pure cache-first — so `app.js`,
  which had changed three times under `clm-studio-shell-v1`, was served from
  cache indefinitely. Any already-installed PWA would have kept running the
  pre-fix frontend and, because the pairing URL moved, would have been unable
  to re-pair. The cache name is bumped and the shell now uses
  stale-while-revalidate, so a future missed bump costs one stale load rather
  than permanent staleness.
