# Handover: Recordings UX Follow-Ups

> **Status**: Design-first. No code changes yet. Each phase listed here
> needs design decisions before implementation. Do **not** run
> `/implement-next-phase` blindly — every phase below starts with open
> questions that require a user check-in or a short design note.

## 1. Feature Overview

**Name**: Recordings UX Follow-Ups

This handover tracks the deferred items from the original Recordings UX
Redesign (archived at
`docs/claude/recordings-ux-redesign-handover-archive.md`, retired
2026-04-17). The five-phase redesign shipped in full, but seven
follow-up items were called out as "nice to have / not in scope" and
left without enough specification to implement directly.

Each item is a small, independently shippable enhancement to the
recordings workflow. Most are user-visible UI or CLI polish; two
(record_retake wiring + web-app state injection) are data-integrity
improvements that complete the Phase 3 part/take model end-to-end.

**Related prior work**: Recordings UX Redesign archive (above).
**Design docs** (already authored 2026-04-17, still the primary
references):
- `docs/claude/design/recordings-workflow-ux-redesign.md`
- `docs/claude/design/recordings-parts-and-takes.md`
- `docs/claude/design/recordings-job-progress-and-reconciliation.md`

**Branch**: none yet — each phase should open its own `claude/` branch
from `master`.

**Status**: All phases are [TODO]. Phases are not strictly ordered, but
**Phase A (record_retake wiring + state injection)** is a prerequisite
for the parts-inline UI (Phase C) to show meaningful take history.

## 2. Design Decisions (Inherited)

All the design decisions from the original redesign still apply — see
§2 of the archived handover. Notably:

- "Part" stays as the UI term; "take" is secondary.
- Active takes use unadorned filenames; only superseded takes carry
  `(take K)`.
- `takes/` is a separate directory from `superseded/`.
- Pydantic schema additions must have defaults — old `state.json`
  files must load.
- Windows-first. No POSIX-only paths, no bash-only scripts.

Each phase below will introduce its own small decisions; those go in
the phase body, not here.

## 3. Phase Breakdown

### Phase A — `record_retake` wiring + web-app state injection [TODO]

**Goal**: Close the loop on Phase 3's part/take model so take-history
tracking actually fires on real course state (not just in tests).

**Open design questions** (must be resolved before coding):
- **Lecture-ID resolution**: The session knows
  `(course_slug, section_name, deck_name)` but not `lecture_id`. Three
  candidate strategies:
  1. Pass `lecture_id` from the web-app into `ArmedDeck` at
     `arm()` time (web-app resolves it via the `Course` spec).
  2. Inject a resolver callable into `RecordingSession` that maps
     `(section, deck)` → `lecture_id`.
  3. Defer the `state.record_retake(...)` call until the watcher
     picks the file up in `to-process/` — the watcher already has
     course-state access.
  Pick one and document why.
- **Where does `CourseRecordingState` come from in `app.py`?** Today
  the app has a `Course` object but no loaded `CourseRecordingState`.
  Is the state loaded at startup for a single known course, or
  per-course on first use? Where does `course_id` come from?
- **When to call `record_retake`**: at retake pre-move (before the
  new file lands), or after the new file lands? The pre-move path is
  closer to where take numbers are assigned, but the post-move path
  is simpler to reason about.

**What it accomplishes** (pending the above):
- `app.py` constructs `CourseRecordingState` and passes it to
  `RecordingSession(...)`.
- Session calls `state.record_retake(...)` at the resolved trigger
  point, so `state.json` reflects take history on real runs.
- `rename_recording_paths` starts firing for real (today only tests
  exercise it).

**Files likely involved**:
- `src/clm/recordings/web/app.py`
- `src/clm/recordings/workflow/session.py`
- `src/clm/recordings/state.py` (maybe — depends on resolver choice)

**Acceptance criteria**:
- Real retake (via the web UI) causes `state.json.takes[]` to grow
  and `active_take` to bump.
- `state.json` paths stay consistent with filesystem after a retake
  pre-move + multi-part cascade.
- Old-schema `state.json` still loads (regression).

### Phase B — OBS reconnect loop [TODO]

**Goal**: Detect OBS disconnects and auto-reconnect, surface the
connection state to the UI. Without this the `EventClient` can die
silently and the dashboard shows stale state.

**Open design questions**:
- **Ping interval** and **backoff policy**: Pick concrete numbers
  (e.g., ping every 5 s, retry with 1 → 2 → 4 → 8 → 30 s capped).
- **Detection**: Is `get_record_status` enough, or should we also
  monitor the event thread?
- **UI behavior on disconnect**: Disable the Record button? Show a
  warning badge? Both?
- **SSE event names**: `obs_connected` / `obs_disconnected`? Or a
  single `obs_state` with a payload?

**What it accomplishes**:
- `ObsClient` gains an optional `auto_reconnect: bool` mode with a
  background watchdog thread.
- New SSE events expose connection state to the dashboard.
- Status partial surfaces an "OBS disconnected" warning when
  applicable.

**Files likely involved**:
- `src/clm/recordings/workflow/obs.py`
- `src/clm/recordings/web/app.py`
- `src/clm/recordings/web/templates/partials/status.html`

**Reference**: `recordings-job-progress-and-reconciliation.md` §8.1.

**Acceptance criteria**:
- Killing OBS while the web dashboard is open surfaces a visible
  warning within the ping interval.
- Restarting OBS reconnects automatically; the warning clears.
- Connection loss does not crash the session or lose armed state.

### Phase C — UI parts-inline display [TODO]

**Goal**: Replace the one-row-per-deck layout with an inline per-part
indicator (`▶ 1 │ ▶ 2 │ ▶ 3`) and a collapsible takes history.

**Open design questions** (UX decisions — needs a wireframe or mock):
- **Component type**: buttons, links, or custom chips?
- **Interaction**: click a part → modal, sidebar, inline expand, or
  HTMX fetch?
- **Status dot semantics**: colors / icons per state
  (pending/processing/processed/failed)?
- **Wrapping**: how does `▶ 1 │ ▶ 2 │ ▶ 3 │ …` behave on narrow
  screens and with many parts?
- **Retake dropdown integration**: separate control, or folded into
  the per-part click-through?

**What it accomplishes**:
- Lectures page shows parts inline per deck, not one row per deck.
- Each part surfaces its current state and (if takes exist) a small
  "N takes" indicator.

**Files likely involved**:
- `src/clm/recordings/web/templates/lectures.html`
- New: `src/clm/recordings/web/templates/partials/parts.html`
- `src/clm/recordings/web/routes.py` (if new HTMX fetch endpoints
  are needed)

**Reference**: `recordings-parts-and-takes.md` §8.

**Acceptance criteria**:
- Decks with multiple parts render as inline chips.
- Clicking a part reveals its take history and per-part controls.
- Existing single-part decks still render sensibly.

### Phase D — Restore-take UI [TODO]

**Goal**: Expose the already-implemented
`CourseRecordingState.restore_take` to the user. Depends on Phase C
to have somewhere to put the control.

**Open design questions**:
- **Gesture**: single click vs. confirm modal?
- **File handling**: does the UI request a backend swap (server-side
  file moves), or is there a separate CLI/manual flow?
- **After-swap display**: does the previously-active take show up
  in the history with its old take number?

**What it accomplishes**:
- Per-take "Restore" control that swaps the chosen take back to
  active, demoting the current active take to history.
- Corresponding backend route that orchestrates
  `state.restore_take(...)` plus the file moves on disk.

**Files likely involved**:
- `src/clm/recordings/web/routes.py` (new restore route)
- `src/clm/recordings/web/templates/partials/parts.html`
- `src/clm/recordings/workflow/session.py` or a new helper for the
  file swap (design decision: where does swap logic live?)

**Reference**: `recordings-parts-and-takes.md` §10 step 5.

**Acceptance criteria**:
- Clicking "Restore take K" on a part with multiple takes updates
  both `state.json` and the filesystem atomically (or rolls back on
  failure).
- Final and archive files for the restored take become the active
  files; previously active files move to `takes/` with their own
  suffix.

### Phase E — Cut-list artifact versioning on retake [TODO]

**Goal**: Include cut-list sidecars (EDL, etc.) in the retake pre-move
so the history in `takes/` is complete.

**Open design questions**:
- **Which sidecar formats?** EDL, FCP XML, something else? Need a
  concrete list of file-extension patterns to scan for.
- **Discovery**: glob beside the video/wav (`deck (part N).edl`),
  or a known sidecar subdirectory?
- **Backend-specific?** Does only Auphonic produce cut-lists today?
  Should the scanner live in the session (generic) or in the
  backend (backend-specific)?

**What it accomplishes**:
- Retake pre-move picks up matching sidecar files and moves them
  alongside the video/wav into `takes/<rel>/deck (part N, take K).<ext>`.

**Files likely involved**:
- `src/clm/recordings/workflow/session.py` (retake pre-move scanner
  extension) — or a new helper in `naming.py` / a new module.
- Tests in `tests/recordings/test_session.py`.

**Reference**: `recordings-parts-and-takes.md` §11.

**Acceptance criteria**:
- A retake of a part whose active take has a cut-list sidecar
  preserves both the media files AND the sidecar in `takes/`.
- A retake of a part without sidecars still works (regression).

### Phase F — `clm recordings prune-takes` CLI [TODO]

**Goal**: Disk-retention tool for accumulated `takes/` history.

**Open design questions**:
- **Scope flag**: `--course`, `--lecture`, or global?
- **Retention semantics**: mtime-based (`--older-than=30d`) or
  state.json `superseded_at` timestamp?
- **Safety**: dry-run mode? Interactive confirmation? Report of
  bytes that will be freed?
- **Orphan handling**: takes referenced in `state.json` that are
  missing on disk (and vice versa) — delete state entries? Warn?
- **Backend quota awareness**: should the command also
  `DELETE /api/production/<uuid>.json` upstream for pruned takes?
  (Probably no — too destructive. Document the limitation.)

**What it accomplishes**:
- New CLI command: `clm recordings prune-takes --older-than=<spec>`.
- Updates `src/clm/cli/info_topics/commands.md` (mandatory per
  CLAUDE.md).

**Files likely involved**:
- `src/clm/cli/commands/recordings.py`
- `src/clm/cli/info_topics/commands.md`
- `src/clm/recordings/state.py` (if state-level pruning helper is
  useful)

**Reference**: archived handover §5; `recordings-parts-and-takes.md`
§11.

**Acceptance criteria**:
- Dry-run mode lists everything that would be deleted without
  touching disk.
- Real run removes files from `takes/` and updates `state.json` in
  lockstep.
- Orphaned state entries are cleaned up (or explicitly flagged —
  design decision).
- `clm info commands` output mentions the new command.

## 4. Current Status

- **In progress**: None.
- **Blocked on**: User review + design decisions listed under each
  phase's "Open design questions".
- **Tests**: Existing 554 recordings tests still pass (baseline).

## 5. Next Steps

1. **Pick a phase to start with.** Suggested order:
   - **Phase A** first (completes Phase 3 of the archived redesign;
     unlocks Phase C's UI).
   - **Phase B** can run in parallel with A (independent surface).
   - **Phase C** before D (D depends on C's UI hooks).
   - **Phase E**, **F** are independent; schedule by urgency.
2. **Answer the phase's "Open design questions"** — either via a
   brief user check-in or a short design note appended to this
   handover under the phase body. Do not implement until those are
   answered.
3. **Open a `claude/recordings-ux-followups-<phase-letter>-<slug>`
   branch** from `master` per phase.
4. **Re-run the phase check + phase-check skill** after each phase
   ships; update this handover's Status section.

## 6. Key Files & Architecture

See the archived handover §6 for the full inventory. The only new
areas this follow-up set touches that weren't already mapped:

| File | Role | Phase |
|---|---|---|
| `src/clm/cli/commands/recordings.py` | Recordings subcommand group | F |
| `src/clm/cli/info_topics/commands.md` | Version-accurate CLI docs (mandatory update) | F |
| `src/clm/recordings/web/templates/partials/parts.html` *(new)* | Per-part chip rendering | C, D |

## 7. Testing Approach

Same strategy as the archived feature: unit per module, integration
at the web-route layer, mock OBS and Auphonic HTTP. Each phase adds
targeted tests — details in the per-phase acceptance criteria.

## 8. Session Notes

### User preferences inherited from the archive

- Windows-first; Python for tooling, not bash.
- Each phase should be independently shippable — don't batch.
- Keep "part" as the UI term.
- Retakes must preserve previously-processed artifacts (extended by
  Phase E to include sidecars).

### Why each phase is "needs design" not "ready to code"

This handover exists precisely because each item has one or more
decisions that a developer cannot make unilaterally:
- Phases A, B, F: implementation choice (lecture-ID strategy, backoff
  constants, pruning semantics).
- Phases C, D: UX/UI decisions that need a wireframe or a user
  gesture choice.
- Phase E: domain discovery (which sidecar formats exist).

Do not skip the design step. The original redesign's five phases each
had a full design doc section backing them; these follow-ups do not,
and that gap is the reason they were deferred.

---

**Last updated**: 2026-04-17
**Next action**: User to pick the starting phase and answer its open
design questions.
