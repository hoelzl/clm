# Handover: Slide-Deck-Based Lecture Selection & Dashboard Improvements

## 1. Feature Overview

Converted the recordings dashboard Lectures page from topic-based listing to
slide-deck-based listing, then added a suite of usability improvements
identified during smoke testing: a superseded-recordings folder, OBS
connect/disconnect controls, watcher scan-on-start, dynamic multi-part naming,
and per-deck recording status with manual processing.

**Branch**: `worktree-magical-chasing-cookie`
**Plan**: `~/.claude/plans/inherited-coalescing-mitten.md`
**Status**: All phases complete, ready to merge.

## 2. Design Decisions

### Deck-based listing (original commit)
Replaced topic-based listing with individual slide-deck (notebook file) rows.
Course object is built once at startup via `Course.from_spec()` and cached in
`app.state.course`. Language toggle uses a `clm_lang` cookie.

### Superseded folder (Phase 1a)
When re-recording over an existing file, the old recording moves to
`superseded/` rather than being overwritten or causing an error. Preserves
directory structure mirroring `to-process/`. Incrementing `(2)`, `(3)` suffixes
prevent collisions in superseded. Also moves companion `.wav` files.

**Why not just overwrite?** User wants the ability to recover old takes.
**Why not `trash/` or `deleted/`?** The term "superseded" is more precise —
these files were replaced by a newer recording, not explicitly deleted.

### OBS connect/disconnect (Phase 1b)
Added because OBS may not be running at server startup. The `ObsClient` already
supports disconnect+reconnect since callbacks are stored in
`_record_callbacks` and re-registered on each `connect()` call. Simple
POST endpoints + buttons in the status partial.

### Watcher scan-existing (Phase 2)
Added a `_submitted` set alongside `_processing` in `WatcherState` to prevent
double-dispatch across stop/start cycles. `_scan_existing()` walks
`to-process/` after the observer starts, using the same `_on_file_event` path
as live events.

**Why not check job manager for existing jobs?** Couples watcher to job manager
internals. The `_submitted` set is simpler and sufficient for a single server
lifetime.

### Dynamic part naming (Phase 3)
**Rule**: single recording = no `(part N)` suffix; multiple parts = all get
suffixes including part 1. When recording part N>0 and an unsuffixed file
exists, it's renamed to `(part 1)` — cascade includes companion `.wav` and
matching files in `final/`. Supersede logic handles re-recording an existing
part.

**Why not always use part suffixes?** User preference: single recordings should
have clean names matching the slide deck exactly.

### Deck recording status (Phase 4)
New `deck_status.py` module scans `to-process/` and `final/` per-section.
Priority: completed > ready > recorded > failed > no_recording. Status badges,
visible part-number input, and a "Process" button for manual job submission
added to the lectures template.

## 3. Phase Breakdown

### Phase 0: Slide-deck-based lecture selection [DONE]
- Commit `3d71b1e`: `ArmedTopic` → `ArmedDeck`, Course object caching,
  `/lectures` with deck rows, language toggle, `/lectures/refresh`

### Phase 1a: Superseded folder [DONE]
- Added `superseded/` as fourth subdirectory
- `_supersede_file()` helper in `session.py`
- Integrated into `_rename_recording()` — if target exists, supersede first

### Phase 1b: OBS connect/disconnect buttons [DONE]
- `POST /obs/connect` and `POST /obs/disconnect` endpoints
- Connect/Disconnect buttons in status partial next to OBS badge

### Phase 2: Watcher scans existing files on start [DONE]
- `WatcherState._submitted` set prevents double-dispatch
- `_scan_existing()` walks `to-process/` after observer starts
- `mark_submitted()` called after successful `job_manager.submit()`

### Phase 3: Dynamic part naming [DONE]
- `find_existing_recordings()` in `naming.py` scans directory for matching files
- `_prepare_target_slot()` in `session.py` handles cascade rename and supersede
- `_rename_final_to_part1()` handles the `final/` directory cascade

### Phase 4: Deck recording status + manual processing [DONE]
- New `deck_status.py` module with `DeckRecordingState` enum and `DeckStatus`
- `scan_section_deck_statuses()` for batch scanning
- Status badges, part-number input, Process button in `lectures.html`
- `POST /process` endpoint for manual job submission
- `_get_failed_jobs_map()` helper in routes

## 4. Current Status

**All phases complete.** 444 recordings tests pass. Ruff clean.

### Bug fix applied during smoke test
`_build_course()` in `app.py` was using `spec_file.parent` as course root.
Fixed to use `resolve_course_paths(spec_file)` which correctly goes up 2 levels
for spec files in `course-specs/` subdirectories.

### Not yet done
- [ ] Commit the changes (14 modified files, 2 new files)
- [ ] Merge to master
- [ ] Manual smoke test of the full feature set with live course spec

## 5. Next Steps

1. **Commit** all changes as a single commit (or split by phase if preferred)
2. **Smoke test** with the ML-AZAV course spec:
   - Start server: `clm recordings serve C:\Users\tc\Tmp\AuphonicTest --spec-file ...machine-learning-azav.xml`
   - Verify lectures page shows status badges
   - Arm a deck, record, check supersede on re-record
   - Test OBS connect/disconnect
   - Test watcher picking up pre-existing files
   - Test manual Process button
3. **Merge** to master

## 6. Key Files & Architecture

### Source files modified

| File | Change |
|---|---|
| `src/clm/recordings/web/app.py` | Fixed `_build_course()` to use `resolve_course_paths()` |
| `src/clm/recordings/web/routes.py` | Added `/obs/connect`, `/obs/disconnect`, `/process` endpoints; deck status integration in `/lectures`; `_get_failed_jobs_map()` helper |
| `src/clm/recordings/web/templates/base.html` | CSS for new deck status badges |
| `src/clm/recordings/web/templates/lectures.html` | Status column, part-number input, Process button |
| `src/clm/recordings/web/templates/partials/status.html` | OBS Connect/Disconnect buttons |
| `src/clm/recordings/workflow/directories.py` | `superseded/` in `SUBDIRS`; `superseded_dir()` helper |
| `src/clm/recordings/workflow/naming.py` | `find_existing_recordings()` — scans dir for matching raw files |
| `src/clm/recordings/workflow/session.py` | `_supersede_file()`, `_prepare_target_slot()`, `_rename_final_to_part1()`; updated `_rename_recording()` to use them |
| `src/clm/recordings/workflow/watcher.py` | `WatcherState._submitted` set; `mark_submitted()`; `_scan_existing()` called from `start()` |

### New source files

| File | Purpose |
|---|---|
| `src/clm/recordings/workflow/deck_status.py` | `DeckRecordingState` enum, `DeckStatus` dataclass, `scan_deck_status()`, `scan_section_deck_statuses()` |

### Test files modified/created

| File | Change |
|---|---|
| `tests/recordings/test_directories.py` | `superseded_dir()` test; updated assertion counts |
| `tests/recordings/test_naming.py` | `TestFindExistingRecordings` class (7 tests) |
| `tests/recordings/test_session.py` | `TestSupersede` (5 tests), `TestDynamicPartNaming` (7 tests) |
| `tests/recordings/test_watcher.py` | Submitted-set tests (3), `TestScanExisting` (3 tests) |
| `tests/recordings/test_web.py` | `TestObsControls` (5 tests) |
| `tests/recordings/test_deck_status.py` | New: `TestScanDeckStatus` (8 tests), `TestScanSectionDeckStatuses` (1 test) |

### Architecture

```
lectures page request
  └─► routes.lectures()
        ├─► Course.sections → deck list
        ├─► scan_section_deck_statuses() → status per deck
        └─► render lectures.html (badges, part input, process btn)

recording flow
  └─► session.arm(course, section, deck, part)
        └─► OBS record → _rename_recording()
              └─► _prepare_target_slot()
                    ├─► find_existing_recordings() — survey what exists
                    ├─► rename unsuffixed → (part 1) if adding parts
                    └─► _supersede_file() if target exists

watcher.start()
  ├─► Observer (watchdog) for new files
  └─► _scan_existing() for pre-existing files
        └─► _on_file_event() → _dispatch() → job_manager.submit()
```

## 7. Testing Approach

- **Unit tests**: All logic tested via pytest with `tmp_path` fixtures
- **Session tests**: Mock OBS client, `_fire_event()` helper simulates OBS events, `_wait_for_state()` polls for async completion
- **Web tests**: FastAPI `TestClient` with mocked OBS; mock Course objects injected via `app.state.course`
- **Watcher tests**: `_FakeBackend` + `_InMemoryJobStore` test doubles; both unit dispatch tests and live watchdog observer tests
- **Run**: `uv run pytest tests/recordings/ -x` (444 tests, ~8s)

## 8. Session Notes

### Deferred work (from original feature)
- **(c) Discard & re-record**: UI button to move bad takes to `discarded/`.
  Currently manual file operations. (Partially addressed by the superseded
  folder — re-recording now auto-supersedes.)
- **(d) Retrospective split**: UI button to rename recording to part 1 and
  re-arm as part 2. Currently manual.

### Sanitization note
`recording_relative_dir()` sanitizes both course slug and section name for
filesystem safety. `find_existing_recordings()` and `scan_deck_status()`
must match against the sanitized form. The `scan_deck_status` function uses
the directory names directly (already sanitized) but matches deck names via
`sanitize_file_name()`.
