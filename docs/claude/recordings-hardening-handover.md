# Handover: Recordings App Hardening

> **Status**: Design decisions locked by user 2026-04-17. Phase 1
> shipped + user-confirmed 2026-04-17. Phases 2, 3, and 4 implemented
> on branch `claude/recordings-hardening`, user-verified via Windows
> smoke test on 2026-04-19. All four phases ready for a single PR.

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

### Phase 3 — OBS auto-reconnect + connection-aware buttons (≡ follow-ups Phase B) [IMPLEMENTED 2026-04-18]

**Goal**: Record / Arm buttons disable when OBS is not connected,
OBS disconnects surface within 5 s, and OBS reconnects without
user action.

**Shipped summary**:

- `ObsClient` gains an `auto_reconnect` kwarg plus a watchdog
  background thread at
  `src/clm/recordings/workflow/obs.py:326` (`_start_watchdog` /
  `_watchdog_run`). The watchdog pings `get_record_status` every
  `watchdog_interval` seconds (default 5 s) and also sanity-checks
  that the obsws-python event-receive thread is still alive.
- `_enter_reconnect_loop` closes stale clients, publishes the
  `reconnecting` state, and retries `_connect_clients` with the
  1 → 2 → 4 → 8 → 30 s backoff schedule (configurable via the
  `backoff_schedule` kwarg; tests monkeypatch it to <1 s delays).
  On the first successful reconnect, state transitions back to
  `connected` and the watchdog resumes its probe loop.
- High-level state is exposed via `ObsClient.connection_state`
  (`"connected"`/`"disconnected"`/`"reconnecting"`) with
  `on_state_change(callback)` for subscribers. User-initiated
  `disconnect()` stops the watchdog — no auto-reconnect after an
  explicit disconnect.
- `create_app` wires `obs = ObsClient(..., auto_reconnect=True)`
  and registers an SSE forwarder that pushes `obs:<state>` events
  (`obs:connected`, `obs:disconnected`, `obs:reconnecting`). The
  existing `_sse_event_name_for` helper classifies them onto the
  `status` channel so the Status panel refreshes naturally. See
  `src/clm/recordings/web/app.py:147`.
- `SessionSnapshot` picks up `obs_state: str` alongside
  `obs_connected`, and the `/status` JSON serializer at
  `src/clm/recordings/web/routes.py:599` exposes it to API
  consumers.
- UI:
  - Lectures page shows an amber "OBS not connected" /
    "OBS reconnecting…" banner above the sections list and adds
    `disabled title="OBS not connected"` on every Record / Arm
    button when `snapshot.obs_connected` is false
    (`src/clm/recordings/web/templates/lectures.html:6,96,106`).
  - Status partial renders a new `reconnecting…` badge (amber)
    with a "Cancel" button that maps to `/obs/disconnect`, so the
    user can bail on an attached reconnect attempt
    (`src/clm/recordings/web/templates/partials/status.html:18`).
  - `badge-reconnecting` + `.obs-banner` CSS added to
    `templates/base.html`.
- Server-side guard: `/arm` and `/record` return 409 Conflict
  with detail `"OBS not connected"` when the request lands in the
  brief window between watchdog ticks
  (`src/clm/recordings/web/routes.py:214,261`).
- **SSE infrastructure fix** (surfaced during the user smoke test
  on 2026-04-18; folded into Phase 3 because Phase 3's watchdog
  was the first feature that genuinely *required* live updates to
  work end-to-end):
  - Replaced the single shared `sse_queue` with a list of
    per-subscriber queues
    (`app.state.sse_subscribers: list[asyncio.Queue[str]]`). The
    shared queue was round-robining events across tabs, so every
    new `/events` subscriber ate events the others were waiting
    for — Dashboard and Lectures tabs were cannibalising each
    other's updates.
  - Dropped `htmx-ext-sse`. The extension dispatches its custom
    events on the element carrying `sse-connect`, and events
    bubble *up* the DOM — so `hx-trigger="sse:status"` on a
    descendant never fired. `from:closest [sse-connect]` did not
    resolve reliably either in htmx 2.0.4. Replaced with a small
    vanilla-JS bridge in `base.html` that opens a single
    `EventSource('/events')` per page and calls
    `htmx.trigger(el, 'sse:<event>')` on every element carrying
    `data-sse-refresh="<event> [<event> …]"`. This gives us full
    control over which elements get notified for which events,
    with no reliance on how the extension routes events.
  - Lectures page gained a hidden
    `data-sse-refresh="status"` refresher that re-fetches
    `/lectures` and `hx-select`s the `#lectures-dynamic` subtree,
    so the OBS banner + Record/Arm disabled state update live
    without disrupting the language toggle or the outer page
    frame.
- 21 new tests added:
  - `tests/recordings/test_obs.py::TestObsClientConnectionState` (5)
    — initial state, connect/disconnect transitions, callback fan-
    out, no-op dedupe.
  - `tests/recordings/test_obs.py::TestObsClientWatchdog` (7) —
    auto_reconnect off vs on, disconnect stops watchdog, probe
    failure triggers reconnect cycle back to `connected`, backoff
    iterator caps, dead event-thread detection, stop mid-reconnect.
  - `tests/recordings/test_web.py::TestObsConnectionGuard` (3) —
    `/arm` and `/record` return 409 when disconnected; happy path
    still works when connected.
  - `tests/recordings/test_web.py::TestObsStateRendering` (5) —
    Record button gets `disabled` + banner when OBS is down;
    reconnecting badge renders; on_state_change callback pushes
    `obs:<state>` to every subscriber; `/status` JSON exposes
    `obs_state`.
  - `tests/recordings/test_web.py::TestSSEEvents::`
    `test_sse_events_fan_out_to_every_subscriber` (1) — two
    subscriber queues both receive the same event, guarding
    against any future regression of the shared-queue bug.
- **Tests**: 591 recordings tests green (was 570); full fast
  suite (3421) green; ruff + mypy clean on changed files.

**Design** (locked, for reference):

- **Watchdog**: background thread in `ObsClient` pings
  `get_record_status` every 5 s when `auto_reconnect=True`. Also
  checks that the obsws-python event thread is alive (catches
  silent-death cases).
- **Backoff**: 1 → 2 → 4 → 8 → 30s capped, reset on success.
- **SSE event**: `obs:<state>` payloads with the three allowed
  values routed onto the existing `status` channel — keeps the
  wire format compatible with the dashboard's HTMX `sse-swap="status"`
  wiring that was already in place.
- **User-initiated disconnect preserved**: `ObsClient.disconnect()`
  stops the watchdog, so clicking the dashboard's Disconnect button
  does not immediately trigger a reconnect loop. The user must
  click Connect (or restart the server) to re-enable the watchdog.

**Known limitations carried into later phases**:

- The watchdog's `evt.thread_recv` liveness check uses
  `getattr(evt, attr, None)` across a small list of candidate
  attribute names (`thread_recv`, `_thread_recv`, `thread`). If
  `obsws-python` renames the attribute in a future release, the
  check silently degrades to "probe only" — still safe, just less
  thorough. Swap in a proper introspection path if the library
  exposes one.
- Reconnect does not attempt to replay missed `RecordStateChanged`
  events that fired between disconnect and reconnect. If OBS
  flipped from recording → stopped while we were disconnected, the
  session state machine will be out of sync until the next manual
  action. Acceptable for solo use; revisit if users hit it in
  practice.

### Phase 4 — UI feedback + aesthetic refresh [IMPLEMENTED 2026-04-19]

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

**Phase 2 cleanups folded in** (surfaced by the 2026-04-18 smoke
test; user decided to batch them with Phase 4 rather than open a
dedicated follow-up):

- **In-flight raw_path rewrite on cascade rename**. When a new part
  is recorded and the existing unsuffixed raw file cascade-renames
  to `(part 1)`, any job that has already captured the old path
  (raw_path + final_path) keeps writing the Auphonic output to the
  stale stem — see `src/clm/recordings/workflow/session.py`
  `_sync_state_after_rename` (around line 851) for the trigger
  point. Rewrite the matching job's `raw_path` and `final_path`
  at that moment so the final lands at the renamed stem. Touches
  `JobManager` (needs a lookup-by-old-path helper) and the
  session's rename thread. Add a regression test that replays the
  interleaving scenario with a slow mocked backend and asserts the
  final path ends in `(part 1)`.
- **`deck_status.final_parts` double-counts companions**. In
  `src/clm/recordings/workflow/deck_status.py:91-98`, the `final/`
  scan iterates every file, so Auphonic's `.edl` companion beside
  each `.mp4` inflates `final_parts` (e.g. `[0, 0]`). Filter by
  the canonical video extension set
  (`VIDEO_EXTENSIONS` from `clm.recordings.processing.batch`) the
  same way `_scan_active_take_files` in `session.py` already does.
  Add a unit test with a deck that has both `.mp4` and `.edl` in
  `final/` and assert `final_parts == [0]`.

**Files**:

- `src/clm/recordings/web/templates/base.html` — new CSS system.
- `src/clm/recordings/web/templates/**/*.html` — migrate styles.
- `src/clm/recordings/web/routes.py` — `notice` SSE topic.
- `src/clm/recordings/web/app.py` — notice event wiring.
- Static assets under `src/clm/recordings/web/static/` (new
  directory if needed).
- `src/clm/recordings/workflow/session.py` — rewrite in-flight
  job paths on cascade rename.
- `src/clm/recordings/workflow/job_manager.py` — helper to
  locate a job by its raw_path for the rewrite above.
- `src/clm/recordings/workflow/deck_status.py` — filter `final/`
  scan by video extension.
- `tests/recordings/test_web.py` — disabled-button and
  notice-event assertions.
- `tests/recordings/test_session.py` — in-flight rename
  rewrite regression.
- `tests/recordings/test_deck_status.py` — `.edl` companion
  double-count regression.

**Acceptance criteria**:

- Every action gives immediate visual feedback (spinner or
  disabled-state) within one frame.
- A failed route produces a dismissible toast, not just a log
  line.
- Dashboard renders cleanly at >20 lectures per section without
  horizontal scroll.
- Dev-server smoke test of the full Record → Process loop before
  marking the phase complete (per CLAUDE.md UI rule).
- Interleaving scenario (record Part 0 → Process → record Part 2)
  produces a final output at `... (part 1).mp4`, not the
  unsuffixed stem.
- Lectures list shows `done: 1; raw: 2` (not `done: 0, 0;
  raw: 1, 2`) when one part has processed and one is pending.

**Post-implementation notes (2026-04-19)**:

- **Design system choice**: went with a handcrafted CSS file
  (~420 lines, CSS custom properties) rather than Tailwind. The
  dashboard only has six templates and no Node toolchain, so a
  bundled `app.css` + local `htmx.min.js` beats a CDN for the
  "record without internet" scenario the user flagged. Auphonic
  obviously still requires internet but the rest of the dashboard
  keeps working offline.
- **SSE reload gap**: the biggest behavioural bug surfaced by the
  smoke test was "the recording badge sometimes doesn't show up".
  Root cause was the reload window: after `/record` responds with
  `HX-Redirect: /lectures`, the old SSE connection closes, OBS
  fires `RecordStateChanged` while zero subscribers are attached,
  the event hits no queues and is lost. Fix: seed every new
  subscriber's queue with a `state_changed` event in
  `routes.py::events`, so the client refetches `/lectures`
  immediately on reconnect and catches up.
- **Part-number clobber on swap**: SSE-driven refreshes of the
  lectures page outerHTML-swap `#lectures-dynamic`, which replaces
  the `<input name="part_number">` with a fresh `value="0"` and
  wipes whatever the user had just typed. Addressed by snapshotting
  typed values on `htmx:beforeSwap` and restoring them on
  `htmx:afterSwap`, keyed by each row's `deck_name` hidden input.
- **Failed-badge staleness**: `_get_failed_jobs_map` used to pick
  the first FAILED job per deck regardless of newer successful
  retries. Now keyed on `(deck, part)` and dedupes newest-first, so
  a successful retake of a specific part clears its failed
  indicator — but a success on a different part cannot mask an
  unresolved failure elsewhere in the deck.
- **Cascade rename scope**: `_cascade_unsuffixed_to_part1` is now
  stem-based rather than extension-based. Every file sharing the
  unsuffixed stem across `to-process/`, `archive/`, and `final/`
  gets renamed together. Covers the `.edl` cut-list Auphonic
  already emits and future `.vtt`/`.srt`/`.json`/`.html` sidecars
  without further changes.
- **Auphonic schema drift**: `AuphonicProduction._none_to_empty`
  renamed to `_coerce_to_empty_str` and now coerces any non-string
  scalar. Real-world trigger: an aborted-no-speech production
  returned `error_status: 2` (int), which Pydantic rejected with
  `string_type`. The job manager classified `ValidationError` as
  transient, so the job retried forever and stayed pinned at
  `processing` ~40 %. Coercion unblocks the happy path; the
  retry-forever pathology is filed for Commit B (make persistent
  `ValidationError` permanent after N attempts, and present the
  progress bar as 0/100 on terminal states rather than stuck mid-
  arc).

**Deferred to follow-up work** (surfaced during the smoke test but
not critical enough to block Phase 4 shipping):

- Auphonic FAILED jobs leave the jobs-panel progress bar stuck at
  ~40 %. Cosmetic — the badge itself flips to `failed` correctly.
- Clicking any button scrolls the lectures page back to the top;
  users with long lecture lists have to scroll back down. Candidate
  fixes: collapsible sections, or preserve scroll position via an
  HTMX swap strategy that keeps the row in view.
- "Elapsed time" display while a deck is recording — requested
  during the smoke test, would leverage the existing
  `_recording_started_at` attribute on the session.
- Manual "advance to next take" affordance (`POST /advance`) so the
  user can slot a recording session's first take into the `takes/`
  history without actually recording a throwaway take first.

## 4. Current Status

- **Shipped (merged to master)**: Phase 1 (`submit_async`
  non-blocking `/process`). User-verified 2026-04-17.
- **Implemented on branch** (`claude/recordings-hardening`,
  smoke-tested by user on Windows 2026-04-19, ready for PR):
  - Phase 2 — web state wiring (2026-04-18).
  - Phase 3 — OBS auto-reconnect + connection-aware buttons
    (2026-04-18).
  - Phase 4 — UI feedback, design-system migration, SSE toasts,
    cascade rename for all companions, Auphonic schema-drift
    hardening (2026-04-19).
- **Next up**: open the single PR for `claude/recordings-hardening`
  against master; then pick up the Commit-B follow-ups listed in
  §3 Phase 4's post-implementation notes.
- **Tests**: 616 recordings tests green (up from 554); full fast
  suite (3445 tests) green; ruff + mypy clean on changed files.

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

**Last updated**: 2026-04-19 (Phase 4 implemented, all phases
smoke-tested, branch ready for PR)
**Next action**: Open a single PR for `claude/recordings-hardening`
bundling Phases 2 + 3 + 4 against master. The Commit-B follow-ups
(progress-bar cosmetics on FAILED, scroll preservation, elapsed-
time indicator, manual "advance take" button) are optional and can
follow later.
