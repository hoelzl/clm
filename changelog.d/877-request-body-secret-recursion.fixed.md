- **Cassette request bodies are filtered at any depth (#877).** The
  record-time filter read only the top level of a JSON request body, and so
  did `clm cassette scan` — so `{"data": {"api_key": "sk-live-…"}}` was
  recorded verbatim *and* the audit reported the file clean. Because both
  sides were consistently top-level-only they agreed, which is why the
  scanner/recorder parity suite passed on it: a shared blind spot rather than
  a divergence. The filter now walks the whole body (nested objects, arrays,
  and a top-level array root), removing a matched key together with its
  subtree, and the audit reports the same shapes. Both sides share one
  implementation now instead of two walks. Unmatched bodies keep their bytes,
  and a body too deeply nested to walk is left alone rather than raising —
  raising would send the request to the live network unrecorded. Request
  bodies are part of the replay match key, so a cassette carrying a nested
  secret will now replay-*miss* until the deck is re-recorded; `clm info
  migration` documents that class and `clm cassette scan` names the entries.
- `clm cassette scan` no longer reports a form-encoded request body without an
  `=` as clean (`token` on its own). The recorder strips such a name, so
  skipping those bodies was a false all-clear on a cassette that will
  replay-miss.
