# Recordings: Parts and Takes Model

**Status**: Draft for discussion
**Author**: Claude (Opus 4.7) + TC
**Date**: 2026-04-17
**Scope**: `src/clm/recordings/state.py`, `src/clm/recordings/workflow/session.py`, `directories.py`, `naming.py`

---

## 1. Problem

The current code has a single concept called "part" that conflates two different user intents:

- **Segment** — "this is part 2 of a 3-part lecture". Additive: parts 1, 2, 3 together make up the whole lecture.
- **Take** — "I made a mistake in part 2; let me record it again". Supersedes: take 2 replaces take 1.

Both intents end up routed through `ArmedDeck.part_number` and `RecordingPart.part`, which leads to several concrete bugs:

- `session.arm(part_number=1)` after an existing part-0 recording triggers the cascade in `_prepare_target_slot` (`session.py:106`) so the existing unsuffixed file becomes `(part 1)`; the new recording *also* tries to land at `(part 1)`, and the `_supersede_file` call at `session.py:132` moves it into `superseded/`. Net result: both takes end up in wrong places.
- `CourseRecordingState.assign_recording` (`state.py:125`) blindly uses `part = len(lecture.parts) + 1`, so state.json and the session's `part_number` can drift apart when the user types a number.
- Retaking an already-processed part silently overwrites `final/.../deck.mp4` (via `_download_video`, `auphonic.py:383`) and either crashes (Windows `FileExistsError` in `_archive_raw`) or loses the old raw (Unix `shutil.move` overwrite). **Auphonic credits are burned and the previous good take is destroyed.**
- When the filesystem cascade (`_prepare_target_slot`) renames a file on disk, state.json is not updated; its `raw_file` / `processed_file` paths become stale.

## 2. Goals

1. A single, unambiguous model with two distinct concepts: **part** (segment) and **take** (attempt).
2. No data loss on retake: every previously-processed final is preserved somewhere retrievable.
3. state.json and the filesystem stay consistent across every rename/supersede operation.
4. The UI keeps the word "part" (matches the `(part N)` filename suffix users already see). "Take" is secondary, surfaced as a history indicator.
5. Safe to implement incrementally — the schema change is additive.

## 3. Model

### 3.1 User-visible concepts

| Concept | Meaning | Default when user omits |
|---|---|---|
| **Part** | A segment of a lecture. Parts 1..N together make up the whole lecture. | "Next available part for this lecture" = `max(existing_parts) + 1` |
| **Take** | A recording attempt for a specific part. Later takes supersede earlier ones. | "Next available take for this part" = `max(existing_takes_for_part) + 1` |

### 3.2 Filename convention

The *active* take's filenames stay the way they are today — no take number in the name:

- Active raw: `deck (part N)--RAW.mp4` (or unsuffixed if there's only ever one part)
- Active final: `deck (part N).mp4`

Superseded takes are moved to `takes/` with a take number in the filename:

- Superseded raw: `takes/<rel>/deck (part N, take K)--RAW.mp4`
- Superseded final: `takes/<rel>/deck (part N, take K).mp4`

Rationale: the take concept is only visible when history exists, so putting it in the filename only for superseded files keeps the common case unchanged and makes the history self-describing.

### 3.3 Directory layout

```
recordings/
├── to-process/        live recordings waiting for processing
├── final/             current take's processed output  (active)
├── archive/           current take's raw               (active)
├── takes/             superseded takes                  (history, new)
│   └── <course>/<section>/
│       ├── deck (part 2, take 1).mp4           ← old final
│       └── deck (part 2, take 1)--RAW.mp4      ← old raw
└── superseded/        unchanged (zero-length / abandoned before processing)
```

`takes/` is for **fully-processed takes that were replaced by a later take**. `superseded/` is for **pre-processing garbage** (zero-length OBS outputs, accidental re-recordings caught before processing finished). Keeping them separate means a user browsing `takes/` sees exactly what they spent Auphonic credits on.

### 3.4 Single-part optimization

When a lecture has only one part, its filenames are unsuffixed (`deck.mp4`, `deck--RAW.mp4`) — today's behavior is preserved by `_prepare_target_slot`'s cascade when the user records a second part. Adding takes doesn't change this: the takes/ shelf for a single-part lecture uses `deck (take 1).mp4` / `deck (take 2).mp4` without the `(part N)` prefix.

## 4. Retake lifecycle

User clicks **Retake** on part K of a lecture that has already been processed.

1. **Session-level pre-move** (new logic, runs before the OBS output moves into `to-process/`):
   - Compute `take = max(existing takes for part K) + 1`. If no history exists, `take = 2` (the current active take becomes take 1).
   - If `final/<rel>/deck (part K).mp4` exists: move it to `takes/<rel>/deck (part K, take K-1).mp4`.
   - If `archive/<rel>/deck (part K)--RAW.mp4` exists: move it to `takes/<rel>/deck (part K, take K-1)--RAW.mp4`.
   - If `to-process/<rel>/deck (part K)--RAW.mp4` exists (incomplete previous take): same move.
2. **Rename the new OBS output** into `to-process/<rel>/deck (part K)--RAW.mp4` (the normal slot).
3. **Processing proceeds normally** — the backend sees a raw file in `to-process/` and produces a fresh final in the unadorned slot.

This is idempotent: if the pre-move finds no existing files (because the user retook before the previous take finished processing), it simply does nothing for the missing pieces.

## 5. state.json schema changes

### 5.1 New `TakeRecord` dataclass

```python
class TakeRecord(BaseModel):
    take: int                  # 1, 2, 3, ...
    raw_file: str              # absolute or recordings-root-relative path
    processed_file: str | None
    git_commit: str | None = None
    git_dirty: bool = False
    recorded_at: str           # ISO 8601
    status: RecordingStatus
    superseded_at: str | None = None  # ISO 8601 of the moment this take was retired
```

### 5.2 `RecordingPart` additions

```python
class RecordingPart(BaseModel):
    part: int
    # Active take (current best):
    raw_file: str
    processed_file: str | None
    git_commit: str | None
    git_dirty: bool
    recorded_at: str
    status: RecordingStatus
    # History:
    takes: list[TakeRecord] = []    # NEW — superseded takes, oldest first
    active_take: int = 1            # NEW — which take number the active fields refer to
```

All new fields have defaults, so existing state.json files load unchanged and are upgraded on the next save.

### 5.3 Operations

New methods on `CourseRecordingState`:

```python
def record_retake(
    self,
    lecture_id: str,
    part: int,
    new_raw_file: str,
    *,
    git_commit: str | None,
    git_dirty: bool,
) -> TakeRecord:
    """Demote the part's active fields into `takes` and replace them with the new take.

    Returns the TakeRecord that was just demoted (for the caller to move on disk).
    Raises ValueError if the part does not exist.
    """

def restore_take(self, lecture_id: str, part: int, take: int) -> None:
    """Swap take K with the current active take.

    Used by the UI "restore this take" action. Moves the active fields into
    `takes` and promotes the requested TakeRecord to active. The caller is
    responsible for moving the corresponding files on disk (or this method
    can accept a callback — see §7).
    """

def rename_recording_paths(
    self,
    old_raw: str,
    new_raw: str,
    *,
    old_processed: str | None = None,
    new_processed: str | None = None,
) -> None:
    """Update raw_file/processed_file references after a filesystem rename.

    Scans all lectures, all parts, all takes. No-ops if the old paths aren't
    found (cascade may have acted on files not yet tracked in state.json).
    """
```

## 6. Session ↔ state.json reconciliation

Today, the session (`RecordingSession`) and `CourseRecordingState` are decoupled: the session moves files, the state manager tracks files, and the watcher is the only glue (it calls `assign_recording` when a new raw lands in `to-process/`). This is why the cascade in `_prepare_target_slot` leaves state.json stale.

**Proposal**: inject a `CourseRecordingState`-like handle into `RecordingSession` so every filesystem operation is paired with a state-mutation. Specifically:

- When `_prepare_target_slot` renames a file on disk: call `state.rename_recording_paths(...)` in the same try-block.
- When a retake pre-move fires: call `state.record_retake(...)` to update the in-memory model, then move the files.
- On session shutdown / server stop: `state.save()` is already called after each mutation — no extra plumbing needed.

The session should treat the state handle as optional (tests that don't care about state tracking can pass `None`), but the web app always wires it up.

## 7. Answering the concrete question from the conversation

> If we have already processed one or more parts of a recording and record either a new take for one of the existing parts or a new part, will "process" trigger the correct processing?

### Case A — New part added after some parts were already processed

**Correct today.** Traced through the code:

- `RecordingsWatcher._on_file_event` (`watcher.py:143`) only sees events in `to-process/`. Previously processed parts have their raw in `archive/` (moved by `_archive_raw`, `auphonic.py:431`), not in `to-process/`, so the watcher never considers them again.
- `_scan_existing` (`watcher.py:178`) on server restart only walks `to-process/`.
- Every backend's `accepts_file` is filename-pattern only, but archived raws are outside `to-process/` so they never reach it.
- Manual `/process` (`routes.py:214`) only lists files from `find_pending_pairs(to-process)` (`directories.py:79`), so archived raws aren't shown.

The new part gets processed in isolation. ✅

### Case B — New take for an already-processed part

**Broken today.** This is the core motivation for the `takes/` directory and the session pre-move described in §4:

1. `_prepare_target_slot` (`session.py:81`) only scans `to-process/`, so it doesn't see the previous take's raw in `archive/` or final in `final/`. No supersede happens.
2. When the new take finishes processing, `_archive_raw` (`auphonic.py:431`) calls `shutil.move(raw, archive/.../deck (part K)--RAW.mp4)`. The destination already exists → on Windows this raises `FileExistsError`; on Unix it overwrites and the old raw is lost.
3. `_download_video` (`auphonic.py:383`) writes unconditionally to `job.final_path` — the previous processed final is overwritten.
4. state.json has no concept of takes; the part is double-booked.

With the design in this document:

1. Before the new OBS output lands in `to-process/`, the session's retake path moves the existing `final/` + `archive/` files into `takes/`.
2. `_archive_raw` and `_download_video` now see clear destinations.
3. state.json gets a new `TakeRecord` demoted into `takes[]` and fresh active fields for the new take.

## 8. UI changes

- Each lecture row shows its parts inline: `▶ 1 │ ▶ 2 │ ▶ 3` with a status dot per part.
- Clicking a part opens a panel showing:
  - Active take metadata (recorded_at, git commit, processed file path, play button).
  - Takes history as a collapsed list. Expanding reveals each superseded take with restore/delete actions.
- Next to the lecture title: a **+ Record next part** button (default action) and a separate **↻ Retake** dropdown with a row per existing part.

The retake dropdown is what disambiguates "new part" from "new take of existing part" without a modal.

## 9. Tests to add (`tests/recordings/`)

In `test_session.py`:

- `test_retake_moves_final_and_archive_to_takes` (happy path)
- `test_retake_when_only_raw_exists` (processing failed before retake)
- `test_retake_when_only_final_exists` (raw manually deleted)
- `test_retake_when_nothing_exists_yet` (retake before first processing finished)
- `test_new_part_after_processed_parts_preserves_existing` (regression guard for Case A)
- `test_session_updates_state_json_after_cascade` (regression guard for the stale-paths bug)

In `test_state.py`:

- `test_record_retake_demotes_active_to_takes`
- `test_record_retake_rejects_unknown_part`
- `test_restore_take_swaps_active_and_history`
- `test_rename_recording_paths_scans_all_takes`
- `test_load_older_state_json_without_takes_field` (backcompat)

In `test_directories.py`:

- `test_takes_dir_helper_returns_correct_subtree`
- `test_find_pending_pairs_ignores_takes_subtree`

## 10. Implementation order

1. **Schema-only change first**: add `takes: list[TakeRecord]` + `active_take` to `RecordingPart`, add `record_retake` / `restore_take` / `rename_recording_paths` methods. No UI, no filesystem changes. Ship — state.json upgrades transparently.
2. **Directory helpers**: add `takes_dir(root)` to `directories.py`. Ship.
3. **Session pre-move for retake**: update `_prepare_target_slot` (or a new `_prepare_retake_slot`) to move `archive/` + `final/` copies to `takes/` when a retake is detected, wire `state.record_retake` into the session, and fix the stale-paths bug via `rename_recording_paths`. Ship with full test coverage.
4. **UI**: add the parts inline display and retake dropdown. Ship.
5. **Restore**: add the restore-take UI + file-swap helper. Ship.

Each step lands on master without breaking earlier releases.

## 11. Open questions

- **Should `takes/` be disk-pruneable?** Auphonic-processed videos are large; a course with many retakes accumulates gigabytes. Options: (a) keep everything forever (user prunes by hand), (b) prune takes older than N months, (c) make it a CLI command `clm recordings prune-takes --older-than=…`. My lean: ship (a) initially, add (c) when someone complains.
- **When restoring a take, do we keep the current active as a new take, or discard it?** Proposal: always keep it. Restore is a swap, not an overwrite — matches the "never lose work" ethos.
- **Should we also version the cut list artifact?** When Auphonic produces a cut list (`job.artifacts["cut_list"]`), today it's stored next to the final. Takes should preserve it too — use the same `(part N, take K)` suffix.
