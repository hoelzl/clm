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

  `clm cassette scan` applies the same rule. It has to: a finding the recorder
  would not act on is one that re-recording cannot clear, and the scan exits
  non-zero on findings, so a divergence makes a repo audit unsatisfiable. The
  test was written twice and had drifted; it is one shared function now
  (`cassette_format.is_secret_body_value`).

  No committed cassette was damaged — the affected decks predate the
  response-side filter, so this fixes what re-recording them *would* have done.
