- **`requests`/`aiohttp` decks work under the mitmproxy replay transport
  again.** The kernel's cassette-routing tag bootstrap only patched `httpx`,
  so traffic from `requests` (and `aiohttp`) reached the shared replay proxy
  *untagged* and was matched/recorded against the per-build catch-all cassette
  instead of the topic's canonical cassette — record/replay was silently
  broken for any deck using plain `requests.get` (the original motivating
  use case for HTTP replay). The bootstrap now also tags
  `requests.Session.send` and `aiohttp.ClientSession._request` (both
  import-guarded — the libraries stay optional in kernel environments).
  Affected topics need a one-time re-record (`--http-replay=refresh`): their
  untagged interactions never made it into the committed cassette.
- **Untagged replay-proxy traffic now triggers a loud build-log warning.**
  When a request reaches the replay proxy without an `X-CLM-Cassette` routing
  tag (an HTTP stack the tag bootstrap does not patch, e.g. `urllib.request`),
  the mitmproxy addon logs a once-per-build `CLM-HTTP-REPLAY-UNTAGGED` warning
  naming the first offending request, and the proxy manager relays it into the
  CLM build log. Previously such traffic silently fell through to the
  catch-all cassette — the failure mode that hid the missing `requests` patch.
  Client-library coverage is now documented in
  `docs/user-guide/http-replay.md` → "Client-library coverage".
