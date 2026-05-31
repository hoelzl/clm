# Issue #165 — Phase 0 GO-gate findings

Phase 0 of the mitmproxy migration (issue #165) is the evidence gate that
decides whether to proceed. Two load-bearing unknowns dominated the assessment;
both have now been measured empirically. Probe scripts live (throwaway) at
`C:\Users\tc\Tmp\clm-phase0\`.

Environment: Windows 11, Python 3.13.2, httpx 0.28.1, vcrpy 8.1.1 (the version
CLM pins and forks), mitmdump via `uv tool install mitmproxy`
(`C:\Users\tc\AppData\Roaming\uv\tools\mitmproxy\Scripts\mitmdump.exe`).

## Gate 1 — HTTPS CA trust: **GREEN** (the easy path)

The assessment's #1 risk: httpx/openai build their TLS context from vendored
`certifi` and might ignore an injected CA, forcing a fragile per-kernel
`certifi`-splice. The adversarial critic explicitly predicted httpx would
ignore `SSL_CERT_FILE`.

`ca_trust_probe.py` starts `mitmdump` with an isolated confdir, then drives a
real HTTPS GET through it under each CA-trust config:

| Config | Result |
|---|---|
| httpx default, no CA env | **FAIL** `CERTIFICATE_VERIFY_FAILED` (confirms mitm *is* intercepting) |
| httpx default + `SSL_CERT_FILE`=mitm CA | **PASS 200** ← the key test |
| httpx default + `REQUESTS_CA_BUNDLE`=mitm CA | FAIL (httpx ignores the requests-specific var) |
| httpx `verify=<mitm CA>` explicit | PASS 200 (control: interception works) |
| httpx default + certifi-splice (certifi+mitm) | PASS 200 (fallback also works) |
| `requests` + `REQUESTS_CA_BUNDLE`=mitm CA | PASS 200 (LangSmith/telemetry path) |
| openai-SDK-style httpx client + `SSL_CERT_FILE` | PASS 200 (inherits httpx) |

**Conclusion:** httpx 0.28.1 **honors `SSL_CERT_FILE`** — no certifi-splice
required. This refutes the assessment's highest-uncertainty risk; the splice
remains as a proven fallback. Production guidance:

- Point `SSL_CERT_FILE` at a **certifi + mitm-CA concatenated bundle** (not the
  mitm CA alone), so `ignore_hosts` pass-through traffic that goes *directly* to
  a real endpoint still validates against the real CA.
- Use `REQUESTS_CA_BUNDLE` too for the `requests`-based LangSmith path.

**Caveat:** measured on httpx 0.28.1 (CLM's locked version). Re-confirm if a
course repo pins an older httpx.

## Gate 2 — deadlock defeat (mechanism level): **GREEN**

The assessment's #2 concern (critic): the claim that an out-of-process proxy
"eliminates the deadlock class" was asserted by design, never measured.

`deadlock_mechanism_probe.py` reproduces the #143 mechanism in miniature — a
kernel httpx pool capped at `max_connections=2`, a burst of 6 concurrent GETs —
and compares two transports:

| Arm | Transport | Result |
|---|---|---|
| `vcrpy` | vanilla vcrpy 8.1.1 record-mode (the **unpatched, leaking** httpcore stub — master *minus* the #143 close() fork) | **DEADLOCK** — only 2/6 return; the other 4 block forever in `wait_for_connection` (subprocess hangs to the 30s timeout) |
| `mitmproxy` | same burst through `mitmdump`; kernel uses real httpx/httpcore | **ALL 6/6 COMPLETED** in ~4s |

**Conclusion:** the exact #143 pool-exhaustion deadlock reproduces with vcrpy's
leaking in-process stub, and the out-of-process transport eliminates it — the
kernel's real httpx returns connections to its pool normally because nothing is
patched in-process. The deadlock *class* is genuinely defeated, not just the
one instance the #168 stopgap patched.

## Gate 3 — full-build single-proxy throughput: **GREEN**

The critic's follow-on to gate 2: a single-process micro-repro can't show
whether **one shared `mitmdump`** sustains a real **16-kernel `.batch()` burst**
or becomes a new serialization/throughput bottleneck. Run against the full
reproducer (`chains-parallel-stress-cassette.xml`, 9 topic copies × DE/EN = 18
notebooks, `--notebook-workers 16`, `--http-replay new-episodes`) through the
minimal transport wiring (this branch), launcher `C:\Users\tc\Tmp\clm-phase0\run-mitm.ps1`
(`PYTHONPATH`→this worktree, `CLM_HTTP_REPLAY_TRANSPORT=mitmproxy`,
`CLM_MITMDUMP`→uv-tool mitmdump), key sourced from `PythonCourses/.env`,
`LANGSMITH_TRACING=false`:

```
✓ Build completed successfully in 118.5s
  Stage 3/4: HTML Speaker (18 jobs)
  36 files processed · 0 errors · 0 warnings
```

Corroborating evidence:
- `mitm/transport.mitm` = **506 KB** — the proxy recorded real OpenRouter HTTPS
  flows (the LLM calls genuinely routed through it and the CA was trusted under
  load).
- `clm.log`: `mitmproxy transport active: proxy=http://127.0.0.1:NNNNN
  mode=new-episodes …` then `Stopping mitmproxy transport…` — clean lifecycle.
- **0** occurrences of `wait_for_connection` / `PoolTimeout` / `CellTimeoutError`
  in the build log — the exact inverse of the vcrpy run, where `py-spy` showed
  all 16 kernels frozen in `wait_for_connection`.
- **0** vcrpy bootstraps injected (`grep -ci "import vcr|_vcr_handle"` == 0) —
  the bypass holds under full concurrency.

**Conclusion:** the same 16-worker reproducer that deadlocks under vcrpy
completes cleanly in ~2 minutes through one shared mitmdump. The single-proxy
throughput concern is empirically cleared at production-like load; per-worker
proxies are not needed for this workload. The HTTPS/CA path also works
end-to-end against real OpenRouter under load (gate 1 confirmed at scale).

### Minimal transport wiring (this branch, opt-in, default-off)

Three surgical edits gated on `CLM_HTTP_REPLAY_TRANSPORT=mitmproxy` (unset =
today's behavior, bit-identical):
- `build.py` `_maybe_start_mitmproxy_transport`: starts one `mitmdump` before
  workers spawn, splices a **certifi + proxy-CA** bundle, sets
  `HTTP(S)_PROXY`/`SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` in `os.environ` (Direct
  workers inherit via `os.environ.copy()`); stopped in the build `finally`.
- `notebook_processor.py` `_resolve_cassette_paths`: returns `None` under the
  transport → no vcrpy bootstrap, no staging, no merge; kernel httpx is never
  patched.
- `proxy_manager.py` `_locate_mitmdump`: `CLM_MITMDUMP` override for the
  `uv tool install` model.

Note: this proof used **one shared cassette** (no per-(topic,language,kind)
routing) and **Direct** workers only — both correctness/coverage items remain
for later phases, but neither affects the deadlock/throughput conclusion.

## What still remains for production (later phases, per the staged plan)

Not blockers for the GO decision, but required before vcrpy can be retired:
the YAML cassette bridge (vs the prototype's native `.mitm`), request→cassette
routing for concurrent multi-topic correctness, secret-filtering + `ignore_hosts`
+ JSON-body-match parity in the addon, strict-replay-miss → non-zero build exit
parity, and Docker-worker reachability (`host.docker.internal` + CA-in-container).
vcrpy stays the default until those parity gates pass.

## Prototype package status (this branch)

`src/clm/infrastructure/http_replay_mitm/` (ported from
`claude/mitmproxy-prototype`): `MitmproxyManager` (Windows-aware lifecycle:
`CREATE_NEW_PROCESS_GROUP` + `CTRL_BREAK_EVENT`, port pick, readiness poll) and
`ClmReplayAddon` (record / replay / new-episodes / refresh / once; 599-on-miss;
eager flush; native `.mitm` format — YAML bridge is later work). The 4 smoke
tests pass with the `uv tool` mitmdump. The smoke test's
`importorskip("mitmproxy")` was removed (the in-process import is wrong for the
`uv tool install` model — the test only needs mitmdump as a subprocess).

## Net effect on the decision

Both dominant unknowns resolved favorably. The conservative ~2–3.5 week estimate
was driven largely by the HTTPS/certifi unknown, now de-risked. The GO case is
materially stronger than the cautious baseline assumed. Remaining real work:
the single-proxy throughput proof, the YAML cassette bridge, request→cassette
routing for concurrent multi-topic builds, secret/`ignore_hosts` parity in the
addon, and Docker-worker reachability — all per the staged plan, vcrpy remaining
the default until parity gates pass.
