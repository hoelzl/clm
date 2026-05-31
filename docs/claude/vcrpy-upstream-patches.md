# Upstream vcrpy patches (retire the CLM forks)

CLM's HTTP-replay bootstrap (`src/clm/workers/notebook/notebook_processor.py`)
carries two forks of vcrpy *internals*. Both are tactical workarounds for
genuine upstream bugs. Landing them upstream lets us delete the forks and the
tight `vcrpy>=8.1.1,<8.2` pin in `pyproject.toml [replay]`, removing the
"silent rot on a vcrpy bump" risk entirely.

This document is the ready-to-submit material. **A human submits these PRs** —
Claude cannot push to `kevin1024/vcrpy`. Target the latest `vcrpy` `main`
(validated against the released **8.1.1**, Jan 2026).

---

## Patch 1 (PRIORITY): close the leaked httpcore connection — fixes the deadlock

### The bug

`vcr/stubs/httpcore_stubs.py` reads the real response body, then **replaces**
`real_response.stream` with a buffered `ByteStream` and returns the response —
but never `close()`s the original `httpcore.Response`. In httpcore, the pooled
connection is returned to the `ConnectionPool` when the response is closed.
Because vcrpy swaps the stream and hands the response to httpx, httpx later
closes vcrpy's *replacement* `ByteStream` (a no-op for connection lifecycle),
not the original — so **every recorded request leaks one pooled connection**.

Under a burst of concurrent requests (e.g. LangChain `RunnableParallel` /
`.batch()` against an OpenAI-compatible endpoint through httpx), the pool's
`max_connections` is exhausted and all callers block **forever** in
`httpcore._sync/_async.connection_pool.wait_for_connection`. There is no error
and no timeout by default — the process simply hangs.

This affects only **recording** paths (`record`, `new_episodes`, `once` on a
miss, `all`); pure replay returns `vcr_response` before touching the network.

### The fix (≈2 lines each)

Read the body first (as today), then `close()`/`aclose()` the original
response before swapping its stream.

```diff
 def _vcr_handle_request(cassette, real_handle_request, self, real_request):
     # Reading the request stream consumes the iterator, so we need to restore it afterwards
     real_request_body = b"".join(real_request.stream)
     real_request.stream = ByteStream(real_request_body)

     vcr_request, vcr_response = _vcr_request(cassette, real_request, real_request_body)

     if vcr_response:
         return vcr_response

     real_response = real_handle_request(self, real_request)

     # Reading the response stream consumes the iterator, so we need to restore it afterwards
     real_response_content = b"".join(real_response.stream)
+    # Close the original response so its pooled connection is returned to the
+    # httpcore ConnectionPool. We replace .stream with a buffered ByteStream
+    # just below; without closing here, the caller (e.g. httpx) later closes
+    # the *replacement* stream — a no-op for connection lifecycle — and the
+    # real connection leaks. Under concurrent requests the pool is exhausted
+    # and callers block forever in wait_for_connection.
+    real_response.close()
     real_response.stream = ByteStream(real_response_content)

     _record_responses(cassette, vcr_request, real_response, real_response_content)

     return real_response


 async def _vcr_handle_async_request(cassette, real_handle_async_request, self, real_request):
     # Reading the request stream consumes the iterator, so we need to restore it afterwards
     real_request_body = b"".join([part async for part in real_request.stream])
     real_request.stream = ByteStream(real_request_body)

     vcr_request, vcr_response = _vcr_request(cassette, real_request, real_request_body)

     if vcr_response:
         return vcr_response

     real_response = await real_handle_async_request(self, real_request)

     # Reading the response stream consumes the iterator, so we need to restore it afterwards
     real_response_content = b"".join([part async for part in real_response.stream])
+    # See sync variant above: return the pooled connection to httpcore.
+    await real_response.aclose()
     real_response.stream = ByteStream(real_response_content)

     _record_responses(cassette, vcr_request, real_response, real_response_content)

     return real_response
```

`httpcore.Response.close()` / `aclose()` close the (already fully consumed)
underlying stream, which releases the connection back to the pool. Reading the
body **before** closing is required and preserved.

### Suggested PR

- **Title:** `Fix httpcore connection-pool leak: close real response before swapping stream`
- **Body:** the bug section above + the diff. Note it manifests as a hang under
  concurrent recording, not an error.
- **Test** (vcrpy's `tests/integration/test_httpx.py` style): record N > pool
  `max_connections` sequential-then-concurrent requests against a local server
  with a small `httpcore` pool and assert the recording completes without
  hanging (wrap in a timeout). A regression test that hangs without the fix and
  passes with it.

### Minimal standalone reproducer

A full CLM-side reproducer (16 workers, py-spy proof) lives at
`~/Programming/Python/Tests/clm-bug-repros/issue-143-cassette-connection-pool-deadlock/`.
For the upstream PR, a tiny version: a threaded httpx client doing
`max_connections + 2` POSTs through a recording cassette against a localhost
echo server — hangs on the stock build, completes with the patch.

---

## Patch 2 (SECONDARY, needs design discussion): scope `force_reset()` to urllib3

### The bug (CLM issue #129)

`vcr.patch.force_reset()` is a context manager that **globally un-patches every
vcr stub** for the duration of its body (it yields from `reset_patchers()`).
vcrpy's urllib3 stub opens `force_reset()` on every connection construction and
`connect()` so the real connection's `super().__init__()` doesn't re-enter
vcr's patched `HTTPConnection`. But un-patching is **global**: it also
un-patches `httpcore.ConnectionPool.handle_request` for that window.

The race: a foreground thread making an httpcore call (httpx-based LLM SDK)
while a background thread constructs a urllib3 connection (e.g. a telemetry
upload via `requests`) can resolve `pool.handle_request` during the unpatched
window, **bypass vcr entirely, hit the real upstream, and never record it** —
silently invalidating the cassette.

### The shape of an upstream fix

`force_reset()` only needs to un-patch the **urllib3** patchers (the ones whose
recursion it is guarding against), not httpcore/boto/etc. Options:

1. Parameterize `reset_patchers()` / `force_reset()` to filter to a given
   library, and have the urllib3 stub call `force_reset(only="urllib3")`.
2. Or make `force_reset()` only reset patchers whose target module is in the
   `urllib3`/`http.client` family.

CLM currently swaps `reset_patchers` for a filtered generator that yields all
patchers **except** the httpcore ones (`reset_patchers` is looked up via module
globals at call time, so the swap propagates). That's the behavioral spec for
an upstream fix, but the upstream version should scope *positively* to urllib3
rather than *negatively* excluding httpcore.

Full investigation: CLM `docs/claude/issue-129-vcrpy-force-reset-investigation.md`.

### Note

This is a more invasive change than Patch 1 and touches `force_reset`'s
contract, so it warrants a design discussion in an issue first. Patch 1 is the
high-value, low-risk one to land first — it removes the **deadlock** class.

---

## After either lands upstream

1. Bump the `[replay]` pin to include the fixed vcrpy release
   (`>=<fixed>,<next-minor>`), re-run the pin-guard test
   (`tests/workers/notebook/test_http_replay_vcr_pin_guard.py`).
2. `test_upstream_still_leaks_so_the_fork_is_still_needed` will start **failing**
   once Patch 1 lands (upstream now closes the response) — that failure is the
   signal to delete the corresponding fork block from
   `_HTTP_REPLAY_BOOTSTRAP_TEMPLATE` and update the test.
3. Removing a fork reduces the in-kernel workaround count (currently 8) and
   shrinks the surface a future vcrpy bump can break — independent of the
   larger issue #165 mitmproxy migration.
