# Issue #165 P5 — cutover readiness report

> **HISTORICAL (issue #355, executed).** The cutover this document prepared
> is complete: mitmproxy became the default in 1.10 and the in-kernel vcrpy
> transport (bootstrap, workarounds, pin guard) was deleted in #355 stage 1.
> The deletion inventory and gate checklist below are kept as the record of
> what was removed and why.


**Status:** prep (2026-05-31). P5 step 1 (trace-harness re-port) is **done**; the
default-flip and workaround deletion remain **deferred to a post-merge release**
per the plan invariant.

This report is the decision aid for the eventual cutover. It records (1) why the
flip is not done in this session, (2) the parity-gate readiness — distinguishing
gates with committed runnable tests from those proven only by uncommitted
e2e/probe runs, (3) the exact in-kernel-workaround deletion inventory with
per-item deletion-safety, and (4) the deletion blast radius. Two empirical
re-proofs run this session (gate 1 via the re-ported harness; gate 6 record-mode)
are recorded inline.

## Why the flip is deferred (not done now)

The transport (P1–P4) is **branch-only**: draft PR #173 on
`claude/issue-165-mitmproxy-transport`, **not merged to master, not in any
release** (latest tag `v1.6.2`; master tip is #177/#172/#175). The plan invariant
(issue-165-production-plan.md:8–14) says:

> "The default flips to mitmproxy only after every parity gate holds **across a
> full release cycle**, and the 8 in-kernel workarounds are deleted only then."

Since the transport has not shipped behind the flag in any release, the flip and
the deletions are categorically future-release work. Doing them now would violate
the staging discipline. **vcrpy stays the default and is never uninstalled** (it
remains the YAML serializer behind `cassette doctor` / `strip_cassette_hosts` /
the cassette bridge).

## Parity-gate readiness

Audited gate-by-gate (workflow `wf_0d04be79-2d0`, skeptical lens: a gate is only
*closed* if a runnable test proves it). Headline: the gates are **mechanism-proven
by committed unit/smoke tests**, but several **scale / real-TLS / whole-build-byte
proofs live only in uncommitted probe scripts + manual builds**. Before the
cutover these should be committed as tests or re-run and recorded.

| # | Gate | Committed test? | Residual gap |
|---|------|-----------------|--------------|
| 1 | Deadlock-defeat (16-worker, 0 stalls, py-spy clean) | **No** | Re-confirmed this session via the re-ported harness — isolated 0-bypass proof + 116-flow build with no pool-exhaustion signature (below). The full-scale *completing* 16-worker build is still not a committed test (cutover-gate item). |
| 2 | HTTPS round-trip on Windows, real endpoints | Partial | Committed smoke tests are **HTTP-only (localhost, no CA trust)**; real HTTPS + `SSL_CERT_FILE`/CA only in uncommitted probe + manual OpenRouter build. |
| 3 | Cassette byte-identity vs vcrpy | **Yes** | Strong: 9 `cassette_format` tests (plain/gzip/multi-value headers/LF/UTF-8/round-trip/reason-phrase). Only residual: identity vs a *real committed* cassette was e2e-only. |
| 4 | Tooling (doctor / strip / merge) loads bridge cassettes | Partial | Only **merge** has a committed test against a bridge cassette; doctor/strip rely on the gate-3 byte-identity argument, not a direct test. |
| 5 | Secret/telemetry hygiene; no-op rebuild grows nothing | **Yes** (+ live) | Secret-strip + ignore_hosts + filter byte-identity covered by unit/smoke; the **"grows nothing"** record half **closed this session** (real OpenRouter record → secret-clean cassette, below). Bonus: the partial build matched 111 dummy-key requests to real-key cassettes (filter parity at scale). |
| 6 | JSON-match parity (real LLM JSON POSTs replay-hit) | **Yes** (+ live) | Replay-hit semantic JSON matching unit+smoke proven; **record-mode "grows nothing" closed this session** (real OpenRouter, byte-identical no-op rebuild, below). |
| 7 | Strict-replay miss → fast non-zero build exit | Partial | Addon-layer non-retryable 404 is tested. This session showed the addon fast-fail is real, but a **langchain agent/retry layer above the SDK can re-issue a missed request**, so a miss can stall until the per-cell **Option-F timeout** (kept) fires — the full "fast exit" chain depends on that backstop, reinforcing the keep-Option-F decision. |
| 8 | Concurrency routing (DE/EN multi-topic, split fallback) | Partial | Demux mechanism well covered; **actual concurrent** DE/EN traffic and the `.de/.en` split base-cassette fallback are not pinned for this transport (asserted by design). |
| 9 | Default-unchanged (transport unset ⇒ bit-identical) | Partial | Early-return / no-inject invariants tested; **whole-build byte-identity** only verified in the uncommitted e2e. |

**Cutover-gate recommendation:** before flipping the default, convert the
uncommitted e2e/probe proofs for gates 1, 2, 7, 8, 9 into committed tests (or a
documented, repeatable cutover-gate script), and close the gate-5/6 record-mode
half (gate 6 run below).

### Gate 1 re-proof (this session) — re-ported harness, mitmproxy transport

Two empirical checks, both via the re-ported harness:

1. **Isolated proxy smoke** (clean process, no kernel/venv): the real `mitmdump`
   proxy started, the addon emitted `proxy.ready`, an HTTPS `GET` through the
   proxy returned **200** (CA trust via the certifi+proxy-CA bundle works), the
   `proxy` stream logged the request+response, and
   `analyze_http_replay_trace.py` (transport mode) classified every kernel
   `socket.connect` as **to-proxy with 0 bypasses**.
2. **Partial reproducer build** (`chains-parallel-repro`, replay, 2 DE/EN decks,
   dummy key): the transport intercepted at production fidelity — the addon
   **served 111 of 116 flows** as cassette hits, with **no connection-pool
   deadlock signature** (the kernel's real httpx pooled normally through the
   proxy; the #143 leak class is structurally gone). Notably, those 111 hits
   were dummy-key requests matching **real-key** committed cassettes — strong
   at-scale evidence for the gate-5 secret-filter and gate-6 JSON-matcher
   **replay** parity.

   The build did **not complete**: 5 requests replay-**missed** the committed
   cassettes (which predate the current build → request drift) → the addon's
   non-retryable 404 → **langchain's agent/retry layer re-invoked** rather than
   fast-failing, so the cell stalled (the per-cell **Option-F timeout** — kept —
   is the real backstop here). This is a stale-cassette + langchain-retry
   finding the harness *surfaced* (111 served / 5 miss attribution), **not** a
   transport deadlock. It also sharpens gate 7: the addon-layer fast-fail is
   real, but a retry/agent layer *above* the SDK can re-issue a missed request,
   so the per-cell timeout must stay.

**Conclusion:** deadlock-defeat is re-confirmed for the transport (isolated
0-bypass proof + no pool-exhaustion signature at 116 flows). The full-scale
16-worker *completing*-build proof remains a cutover-gate item (as already
noted), now additionally gated on refreshing the reproducer's stale cassettes.
Whether vcrpy would also miss those 5 requests was not A/B-tested — flag for the
cutover gate.

### Gate 6 re-proof (this session) — record-mode "grows nothing", real OPENROUTER

**PASS.** A minimal real OpenRouter chat completion recorded through the addon
(`new-episodes`): recorded a real interaction (HTTP 200, 2225-byte cassette);
the recorded cassette is **secret-clean** — no raw key value, no
`authorization`/`bearer` header, no `x-api-key`, no LangSmith host (filter
parity with vcrpy holds on a *real* recording, not just synthetic fixtures); and
a second identical request in `new-episodes` **served from cache** (no second
upstream call) leaving the cassette **byte-identical** (sha unchanged,
2225→2225). The record-mode "no-op rebuild grows nothing" half of gates 5/6 is
therefore closed. (One-call run, key read from `.env`, never printed or
persisted; the scratch cassette held no secret and was discarded.)

## In-kernel workaround deletion inventory

The "~480-line bootstrap" = `_HTTP_REPLAY_BOOTSTRAP_TEMPLATE`
(notebook_processor.py:209–~524) + `_HTTP_REPLAY_TRACE_TEMPLATE` (~527–~720). It
embeds these workarounds. **None is deletable before the default-flip AND removal
of the in-kernel vcrpy bootstrap** — both transports coexist behind the flag and
vcrpy is still the default, so deleting any of these reintroduces its bug for
every default (non-mitmproxy) build.

| Workaround | In-kernel site | Disposition under transport | Replacement / parity |
|---|---|---|---|
| `scoped_force_reset` (#129) | nbp:253–269 | **Moot** (kernel httpcore never patched) | none needed — addon never enters a vcr cassette context |
| `httpcore_close_fork` (#143) | nbp:442–514 | **Moot** (kernel never patched) | none needed; delete with bootstrap + relax the `[replay]` vcrpy pin + remove `test_http_replay_vcr_pin_guard.py` |
| `convert_to_unicode_deepcopy` | nbp:284–289 | **Reimplemented** | `cassette_format.py:378` (deepcopy in `serialize_interactions`); ⚠ no drift-guard ties the two deepcopy sites |
| `clm_json_body_matcher` | nbp:292–395 | **Reimplemented** | `cassette_format.py:279` + `REPLAY_MATCHERS`; drift-guarded by `test_filter_constants_and_matchers_match_bootstrap` |
| `ignore_hosts` | nbp:193 (shared fn) + template:376 | **Reimplemented** (shared policy) | `resolve_http_replay_ignore_hosts` is **module-scope, used by both transports** — KEEP the function; only the template usage goes |
| `allow_playback_repeats` | template:417 | **Reimplemented by construction** | addon serve loop is non-depleting (addon.py:292–297); regression test added (`test_replay_serves_repeated_identical_requests_non_depleting`) |
| `eager_append` | nbp:432–438 | **Reimplemented** | addon eager `write_cassette` (addon.py:343); kill-survival differs by design (markerless staging is swept, not folded) |
| `per_cell_timeout` (Option-F) | nbp:146–169, applied :1885 | **KEEP** | transport-agnostic loud-failure net; not an HTTP concern — do **not** delete at the flip |

Parity confidence is **high** for every reimplemented item (verbatim mirrors with
a drift-guard test on the filter/matcher constants). Two follow-ups surfaced:

- **`allow_playback_repeats` mitmproxy regression test — DONE.**
  `test_replay_serves_repeated_identical_requests_non_depleting`
  (`tests/infrastructure/test_http_replay_mitm.py`) records one interaction then
  replays the identical request 3× in one proxy lifecycle, asserting all serve
  from the single recorded entry with the upstream counter never advancing — the
  non-depletion guarantee is now pinned on the surviving transport.
- **`convert_to_unicode_deepcopy` drift is not guarded.** The kernel persister and
  the addon's `serialize_interactions` deepcopy are independent; retiring one does
  not auto-flag the other. Acceptable (the in-kernel one is deleted at the flip),
  but note it.

## Deletion blast radius (what to update at the flip)

When `_HTTP_REPLAY_BOOTSTRAP_TEMPLATE` + `_inject_http_replay_bootstrap` are
removed (mitmproxy becomes the sole transport):

- **Delete** `TestBootstrapDurability` (test_notebook_processor.py ~3620–3971) and
  the inject-shape/inject-policy tests (~2160–2380) — they `.format()`/`exec` the
  template.
- **Delete** `tests/workers/notebook/test_http_replay_vcr_pin_guard.py` entirely
  (it guards the forked vcrpy 8.1.x internals) and **relax/remove** the
  `pyproject.toml [replay]` vcrpy `>=8.1.1,<8.2` upper bound it protects.
- **Rewrite** `test_filter_constants_and_matchers_match_bootstrap` to assert the
  `cassette_format` constants directly (it currently reads the template text).
- **Re-point** the two `cassette_format.py` doc comments (lines ~65, ~283) that
  name the bootstrap as the parity source-of-truth.
- **Keep** `resolve_http_replay_ignore_hosts` (module-scope, used by build.py).
- The in-kernel `_HTTP_REPLAY_TRACE_TEMPLATE` and its
  `test_trace_template_inlined_redactor_matches_host` test are superseded by the
  P5 proxy-stream trace path (this session).

## Sequencing for the cutover (future release)

1. ~~Land the transport behind the flag in a release (merge #173). Let it bake.~~
   **DONE** — #173 merged to `master` (`4fcbefb`, 2026-06-01); now baking.
2. Run the **cutover-gate checklist** below before the flip. The
   `allow_playback_repeats` mitmproxy regression test is **DONE**; the unit/smoke
   gates are committed; the e2e gates are a documented manual checklist for now
   (an automated harness is deferred — see the note after the checklist).
3. After the gates hold across that release cycle, flip the default to mitmproxy
   (keep `CLM_HTTP_REPLAY_TRANSPORT=vcrpy` selectable as rollback insurance).
4. **Only then**, in the same commit that removes the in-kernel vcrpy bootstrap,
   delete the moot/reimplemented workarounds above, remove the pin-guard, and
   relax the vcrpy pin. Keep the Option-F per-cell timeout and vcrpy-as-serializer.

## Cutover-gate checklist (run before the flip)

Run this once the transport has baked, immediately before flipping the default.
Gates 3/4/5/6(replay)/7(addon)/8/9 are **committed tests** — green CI is the
proof. Gates 1/2/6(record)/7(full-chain)/9(whole-build) need a real build or
network/$, so they are **manual** until the automated harness lands.

**Shared setup** (PowerShell; CLM must be a mitmproxy-aware build — ≥ the merged
`4fcbefb`):

```powershell
$env:CLM_MITMDUMP = "C:\Users\tc\.local\bin\mitmdump.exe"   # uv tool install mitmproxy --with vcrpy
$env:CLM_HTTP_REPLAY_TRANSPORT = "mitmproxy"
$Repro = "C:\Users\tc\Programming\Python\Tests\clm-bug-repros\issue-143-cassette-connection-pool-deadlock"
```

Optional tuning: `CLM_MITM_STARTUP_TIMEOUT` (seconds) overrides the proxy
readiness budget. The default is 30s; raise it only if `mitmdump did not become
ready` fires on a loaded host. The timeout error names this var, and
distinguishes an overloaded-but-still-starting proxy from a genuine crash
(issue #184).

| Gate | How to run | PASS criterion |
|---|---|---|
| 3 byte-identity, 4 tooling | `pytest tests/infrastructure/test_http_replay_mitm_cassette_format.py` | green (skip-safe w/o mitmdump) |
| 5 secret-strip, 6 JSON-match (replay), 7 addon-404, 8 routing | `pytest tests/infrastructure/test_http_replay_mitm.py tests/workers/notebook/test_http_replay_mitm_tag.py` | green |
| 8 host merge, 9 default-unchanged | `pytest tests/core/course_test.py -k mitmproxy_cassette_staging` and `tests/cli/test_build_command.py::TestMitmproxyTransportBindHost` + the no-op-inject tests | green |
| **1** deadlock-defeat + no-bypass | `CLM_HTTP_REPLAY_TRACE=1` build of the reproducer in replay, **with current cassettes** (see ⚠), `--ignore-cache --notebook-workers 16`, then `python scripts/analyze_http_replay_trace.py <trace_dir>` | build completes (no stall); analyzer **Bypassed: 0**, proxy **served == flows**, conn-bound ratio low |
| **2** HTTPS + CA on Windows | reference: `Tmp\p5_proxy_smoke.py` — start `MitmproxyManager(new-episodes)`, httpx `GET https://example.com` through `proxy_url` with a certifi+CA bundle | status 200; analyzer 0 bypass |
| **6** record "grows nothing" (real $) | reference: `Tmp\p5_gate6.py` — one real OpenRouter record (key from `.env`, never print/persist) | cassette secret-clean; no-op rebuild byte-identical |
| **7** full strict-miss→exit chain | rename a committed cassette, replay-build the reproducer | build exits non-zero **fast** with a `NotFoundError: clm_replay_miss` cell error (not a timeout) |
| **9** whole-build byte-identity | build a deck with the transport **unset** vs a pre-transport baseline; `python scripts/diff_build_outputs.py` | outputs identical |

⚠ **Gate 1 caveat (found this session):** the committed reproducer cassettes
predate the current build, so a straight replay **misses 5/116 requests** →
langchain's agent/retry layer re-issues the 404 → the cell stalls until the
Option-F per-cell timeout. Before running gate 1, **refresh the cassettes**
(record them once with a real key under the transport) so replay is a clean
0-miss hit; otherwise the "build completes" criterion is masked by the stale-
cassette stall, not a transport deadlock. (The harness *did* show 0 transport
bypass and no pool-exhaustion — the stall is purely the stale-cassette miss.)

**Deferred — automated cutover-gate harness.** A `scripts/run_cutover_gates.py`
that drives the reproducer build + records each e2e gate's pass/fail is
intentionally **not built now**: it would bit-rot across the bake cycle before
the flip, and the real-LLM/record gates need creds + spend at run time. Promote
the `Tmp\p5_proxy_smoke.py` / `p5_gate6.py` reference scripts into `scripts/`
when the flip is actually scheduled.
