# HTTP-replay mitmproxy prototype

**Status**: **On hold.** Branch `worktree-mitmproxy-prototype` retained as a sub-week-implementable contingency. The prototype itself is real and tested; the architectural case for finishing the migration has weakened — see "Update: Phase A findings" below.

**Context**: We had accumulated seven distinct workarounds in the vcrpy bootstrap (force_reset race, deep-copy persister, JSON body matcher, eager append, allow_playback_repeats, custom matching, staging+merge). The hypothesis when this prototype was written was that *most* of these were fighting in-process patching hazards that would be structurally absent if the proxy ran as a separate process — so an out-of-process mitmproxy might be a structural rather than a tactical fix.

## Update: Phase A findings (PR #146)

After this prototype was committed, the Phase A trace harness (merged in [PR #146](https://github.com/hoelzl/clm/pull/146)) was run against the AZAV ML course to measure what was actually escaping. The result reshapes the case for this prototype materially:

- **Zero cassette bypasses** and **zero issue-129 race candidates** observed across W02 / W03b / W05 trace runs. `scoped_force_reset: true` confirmed in every worker via `bootstrap.complete` events — the existing vcrpy workaround empirically holds.
- The residual misery wasn't a vcrpy-architecture problem at all. It was **three deterministic bugs**, all fixed in the same PR:
  1. `_sweep_orphan_cassette_staging_files` was documented to run pre-build but never actually invoked from `clm build` (issue #145 — one-line fix in `process_course_with_backend`).
  2. LangSmith telemetry was being recorded with per-build timestamps.
  3. The merge dedup key used `str(BytesIO)` (object memory address) for stream bodies — every loaded LangSmith entry looked "new" each rebuild and got re-folded into canonical.
- After (2) + (3): `git diff` of cassettes after a no-op rebuild is **empty** at steady state.

### What that means for this prototype

The original case was: "if Phase A shows threading races dominate, in-process patching is the disease, mitmproxy is the cure." Phase A showed threading races are not dominant — they're not even secondary. Three deterministic, fixable bugs were.

| Original argument | Status after PR #146 |
|---|---|
| Eliminate 5 of 7 vcrpy workarounds | Still true, but now aesthetic, not correctness-load-bearing |
| Fix issue-129 race structurally | Existing scoped-reset workaround empirically confirmed working |
| Enable replay across processes (presentation/recording use case) | Still a real future-capability story, independent of correctness |
| 2-3 days YAML-format work + 1 day integration + HTTPS CA setup | Same cost; the offsetting correctness benefit shrank to ~zero on the measured corpus |

The branch is not abandoned because the *capability* still has value — particularly if a future workload (different LLM providers, different HTTP libs, more concurrency) reopens the threading-race risk, or if cross-process replay becomes operationally useful (e.g., capturing presentation-time traffic to share the same cassette as the build pipeline). But the work should not be picked up speculatively. Restart when there is evidence-driven motivation, not before.

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

**Known gap**: some libraries vendor `certifi` and read from it preferentially. The Phase A trace harness (now merged via PR #146) emits `socket.connect` events that can identify any libraries bypassing the configured CA — re-run it against the workload of interest when picking this work up; if any culprit shows up, the fix is to splice mitmproxy's CA into the certifi bundle on first start.

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

## Dependency handling

`mitmproxy` has real, unfixable conflicts with two of CLM's existing
extras when forced into the same Python environment:

* **`h11`**: mitmproxy 11.0.x pins `<=0.14`; langchain-openrouter
  (transitively in `[summarize]`) requires `>=0.16` via `httpcore`.
* **`protobuf`**: mitmproxy needs `<6`; `[ml]` requires `>=6.32.1`.

Because the proxy runs as a subprocess, mitmproxy does **not** need to
share a Python environment with the workers. The supported install
paths reflect that:

1. **Recommended (production and most contributors)**:
   `uv tool install mitmproxy`. Installs the latest mitmproxy into its
   own isolated environment with its own Python (chosen by `uv` to
   match mitmproxy's `requires-python`, typically 3.12+ for newer
   releases). Places `mitmdump` on PATH; the proxy manager finds it
   via `shutil.which`. Compatible with any project venv configuration,
   including `[all]`.

2. **`[mitmproxy]` extra (dev convenience only)**:
   `uv sync --extra mitmproxy`. Installs into the project venv but
   declared mutually exclusive with `[ml]`, `[summarize]`, and `[all]`
   via `[tool.uv] conflicts` in `pyproject.toml`. Pinned to
   `mitmproxy>=11.0,<11.1` because that's the last release supporting
   CLM's `requires-python>=3.11`. Use this only when iterating on the
   addon/manager code and a full-feature CLM install is not needed.

## Follow-up work to make this production

**Precondition (added after PR #146)**: do not start this work without a fresh empirical signal that vcrpy is the bottleneck. Phase A showed it currently isn't. Re-run the trace harness against the workload in question and confirm a non-trivial bypass or race-candidate rate before resuming.

If/when that signal arrives, the rough order is:

1. **YAML cassette format**: addon reads/writes the existing vcrpy schema. (Estimate: 2-3 days.)
2. **Integration into `clm build`**: spawn manager based on `CLM_HTTP_REPLAY_TRANSPORT`, plumb env into `worker_executor.py`, add the vcrpy bootstrap bypass. (Estimate: 1 day. Touches both `build.py` and `worker_executor.py` lightly.)
3. **HTTPS CA setup**: confirm certifi-bundling libraries used by workers honor `SSL_CERT_FILE` (or splice CA into certifi bundle). The Phase A trace harness can quantify this — `socket.connect` events vs. proxy hits reveal any libraries bypassing the configured CA. (Estimate: 1-2 days, mostly verification.)
4. **Staging/merge integration**: addon writes to per-worker staging paths; reuse `merge_staging_into_canonical`. (Estimate: 1 day; mostly threading existing path-resolution code. Note: the merge layer has now been hardened by the issue #145 fix and the BytesIO dedup-key fix from PR #146 — both apply transport-agnostically.)
5. **Side-by-side comparison build**: pick one AZAV ML topic, build under both transports, diff outputs and cassettes. Phase A's instrumentation provides ground truth for whether the residual escape rate actually changes.

Total estimated effort to production-ready: ~1 week of focused work (down from ~1.5 — `uv tool install` install path is now resolved, no longer a dedicated step), decoupled from any other planned change.
