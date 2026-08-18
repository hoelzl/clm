- **Cassette response-body redaction no longer corrupts word-keyed JSON maps**
  (#875). The response filter decided to redact from the key name alone, so a
  body that is a *dictionary keyed by ordinary words* lost data: GPT-2's BPE
  vocabulary (`encoder.json`, fetched by the text-chunking deck) maps
  `"secret"` to the integer `21078`, and recording it replaced four integer
  token ids with the placeholder **string** — a corrupted vocabulary on replay,
  and a changed JSON value type under whatever reads it.

  A number, boolean or `null` under a secret-named key is now left alone; no
  credential this filter exists for is one. Strings, objects and arrays are
  still redacted, containers wholesale — note that the tempting inverse rule,
  "redact only strings", *leaks*: `{"secret": {"value": "sk-live-…"}}` would be
  recursed into, and `value` is not on the key list, so the secret would
  survive. There is a regression test for that.

  Also fixed, found while reviewing the above: a **repeated JSON name** could
  hide a secret from both the filter and the audit. `json.loads` keeps only the
  last of two identically-named pairs, so `{"secret":"sk-live-…","secret":1}`
  parsed to the exempt number, redacted to nothing, and the byte-preservation
  shortcut re-emitted the plaintext verbatim while `clm cassette scan` reported
  the file clean. The recorder now re-serializes such a body and the audit
  reports it under the new location `response body (repeated name)`. Scoped to
  filter-list names, so an ordinary `{"a":1,"a":2}` still takes the fast path.

- **A pathologically nested JSON response body no longer forwards the request to
  the live network, nor aborts a repo-wide audit** (#878). Recursing over a body
  a few thousand levels deep raised `RecursionError` out of the response filter,
  and the replay addon reads a raised filter as "unfilterable" — handling it
  like an ignore-host, forwarding to the **live network** in every mode
  including strict `replay`, and recording nothing. The same overflow escaping
  `clm cassette scan` took down the audit of every cassette after it. Both sides
  now leave such a body alone and keep going. Note *which* half overflows —
  the parse or the walk — depends on the interpreter build, so both are guarded
  together.

- **`clm cassette scan` no longer reports a false all-clear for a response body
  with a byte-order mark** (#875). The audit decoded cassette bodies as strict
  UTF-8 before parsing, so a body carrying a BOM — or encoded as UTF-16/32,
  both of which `json.loads` detects on its own — was silently unparseable and
  reported **clean**, while the recorder redacted the token inside it. That is
  the worst direction for a gate, and it hit exactly the population the audit
  exists for: bodies written verbatim before the response filter existed. The
  scan now hands bytes to the parser exactly as the recorder does.

  `clm cassette scan` applies the same value rule as the recorder. It has to: a
  finding the recorder would not act on is one that re-recording cannot clear,
  and the scan exits non-zero on findings, so a divergence makes a repo audit
  unsatisfiable. The two implementations never disagreed — they agreed and were
  wrong together, which is the more dangerous shape, because one bug then needs
  fixing in two files and nothing notices if you fix only one. The value test is
  shared now, and a new parity suite runs ~60 payload shapes — including raw
  bodies and non-UTF-8 encodings a Python dict cannot express — through *both*
  sides and requires the same verdict.

  No committed cassette was damaged — the affected decks predate the
  response-side filter, so this fixes what re-recording them *would* have done.
