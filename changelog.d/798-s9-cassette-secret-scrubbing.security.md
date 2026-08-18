- **HTTP-replay cassettes no longer record response secrets, and the mitmproxy
  CA private key left the course tree** (adversarial-review finding S9, #798).
  Cassettes are committed files in course repositories, so whatever the
  recorder writes goes out in a pull request — and the recorder filtered
  *requests* only. A `Set-Cookie` header or an OAuth token response was
  committed verbatim.

  Recording now applies a response filter as well: `Set-Cookie` is dropped, and
  the values of OAuth-shaped keys in JSON response bodies (`access_token`,
  `refresh_token`, `id_token`, `client_secret`, `api_key`, `apikey`,
  `authorization`, `password`, `secret`, `session_token`) are replaced with
  `[REDACTED-BY-CLM]`, recursively, with the payload's shape preserved. Key
  names are matched **exactly**, never as substrings — an LLM response
  legitimately carries `completion_tokens` / `total_tokens`, and clipping those
  would silently corrupt the usage data of every replayed cassette. A response
  that cannot be scrubbed is not recorded at all, the same stance the request
  side already took — but the filter avoids raising wherever it can
  (unparseable, deeply nested, or surrogate-bearing bodies are left alone),
  because a dropped response to a *repeated* request replays as the previous
  one rather than missing loudly.

  The request side gained the provider spellings it was missing — headers
  `api-key` (Azure), `x-goog-api-key` (Gemini), `proxy-authorization`,
  `x-amz-security-token`, `x-auth-token`; query parameters `key`,
  `access_token`, `apikey`, `subscription-key`, `X-Amz-Signature` — and closed
  three gaps in how they were applied: a JSON content-type is matched by
  prefix (so `application/json; charset=utf-8` bodies are filtered instead of
  skipped), query-parameter names are matched case-insensitively, and bodies
  are filtered on any method rather than `POST` alone. A body that will not
  parse is left untouched rather than dropping the interaction.

  New `clm cassette scan [SPEC-FILE]` audits **already-committed** cassettes
  read-only, naming the file, interaction index and key, and exits non-zero
  when it finds anything — the way to decide which decks in a course repo are
  worth re-recording, instead of blanket re-recording thousands of files that
  each need a live service. Responses are not part of the replay match key, so
  the response-side change cannot cause a replay miss. Two request-side ones
  can, loudly: a cassette recorded with a now-filtered *query parameter*, and
  one whose *request body* kept a `password`/`token`/`api_key` because its
  content-type carried a charset, its method was not `POST`, or it was
  form-encoded. Both are part of the replay match key; the scanner flags
  exactly those cassettes, and `clm info migration` says which findings are
  urgent.

  The mitmproxy confdir — which holds the proxy's CA **private key** — was
  created next to the jobs database, i.e. inside the course working tree, where
  `umask_secret()` is a no-op on Windows and a `CLM_JOBS_DB_PATH` pointing at a
  network share put the key on that share. It now lives in the per-user data
  dir alongside `kernel-envs/`, giving one stable CA per machine. Delete any
  leftover `<jobs-db-dir>/mitm/confdir` — it contains a private key. See
  `clm info migration`.
