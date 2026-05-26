# HTTP-replay mitmproxy prototype

**Status**: Prototype on branch `worktree-mitmproxy-prototype`. Lifecycle and addon are real and tested; integration with `clm build` is sketched here but not implemented.

**Context**: We've accumulated seven distinct workarounds in the vcrpy bootstrap (force_reset race, deep-copy persister, JSON body matcher, eager append, allow_playback_repeats, custom matching, staging+merge). Most are fighting in-process patching hazards that are structurally absent when the proxy runs as a separate process. This prototype validates that running mitmproxy as a subprocess under `clm build`'s control is operationally viable.

## What's in this prototype

| Component | Status | Location |
|---|---|---|
| `MitmproxyManager` lifecycle | Real, tested | `src/clm/infrastructure/http_replay_mitm/proxy_manager.py` |
| Custom addon (record/replay) | Real, tested | `src/clm/infrastructure/http_replay_mitm/addon.py` |
| Smoke tests (record → replay → strict miss) | Real, passing | `tests/infrastructure/test_http_replay_mitm.py` |
| `[mitmproxy]` extra in `pyproject.toml` | Real | `pyproject.toml` |
| Integration with `clm build` | **Stubbed** — sketched below | — |
| YAML/vcrpy cassette format compatibility | **Out of scope** — see follow-up | — |
| HTTPS interception with CA trust | **Out of scope** — design only | — |

## Architecture

```
clm build (parent process)
├── if CLM_HTTP_REPLAY_TRANSPORT=mitmproxy:
│     MitmproxyManager(cassette_path, mode).start()
│       ├── locate mitmdump executable
│       ├── pick free port on 127.0.0.1
│       ├── spawn mitmdump --scripts <addon.py> --set clm_cassette_path=...
│       ├── poll listen port until TCP accept succeeds (≤10s)
│       └── return self
├── for each worker subprocess:
│     env.update(manager.env_vars())    # HTTP_PROXY, HTTPS_PROXY (+ CA bundle for HTTPS)
│     env["CLM_HTTP_REPLAY_TRANSPORT"] = "mitmproxy"   # signals worker to skip vcrpy bootstrap
├── (build proceeds; workers' HTTP calls route through proxy)
└── on exit (success or failure):
      manager.stop()
        ├── send CTRL_BREAK_EVENT (Windows) / SIGTERM (Unix)
        ├── wait 5s for graceful flush
        └── force-kill if still running
```

### Why subprocess, not in-process

`mitmproxy` exposes a Python API (`mitmproxy.master.Master`, `DumpMaster`) that can run inside the parent process. We deliberately don't use it because:

- **Kernel termination isolation**: notebook kernels still get `TerminateProcess`'d on timeout. With the proxy in its own process, kernel kills cannot truncate cassette writes mid-flow.
- **Reproducibility**: an `mitmdump …` command line can be handed to a developer and re-run by hand to investigate a problem. An embedded `Master` running on an asyncio task inside `clm build` has no such reproducer.
- **Crash containment**: an mitmproxy bug or hang doesn't take down the build orchestrator. We get "proxy died, here's the log" rather than a tangled stack trace.

## Mode mapping

CLM's existing `--http-replay` flag has five modes. They map one-for-one to addon behaviour:

| CLM mode | Addon `clm_mode` | Behaviour |
|---|---|---|
| `replay` | `replay` | Serve cassette hits; **synthesize 599 on miss** (does not escape to network). |
| `record` | `record` | Always hit upstream; append new entries to cassette. |
| `new-episodes` | `new-episodes` | Serve cassette hits; record new requests upstream. (Default for local builds.) |
| `refresh` | `refresh` | Delete cassette, re-record everything from upstream. |
| `once` | `once` | Like `new-episodes`, but cassette must exist (refuses to start otherwise). |
| `disabled` | (n/a) | Manager not started; no proxy. |

## Strict-miss diagnostics

When the addon receives a request not in the cassette in `replay` mode, it synthesizes a `599` response with a JSON body:

```json
{
  "error": "clm_replay_miss",
  "method": "GET",
  "url": "https://api.openai.com/v1/chat/completions",
  "cassette": "/abs/path/to/cassette.mitm"
}
```

This makes the failure mode loud and self-describing — the worker sees a real HTTP response with diagnostic content rather than a hang, network error, or silently-wrong cached entry. The 599 status code is non-standard (RFC 6585 doesn't define it) but is widely used as a "client-side synthetic" marker; it won't collide with real upstream responses.

## CA trust for HTTPS interception (design only — not in prototype)

The smoke test uses plain HTTP to avoid the cert-trust setup. For real LLM endpoints (all HTTPS) the per-build setup is:

1. On manager start, use `--set confdir=<project>/.clm/mitm/` to isolate mitmproxy's CA + config from the user's home directory (and from other concurrent builds).
2. First start writes mitmproxy's auto-generated CA to `<confdir>/mitmproxy-ca-cert.pem`. Add `.clm/mitm/` to `.gitignore`.
3. Export the cert path to workers via `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`. The manager's `env_vars(include_ca=True)` already does this.
4. Workers' HTTP libraries (`requests`, `urllib3`, `httpx`, `openai` SDK) honour those env vars and trust the proxy for TLS termination.

**Known gap**: some libraries vendor `certifi` and read from it preferentially. Phase A's trace harness will tell us whether anything CLM workers actually use falls into this category — if so, the fix is to splice mitmproxy's CA into the certifi bundle on first start.

## Bypassing the vcrpy bootstrap

Today, `_inject_http_replay_bootstrap()` (in `src/clm/workers/notebook/notebook_processor.py`) prepends a 200-line bootstrap cell that activates vcrpy in the kernel. When mitmproxy is the transport, no in-kernel patching is needed at all — the proxy intercepts at the TCP level.

The wiring change is one branch:

```python
def _inject_http_replay_bootstrap(nb, cassette_path, mode) -> None:
    if os.environ.get("CLM_HTTP_REPLAY_TRANSPORT") == "mitmproxy":
        return  # network-level interception; no kernel bootstrap needed
    # ... existing vcrpy bootstrap code unchanged ...
```

This makes the transport switch entirely backwards-compatible: vcrpy remains the default and continues to work; `CLM_HTTP_REPLAY_TRANSPORT=mitmproxy` opts in.

## Cassette format: prototype uses `.mitm`, production needs YAML compatibility

The addon currently persists flows in mitmproxy's native binary `.mitm` format (`FlowReader`/`FlowWriter`). This was deliberate scope-cutting:

- **Pro**: zero work to validate transport architecture; mitmproxy's serializer is battle-tested.
- **Con**: incompatible with existing YAML cassettes in course repos.
- **Con**: binary diffs are useless for review; authors can't eyeball a recorded payload.

For production we'd need the addon to read/write the existing vcrpy YAML schema. The shape of that work:

1. Reuse `vcr.persisters.filesystem.FilesystemPersister.load_cassette` / `vcr.serialize.serialize` to read/write YAML. They are pure functions and don't require the rest of vcrpy to be active.
2. Convert between mitmproxy's `HTTPFlow` and vcrpy's `(Request, Response)` tuples at the addon boundary. Both are simple dataclasses.
3. Plug into the existing `merge_staging_into_canonical` infrastructure in `http_replay_cassette.py` — it's already format-aware via vcrpy's persisters.

Estimated effort: 2-3 days. Independent of the transport architecture itself.

## How this kills the existing workarounds

With this transport active, the seven vcrpy patches reduce to:

| # | Patch | Status under mitmproxy |
|---|---|---|
| 1 | `_clm_scoped_reset_patchers` (issue #129) | **Eliminated** — no in-process patching. |
| 2 | `_ClmDeepCopyPersister` | **Eliminated** — vcrpy's serializer isn't in the loop. |
| 3 | `_clm_json_body_matcher` | Re-implementable in addon; trivial. |
| 4 | `allow_playback_repeats=True` | **Eliminated** — addon keeps entries in its index regardless of how many times they're served. |
| 5 | body in `match_on` | **Kept by design** in `_request_key`. |
| 6 | `_clm_eager_append` | **Eliminated** — proxy isn't killed by kernel termination. |
| 7 | staging + completion markers + merge | **Kept** — multi-language concurrent builds still need it; format-agnostic. |

Five of seven workarounds vanish. The two that remain (#5 and #7) are either pure design choices (#5) or fundamental concurrency infrastructure (#7), unrelated to which library does the interception.

## Known packaging issue

`mitmproxy>=11.0,<11.1` transitively requires `protobuf>=6.32`. The `[ml]` extra (via langchain) currently coexists with that bound, but installing `[all]` fails resolution because `protobuf>=6.32.1` from `[ml]` and mitmproxy 11.0.x's lower bound differ from what `[all]`'s lockfile resolution expects. Workarounds:

- **For the prototype**: install mitmproxy directly with `uv pip install 'mitmproxy>=11.0,<11.1' --prerelease=allow`. The `[mitmproxy]` extra is declared but not folded into `[all]` cleanly.
- **For production**: run mitmproxy out of a dedicated `uv tool install mitmproxy` environment, locate the executable by path, and never share its Python deps with worker/CLM Python. This is the cleanest split given that mitmproxy and the workers don't need to share any runtime objects.

## Follow-up work to make this production

In rough order:

1. **YAML cassette format**: addon reads/writes the existing vcrpy schema. (Estimate: 2-3 days.)
2. **`uv tool`-style isolated install**: mitmproxy in its own env, called by path. Resolves the `[ml]` protobuf conflict cleanly. (Estimate: 1 day.)
3. **Integration into `clm build`**: spawn manager based on `CLM_HTTP_REPLAY_TRANSPORT`, plumb env into `worker_executor.py`, add the vcrpy bootstrap bypass. (Estimate: 1 day. Touches both `build.py` and `worker_executor.py` lightly.)
4. **HTTPS CA setup**: confirm certifi-bundling libraries used by workers honor `SSL_CERT_FILE` (or splice CA into certifi bundle). Phase A trace harness data will inform this. (Estimate: 1-2 days, mostly verification.)
5. **Staging/merge integration**: addon writes to per-worker staging paths; reuse `merge_staging_into_canonical`. (Estimate: 1 day; mostly threading existing path-resolution code.)
6. **Side-by-side comparison build**: pick one AZAV ML topic, build under both transports, diff outputs and cassettes. Phase A's instrumentation helps quantify whether the residual escape rate actually drops.

Total estimated effort to production-ready: ~1.5 weeks of focused work, decoupled from any other planned change.
