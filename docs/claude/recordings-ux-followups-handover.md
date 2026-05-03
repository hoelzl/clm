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

### Phase D — Restore-take UI [DESIGN LOCKED 2026-05-03]

**Goal**: Expose the already-implemented
`CourseRecordingState.restore_take` to the user. Depends on Phase C
to have somewhere to put the control.

**Design decisions (locked 2026-05-03)**:

**Q1 — Gesture**: Two-step in-panel. First click on Restore morphs
the button to "Confirm restore"; second click commits. No native
`confirm()` modal, no toast-only path. Cheap to implement (HTMX
partial swap), reversible by clicking elsewhere or pressing
`Escape`. Toast on success.

**Q2 — Helper location**: New `_swap_active_with_take(...)` private
helper next to `_demote_active_to_takes` in
`src/clm/recordings/workflow/session.py`. Reuses
`_scan_active_take_files`, `take_filename`, `takes_dir`, and
`_classify_retake_source`. State stays pure-data (no method on
`CourseRecordingState`).

**Q3 — Atomicity / rollback**: Mirror the existing
`_prepare_target_slot` pattern. Plan all `(src, dst)` moves up front
(both directions: active → `takes/` and chosen-take → active
filenames). Execute, recording each completed rename. On any
exception mid-execution, replay the inverse on the partial list
and re-raise without touching `state.json`. State mutation
(`state.restore_take(...)` + JSON dump) runs **last**, only after
all filesystem moves succeed.

**Q4 — After-swap display + take-numbering invariant**:
- Re-render chip strip + takes panel; take numbers are stable
  identity, so they reappear with their original `K`. A transient
  toast (`HX-Trigger: showToast`) confirms the action.
- **Invariant**: a retake after a restore must allocate a fresh
  take number — `max(active_take, max(takes[].take)) + 1` — never
  reuse an existing one. The current `state.record_retake`
  implementation uses `demoted.take + 1`, which would collide
  after a restore (e.g. takes [1,2,3] with active=3, restore→1
  makes active=1; a retake would compute 1+1=2, clobbering the
  existing take 2). Phase D fixes this in `record_retake` and
  adds an explicit interaction test.

**Q5 — Restore button placement**: New rightmost action column in
`partials/takes.html`'s takes table, beside "Open in Explorer".
The Phase C placeholder slot (`restore_url_for=None`) becomes the
hook.

**Q6 — Armed/recording protection**: Restore button is disabled
(and its column header replaced by an info note) whenever the
deck row is armed or recording. The route handler returns 409
Conflict if called against an armed deck — defense in depth.

**Q7 — Route shape**:
`POST /decks/{course}/{section}/{deck}/takes/{take}/restore?part=N`.
Resourceful counterpart to the Phase C
`GET /decks/.../takes` route. Returns the re-rendered takes panel
plus an `HX-Trigger` event that refreshes the chip strip and
shows the success toast.

**What it accomplishes**:
- Per-take "Restore" control that swaps the chosen take back to
  active, demoting the current active take to history.
- Corresponding backend route that orchestrates
  `state.restore_take(...)` plus the file moves on disk with
  rollback on failure.
- Fixes the `record_retake` numbering collision so retake after
  restore allocates a fresh take number.

**Files likely involved**:
- `src/clm/recordings/state.py` — fix `record_retake` numbering
  to `max(active_take, max(takes[].take)) + 1`.
- `src/clm/recordings/workflow/session.py` — new
  `_swap_active_with_take` helper with planned-rename rollback.
- `src/clm/recordings/web/routes.py` — new
  `POST /decks/{course}/{section}/{deck}/takes/{take}/restore`
  route; passes a real `restore_url_for` callable into the takes
  partial.
- `src/clm/recordings/web/templates/partials/takes.html` — add
  Restore action column with two-step morph.
- `src/clm/recordings/web/static/app.css` — `.btn-restore`,
  `.btn-restore-confirm` two-state styling.
- `src/clm/recordings/web/templates/base.html` — toast handler
  bound to the `showToast` HX-Trigger event.
- Tests:
  - `tests/recordings/test_state.py::test_retake_after_restore_does_not_collide`
    — locks the numbering invariant.
  - `tests/recordings/test_session.py::test_swap_active_with_take_*`
    — happy path, raw-only, final-only, missing target.
  - `tests/recordings/test_session.py::test_swap_active_with_take_rolls_back_on_failure`
    — patch `shutil.move` to raise mid-way; assert filesystem
    and state are untouched.
  - `tests/recordings/test_web.py::test_restore_route_*` — 200
    happy path returns refreshed partial; 409 when armed; 404
    when take missing.
  - Manual smoke (per CLAUDE.md UI rule): record → record retake
    → restore take 1 → record another retake; confirm chip strip
    flips, history shows fresh take number, no clobber.

**Reference**: `recordings-parts-and-takes.md` §10 step 5, §11
("When restoring a take, do we keep the current active as a new
take, or discard it?" — Phase D adopts the design doc's lean:
always keep, restore is a swap).

**Acceptance criteria**:
- Clicking "Restore" on a take morphs to "Confirm restore"; second
  click commits, swapping `state.json` and filesystem atomically
  (rolls back on failure).
- Final and archive files for the restored take become the active
  files; previously active files move to `takes/` with their own
  suffix and original take number.
- A retake after a restore allocates a take number strictly
  greater than every existing take in `state.json` — no overwrite.
- Restore button is disabled and the route returns 409 while the
  deck is armed or recording.
- Existing 786 recordings tests still pass; new state, session,
  and web-route tests added.

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
- **Design locked**: Phase D (2026-05-03) — ready for implementation.
- **In progress**: Phase D (Restore-take UI) — implementation
  starting on `worktree-recordings-ux-phase-c-design`.
- **Blocked on**: User review + design decisions for Phases E and F.
- **Tests baseline**: 786 recordings tests green at the end of
  Phase C (2026-05-03).

### Open Design Questions (consolidated)

Quick index for future sessions — full context lives in each phase's
body above. Resolve these before opening an implementation branch.

| Phase | Question | Status |
|---|---|---|
| D | Gesture: single click vs. confirm modal? | LOCKED — two-step in-panel |
| D | Where does the file-swap helper live (session.py vs new helper)? | LOCKED — `_swap_active_with_take` in `session.py` |
| D | Should backend file moves be atomic with `state.restore_take`, or two-phase with rollback? | LOCKED — planned-rename rollback, state mutation last |
| D | After-swap display: does the previously-active take reappear in history with a new take number? | LOCKED — stable take numbers + retake-after-restore numbering invariant |
| E | Concrete sidecar file-extension list (EDL only? FCP XML? subtitles?) | OPEN |
| E | Discovery strategy: glob beside the video/wav, or a known sidecar subdir? | OPEN |
| E | Backend-specific scanner (Auphonic only) vs. generic in `session.py`? | OPEN |
| F | Scope flag: `--course`, `--lecture`, or global? | OPEN |
| F | Retention semantics: mtime vs `state.json` `superseded_at`? | OPEN |
| F | Safety: dry-run mode? Interactive confirmation? | OPEN |
| F | Orphan handling: delete vs warn for state-without-disk and disk-without-state? | OPEN |
| F | Backend quota awareness: also delete the upstream Auphonic production? (lean: no) | OPEN |

## 5. Next Steps

1. **Implement Phase D** on this worktree
   (`worktree-recordings-ux-phase-c-design`). Design is locked —
   see §3 Phase D above. Implementation order:
   1. Fix `record_retake` numbering invariant + add
      `test_retake_after_restore_does_not_collide`.
   2. Add `_swap_active_with_take` helper with rollback +
      session-level tests.
   3. Add `POST .../takes/{take}/restore` route + web-route tests.
   4. Wire `restore_url_for` callable through the takes partial;
      add Restore-button two-step morph + CSS.
   5. Manual smoke per the acceptance criteria.
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
| `src/clm/recordings/web/templates/partials/takes.html` *(new)* | Inline take-history panel | C, D |

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
2026-05-03 — see §3 Phase C above for the answers. Phase D's
questions were locked in a Q1–Q7 walkthrough on the same day; the
user added the take-numbering invariant under Q4 (a retake after
restore must allocate `max(active_take, max(takes[].take)) + 1`
to avoid clobbering existing history).

---

**Last updated**: 2026-05-03 (Phase D design locked; Phase C
shipped earlier the same day — chip strip + inline take-history
panel + /takes route; 786 recordings tests green).
**Next action**: Implement Phase D on this worktree. See §5
Next Steps for the implementation order.
