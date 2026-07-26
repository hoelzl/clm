- **Removed the Studio's dead tier-2 preview path.** `renderJ2()` was gated on
  `cell.cell_type === "markdown"`, but the API types a Jinja cell as
  `cell_type: "j2"` (a `markdown` cell always has `is_j2 === false`), so the
  branch had been unreachable since it was written — and what it contained was
  an unescaped `innerHTML` assignment of a server response. The server-side
  endpoint is live, sandboxed and tested; only the in-page consumer is gone.
  Wiring it up needs a decision about sanitizing macro-emitted HTML, so it is
  tracked as issue #697 rather than fixed in passing.
