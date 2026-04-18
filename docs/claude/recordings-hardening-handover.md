# Handover: Recordings App Hardening

> **Status**: Design decisions locked by user 2026-04-17. Phase 1
> shipped + user-confirmed 2026-04-17. Phase 2 implemented
> 2026-04-18 (pending user smoke test). Phase 3 is next.

> **Branch strategy update (2026-04-18)**: all four phases ship on
> the single branch `claude/recordings-hardening` with one PR at
> the end. Another agent is working directly on master, so
> per-phase merges would risk conflicts. The per-phase branches
> mentioned in §1 are superseded by this decision.

## 1. Feature Overview

**Name**: Recordings App Hardening

Four targeted phases that address concrete pain points reported after
the Recordings UX Redesign shipped (archived at
`docs/claude/recordings-ux-redesign-handover-archive.md`). The
underlying architecture (FastAPI + HTMX + SSE + threadpool backend
runner + JSON-on-disk state) is kept — each phase plugs a specific
gap rather than rewriting the stack.

The user's operational context: solo lectures, Windows, 15–30 minute
recordings processed via the Auphonic cloud backend. Courses are
growing large (10–15 % enabled today; more coming), so Phase 4's UI
refresh is scoped to scale for that growth.

**Related prior work**:

- `docs/claude/recordings-ux-redesign-handover-archive.md` — the
  five-phase redesign (shipped 2026-04-17). This hardening plan
  picks up issues observed in production after that shipped.
- `docs/claude/recordings-ux-followups-handover.md` — separate
  backlog of deferred items (Phases A–F in that doc). Phase A and
  Phase B of the follow-ups are folded into this plan as Phases 2
  and 3. Phases C/D/E/F stay deferred — they are larger initiatives
  with open UX questions that need to be tackled after this plan
  lands.

**Branch strategy**: one `claude/recordings-hardening-phase-<N>-<slug>`
branch per phase, merged to master on completion. Phases are
strictly sequential — do not overlap.

**Tests baseline**: 554 recordings tests green at start of Phase 1;
564 green after Phase 1 shipped (10 new tests added).

## 2. Design Decisions (Locked)

Inherited from the archived redesign:

- Windows-first; Python for tooling, not bash.
- "Part" stays as the UI term; "take" is secondary.
- Pydantic schema additions must have defaults — old `state.json`
  files must load.
- Each phase is independently shippable.

Phase-specific decisions locked 2026-04-17 by user:

- **Phase 1**: Option A — `JobManager.submit_async` with a proper
  placeholder + worker-thread swap, not fire-and-forget
  `asyncio.to_thread`. Rationale: keeps thread-lifecycle inside the
  manager where the existing poller already lives.
- **Phase 2**: `course_id == course_slug` for now. Distinct ID
  concept can come later if multiple courses need to share state.
- **Phase 3**: Backoff schedule 1 → 2 → 4 → 8 → 30s capped, reset
  on success. Ping interval 5s.
- **Phase 4**: Move to an opinionated design system (Tailwind or
  shadcn-style) rather than extending PicoCSS. User's courses are
  growing and the lecture list is becoming long; a more structured
  design system gives headroom for density + navigation improvements
  in later passes.
- **Sequencing**: strict 1 → 2 → 3 → 4. User wants all four shipped
  before the next big recording sessions.

## 3. Phase Breakdown

### Phase 1 — Non-blocking `/process` handler [SHIPPED 2026-04-17]

**Goal**: Clicking the Process button returns an HTMX response in
<200 ms regardless of backend. Previously the handler blocked for
the full Auphonic upload duration (10–30 s on a 30-minute recording)
because `JobManager.submit()` uploaded inline.

**Shipped summary**:

- `JobManager.submit_async()` at
  `src/clm/recordings/workflow/job_manager.py:239` — creates a
  QUEUED placeholder, publishes it, then dispatches
  `backend.submit` to a dedicated
  `ThreadPoolExecutor(max_workers=2, thread_name_prefix="clm-submit")`.
- `_PlaceholderSwappingContext` at
  `src/clm/recordings/workflow/job_manager.py:118` — wraps the
  default `JobContext` and rewrites `job.id` on every
  `ctx.report(...)` so the backend's progress events all carry the
  placeholder's id. A belt-and-braces id assignment after
  `backend.submit` returns covers the case where a backend returns
  a fresh job without reporting it first.
- `_run_backend_submit` is the worker-thread entry point; it
  catches any exception the backend fails to handle and transitions
  the placeholder to `FAILED` so the dashboard never shows a stuck
  QUEUED job.
- `shutdown()` disposes the submit pool alongside the poller.
- `/process` at `src/clm/recordings/web/routes.py:283` now calls
  `submit_async` instead of `submit`.
- 10 new tests: 7 in `tests/recordings/test_job_manager.py::TestSubmitAsync`,
  3 in `tests/recordings/test_web.py::TestProcessRoute`.

**User verification 2026-04-17**: manual smoke test confirmed —
clicking Process in the lectures page updates the UI immediately;
switching to the dashboard shows the job in uploading state.

**Known limitations carried into later phases**:

- Cancellation of an in-flight `submit_async` is best-effort: a
  `cancel()` can mark the placeholder `CANCELLED`, but if the
  worker thread is mid-upload, its next `ctx.report` or return
  value will overwrite that state. Hardening cancellation is a
  Phase 4 (or later) concern; for now the UI-side `hx-disabled-elt`
  pass in Phase 4 also reduces the surface for accidental double-
  submits.
- The watcher still calls the blocking `submit()` (not
  `submit_async`). The watcher runs on its own background thread
  so it does not block the event loop, but multiple discoveries
  serialize behind a long upload. Not fixed in Phase 1 to keep the
  diff focused; can be revisited if it becomes a real problem.

**Root cause (verified)**:

- `src/clm/recordings/web/routes.py:284` calls
  `job_manager.submit(path, options=...)` directly inside
  `async def process_file`.
- `JobManager.submit` at `src/clm/recordings/workflow/job_manager.py:203`
  calls `self._backend.submit(...)` synchronously.
- `AuphonicBackend.submit` at
  `src/clm/recordings/workflow/backends/auphonic.py:210`
  does create_production → streamed upload (0.0–0.4 of progress,
  line 254) → start_production. The upload blocks the event loop.

**Design** (Option A, locked):

1. Add `JobManager.submit_async(raw_path, options) -> ProcessingJob`:
   - Build a QUEUED placeholder synchronously with
     `backend_name`, `raw_path`, `final_path`, `relative_dir`
     filled in.
   - Persist + publish the placeholder immediately so the dashboard
     sees the new job on the next SSE tick.
   - Schedule the real `backend.submit(...)` on a dedicated
     `ThreadPoolExecutor(max_workers=2, thread_name_prefix="clm-submit")`
     managed by the `JobManager`.
   - Return the placeholder to the caller.

2. Introduce a `_PlaceholderSwappingContext` that wraps the default
   `JobContext` and rewrites `job.id` to the placeholder's id
   before delegating to `ctx.report(...)`. This guarantees every
   progress event the backend emits during the upload carries the
   placeholder's id, so the UI sees a single job evolving through
   QUEUED → UPLOADING → PROCESSING → COMPLETED.

3. When `backend.submit(...)` returns, copy the returned job's
   fields back onto the placeholder id (via a final `_store_job`
   + `_bus.publish`), and start the poller if the backend is async
   and the job is not terminal.

4. On exception from `backend.submit`, transition the placeholder to
   `FAILED` with the exception message and publish.

5. Wire `JobManager.shutdown(...)` to also shut down the submit
   pool cleanly.

**Rationale for the swapping-context approach**: the existing
`ProcessingBackend.submit` protocol creates its own `ProcessingJob`
inside the backend, with a fresh UUID. Preserving the placeholder's
id without changing the protocol means intercepting the reports. The
wrapper is ~15 lines and avoids a protocol change that would ripple
through every backend.

**Files**:

- `src/clm/recordings/workflow/job_manager.py` — new method +
  helper class + pool lifecycle.
- `src/clm/recordings/web/routes.py:266` — call `submit_async`
  instead of `submit`.
- `tests/recordings/test_job_manager.py` (or a new
  `test_job_manager_async.py`) — unit tests for the placeholder/swap
  behaviour.
- `tests/recordings/test_web.py` — integration test that `/process`
  returns quickly with a slow-mocked backend.

**Acceptance criteria**:

- `POST /process` returns in <200 ms even when the mocked backend's
  `submit()` sleeps for 2 s.
- SSE emits a `job` event for the placeholder (QUEUED) before the
  slow `submit()` call completes.
- All 554 existing recordings tests still pass.
- Cancelling a placeholder job (before the worker thread has
  promoted it past QUEUED) behaves sensibly — either cancel via the
  future or mark as CANCELLED on the next opportunity.

**Out of scope** (deferred to Phase 4 or later):

- Upload progress bar rendering beyond what the existing
  `progress` field already carries.
- Idempotency / debounce for double-clicks on Process — the Phase 4
  `hx-disabled-elt` pass covers the UI side.

### Phase 2 — Web state wiring (≡ follow-ups Phase A) [IMPLEMENTED 2026-04-18]

**Goal**: Fix the interleaving bug where bumping the Part number
during a running job leaves the previous take un-renamed. Complete
Phase 3 of the archived redesign by wiring
`CourseRecordingState` into `RecordingSession` on live runs.

**Shipped summary**:

- `CourseRecordingState.ensure_lecture` / `ensure_part` helpers at
  `src/clm/recordings/state.py:104` — idempotent get-or-create so
  the web layer can seed lectures on first `/arm` and parts on
  first recording without clobbering existing entries.
- `load_or_create(course_id)` module helper at
  `src/clm/recordings/state.py:463` — returns either the loaded
  state or a fresh empty one without persisting (no sentinel
  files for courses the user just browsed to).
- `ArmedDeck.lecture_id: str | None = None` at
  `src/clm/recordings/workflow/session.py:87` — threaded through
  `arm()` and `record()` so the session can talk to state.json
  without re-deriving identity from section+deck on every rename.
- `RecordingSession` gains `state_provider` + `on_state_mutation`
  callbacks. The web app provides a per-deck provider keyed by
  `course_slug`, and a persistence callback that calls
  `save_state`. Single-course CLI/test use still works via the
  legacy `state=` constructor parameter.
- `_sync_state_after_rename` at
  `src/clm/recordings/workflow/session.py:851` — after the rename
  thread places the new raw, it either calls `record_retake(...)`
  (when `_preserve_active_take` demoted files — i.e. a same-part
  retake) or `ensure_part(...)` (new part, including the
  first-recording case).
- **`part_number == 0` convention**: the UI's unsuffixed
  single-part mode maps to `state.part = 1`. This keeps state
  consistent with the on-disk cascade (`deck--RAW.mkv` →
  `deck (part 1)--RAW.mkv`) when the user later records part 2.
- `/arm` and `/record` in
  `src/clm/recordings/web/routes.py:90,213,253` — both routes now
  call `_resolve_lecture_id(section_name, deck_name)` (format:
  `"<section>::<deck>"`), get-or-load the course state via
  `app.state.recording_states`, and thread the lecture id into
  `session.arm(...)` / `session.record(...)`.
- 6 new tests added:
  - `tests/recordings/test_session.py::TestStateWiring` —
    interleaving bug repro (`test_interleaving_scenario_tracks_both_parts`),
    `on_state_mutation` callback, multi-course `state_provider`.
  - `tests/recordings/test_web.py::TestArmDisarm` — lecture id
    resolution, state cache seeding, state cache reuse.
- **Tests**: 570 recordings tests green (was 564); full fast suite
  (3400) green; ruff + mypy clean on changed files.

**Design** (locked, for reference):

- **Lecture-ID resolution**: Option 1 — web-app resolves
  `(section_name, deck_name) → lecture_id` via the `Course` spec and
  passes it into `ArmedDeck` at `arm()` time.
- **State ownership**: `CourseRecordingState` cached per-course on
  `app.state.recording_states: dict[str, CourseRecordingState]`.
  Keyed by `course_slug`; loaded lazily on first `arm()` for a
  given course. Persisted on each mutation (already the pattern).
- **`course_id == course_slug`**.
- **record_retake trigger**: at retake pre-move time inside
  `session.py`, before the new file lands, so the state update and
  the filesystem move happen together.

**Known limitations carried into later phases**:

- In-flight jobs' `raw_path` is not rewritten when the cascade
  renames a file underneath them. The dashboard's deck-to-job
  matching works on parsed deck names (stable across the
  cascade), so the job keeps showing up on the correct row; but
  the final output path the backend writes is derived from the
  stale `raw_path` and may need a later follow-up. Deferred to
  Phase 4 polish or a dedicated follow-up.
- `lecture_id` resolution is a simple `"<section>::<deck>"`
  concatenation rather than a Course-spec walk. Good enough
  because the id is only used internally; can swap in a spec
  walk later if we want to key state by a more stable field
  (e.g. notebook `number_in_section`).

**Acceptance criteria** (met):

- Scenario: record part 0 → start processing → change part to 2 →
  start recording → stop. Result: original file renamed to carry
  `(part 1)`; `state.json.takes[]` reflects both parts; processing
  of the original part does not report as failed on the dashboard.
- Old-schema `state.json` still loads.
- All existing recordings tests still pass.

### Phase 3 — OBS auto-reconnect + connection-aware buttons (≡ follow-ups Phase B) [TODO]

**Goal**: Record / Arm buttons disable when OBS is not connected,
OBS disconnects surface within 5 s, and OBS reconnects without
user action.

**Design** (locked):

- **Watchdog**: background thread in `ObsClient` pings
  `get_record_status` every 5 s when `auto_reconnect=True`. Also
  checks that the obsws-python event thread is alive (catches
  silent-death cases).
- **Backoff**: 1 → 2 → 4 → 8 → 30s capped, reset on success.
- **SSE event**: single `obs_state` event with
  payload `{"state": "connected"|"disconnected"|"reconnecting"}`.
  Matches the `job` event's single-topic-plus-payload pattern.
- **UI**:
  - Record and Arm buttons gain
    `{% if not snapshot.obs_connected %}disabled title="OBS not connected"{% endif %}`.
  - Status partial shows a `reconnecting` badge with a spinner
    during backoff.
- **Server-side guard**: `/record` and `/arm` return 409 Conflict
  when OBS is not connected — defence in depth covering the brief
  window between watchdog ticks.

**Files**:

- `src/clm/recordings/workflow/obs.py` — watchdog, reconnect loop,
  event emission.
- `src/clm/recordings/web/app.py` — subscribe to `obs_state`, push
  to SSE queue.
- `src/clm/recordings/web/templates/lectures.html:91,101` — disable
  attribute on Record/Arm buttons.
- `src/clm/recordings/web/templates/partials/status.html` —
  reconnecting badge.
- `src/clm/recordings/web/routes.py` — 409 guard in `/record` and
  `/arm`.
- `tests/recordings/test_obs.py` — watchdog + reconnect.
- `tests/recordings/test_web.py` — button disabled rendering +
  409 behaviour.

**Acceptance criteria**:

- Kill OBS → within 5 s the dashboard shows a disconnected badge
  and Record/Arm buttons are disabled.
- Restart OBS → dashboard shows connected badge on next watchdog
  tick; buttons re-enable.
- Record/Arm POST with OBS disconnected returns 409 and does not
  arm the session.
- Connection loss does not crash the session or lose armed state.

### Phase 4 — UI feedback + aesthetic refresh [TODO]

**Goal**: Make the dashboard feel immediate, surface errors that
previously went only to the log, and move the visual design to an
opinionated system that will scale for larger courses.

**Design** (locked):

- **Design system**: replace PicoCSS with either Tailwind (CDN
  build) or a shadcn-style static system. Decision deferred to
  Phase 4 start — evaluate both briefly before committing. Keep
  the HTMX + SSE wiring unchanged.
- **Optimistic UI**: every action that can exceed 200 ms gains
  `hx-disabled-elt="find button"` and an `hx-on::before-request`
  handler that swaps in a spinner / "working..." affordance.
- **Error toasts**: new `notice` SSE event; dismissible toast
  region in the dashboard header. Any route failure pushes a
  notice rather than logging silently.
- **Keyboard affordances**: Enter in the Part-number field triggers
  Record; Escape disarms.
- **Layout density**: tighter table typography, stronger armed-row
  treatment, distinct status-badge palette for
  pending/processing/processed/failed.

**Scope guard**: this phase does **not** restructure the
deck/part/take data layout. The per-part chips (follow-up Phase C)
and restore-take UI (follow-up Phase D) stay deferred. Phase 4 is
polish + design-system migration only.

**Files**:

- `src/clm/recordings/web/templates/base.html` — new CSS system.
- `src/clm/recordings/web/templates/**/*.html` — migrate styles.
- `src/clm/recordings/web/routes.py` — `notice` SSE topic.
- `src/clm/recordings/web/app.py` — notice event wiring.
- Static assets under `src/clm/recordings/web/static/` (new
  directory if needed).
- `tests/recordings/test_web.py` — disabled-button and
  notice-event assertions.

**Acceptance criteria**:

- Every action gives immediate visual feedback (spinner or
  disabled-state) within one frame.
- A failed route produces a dismissible toast, not just a log
  line.
- Dashboard renders cleanly at >20 lectures per section without
  horizontal scroll.
- Dev-server smoke test of the full Record → Process loop before
  marking the phase complete (per CLAUDE.md UI rule).

## 4. Current Status

- **Shipped (merged to master)**: Phase 1 (`submit_async`
  non-blocking `/process`). User-verified 2026-04-17.
- **Implemented on branch** (`claude/recordings-hardening`,
  pending user smoke test + later PR): Phase 2 — web state wiring
  on 2026-04-18.
- **Next up**: Phase 3 — OBS auto-reconnect + connection-aware
  buttons.
- **Tests**: 570 recordings tests green (up from 564); full fast
  suite (3400 tests) green; ruff + mypy clean on changed files.

## 5. Next Steps

All design questions for Phase 2 are locked (see §3 Phase 2). A
fresh session can go straight to implementation. The order of
operations below assumes a clean slate on the hardening branch.

**Phase 2 entry checklist** (for the new session):

1. Read this handover top to bottom (it's the source of truth for
   design decisions — CLAUDE.md and the archived redesign handover
   cover everything else).
2. Read `src/clm/recordings/state.py` to understand
   `CourseRecordingState`, `record_retake`, and
   `rename_recording_paths` — these are the APIs Phase 2 wires up.
3. Read `src/clm/recordings/workflow/session.py` — identify the
   retake pre-move branch (this is where `state.record_retake(...)`
   must be called before files land).
4. Read `src/clm/recordings/web/app.py` — identify where `Course`
   is loaded and where the session is constructed; this is where
   the `app.state.recording_states` cache lives.
5. Read `src/clm/recordings/web/routes.py` `/arm` and `/record`
   handlers — these need to resolve `lecture_id` from the Course
   spec and pass it into `ArmedDeck`.
6. Write tests first for the bug scenario (see Phase 2 acceptance
   criteria in §3).
7. Implement: Course-to-lecture-id resolver → state cache in
   `app.py` → `ArmedDeck.lecture_id` field → session
   `record_retake` call at pre-move.
8. Run `tests/recordings/` fast, then full fast suite. Green is
   the bar.
9. Manual smoke: replay the original bug scenario (record part 0 →
   start processing → bump part to 2 → record → stop) and confirm
   `(part 1)` rename and `state.json` both update correctly.
10. Update this handover: mark Phase 2 as shipped with a summary
    block like Phase 1's, update §4 Current Status, hand off to the
    user for Phase 3.

**Subsequent phases** (no session-boundary checklist — the user
may decide to overlap or re-order):

- Phase 3: connection-aware UI + OBS auto-reconnect.
- Phase 4: start with a brief design-system evaluation (Tailwind
  CDN vs. shadcn-style static) and capture the choice in a short
  note appended to §3 Phase 4 before coding.

## 6. Key Files & Architecture

See the archived redesign handover §6 for the full inventory.
New touch-points unique to this plan:

| File | Role | Phase |
|---|---|---|
| `src/clm/recordings/workflow/job_manager.py` | New `submit_async` + swap context (shipped) | 1 |
| `src/clm/recordings/web/app.py` | Recording-state cache (Phase 2), obs_state subscription (Phase 3), notice wiring (Phase 4) | 2, 3, 4 |
| `src/clm/recordings/workflow/obs.py` | Watchdog + reconnect | 3 |
| `src/clm/recordings/web/templates/base.html` *(rewrite)* | Design-system migration | 4 |
| `src/clm/recordings/web/static/` *(new)* | Custom stylesheet / bundled assets | 4 |

## 7. Testing Approach

Same strategy as the archived feature: unit per module, integration
at the web-route layer, mock OBS and Auphonic HTTP. Each phase adds
targeted tests — details under the per-phase acceptance criteria.

The fast suite (`pytest`) must stay green at every phase boundary.
Docker-marked tests are not required locally — CI runs them.

## 8. Session Notes

### User preferences carried forward

- Python over bash for scripts (CLM is Windows-first).
- Each phase independently shippable — no batching.
- Terse communication, no trailing summaries.
- Retakes must preserve previously-processed artifacts (Phase 2
  extends this contract to include on-disk state.json entries).

### Why these four phases, not the handover's A–F

The follow-ups handover (`recordings-ux-followups-handover.md`)
lists six items (A–F). This hardening plan subsumes A (Phase 2)
and B (Phase 3) because they directly address the user's reported
bugs, and adds two new phases (1 and 4) for the
process-button lag and the design-system migration. Follow-ups
C/D/E/F stay deferred and remain tracked in the follow-ups handover
for a later pass.

---

**Last updated**: 2026-04-18 (Phase 2 implemented on branch)
**Next action**: User smoke test of Phase 2 on the hardening branch,
then new session picks up Phase 3 — OBS auto-reconnect +
connection-aware buttons.
