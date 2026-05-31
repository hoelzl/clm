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

## What Phase 0 did NOT yet prove (remaining gate)

This mechanism proof is single-process and small. It does **not** answer the
critic's follow-on: can a **single shared `mitmdump`** sustain a real
**16-kernel `.batch()` burst** without becoming a *new* serialization/throughput
bottleneck? That needs the full reproducer:

- Rig: `~/Programming/Python/Tests/clm-bug-repros/issue-143-cassette-connection-pool-deadlock/`,
  spec `course-specs/chains-parallel-stress-cassette.xml` (9 topic copies, 16
  workers), launcher `run-clm.ps1` (repoint its `PYTHONPATH` worktree to this
  branch).
- Requires: real OpenRouter API credit (recording path), the minimal
  `CLM_HTTP_REPLAY_TRANSPORT=mitmproxy` build wiring (start manager in
  `build.py`, inject `HTTP(S)_PROXY` + the certifi+CA bundle in
  `worker_executor.py`, bypass the vcrpy bootstrap in `notebook_processor.py`),
  and a `py-spy` clean check vs. the patched-vcrpy reference run.

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
