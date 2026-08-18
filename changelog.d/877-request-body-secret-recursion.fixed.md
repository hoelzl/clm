- **Cassette request bodies are filtered at any depth (#877).** The
  record-time filter read only the top level of a JSON request body, and so
  did `clm cassette scan` — so `{"data": {"api_key": "sk-live-…"}}` was
  recorded verbatim *and* the audit reported the file clean. The
  scanner/recorder parity suite did not catch it because it had no
  request-body rows at all; and adding rows would not have caught it either,
  since both sides were consistently top-level-only and therefore agreed. The
  filter now walks the whole body (nested objects, arrays, and a top-level
  array root), removing a matched key together with its subtree, and the audit
  reports the same shapes. Both sides share one implementation now instead of
  two walks. Unmatched content is preserved — an array or scalar root stays
  byte-identical, while a JSON object body is still re-dumped either way (the
  long-standing vcrpy quirk) — and a body too deeply nested to walk is left
  alone rather than raising, since raising would send the request to the live
  network unrecorded. Request bodies are part of the replay match key, so a
  cassette carrying a nested secret will now replay-*miss* until the deck is
  re-recorded; `clm info migration` documents that class and `clm cassette
  scan` names the entries.
- **`clm cassette scan` reads form-encoded request bodies exactly as the
  recorder does.** Three fixes in one, all of them cases where the audit and
  the recorder disagreed about what a parameter name even is: a name with no
  `=` (a bare `token`) is now reported, because the recorder strips it; a
  percent-encoded name (`api%5Fkey=…`) and a `+` in a name are now **not**
  reported, because the recorder does not; and a non-UTF-8 byte in a
  parameter *value* no longer makes the audit skip the whole body, which had
  it vouching for a cassette the recorder does rewrite. The recorder missing a
  percent-encoded name is a real leak, tracked separately as #881.
