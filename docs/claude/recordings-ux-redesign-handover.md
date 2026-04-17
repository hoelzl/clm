# Handover: Recordings UX Redesign

## 1. Feature Overview

**Name**: Recordings UX Redesign (one-click record + takes model + live job progress + reconciliation)

This feature overhauls the recording workflow in CLM's web dashboard and recording subsystem to address five concrete pain points the user hit after shipping the pluggable-backend / Auphonic work:

1. **Alt-tabbing between CLM and OBS on every take** is tedious and slow.
2. **Stopping and restarting OBS mid-take silently loses the deck association** — the second recording lands in OBS's default directory with no rename because the session clears `_armed` the moment STOPPED fires.
3. **The web UI appears frozen for minutes** during Auphonic upload/processing because progress callbacks are chunk-granular (8 MiB) and polling has a 30-second gap between upload-complete and the first status query.
4. **The "parts" concept conflates two user intents** ("add another segment" vs. "redo this segment") — asking for part=1 after recording part=0 will silently destroy the new take today, and retaking an already-processed part overwrites the old final (burning Auphonic credits and losing data).
5. **Jobs get stuck in FAILED after server restart** even when the Auphonic production finished processing upstream — the 120-minute hard timeout and the UPLOADING→FAILED-on-restart rule both auto-fail healthy work with no recovery path.

The redesign adds: a one-click Record button that drives OBS via WebSocket, a retake window that keeps the deck armed across stop/restart, a formal part/take model with a `takes/` directory that preserves superseded processed finals, time-gated progress updates, and a per-job "Verify" action that reconciles state against both the filesystem and the Auphonic API.

**Branch**: TBD (start from `master`). No implementation commits exist yet.
**Related prior work**: Pluggable-backend refactor merged 2026-04-06 (`docs/claude/design/recordings-backend-architecture.md`).
**Design docs** (three, authored 2026-04-17):
- `docs/claude/design/recordings-workflow-ux-redesign.md`
- `docs/claude/design/recordings-parts-and-takes.md`
- `docs/claude/design/recordings-job-progress-and-reconciliation.md`

**Status**: No code changes. All work is TODO.

## 2. Design Decisions

### 2.1 Keep the term "part" in the UI; make "take" the secondary concept

The filename convention users already see is `deck (part N)--RAW.mp4`. Renaming that to "segment" would be a churn-for-churn-sake rename. Instead, "part" keeps its meaning as **segment** (additive), and a new **take** concept (supersedes) rides alongside. The UI surfaces takes only when history exists — a small "N takes" indicator per part.

**Alternative rejected**: renaming to "segment" + "take" across the UI and filenames. Too much user-facing churn.

### 2.2 Active take uses unadorned filenames; only superseded takes carry `(take K)` in the name

Active files stay at `final/.../deck (part N).mp4` and `archive/.../deck (part N)--RAW.mp4` (identical to today). Superseded takes move to `takes/.../deck (part N, take K).mp4`. This keeps the common case unchanged — anything already downloading the final doesn't need to learn about takes — and makes the history self-describing when you browse `takes/`.

**Alternative rejected**: always include `(take 1)` in filenames. Breaks every existing tool and script that assumes the current naming.

### 2.3 Separate `takes/` directory, not a subfolder of `superseded/`

`takes/` holds **fully-processed takes replaced by a later take** (precious — cost Auphonic credits). `superseded/` holds **pre-processing garbage** (zero-length OBS outputs, accidental re-records caught before processing). Mixing them hides the expensive history behind the throwaway files.

### 2.4 `ARMED_AFTER_TAKE` state + retake window, not unlimited-stay-armed

When a take stops, we stay armed for a configurable window (default 60 s). A new OBS recording within the window is treated as a retake of the same deck; after the window expires, the deck disarms.

**Alternative rejected**: never auto-disarm. Risk: if the user walks away after the last segment, the next unrelated OBS recording gets hijacked.

**Alternative rejected**: require explicit retake click. Defeats the "I made a mistake, just start again" UX goal.

### 2.5 Zero-length takes are auto-superseded without changing the armed state

If OBS reports a stop within `short_take_seconds` (default 5 s) of start, the file goes straight to `superseded/` without being counted as a take. Matches the "start-and-immediately-stop to test audio levels, then start for real" habit.

### 2.6 OBS window focus is opt-in (default off)

Stealing focus is polarizing. `focus_obs_on_record = False` by default; Windows-first implementation via `ctypes` + `SetForegroundWindow`, best-effort AppleScript / `wmctrl` on other platforms.

### 2.7 Progress UX uses three small, independent levers

Rather than one big overhaul, three focused changes that each help:
- **Time-gated upload callback** (every 250 ms, not just on 8 MiB boundaries) — kills upload-appears-stuck.
- **`request_poll_soon()` signal** on `JobContext` — collapses the 30 s gap between upload-complete and first status poll.
- **Elapsed-time in job message** (`"Auphonic: Audio Processing — 3m 47s"`) — gives the UI something to tick on every poll even when upstream status is unchanged.

### 2.8 Reconciliation is a generic backend Protocol method, not Auphonic-only

`ProcessingBackend.reconcile(job, ctx)` is optional. Auphonic implementation combines filesystem check + `get_production(uuid)` + fallback title-based `list_productions()`. Audio-first backends get a default implementation that checks `final_path` existence. Keeps the fix useful across the whole job subsystem.

### 2.9 Soften timeouts rather than strengthen failure paths

Current code fails jobs on the 120-minute wall-clock timeout and on server-restart-while-uploading. Both are wrong when Auphonic's own state is fine. Change semantics:
- `AUPHONIC_POLL_TIMEOUT_MINUTES` → `AUPHONIC_STALE_WARN_MINUTES`: flag as stale, don't fail.
- UPLOADING-on-restart: if `backend_ref` is set, transition to PROCESSING and let the next poll clarify; only fail if no production was ever created.

**Alternative rejected**: leave fail-fast behavior and rely solely on reconcile to repair. Users shouldn't have to notice and fix every transient crash; the system should degrade gracefully.

### 2.10 Five-phase rollout, each independently shippable

No flag-day rewrite. Each phase adds behavior or wraps old behavior — never breaks the existing flow. Earlier phases are user-visible UX wins; later phases are data-integrity fixes that require more careful testing.

## 3. Phase Breakdown

### Phase 1 — One-click record [TODO]

**Goal**: Single "Record" button in the dashboard that arms the deck and starts OBS in one step, plus a "Stop" button that tells OBS to stop.

**What it accomplishes**:
- New high-level session method `RecordingSession.record(course, section, deck, ...)` = `arm()` + `obs.start_record()`.
- New `RecordingSession.stop()` = `obs.stop_record()`.
- `ObsClient.start_record()` / `ObsClient.stop_record()` wrappers around `obsws-python` request methods.
- New `POST /record` and `POST /stop` web routes; existing `/arm` and `/disarm` kept as primitives (moved under an "Advanced" disclosure in the UI).
- Status partial renders a single primary button whose label and action switch between Record / Stop based on session state.

**Files involved**:
- `src/clm/recordings/workflow/obs.py` (add `start_record`, `stop_record`)
- `src/clm/recordings/workflow/session.py` (add `record`, `stop`)
- `src/clm/recordings/web/routes.py` (add `/record`, `/stop`)
- `src/clm/recordings/web/templates/partials/status.html`
- `src/clm/recordings/web/templates/lectures.html`

**Acceptance criteria**:
- Clicking "Record" on a lecture arms the deck and triggers OBS recording without the user leaving the web page.
- Clicking "Stop" in the web UI or the OBS window both stop the recording cleanly and trigger the existing rename flow.
- Existing `/arm` + `/disarm` routes still work (regression).
- If OBS is disconnected, `/record` surfaces a clear error and leaves the session in a recoverable state.

### Phase 2 — Retake handling (short-take + ARMED_AFTER_TAKE) [TODO]

**Goal**: Make stop-and-restart-during-a-take a no-click recovery flow.

**What it accomplishes**:
- Short-take detection: OBS stops within `short_take_seconds` (default 5) → file to `superseded/`, keep `_armed`.
- New `SessionState.ARMED_AFTER_TAKE`: after a real take completes, stay armed for `retake_window_seconds` (default 60). A second OBS start within the window = retake (take number bumps, part number unchanged). Window expiry → IDLE.
- Rename-thread timeout (default 10 min) so a wedged `_wait_for_stable` can't freeze the session forever.

**Files involved**:
- `src/clm/recordings/workflow/session.py` (state enum, event handler, timers)
- `tests/recordings/test_session.py` (new scenarios)
- `src/clm/recordings/workflow/config.py` or equivalent (new config fields)

**Acceptance criteria**:
- Start recording → stop within 3 s → file appears in `superseded/`, dashboard unchanged, deck still armed.
- Start → stop normally → dashboard shows "ready for retake (55s)" ticking down; start again → associated with same deck as a retake.
- Retake window expires → state → IDLE, `_armed` cleared, SSE event fires.
- Rename thread stuck for > 10 min → force-exit, surface error, deck cleared.

### Phase 3 — Part/take model + `takes/` directory [TODO]

**Goal**: Formalize take numbering, stop destroying old processed finals, fix the state.json ↔ filesystem drift bug.

**What it accomplishes**:
- New `TakeRecord` dataclass in `state.py`; `RecordingPart` gets `takes: list[TakeRecord]` + `active_take: int` (both defaulted — backcompat preserved for existing course state files).
- New `CourseRecordingState.record_retake(...)`, `restore_take(...)`, `rename_recording_paths(...)` methods.
- New `takes_dir(root)` helper in `directories.py`.
- **Retake pre-move** in the session: before moving an OBS output into `to-process/`, scan `final/<rel>/deck (part K).mp4` and `archive/<rel>/deck (part K)--RAW.mp4`; if present, move to `takes/<rel>/deck (part K, take J).<ext>` (where J = max existing take for part K, or 1 if none).
- Session constructor accepts an optional `state: CourseRecordingState | None`; when provided, filesystem renames are paired with `state.rename_recording_paths(...)` calls to kill the drift bug.

**Files involved**:
- `src/clm/recordings/state.py` (schema + methods)
- `src/clm/recordings/workflow/directories.py` (`takes_dir`)
- `src/clm/recordings/workflow/session.py` (retake pre-move, state wiring)
- `src/clm/recordings/workflow/naming.py` (take-aware filename helpers)
- `src/clm/recordings/web/app.py` (inject `CourseRecordingState` into session)
- `tests/recordings/test_state.py`, `tests/recordings/test_session.py`, `tests/recordings/test_directories.py`

**Acceptance criteria**:
- Recording a retake of an already-processed part preserves the old final in `takes/` and the old raw in `takes/` (no data loss).
- state.json `takes` list grows; `active_take` tracks the new take number; `raw_file` / `processed_file` point at the fresh active take.
- Recording a new part after some parts are already processed continues to work (regression guard).
- Loading a state.json written by the old schema succeeds (migration is implicit via Pydantic defaults).
- All filesystem cascades update state.json in lockstep — no stale paths.

### Phase 4 — Job progress UX [TODO]

**Goal**: UI never appears frozen during Auphonic uploads or processing.

**What it accomplishes**:
- `AuphonicClient.upload_input`: time-gated `on_progress` (fires every 250 ms minimum, not just on chunk boundaries).
- `JobManager` gains a `request_poll_soon()` wake-event; exposed on `JobContext`.
- `AuphonicBackend.submit` calls `ctx.request_poll_soon()` after transitioning to PROCESSING.
- `AuphonicBackend._message_for` includes elapsed time in the current phase.
- UI audit: `partials/jobs.html` binds refresh to SSE `event:job` (not just `state_changed`), renders progress bar + message + elapsed.

**Files involved**:
- `src/clm/recordings/workflow/backends/auphonic_client.py`
- `src/clm/recordings/workflow/backends/auphonic.py`
- `src/clm/recordings/workflow/job_manager.py`
- `src/clm/recordings/workflow/backends/base.py` (`JobContext.request_poll_soon` addition)
- `src/clm/recordings/web/templates/partials/jobs.html`
- `src/clm/recordings/web/static/*` (SSE handler binding)
- `tests/recordings/test_auphonic_client.py`, `tests/recordings/test_job_manager.py`

**Acceptance criteria**:
- Upload to a 500 MB file on a simulated slow uplink ticks the progress bar at least 4× per second.
- After upload completes, the first poll-driven message appears within 2 s (not 30 s).
- During Auphonic processing, the job row updates at least once per poll even when the upstream status string is unchanged.

### Phase 5 — Reconciliation [TODO]

**Goal**: Per-job "Verify" action recovers jobs whose displayed state doesn't match reality. Soften timeouts that auto-fail healthy work.

**What it accomplishes**:
- `ProcessingBackend.reconcile(job, ctx)` Protocol addition with default implementation (check `final_path`).
- `AuphonicBackend.reconcile` full implementation: local filesystem → `get_production(uuid)` → fallback to title-based `list_productions`.
- New `AuphonicClient.list_productions(title=..., since=...)` wrapping `GET /api/productions.json`.
- `JobManager.reconcile(job_id)` entry point.
- New `POST /jobs/{id}/reconcile` web route.
- UI: per-row "↻ Verify" button on the Processing Jobs table.
- Soften hard timeout: `AUPHONIC_POLL_TIMEOUT_MINUTES` → `AUPHONIC_STALE_WARN_MINUTES`; add `job.stale: bool` field; optional `AUPHONIC_HARD_GIVEUP_DAYS = 7` safety net.
- Soften UPLOADING-on-restart: if `backend_ref` is set, transition to PROCESSING; only fail if no production was ever created.

**Files involved**:
- `src/clm/recordings/workflow/backends/base.py`
- `src/clm/recordings/workflow/backends/auphonic.py`
- `src/clm/recordings/workflow/backends/auphonic_client.py`
- `src/clm/recordings/workflow/job_manager.py`
- `src/clm/recordings/workflow/jobs.py` (add `stale` field)
- `src/clm/recordings/web/routes.py` (reconcile route)
- `src/clm/recordings/web/templates/partials/jobs.html` (verify button, stale badge)
- `tests/recordings/test_auphonic.py`, `test_auphonic_client.py`, `test_job_manager.py`, `web/test_routes.py`

**Acceptance criteria**:
- A FAILED job whose Auphonic production is actually DONE can be reconciled to COMPLETED from the UI (download + archive + state update).
- A FAILED job whose raw is still in `to-process/` and whose `final_path` already exists can be reconciled (recover from mid-rename crash).
- Server restart during UPLOADING with `backend_ref` set → job continues polling instead of failing.
- Jobs older than `AUPHONIC_STALE_WARN_MINUTES` show a warning badge but are not auto-failed.

## 4. Current Status

**Nothing implemented yet.** All five phases are TODO.

**Completed**:
- Design docs authored 2026-04-17 (three files under `docs/claude/design/`). These capture goals, non-goals, file-level changes, test lists, and implementation order for each phase.
- This handover document.

**In progress**: None.

**Open questions / decisions deferred**:
- Should `takes/` be auto-pruneable? Current decision: ship without pruning, add `clm recordings prune-takes --older-than=…` CLI command later when it becomes a problem.
- Cut-list artifact versioning on retake: when Auphonic produces an EDL, takes should preserve it too — use the same `(part N, take K)` suffix. Not yet specified in detail; resolve during Phase 3 implementation.
- Should Phase 4 also add sub-chunk HTTP streaming progress? Not required for the motivating case; time-gating chunk callbacks is enough. Revisit if slow uplinks remain painful.

**State of tests**: No new tests written. The existing 355 recordings tests still pass on master — new phases must not regress them.

## 5. Next Steps

**Start with Phase 1 (One-click record)**. It's the smallest, highest-visibility win and has no dependencies on the other phases.

### Prerequisites

1. Create a branch: `git checkout -b claude/recordings-ux-phase1-one-click-record` from `master`.
2. Install deps if not already done: `pip install -e ".[all]"`.
3. Verify the existing fast test suite passes: `pytest`.
4. Read `docs/claude/design/recordings-workflow-ux-redesign.md` end-to-end before touching code. §5.1 (default flow) and §6 (design changes) are the load-bearing sections.

### Implementation sketch

1. **`src/clm/recordings/workflow/obs.py`**: add `start_record(self) -> None` and `stop_record(self) -> None`. Both call `self._require_connected()` and dispatch to `obsws-python`'s `ReqClient` methods (`req.start_record()` / `req.stop_record()`). Both log the action and re-raise on failure with a user-friendly message wrapping the underlying exception.

2. **`src/clm/recordings/workflow/session.py`**: add `record(course_slug, section_name, deck_name, *, part_number=0, lang="en")` that takes the session lock, calls `self.arm(...)`, then calls `self._obs.start_record()`. On OBS failure, leave the deck armed (so the user can manually start OBS and retry). Add `stop()` that simply calls `self._obs.stop_record()`.

3. **`src/clm/recordings/web/routes.py`**: add `@router.post("/record", response_class=HTMLResponse) async def record_deck(...)` mirroring the signature of the existing `arm_deck`. Add `@router.post("/stop")` that calls `session.stop()`. Return the status partial or an HX-Redirect as appropriate.

4. **Templates**: in `partials/status.html`, change the primary action button to switch between "Record"/"Stop" based on `snapshot.state`. Move the old `arm`/`disarm` buttons under an `<details>` element labeled "Advanced".

5. **Tests**:
   - `tests/recordings/test_obs.py`: test `start_record`/`stop_record` delegate to the underlying client (mock `obsws-python`).
   - `tests/recordings/test_session.py`: test `record()` arms + calls start_record; test `record()` leaves deck armed when OBS raises; test `stop()` calls stop_record.
   - `tests/recordings/web/test_routes.py`: test `/record` hits session.record; test `/stop` hits session.stop; test `/arm` still works.

### Gotchas to watch for

- **OBS event dispatch uses daemon threads in `obsws-python`.** The existing `ObsClient` registers callbacks before `connect()`. Don't change that pattern — events registered after connect risk being missed during the initial state.
- **The session lock protects the state machine but not OBS I/O.** `_obs.start_record()` is a blocking WebSocket call; don't hold the session lock across the call. The existing code style (update state under the lock, then call OBS / notify outside the lock) should be preserved.
- **Windows path handling in tests**: the project is Windows-first; avoid hardcoded POSIX paths in new tests. Use `tmp_path` fixtures.
- **HTMX partial refresh logic**: the existing `status_partial` returns the same template the full page uses for its status panel. If you split the Record/Stop button out into its own sub-partial, make sure both the full page and the HTMX swap paths include it.
- **Don't tie Phase 1 to Phase 2 changes.** Phase 1 leaves `_armed` cleared on OBS STOPPED (current behavior). Retake-stay-armed is Phase 2. Users stop-and-restart recovery is still broken after Phase 1; that's fine — Phase 2 fixes it independently.
- **Pre-commit hook runs ruff + mypy + fast tests.** Commits that fail the hook did *not* happen — fix the issue, re-stage, create a *new* commit. Don't `--amend` (CLAUDE.md rule).

### After Phase 1 ships

Read `docs/claude/design/recordings-parts-and-takes.md` §10 before starting Phase 2/3 — the ordering there suggests schema-only changes (state.json additions) in a separate commit before the session wiring, so existing state files upgrade safely.

## 6. Key Files & Architecture

### Existing files (to be modified across phases)

| File | Role | Phases touching |
|---|---|---|
| `src/clm/recordings/workflow/obs.py` | Thin OBS WebSocket wrapper (ReqClient + EventClient) | 1 |
| `src/clm/recordings/workflow/session.py` | Arm/disarm state machine, rename thread, cascade logic | 1, 2, 3 |
| `src/clm/recordings/workflow/directories.py` | Directory-layout helpers (`to_process_dir`, `final_dir`, `archive_dir`, `superseded_dir`, `find_pending_pairs`) | 3 |
| `src/clm/recordings/workflow/naming.py` | Filename parsing / building (`raw_filename`, `parse_part`, `find_existing_recordings`) | 3 |
| `src/clm/recordings/state.py` | Per-course state JSON (`CourseRecordingState`, `LectureState`, `RecordingPart`) | 3 |
| `src/clm/recordings/workflow/job_manager.py` | Job lifecycle; poller thread; UPLOADING-on-restart handling | 4, 5 |
| `src/clm/recordings/workflow/backends/base.py` | `ProcessingBackend` Protocol; `JobContext` | 4, 5 |
| `src/clm/recordings/workflow/backends/auphonic.py` | Auphonic backend (submit/poll/finalize/cancel) | 4, 5 |
| `src/clm/recordings/workflow/backends/auphonic_client.py` | HTTP client wrapping Auphonic Complex JSON API | 4, 5 |
| `src/clm/recordings/workflow/jobs.py` | `ProcessingJob` model, state enum, capabilities | 5 |
| `src/clm/recordings/web/routes.py` | HTMX / SSE routes | 1, 5 |
| `src/clm/recordings/web/app.py` | App factory, SSE queue wiring, session/job-manager construction | 1, 3, 4 |
| `src/clm/recordings/web/templates/partials/status.html` | Arm/Record/Stop button and session-state display | 1, 2 |
| `src/clm/recordings/web/templates/partials/jobs.html` | Processing Jobs list | 4, 5 |
| `src/clm/recordings/web/templates/lectures.html` | Lectures table; per-part buttons | 1, 3 |

### New files expected to be created

| Path | Purpose | Phase |
|---|---|---|
| (none for Phase 1 — all additions happen in existing files) | | |
| `src/clm/recordings/workflow/config.py` *(if not already present)* | Home for new config fields like `short_take_seconds`, `retake_window_seconds`, `focus_obs_on_record` | 2 |
| Tests: `tests/recordings/test_session_retake.py` or extended `test_session.py` | Retake scenarios | 2 |
| Tests: `tests/recordings/test_takes_model.py` or extended `test_state.py` | Take / state.json schema | 3 |
| Tests: extended `test_auphonic_client.py`, `test_auphonic.py`, `test_job_manager.py` | Progress + reconciliation | 4, 5 |

### Entry points and control flow

1. **Web app startup** (`src/clm/recordings/web/app.py` `lifespan`) constructs: `ObsClient` → `RecordingSession` → `JobStore` → `JobManager` (with selected backend) → `RecordingsWatcher`. All are held on `app.state`.
2. **Record flow (new, Phase 1)**: UI → `POST /record` → `RecordingSession.record(...)` → under lock: `self.arm(...)`; outside lock: `self._obs.start_record()`. OBS fires `RecordStateChanged` event → `_handle_record_event` on daemon thread → session transitions ARMED→RECORDING → SSE push.
3. **Stop flow**: UI clicks Stop (or presses Stop in OBS) → OBS fires STOPPED → rename thread → `_prepare_target_slot` (Phase 3 extended with retake pre-move) → file moves to `to-process/` → watcher submits to JobManager → backend processes → `_archive_raw` moves raw to `archive/` → `final/` has processed output.
4. **Retake flow (Phase 2)**: STOPPED → rename completes → `ARMED_AFTER_TAKE` state + timer. New STARTED within window → RECORDING with same deck + bumped take. Timer expires → IDLE.
5. **Reconciliation flow (Phase 5)**: UI → `POST /jobs/{id}/reconcile` → `JobManager.reconcile(id)` → `backend.reconcile(job, ctx)` → (filesystem check → upstream query → title fallback) → `ctx.report(job)` → SSE.

### Conventions to continue

- **Thread safety**: session uses a `threading.Lock`. Mutate state under the lock; call OBS, emit SSE events, and fire callbacks outside the lock. See `RecordingSession._handle_record_event` for the canonical pattern.
- **SSE pushing is thread-marshalled**: `_push_sse` in `app.py` uses `call_soon_threadsafe` for background-thread calls. New push sites must use this helper, not raw `asyncio.Queue.put_nowait`.
- **Backend Protocol stays minimal**: don't add Auphonic-specific concepts to `ProcessingBackend`. Add optional methods with sensible defaults so audio-first backends keep working.
- **Pydantic schema additions need defaults**: old state.json files must still load. Every new field on `RecordingPart` / `LectureState` / `CourseRecordingState` must have a default value.
- **Filenames via `naming.py` helpers only**: don't construct `(part N)` / `(part N, take K)` strings inline. Add helpers to `naming.py` so the convention lives in one place.
- **CLAUDE.md info-topics rule**: if any CLI command or spec-file behavior changes, update `src/clm/cli/info_topics/commands.md` or `spec-files.md` accordingly. Phase 1 adds no CLI changes, but Phase 5 (reconcile CLI command) will.

## 7. Testing Approach

### Strategy

- **Unit tests per module**, heavy emphasis on the state machine and state.json transitions. The session and job manager are the integrity-critical surfaces; test exhaustively.
- **Integration tests** at the web-route layer for the new `/record`, `/stop`, `/jobs/{id}/reconcile` routes — use FastAPI's `TestClient`.
- **Mock OBS via a fake `ObsClient`** for session tests; do not require a running OBS during `pytest`. The existing `test_session.py` already has this pattern — extend it.
- **Mock Auphonic HTTP** via `httpx.MockTransport` for `AuphonicClient` tests. The existing `test_auphonic_client.py` already establishes the pattern.
- **No Docker-marker tests for this feature.** Everything should run in the fast or non-docker suite.

### What's tested already (existing 355 recordings tests — regression guards)

- Arm/disarm state transitions (`test_session.py`)
- Multi-part cascade rename (`test_session.py::test_part_2_renames_*`)
- Supersede logic (`test_session.py::test_supersede_*`)
- Job submission, polling, permanent vs transient errors (`test_job_manager.py`)
- UPLOADING-on-restart rehydration (`test_job_manager.py`)
- State.json assign/reassign/status updates (`test_state.py`)
- Watcher dispatch + scan-existing (`test_watcher.py`)
- Auphonic client HTTP flows (`test_auphonic_client.py`)

### What needs new tests (per phase)

See §3 acceptance criteria and the "Tests to add" section of each design doc for the full list. Summary:

- **Phase 1**: `record()` / `stop()` delegation; `/record` + `/stop` routes.
- **Phase 2**: short-take auto-supersede; `ARMED_AFTER_TAKE` transitions; retake window timer; rename timeout.
- **Phase 3**: `record_retake` demotes active → takes; `restore_take` swaps; `rename_recording_paths` scans all takes; retake pre-move preserves final + raw; old-schema state.json loads.
- **Phase 4**: time-gated upload callback; `request_poll_soon` wakes poller; elapsed-time in message.
- **Phase 5**: reconcile routes to backend; reconcile resurrects FAILED → COMPLETED when upstream done; reconcile uses local final when present; reconcile by title when UUID missing; UPLOADING-with-backend-ref transitions to PROCESSING on restart; stale flag doesn't fail.

### How to run

- Fast suite (runs as part of pre-commit): `pytest` — excludes `slow`, `integration`, `e2e`, `db_only`, `docker` markers. ~30 s.
- Recordings-only: `pytest tests/recordings/` — useful while iterating.
- Pre-release gate: `pytest -m "not docker"`. ~2 min.
- Lint + format: `uv run ruff check src/ tests/` and `uv run ruff format src/ tests/`.
- Type check: runs via the pre-commit hook; manual invocation is `uv run mypy src/`.

## 8. Session Notes

### User preferences expressed

- Keep "part" as the UI term (matches filename convention the user sees). Don't rename to "segment".
- Retakes must preserve previously-processed finals — Auphonic credits and time cost matter.
- Windows-first. Every change needs to work on Windows (no POSIX-only path assumptions, no bash-only scripts; prefer Python for tooling).
- Each phase should be shippable independently — don't batch everything into a big PR.
- Auto mode is active for this work; the user expects continuous, autonomous progress on low-risk changes and explicit check-ins only for destructive or ambiguous choices.

### Discoveries during analysis

- The "stop-mistake, restart OBS" bug is caused by `_handle_record_event` clearing `_armed` on STOPPED (session.py:411). Phase 2's `ARMED_AFTER_TAKE` state is the clean fix.
- The data-loss-on-retake bug has three contributing code paths: `_prepare_target_slot` only scans `to-process/` (not `archive/` / `final/`); `_archive_raw` uses `shutil.move` which raises `FileExistsError` on Windows or silently overwrites on Unix; `_download_video` writes to `final_path` unconditionally. Phase 3's retake pre-move clears all three.
- `_prepare_target_slot` cascades filenames on disk but `state.json` is not updated, leaving stale `raw_file` / `processed_file` paths. Fixing this requires injecting `CourseRecordingState` into `RecordingSession` — currently the two are decoupled (state is only touched by the watcher on `assign_recording`).
- Auphonic polling has a 30 s first-poll gap plus 5 min long-poll; combined with the chunk-granular upload progress, the perception of "UI frozen" is mostly about the lack of sub-10-second ticks. The three small fixes in Phase 4 are likely sufficient without deeper changes.
- UPLOADING-on-restart failure is over-aggressive when `backend_ref` is set (production already exists upstream). This is the root cause of the user's real Auphonic incident.

### Things explicitly *not* being done

- No Auphonic webhook support (polling-only per the original backend design).
- No automatic retention policy on `takes/` (user prunes by hand, or via a future CLI command).
- No multi-OBS orchestration.
- No rename of "part" → "segment" in UI or filenames.
- No restore-take UI in Phase 3 initial scope — it's called out as a smaller follow-up (step 5 in the parts-and-takes doc) but can be deferred if time is short.

---

**Last updated**: 2026-04-17
**Next action**: Start Phase 1 implementation on a fresh branch from `master`.
