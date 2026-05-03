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

### Phase C — UI parts-inline display [SHIPPED 2026-05-03]

**Goal**: Replace the bare `<input name="part_number">` selector with
a per-part chip strip that doubles as the part selector and the
status display. Also fixes the `part_number` snap-back bug as a
side-effect of removing the input. Take-history is revealed inline.
Restore-take action stays deferred to Phase D.

**Design decisions (locked 2026-05-03)**:

**Q1 — Chip purpose**: Selection + status (Option B). Chips are the
part selector; clicking a chip writes selection state to
`sessionStorage`. Action buttons (Record/Arm/Process/Advance) consume
the selection. A permanent rightmost "next part" chip represents
"create a new part" and is the default selection on every fresh
render. Single-part recordings and multi-part recordings share the
same mental model: "next part" is always one click away.

**Q2 — Per-chip visual encoding**:
- Status palette tracks the existing badge palette in `app.css`:
  amber (`recorded`, `processing` with pulse), green (`processed`),
  red (`failed`), neutral-dashed outline (next-part placeholder).
- Processed-with-failed-retry adds a small red corner dot (matches
  the existing `failed_job_id && state != 'failed'` heuristic in
  `lectures.html:138-140`).
- Selection: 2px blue outline ring on top of any fill; ring goes
  neutral when the deck row has no valid action (already armed or
  recording).
- Take-count indicator: superscript number after the part number
  (`▶ 2³`) when `takes[]` has more than one entry; absent for
  single-take parts. Capped cosmetically at `⁹⁺`; full count lives
  in the tooltip.
- Tooltip via native `title` attribute, multi-line via `&#10;`:
  ```
  Part {N} — {state label}
  Takes: {K}
  Last job: {job_id_short} ({status})    # only when relevant
  ```
  `aria-label` mirrors the first line so screen readers don't get
  the multi-line noise. Selection state goes into `aria-pressed` on
  the chip button, not into the tooltip.

**Q3 — Default selection + persistence**:
- **Default**: "next part" chip is selected on first render (matches
  current `value="0"` default semantics so the keyboard flow
  doesn't regress).
- **Persistence**: client-side `sessionStorage`, keyed by
  `(course_slug, section_name, deck_name)`. Chip render reads
  storage on first paint after every swap; no snapshot/restore
  handler needed. Survives every kind of swap including
  `hx-target="body"` paths that flush the entire DOM.
- **Auto-advance**: sticky except when the previously selected chip
  was "next part" — then re-target the new "next part" after the
  cascade, since the chip the user selected has just become an
  existing part and staying on it would be confusing.
- **Stale-selection fallback**: every render validates the stored
  selection against the actual chips present and falls back to
  "next part" when stale (e.g. the part was deleted out from
  underneath us via the CLI).
- **Armed/recording deck**: chip strip is read-only until disarm /
  stop; armed part shown with selection ring + small pulsing dot;
  clicking another chip is suppressed (cursor `not-allowed`).

**Q4 — Click semantics**:
- **Single-click / Space / Enter on chip** = select chip
  (client-side only, writes `sessionStorage`). No network call.
  Re-clicking the selected chip is a no-op, never a toggle to
  "unselected" — there is always a selection.
- **Action buttons stay inline** next to the chip strip (no
  per-chip overflow menu). They always operate on the selected
  chip.
- **Right-click / long-press / hover-revealed `⋯` button** = open
  take-history (Q5). Falls back gracefully on touch and for users
  who don't right-click.
- **Tab** focuses chips and action buttons in row order; **Arrow
  Left/Right** moves focus inside the chip strip (ARIA tablist
  pattern).
- **Drop** the existing "Enter inside `part_number` input triggers
  Record" handler (`base.html:191-201`) — it has no input to
  attach to. Optional R/A/P single-key shortcuts deferred to a
  later pass; not in Phase C scope.

**Q5 — Take-history reveal**:
- Container: **inline expand** below the deck row (sibling `<tr>`).
  No singleton enforcement — multiple deck panels can be open at
  once.
- Per-take fields: take number badge, recorded-at timestamp, file
  stem (truncated middle), status mini-badge, optional
  "open in Explorer" link (Windows-first, `file://` URL).
- **No Restore button in Phase C** — Restore is Phase D's
  headliner. Shipping a disabled placeholder would clutter the
  panel; better to introduce the button visibly during Phase D.
- Loading: HTMX **lazy fetch** at
  `GET /decks/{course}/{section}/{deck}/takes` (final URL TBD
  during implementation). Keeps `/lectures` payload lean and lets
  the panel evolve independently (e.g. when Phase E adds
  sidecars, only the new route changes).
- SSE refresh: open panel piggy-backs `data-sse-refresh="job"` and
  refetches on each `job` event; chip strip outside the panel
  refreshes per the existing wiring.
- Close gestures: toggle the `⋯` button, right-click the chip
  again, or `Escape` while focus is inside the panel. Outside-
  click does NOT close (panels are inline, not overlays).

**Q6 — Wrapping / overflow**:
- Chip strip uses `flex-wrap: wrap; gap: 0.4rem`. No horizontal
  scroll inside the cell, no truncation. Status visibility wins
  over row-height compactness.
- Status column gets `min-width: ~240px`; below that the table
  scrolls horizontally (acceptable on phones — recording is a
  desktop activity).
- Action buttons keep Phase 4's `flex-wrap` layout unchanged.
- Take-count display capped at `⁹⁺`; full number stays in tooltip.

**Q7 — Retake-dropdown integration**:
- **No separate retake dropdown.** Chip selection covers it: an
  existing chip is the retake target; the "next part" chip is the
  fresh-record target.
- **No confirmation modal.** When the selected chip already has
  a take, the Record button's `title` warns
  (`"Will move the current take to takes/ before recording"`) and
  a small warning icon appears next to the button.
- **Button labels swap** based on selection: Record↔Retake,
  Arm↔Re-arm.
- **Process targets the selected chip** by default. A separate
  **Process all** button appears only when ≥2 unprocessed parts
  exist on the deck — preserves the fire-and-forget batch flow
  without breaking the chip-centric mental model.
- **Advance targets the selected chip** (replaces the current
  "first recorded part" probe at `lectures.html:206-207`).

**Q8 — Snap-back bug**:
- **Root cause** (most likely): the existing `_partNumberSnapshots`
  code in `base.html:47-82` cannot bridge form-shape changes. When
  a deck transitions Record/Arm form → Pause/Stop form (during
  arm/recording) and back, the input does not exist in the
  intermediate render, so the snapshot map loses the entry. On the
  next disarm, the fresh form arrives with `value="0"` and there's
  nothing to restore.
- **Fix**: Phase C's chip rollout dissolves the bug — selection
  lives in `sessionStorage`, not in a DOM input. Form-shape
  changes are irrelevant; chip strip just reads storage on every
  render.
- **Cleanup in the Phase C diff**: remove `_partNumberSnapshots`,
  `_snapshotPartNumbers`, `_restorePartNumbers`, the
  `htmx:beforeSwap` / `htmx:afterSwap` listeners that drove them,
  and the Enter-on-`part_number` keyboard handler.
- **Click delegation**: single document-level click listener using
  `event.target.closest('[data-chip]')` so swaps cannot break the
  binding.

**What it accomplishes**:
- Lectures page shows parts inline per deck via a chip strip in
  the Status column.
- Each chip surfaces its current state, take count, and selection
  status; click selects, hover/right-click/`⋯` reveals takes.
- Action buttons (Record/Arm/Process/Advance) operate on the
  selected chip; labels swap to Retake/Re-arm when appropriate.
- Eliminates the `part_number` snap-back bug as a side-effect.

**Files likely involved**:
- `src/clm/recordings/web/templates/lectures.html` — chip strip
  in Status column, action buttons consume chip selection,
  warning icon on Retake.
- New: `src/clm/recordings/web/templates/partials/parts.html` —
  chip strip partial.
- New: `src/clm/recordings/web/templates/partials/takes.html` —
  inline-expand take-history panel.
- `src/clm/recordings/web/templates/base.html` — drop dead code,
  add chip-selection JS (event delegation, sessionStorage I/O,
  ARIA tablist focus management).
- `src/clm/recordings/web/routes.py` — new
  `GET /decks/{course}/{section}/{deck}/takes` route; rewire
  `/process`, `/advance`, `/record`, `/arm` to read part from a
  unified place (form field stays the wire format; chip JS
  populates it before submit).
- `src/clm/recordings/web/static/app.css` — `.chip-strip`,
  `.chip`, `.chip-selected`, `.chip-armed`, `.chip-status-*`
  classes; superscript take-count style.
- Tests:
  - `tests/recordings/test_web.py` — server-rendered HTML
    assertions (chip elements present per part, default-selected
    next-part chip, tooltip strings, status classes, button
    label swap, /takes route happy path).
  - Manual smoke test (per CLAUDE.md UI rule): record-stop-rerecord
    sequence with intervening SSE traffic to confirm the snap-back
    symptom is gone.

**Reference**: `docs/claude/design/recordings-parts-and-takes.md` §8.

**Acceptance criteria**:
- Decks render a chip strip with one chip per existing part plus a
  trailing "next part" chip; default selection is the "next part"
  chip.
- Clicking a chip updates selection visibly and persistently
  (survives SSE refresh, body swap, page reload within the tab).
- Action buttons act on the selected chip; labels swap to
  Retake/Re-arm when the selection is an existing part.
- Right-click / `⋯` opens an inline take-history panel below the
  deck row, lazy-fetched, refreshed on `job` SSE events.
- Single-part recordings still render sensibly (one part chip
  plus the next-part chip).
- The `part_number` snap-back symptom does not reproduce in the
  manual smoke test (record → stop → re-arm sequence).
- Existing 740 recordings tests still pass; new HTML-render and
  /takes-route tests added.

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

- **Shipped**: Phases A, B, and C (Phases A+B folded into Recordings
  App Hardening PR #42 on 2026-04-19; Phase C shipped on
  branch `claude/recordings-ux-followups-c-parts-inline`,
  2026-05-03).
- **In progress**: None.
- **Blocked on**: User review + design decisions listed under each
  remaining phase's "Open design questions" (D, E, F).
- **Tests baseline**: 786 recordings tests green at the end of
  Phase C (2026-05-03).

### Open Design Questions (consolidated)

Quick index for future sessions — full context lives in each phase's
body above. Resolve these before opening an implementation branch.

| Phase | Question | Status |
|---|---|---|
| D | Gesture: single click vs. confirm modal? | OPEN |
| D | Where does the file-swap helper live (session.py vs new helper)? | OPEN |
| D | Should backend file moves be atomic with `state.restore_take`, or two-phase with rollback? | OPEN |
| D | After-swap display: does the previously-active take reappear in history with a new take number? | OPEN |
| E | Concrete sidecar file-extension list (EDL only? FCP XML? subtitles?) | OPEN |
| E | Discovery strategy: glob beside the video/wav, or a known sidecar subdir? | OPEN |
| E | Backend-specific scanner (Auphonic only) vs. generic in `session.py`? | OPEN |
| F | Scope flag: `--course`, `--lecture`, or global? | OPEN |
| F | Retention semantics: mtime vs `state.json` `superseded_at`? | OPEN |
| F | Safety: dry-run mode? Interactive confirmation? | OPEN |
| F | Orphan handling: delete vs warn for state-without-disk and disk-without-state? | OPEN |
| F | Backend quota awareness: also delete the upstream Auphonic production? (lean: no) | OPEN |

## 5. Next Steps

1. **Phase D** (Restore-take UI) is the natural next step now that
   Phase C's chip strip and take-history panel exist as anchor
   points. Open design questions still need answers — see §3 Phase D.
2. **Phases E and F** are independent; schedule by urgency.
3. **Re-run the phase-check skill** after each phase ships; update
   this handover's Status section.

## 6. Key Files & Architecture

See the archived handover §6 for the full inventory. The only new
areas this follow-up set touches that weren't already mapped:

| File | Role | Phase |
|---|---|---|
| `src/clm/cli/commands/recordings.py` | Recordings subcommand group | F |
| `src/clm/cli/info_topics/commands.md` | Version-accurate CLI docs (mandatory update) | F |
| `src/clm/recordings/web/templates/partials/parts.html` *(new)* | Per-part chip rendering | C, D |
| `src/clm/recordings/web/templates/partials/takes.html` *(new)* | Inline take-history panel | C |

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

Phase C's design questions were locked in a Q1–Q8 walkthrough on
2026-05-03 — see §3 Phase C above for the answers.

---

**Last updated**: 2026-05-03 (Phase C shipped: chip strip + inline
take-history panel + /takes route; 786 recordings tests green).
**Next action**: Phases D, E, F still need their design questions
answered before implementation. Phase D is the natural next step.
