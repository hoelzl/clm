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

**Status**: Phases A and B were absorbed by the Recordings App Hardening
plan (shipped via PR #42 on 2026-04-19) — see
`docs/claude/recordings-hardening-handover-archive.md` §3 Phases 2 and 3.
Phases C, D, E, F remain [TODO]. Phases are not strictly ordered, but
Phase D depends on Phase C's UI hooks.

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

### Phase A — `record_retake` wiring + web-app state injection [SHIPPED]

Absorbed by Recordings App Hardening Phase 2 (PR #42, 2026-04-19). See
`docs/claude/recordings-hardening-handover-archive.md` §3 Phase 2 for
the shipped summary. Decisions taken: lecture-ID resolution Option 1
(web-app resolves and passes `lecture_id` into `ArmedDeck` at `arm()`
time, format `"<section>::<deck>"`), `CourseRecordingState` cached on
`app.state.recording_states` (lazy per-course), `course_id ==
course_slug`, `record_retake` triggered at retake pre-move time inside
`session.py`.

### Phase B — OBS reconnect loop [SHIPPED]

Absorbed by Recordings App Hardening Phase 3 (PR #42, 2026-04-19). See
`docs/claude/recordings-hardening-handover-archive.md` §3 Phase 3 for
the shipped summary. Decisions taken: 5 s ping interval, 1 → 2 → 4 →
8 → 30 s backoff, `get_record_status` probe + event-thread liveness
check, both warning banner and disabled Record/Arm buttons,
`obs:<state>` SSE events routed onto the existing `status` channel.

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

- **Shipped**: Phases A and B (folded into Recordings App Hardening
  PR #42, 2026-04-19).
- **In progress**: None.
- **Blocked on**: User review + design decisions listed under each
  remaining phase's "Open design questions".
- **Tests baseline**: 740 recordings tests green at the end of
  hardening + Commit-B (2026-04-20).

## 5. Next Steps

1. **Pick a phase to start with.** Suggested order:
   - **Phase C** first (parts-inline UI; foundation for D).
   - **Phase D** after C (Restore-take UI depends on C's hooks).
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
- Phase F: implementation choice (pruning semantics).
- Phases C, D: UX/UI decisions that need a wireframe or a user
  gesture choice.
- Phase E: domain discovery (which sidecar formats exist).

Do not skip the design step. The original redesign's five phases each
had a full design doc section backing them; these follow-ups do not,
and that gap is the reason they were deferred.

---

**Last updated**: 2026-05-03 (Phases A and B confirmed shipped via
hardening PR #42; suggested order refreshed).
**Next action**: User to pick the starting phase (C, D, E, or F) and
answer its open design questions.
