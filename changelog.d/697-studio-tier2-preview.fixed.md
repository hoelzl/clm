- **The Mobile Deck Studio's tier-2 cell preview now actually runs, with its HTML
  sanitized on the server.** The tier expands a cell's Jinja server-side so the
  phone shows a rendered header instead of raw `{{ header_de("…") }}` — and its
  in-page consumer had been unreachable since it was written, gated on
  `cell.cell_type === "markdown"` while the API types a Jinja cell as `"j2"`. The
  gate is now the cell's own `is_j2` flag, as a named function with tests on both
  sides of the contract (the predicate under `node`, and the payload the real
  service emits). Because the header macros deliberately emit markup — centered
  `<div>`s and the course logo as a `data:` image — escaping the output would
  delete the feature, so `POST /api/studio/deck/render-cell` now returns a
  **sanitized** `html` fragment (allowlist in `clm.web.studio.sanitize`, backed by
  `nh3`, new in the `[web]` extra) that the client injects verbatim, with `body`
  echoing the request for the tier-1 fallback. The `%%`-delimiter drop and
  comment-prefix strip moved server-side in the same change, so what gets injected
  is exactly what was sanitized. Without `nh3` the preview fails closed to tier-1
  rather than injecting unchecked HTML. `data:` URIs are confined to `<img src>`
  images (a `data:` link is a navigation vector); authority-relative targets
  (`//host`, and the `\\` / `/\` / `\/` spellings a URL parser treats the same
  way) are refused, as the client's tier-1 `safeUrl()` already refused them;
  `style` is reduced to an inert property allowlist, with `class` disallowed
  because the app's own `.toast { position: fixed }` would otherwise hand
  injected markup the overlay that allowlist exists to refuse; and script /
  iframe / object / form content is removed *with its text* rather than
  unwrapped.
