# Recordings Workflow UX Redesign (Record Button + Retake Handling)

**Status**: Draft for discussion
**Author**: Claude (Opus 4.7) + TC
**Date**: 2026-04-17
**Scope**: `src/clm/recordings/workflow/obs.py`, `session.py`, `src/clm/recordings/web/`

---

## 1. Problem

The current workflow requires the user to:

1. Connect to OBS (or ensure it is already running) before starting the CLM server.
2. Arm the lecture in the CLM web UI.
3. Switch to OBS and press Record.
4. Switch back to OBS and press Stop when done.
5. Switch back to the CLM web UI and Disarm.

Two concrete pain points fall out of this:

- **Alt-tabbing between CLM and OBS** on every take is slow and error-prone.
- **If the user stops OBS, realizes they made a mistake, and hits Record again**, the second recording lands in OBS's default recording directory with no association to the armed deck. The existing state machine clears `_armed` the moment the STOPPED event fires (`session.py:411`), so the re-started recording takes the "Recording started (state=idle, no auto-rename)" branch at `session.py:347`. The user has to disarm, re-arm, and start over — but the first take is already sitting in OBS's default directory and needs manual cleanup.

## 2. Goals

1. Make "record this lecture" a single click in the CLM web UI.
2. Make "I made a mistake, let me redo it" a single click (not a full disarm/re-arm cycle).
3. Keep the low-level primitives (`arm`, `disarm`) available as escape hatches when OBS is unreachable or the user needs to recover by hand.
4. Don't surprise the user by hijacking OBS recordings they didn't intend CLM to manage (e.g. a test recording after the lecture is already over).

## 3. Non-goals

- Replacing OBS as the recording tool. CLM remains a controller that drives OBS via WebSocket.
- Multi-scene orchestration. OBS scenes, profiles, and scene collections remain user-managed.
- Remote OBS control. The scope is the local OBS instance CLM is already configured for.

## 4. Part / take semantics (prerequisite)

See the sibling note `recordings-parts-and-takes.md` for the full model. Summary for this document:

- **Part**: a segment of a lecture's content. Parts 1, 2, 3... add up to the whole lecture.
- **Take**: a recording attempt at a given part. Take 2 of part 1 *supersedes* take 1 of part 1.
- The UI keeps the term "part" (matches the `(part N)` filename suffix users see). "Take" is secondary, exposed as a small history indicator per part.

## 5. Proposed workflow

### 5.1 Default flow (one-click lecture recording)

1. User opens the web dashboard. OBS is already running (or the dashboard shows a clear "Connect to OBS" action).
2. User clicks **Record** on the current lecture row.
   - CLM arms the deck as today.
   - CLM calls `obs.start_record()` over WebSocket.
   - Optionally (config flag, default off): CLM asks the OS to bring the OBS window to the foreground.
3. Status panel shows "Recording part 1 (take 1) of &lt;lecture&gt;". The Record button is replaced by a **Stop** button.
4. When the user is done, they press either:
   - **Stop** in the OBS window (existing habit), or
   - **Stop** in the CLM web UI (new, calls `obs.stop_record()`).
5. CLM's existing STOPPED-event handling takes over: the file is renamed into `to-process/`, the job manager picks it up (see the job-reconciliation note for progress updates), and the lecture row updates to show "part 1 done".

No alt-tabbing required for the common case.

### 5.2 Retake after an obvious mistake

Two overlapping features make this painless:

**(a) Auto-supersede zero-length takes.**
If OBS reports a recording stop within a threshold of the start (default: 5 s, configurable), treat the file as an accidental take: move it directly to `superseded/` without ever assigning it a part/take, and keep the deck armed. The user sees no state change on the dashboard — just a silent "that didn't count" note in the action log.

This catches the most common mistake (start-then-immediately-stop to test audio levels, then start again for real).

**(b) Stay armed across a deliberate stop/restart.**
When a real take stops, the rename thread completes and the session transitions to a new `ARMED_AFTER_TAKE` state (instead of `IDLE`). In this state:

- `_armed` is preserved.
- A 60-second timer is started (configurable).
- If OBS fires STARTED again within the timer: that recording gets associated with the same deck as the previous take, bumping the *take number* (not the part number), and the previous take moves to `takes/` as described in the parts-and-takes note.
- If the timer expires without a new STARTED event: transition to `IDLE`, clear `_armed`, push a `state_changed` SSE event.
- The user can click **Disarm** at any time to exit the window early.

These two features together mean the user never has to manually re-arm to redo a take.

### 5.3 Adding a new part to the same lecture

- After a take completes (or after the `ARMED_AFTER_TAKE` timer expires), the user clicks **Record** again on the same lecture.
- CLM arms the next *part* (part = highest existing part for this lecture + 1), take 1.
- OBS starts recording. Flow continues as in 5.1.

### 5.4 Retaking a specific (already-recorded) part

- The lecture row shows each recorded part with a small "↻ Retake" action.
- Clicking it arms that specific part with take = (highest existing take for this part) + 1.
- The session's rename logic moves the previous active take's raw + final into `takes/` (see parts-and-takes note §4) *before* the new take's rename completes, so the slot is clear.

### 5.5 Escape hatches

- **`arm` / `disarm` primitives remain** and are wired to separate routes (`/arm`, `/disarm`). The UI shows these under an "Advanced" disclosure; they're useful when OBS is unreachable, when the user recorded outside CLM and wants to backfill the rename, or during debugging.
- **Manual upload**: drag a file into `to-process/` still works; the file watcher picks it up as today.

## 6. Design changes

### 6.1 `ObsClient` additions (`src/clm/recordings/workflow/obs.py`)

Add methods that the wrapper currently doesn't expose, using `obsws-python` primitives that already exist:

```python
def start_record(self) -> None:
    """Tell OBS to begin recording. Raises if already recording or not connected."""

def stop_record(self) -> None:
    """Tell OBS to stop the current recording. No-op if not recording."""

def raise_window(self) -> bool:
    """Best-effort: bring the OBS window to the foreground.

    Returns True if the platform-specific hook succeeded, False otherwise.
    Never raises; a missing hook is logged at DEBUG and returned as False.
    """
```

`raise_window` dispatches by `sys.platform`:

| Platform | Implementation |
|---|---|
| `win32` | `ctypes.windll.user32.FindWindowW("Qt5QWindowIcon", None)` + `SetForegroundWindow` with `AllowSetForegroundWindow` pre-call. Multiple OBS window classes exist across OBS versions; probe several. |
| `darwin` | `subprocess.run(["osascript", "-e", 'tell application "OBS" to activate'])`. |
| `linux` | `subprocess.run(["wmctrl", "-a", "OBS"])` if `wmctrl` is on PATH; otherwise log-and-skip. |

Default config: `focus_obs_on_record = False`. Opt-in only — stealing focus is polarizing.

### 6.2 `RecordingSession` changes (`src/clm/recordings/workflow/session.py`)

**New state** `ARMED_AFTER_TAKE` in the `SessionState` enum. Transition map:

```
IDLE ──arm()──► ARMED ──OBS STARTED──► RECORDING
                                         │
                                         ▼ OBS STOPPED
                                       RENAMING
                                         │ rename done
                                         ▼
                                 ARMED_AFTER_TAKE ──60s timer─► IDLE
                                         │                        ▲
                                         │ OBS STARTED            │
                                         ▼                        │
                                     RECORDING (same deck, take++)
                                         │                        │
                                         └────────────────────────┘ (on stop timer expires)
```

**Short-take detection**: when STOPPED fires, if `(stop_time - start_time) < short_take_threshold`, skip the rename entirely; move the OBS output directly to `superseded/` with a reason note, keep `_armed`, and stay in `ARMED`. Threshold defaults to 5 seconds, overridable via `RecordingsConfig.short_take_seconds`.

**New high-level method** `record(course, section, deck, *, part_number=None, take_number=None, lang)`:

```python
def record(
    self,
    course_slug: str,
    section_name: str,
    deck_name: str,
    *,
    part_number: int | None = None,
    take_number: int | None = None,
    lang: str = "en",
) -> None:
    """Arm and start OBS in one step.

    part_number=None → next available part for this deck.
    take_number=None → next available take for (part_number).
    """
```

This wraps `arm()` + `_obs.start_record()` under the session lock, so the UI gets a single atomic operation.

**New method** `stop()` that calls `self._obs.stop_record()` — purely a convenience, lets the dashboard offer a Stop button.

### 6.3 Web routes (`src/clm/recordings/web/routes.py`)

| Route | Replaces | Behavior |
|---|---|---|
| `POST /record` | `POST /arm` as primary action | Calls `session.record(...)`. Returns status partial. On OBS failure, surface a clear error but do not roll back the arm — the user may still want to start OBS manually. |
| `POST /stop` | — | Calls `session.stop()`. Returns status partial. |
| `POST /arm` | kept as primitive | Same as today; surfaced under "Advanced". |
| `POST /disarm` | kept | Same as today. Also valid during `ARMED_AFTER_TAKE` — lets the user exit the retake window immediately. |

### 6.4 Short-take / `ARMED_AFTER_TAKE` configuration

Add to `RecordingsConfig`:

```python
short_take_seconds: float = 5.0
retake_window_seconds: float = 60.0
focus_obs_on_record: bool = False
```

All with module-docstring comments explaining the UX intent.

## 7. Edge cases and how they resolve

| Scenario | Behavior |
|---|---|
| OBS not running when user clicks Record | Arm succeeds; `start_record` raises; UI shows "OBS is not running — start it and press Record again, or arm manually". Deck stays armed; existing OBS reconnect path applies. |
| OBS crashes during a recording | EventClient dies; we don't detect it today (see the reconnect work in §8 of the reconciliation note). Out of scope here, tracked separately. |
| User clicks Disarm during `ARMED_AFTER_TAKE` | Standard disarm path; cancel the timer; state → `IDLE`. |
| Take ends within `short_take_seconds`, then user walks away | OBS file goes to `superseded/`, deck stays armed. If they don't come back, the dashboard shows "armed, waiting" until they disarm. No data is created silently. |
| User clicks Record on a different lecture while `ARMED_AFTER_TAKE` is active | Cancel the timer; arm the new deck; start OBS. Normal arm semantics apply (the existing `arm()` allows re-arm from `ARMED`; we extend that to `ARMED_AFTER_TAKE`). |
| `start_record` succeeds but no STARTED event arrives (network glitch) | The existing `get_record_status` query can be used as a fallback — 1 second after `start_record`, poll it; if `output_active` is true, synthesize a STARTED event to the session. |

## 8. Tests to add

In `tests/recordings/test_session.py`:

- `test_record_arms_and_starts_obs`
- `test_record_requires_obs_connected` (or returns a structured error)
- `test_stop_calls_obs_stop_record`
- `test_short_take_goes_to_superseded_and_keeps_armed`
- `test_armed_after_take_rearms_on_subsequent_start`
- `test_armed_after_take_times_out_to_idle`
- `test_disarm_during_armed_after_take`

In `tests/recordings/web/test_routes.py`:

- `test_record_route_posts_arm_and_start`
- `test_stop_route_calls_obs_stop`
- `test_arm_route_still_works_as_primitive`

Platform-specific `raise_window` behavior is best-effort and doesn't need unit tests; a smoke test on Windows (user-run, not CI) is sufficient.

## 9. Implementation order

1. Add `start_record` / `stop_record` to `ObsClient`. Wire `/record` and `/stop` routes. Keep the existing `arm`/`disarm` paths untouched. Ship.
2. Add short-take detection. Ship.
3. Add `ARMED_AFTER_TAKE` state + retake window. Ship.
4. (Optional) Add `raise_window` behind the opt-in config flag.

Each step is independently testable and independently shippable; no flag day.
