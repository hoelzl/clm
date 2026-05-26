# HTTP-Replay Trace Harness — Phase A Design Draft

**Status:** Design draft for review (2026-05-26). Not yet implemented.
**Purpose:** Add forensic instrumentation to CLM's HTTP-replay subsystem so
we can *measure* what's escaping cassettes, not guess from symptoms. This
is the diagnostic foundation for the Phase B architectural decision (keep
vcrpy / switch library / change abstraction).
**Companion:** [`http-replay.md`](http-replay.md) (existing architecture);
[`../issue-129-vcrpy-force-reset-investigation.md`](../issue-129-vcrpy-force-reset-investigation.md).

## Goal

Produce a complete, post-build forensic dataset that answers, for every
LLM/HTTP call in a build:

1. Was it intercepted by vcrpy at all? (vcr-level evidence)
2. Did it hit the cassette, miss and record, or raise?
3. Did it produce a real TCP connection to a non-loopback host?
4. If both (1)/(2) succeeded but (3) also happened — was a `force_reset`
   window open on another thread when the connection was created?

The three streams are independent on purpose: ground truth at the socket
layer, vcr's own view, and the host's cassette-lifecycle decisions. We
cross-reference them in analysis. **A socket connect with no matching vcr
interception event is a definitive bypass.**

## Non-goals

- No behavior change. The harness is off by default; when off, zero code
  paths differ from today.
- No alternative cassette format, no new fix for any known bug. This is
  pure observation.
- Not a permanent feature. Once we have the Phase B data, the trace code
  can stay as an opt-in debugging aid or be retired entirely.

## Three telemetry streams

### Stream 1 — Socket (ground truth)

**Where:** worker kernel process, injected by the bootstrap template.

**Mechanism:** `sys.addaudithook` subscribing to `socket.connect`
(available since Python 3.8). Fires for every `socket.socket.connect()`
call regardless of which Python HTTP library issued it. Also subscribe
to `ssl.wrap_socket` / `_ssl.sslsocket` events to capture TLS
`server_hostname` (useful when the underlying TCP is to a load balancer
and the host header reveals the actual provider).

**Filter:** log all connects; analysis filters to non-loopback (`127.*`,
`::1`, `localhost`) at read time. We log loopback too because the kernel
talks to Jupyter over loopback and seeing those events helps confirm the
hook is firing.

**Event types:**
- `socket.connect` — `{addr_family, host, port, fd}`
- `ssl.handshake_start` — `{server_hostname}` (when known)

**Why it works:** vcrpy patches *above* the socket layer. If vcr
intercepts, no real `socket.connect` happens. So a connect to
`api.openai.com:443` with no preceding vcr event is the bug we're
hunting.

### Stream 2 — vcrpy (the library's view)

**Where:** worker kernel process, also via the bootstrap template (after
vcr is imported).

**Mechanism:** non-invasive function wrappers on a small set of vcr
internals:

- `vcr.patch.force_reset` — wrap as a context manager that logs
  enter/exit with thread ID and a monotonic timestamp. Critical for
  diagnosing issue #129-style races.
- `Cassette.append` — already wrapped by `_clm_eager_append`; add a
  trace event there.
- `Cassette.play_response` — wrap to log every served response (cassette
  hit).
- `Cassette.can_play_response_for` — wrap to log decisions (especially
  the `False` returns that lead to `CannotOverwriteExistingCassetteException`).
- The `clm_json_body` matcher — emit an event per match attempt with
  the comparison outcome (matched / json-parse-failed / bytes-differ).

**Event types:**
- `vcr.force_reset.enter` / `vcr.force_reset.exit` — `{tid, mono_ts}`
- `vcr.cassette.hit` — `{method, uri, body_sha256}`
- `vcr.cassette.append` — `{method, uri, body_sha256, status}`
- `vcr.cassette.miss` — `{method, uri, body_sha256, why}` (`why ∈
  {no-match, mode-blocks-record, ...}`)
- `vcr.matcher.compare` — `{method, uri, outcome}` (sampled / verbose
  flag controlled; defaults to outcome-only to keep volume sane)

**Why it works:** these are the exact points where vcr's own logic
decides what happens. Combining `force_reset.enter` timestamps with
Stream 1's `socket.connect` timestamps on different threads is the
clinching evidence the issue-129 investigation built by hand — but now
captured automatically for every build.

### Stream 3 — Cassette (host-side lifecycle)

**Where:** host process (CLM main), inside `http_replay_cassette.py` and
`Course._sweep_orphan_cassette_staging_files`.

**Mechanism:** plain function-entry/exit logging at the points where
cassette state changes on disk.

**Event types:**
- `cassette.seed` — `{canonical, staging, seeded_interaction_count}`
- `cassette.completion_marker.write` — `{staging, ts}`
- `cassette.merge.start` — `{canonical, n_staging_files}`
- `cassette.merge.decision` — `{staging, decision, marker_present,
  sweep_orphans}` where `decision ∈ {folded, discarded_orphan,
  skipped_concurrent}`
- `cassette.merge.dedup` — `{kept_fingerprint, dropped_n}` per
  dedup outcome
- `cassette.merge.write` — `{canonical, final_interaction_count}`
- `cassette.sweep.orphan_removed` — `{staging, age_seconds}`

**Why it works:** the cassette-lifecycle bugs (issue #115 partial chains,
the pre-#87 race in seeding) are invisible at the HTTP layer. This
stream catches them.

## File layout

```
$CLM_HTTP_REPLAY_TRACE_DIR/                 # default: ./clm-http-replay-traces/
  └── 2026-05-26T16-39-10_<short-uuid>/     # one dir per build invocation
      ├── manifest.json                     # build metadata (cmd, mode, course)
      ├── host.jsonl                        # Stream 3 events (cassette)
      ├── worker-<pid>.jsonl                # Streams 1+2 per worker process
      ├── worker-<pid>.jsonl
      └── ...
```

**Per-worker files** (not one shared file) so workers never contend on
the write path. Analysis merges them by timestamp.

**Location** — explicitly outside `output-dir` because the build sweep
treats output-dir as exclusively CLM's
([`project_output_dir_exclusively_clm.md`](../../../memory/project_output_dir_exclusively_clm.md)).
Default to a sibling of the working directory; configurable.

## Event schema (JSONL)

One JSON object per line:

```json
{
  "ts_mono": 123.456789,
  "ts_wall": "2026-05-26T16:39:23.123456+00:00",
  "pid": 12345,
  "tid": 67890,
  "stream": "socket" | "vcr" | "cassette",
  "event": "socket.connect",
  "data": { ... event-type-specific ... }
}
```

- `ts_mono` is `time.monotonic()`. Cross-process comparison requires the
  wall-clock; `ts_mono` is for intra-process ordering. We need both
  because `force_reset` races measure in milliseconds and wall-clock
  resolution + NTP jitter would obscure them.
- `tid` is `threading.get_ident()`. Critical for issue-129 diagnosis.
- `data` is free-form per event type; documented inline in the
  trace-writer source.

Body payloads **are** logged, but in a redacted/truncated form designed
to surface whitespace issues (CR/LF differences are a known source of
matcher misses):

- `length`: total byte length
- `sha256`: hex-truncated `sha256(body)[:16]` for fingerprinting
- `head`: `repr(body[:N])` (default `N=2048`), so `\r\n` shows as `\\r\\n`
  rather than collapsing in display
- `tail`: `repr(body[-N:])` when `len(body) > 2*N`, omitted otherwise
- Truncation marker `"...<TRUNCATED M bytes>..."` between head and tail
  when applicable

Cap is configurable via `CLM_HTTP_REPLAY_TRACE_MAX_BODY_BYTES` (default
2048 per side). Course materials are not expected to contain secrets;
`filter_headers` already redacts auth at the cassette layer, and prompts
are part of the lecture content.

## Wiring (env vars + host→worker propagation)

**Enable:**
- `CLM_HTTP_REPLAY_TRACE=1` enables the harness globally.
- `CLM_HTTP_REPLAY_TRACE_DIR=<path>` overrides the default trace
  directory.
- `CLM_HTTP_REPLAY_TRACE_VERBOSE=1` (optional) enables per-matcher-compare
  events for deep dives; off by default to keep volume manageable.

**Host propagation:** in `build.py`, when `CLM_HTTP_REPLAY_TRACE` is set,
the host (a) creates the per-build trace directory and writes
`manifest.json`, (b) passes the trace directory path to workers via the
existing `NotebookPayload` extension mechanism (new optional field
`trace_dir`).

**Worker bootstrap:** the bootstrap template gains a conditional block —
when `trace_dir` is non-empty in the format args, an additional
**`_HTTP_REPLAY_TRACE_TEMPLATE`** is concatenated before the existing
bootstrap body. This template inlines a minimal JSONL writer (cannot
import CLM modules from the kernel) and installs the audit hook and vcr
wrappers.

**Direct vs Docker workers:** for direct workers, the trace dir is a
host path the kernel can write to. For Docker workers, the trace dir
needs to be bind-mounted (similar to how cassette paths are handled
today). Add to the Docker executor's mount list when tracing is
enabled.

## Analysis

A new script: `scripts/analyze_http_replay_trace.py`.

```
uv run python scripts/analyze_http_replay_trace.py <trace_dir>
```

Outputs a structured report:

```
=== Build summary ===
Workers: 3, Wall time: 47.3s
Socket connects: 47 total
  ├── Loopback (kernel/jupyter): 12
  └── Remote: 35
      ├── Matched a vcr event: 30   ✓ recorded or replayed normally
      └── Bypassed: 5               ✗ no vcr event near connect

=== Cassette health ===
Hits: 28, Appends: 7, Misses raising CannotOverwrite: 0

=== Bypass forensics ===
  [PID 4567, TID 8901] socket.connect ('api.openai.com', 443) at ts_wall=...
     Nearest vcr event on another thread: force_reset.enter (TID 8902),
     opened 2.4ms before this connect, still open at connect time.
     ⇒ Likely cause: vcrpy force_reset race (issue #129)
  [PID 4567, ...] socket.connect ('smith.langchain.com', 443) ...
     No nearby vcr event of any kind.
     ⇒ Likely cause: bypass before vcr stub installed
```

`--format=json` for machine-readable output (feeds dashboards / CI
gating later if we want).

## Decisions (settled 2026-05-26)

1. **Trace-dir scoping:** per `clm build` invocation. One timestamped
   directory holds all topic builds from a single invocation.
2. **Redaction:** body content is *kept* (head + tail + length + sha,
   repr-escaped to surface CR/LF). Course materials are not expected to
   contain secrets; the goal is debuggability over conservative
   redaction. Auth headers are already stripped by `filter_headers` at
   the cassette layer; trace events log header *presence* only.
3. **Bootstrap install order:** trace wrappers installed *after* the
   issue-129 `_clm_scoped_reset_patchers` swap, so they wrap the scoped
   `force_reset` rather than the upstream one. This is forensic tooling,
   not long-term production code — order is enforced by template
   ordering rather than runtime checks.
4. **Async paths:** symmetric wrappers for
   `AsyncConnectionPool.handle_async_request` and async cassette
   methods. Mechanical doubling.
5. **Matcher-compare volume:** outcome-only by default; verbose mode
   (per-compare with both bodies) behind
   `CLM_HTTP_REPLAY_TRACE_VERBOSE=1`.

## Touch list (files to modify when we implement)

| Path | Change |
|---|---|
| `src/clm/workers/notebook/notebook_processor.py` | Add `_HTTP_REPLAY_TRACE_TEMPLATE`; conditional concat with `_HTTP_REPLAY_BOOTSTRAP_TEMPLATE`; new format-arg `trace_dir` (empty string when disabled). |
| `src/clm/workers/notebook/http_replay_cassette.py` | Instrument `seed_staging_from_canonical`, `merge_staging_into_canonical`, `write_completion_marker` with `cassette.*` events. |
| `src/clm/core/course.py` | Instrument `_sweep_orphan_cassette_staging_files` with `cassette.sweep.*` events. |
| `src/clm/core/notebook_classes.py` | Add optional `trace_dir: str = ""` field on `NotebookPayload`. |
| `src/clm/cli/build.py` (or wherever HTTP-replay mode resolves) | Read `CLM_HTTP_REPLAY_TRACE`, create trace dir + manifest, propagate to payload. |
| `src/clm/infrastructure/worker_executor.py` | Docker path: bind-mount trace dir when set. Direct path: passes through naturally. |
| `src/clm/workers/notebook/http_replay_trace.py` *(new)* | Host-side JSONL writer + manifest helpers. |
| `scripts/analyze_http_replay_trace.py` *(new)* | Post-build analysis tool. |
| `tests/workers/notebook/test_http_replay_trace.py` *(new)* | Writer thread-safety; bootstrap template includes trace block iff flag set; manifest schema. |

## What we run after this lands

1. `CLM_HTTP_REPLAY_TRACE=1 clm build` against the AZAV ML course
   (the canonical reproducer), `--http-replay=replay` first, then
   `--http-replay=new-episodes --ignore-cache`.
2. `uv run python scripts/analyze_http_replay_trace.py <trace_dir>`.
3. The output drives Phase B: if bypasses cluster around `force_reset`
   races, the issue-129 mitigation is incomplete and we either fix it
   harder or escape vcrpy. If bypasses are elsewhere, we have a new bug
   to chase. If there are no bypasses, we burn down workarounds with
   confidence.

---

**Next step before implementation:** review and iterate on this design.
Specifically: is the per-worker trace file layout right; is the event
schema rich enough; are there other vcr internals worth instrumenting
that I've missed?
