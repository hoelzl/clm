- **`clm slides sync` no longer silently truncates a reconciled cell.** When the
  edit judge returned a body with an unescaped inner `"` (e.g. an English term
  wrapped in German `„ … "` quotes), the lenient response parser could turn the
  malformed reply into a *parseable but truncated* value and write it to disk with
  `0 error(s)` reported (Issue #377). The parser now does a strict-first parse and
  rejects any response with unexpected top-level keys, so such a reply is surfaced
  as a hard error and the edit is rolled back atomically — the safe behavior other
  affected cells already got — instead of shipping a corrupted cell.
