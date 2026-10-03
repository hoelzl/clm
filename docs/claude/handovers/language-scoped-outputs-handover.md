# Language-Scoped Outputs (#1031, #1034) — Handover

**Created**: 2026-10-03 | **Updated**: 2026-10-03 | **Status**: Phase 1 in progress
| **Issues**: https://github.com/hoelzl/clm/issues/1031 (`<dir-group lang>`),
https://github.com/hoelzl/clm/issues/1034 (language-suffixed assets). The design
review that this document implements is the first comment on each issue.
| **Branch**: `claude/issue-1031-1034-lang-scoped-outputs`

---

## 1. Feature Overview

Two related gaps in how a bilingual course ships non-deck files:

1. **#1034: topic assets.** Every topic asset (image, video, data file) is copied
   into **every** language output and every format × kind directory. There is no
   way to ship `img/breakout.de.mp4` to DE only. A 2.4 MB demo video lands 6× per
   language, about a third of the PythonCourses Copilot course output.
2. **#1031: dir-groups.** A `<dir-group>` can rename its output directory per
   language, but its source is shared. A German example project and an English one
   (genuinely different projects, not translations) both ship to both courses.

Both need the same building block: **one rule that decides which output languages a
file or directory goes to**, used both by the copy operation and by the provenance
manifest. The manifest computes placements independently of the copy, and the
release pipeline copies by manifest. If the two disagree, a release either ships
nonexistent paths or misses real ones. This has happened before (#664 review
finding H1).

## 2. Design Decisions

### D1. Asset language suffix: `<stem>.<lang>.<ext>` (#1034 part 1)

- A topic asset whose file name has a `.de` / `.en` segment **immediately before
  the final extension** is copied only into that language's outputs.
  `img/breakout.de.mp4` goes to DE only, `img/diagram.en.png` to EN only.
- Files without the tag (`model.v2.png`, `data.final.csv`, `x.fr.png`) behave
  exactly as before. Only `de`/`en` (the languages clm supports) match.
- Applies to `DataFile`, `DuplicatedImageFile` and `SharedImageFile`. That
  includes generated images: `x.de.drawio` already renders to `x.de.png` (#855),
  so per-language diagrams need no extra work.
- Multi-part tails (`x.de.tar.gz`) do **not** match; documented.
- Split decks (`slides_x.de.py`) are notebooks, not assets, and already route by
  language. Voiceover companions never reach output. Neither is affected.
- **Implementation:** `asset_lang_tag(path) -> str | None` in
  `clm.core.utils.path_utils`, plus `asset_output_languages(path, languages)`,
  which filters an iterable of languages. Both the copy classes and
  `provenance_manifest` call it. A parity test asserts copy targets == manifest
  entries.
- **Migration:** a corpus scan (2026-10-03) of the `slides/` trees in PythonCourses,
  ml-course, advanced-ml-course and ml-for-programmers found no language-tagged
  assets apart from HTTP cassettes, which never reach output. No existing build
  changes. A note still goes into `clm info migration`.
- **Rejected:** a spec-level declaration of per-language assets. The file-name
  convention matches split decks and voiceover companions, and needs no spec edit
  per asset.

### D2. Shared-image collisions: no change needed

`x.de.png` and `x.en.png` have different names, so the image registry never sees
them as colliding. The issue's bullet about this is moot.

### D3. `<dir-group lang="de|en">` (#1031)

- Optional `lang` attribute with `<channel lang>` semantics (#293). Unset means
  every language (unchanged). `de`/`en` means only that language. Any other value
  is a spec error from `CourseSpec.validate()` (same message shape as the channel
  check, validated against `VALID_LANGUAGES`).
- `DirGroupSpec.lang: str | None`, `DirGroup.output_languages(languages)` filters
  a language set. It is used by `DirGroup.get_processing_operation` (called from
  `Course.process_dir_group_for_targets`) and by
  `provenance_manifest._enumerate_dir_group_outputs`.
- **The manifest filter is required for correctness.** A `de`/`en` pair with the
  same `<name>` would otherwise have both groups walk both language folders on
  disk, so every file is claimed twice, once with the wrong owner.
- `clm validate`'s `duplicate_dir_group_destination` (`_validate_dir_group_destinations`
  in `slides/spec_validator.py`) already runs per language. Groups whose `lang`
  doesn't match the language being checked are skipped there. Result: a `de`+`en`
  pair is clean, while unset + `de` on one destination warns for DE only.
- The runtime dedup (`DirGroup._without_seen_copies`) needs no change: per-language
  output roots differ.
- **Rejected:** a bilingual `<path><de>…</de><en>…</en></path>`. It can't express
  "DE only", and it would make `source_dirs` depend on language at every consumer
  (`course.py` source-dir iteration, `affected_specs.py` claims).

### D4. Validator: `image_ref_wrong_language` (#1034, moved into scope)

Silent failure mode: a cell with no language tag, or a cell in the other language,
references `img/x.de.mp4`, and the EN output has a broken link. New **warning**
in `_validate_images`:

- For each deck of a topic, parse cells (`parse_cells`, which carries
  `CellMetadata.lang`). A cell's effective language is its `lang` tag. For a split
  half (`split_lang_suffix`), every cell has that file's language.
- Scan each cell's source for `img/<name>` references where `<name>` carries a
  language tag. This uses a broad string match (`img/…` up to a quote, whitespace
  or `)`), so code-cell `Video("img/x.de.mp4")` is covered too. The existing
  `_image_refs` only sees `<img src>` and markdown images. Because the check only
  fires for **tagged** names, the broad match doesn't add false positives.
- Warn when the cell's effective language is `None` (shared cell) or differs from
  the tag.

### D5. No video/audio display media in `Python/` code outputs (#1034 part 2, narrowed)

- The code format is jupytext `py:light` (`OutputSpec.jupytext_format`). It keeps
  markdown as comments and can be reopened as a notebook. Code cells also read
  images (`Image.open("img/…")` in the ML courses). So **images keep being copied**.
- Skip **video/audio** files for the `code` format only when they are display
  media: located under a topic `img/` or `img-generated/` directory. Audio in
  `data/` may be code input (librosa etc.) and keeps being copied.
- `MEDIA_FILE_EXTENSIONS` (video + audio) in `path_utils`, and a predicate
  `is_display_media(relative_path)`. Applied in `DataFile` and mirrored in the
  manifest through a shared `asset_output_specs(...)` generator, so both use one
  loop.
- No opt-out flag for now. Add one only if a consumer turns up.

### D6. Out of scope: shared media (#1034 part 3)

Shared mode already rewrites markdown `<video src="img/…">`
(`MEDIA_SRC_PATTERN`), so classifying video as a shared image would be cheap for
markdown-embedded videos. `Video(...)` code cells aren't rewritten, though, and
shared mode applies to the whole course. Left as a follow-up. Recommendation for
the Breakout deck: language-tagged markdown cells with
`<video src="img/breakout.de.mp4" controls>`.

## 3. Phase Breakdown

### Phase 1 — Asset language suffix + shared placement helper (#1034 part 1) — IN PROGRESS
- `asset_lang_tag`, `asset_output_languages`, and the `asset_output_specs`
  generator (wraps `output_specs` and applies the language filter, plus the D5
  filter in Phase 4) in `path_utils`.
- `DataFile`, `DuplicatedImageFile`, `SharedImageFile` use them.
- `provenance_manifest` per-file and shared-image enumeration use them.
- Tests: unit tests for the tag parser; build-level tests that DE/EN outputs get
  only their asset; manifest parity (copy ops == manifest entries) for all three
  classes.

### Phase 2 — `<dir-group lang>` (#1031) — TODO
- Spec parse + `CourseSpec.validate()` error; `DirGroup.output_languages`;
  copy + manifest; `_validate_dir_group_destinations` per-language skip.
- Tests: spec parse/validation; pair builds to the right trees; unset+de warns for
  DE only; pair is clean; manifest has each file once with the right owner;
  switching unset→`de` removes the stale EN tree on rebuild.

### Phase 3 — `image_ref_wrong_language` validator (D4) — TODO

### Phase 4 — Skip display video/audio in code outputs (D5) — TODO

### Phase 5 — Docs + changelog + PR — TODO
- `clm info spec-files`: `<dir-group>` `lang` row + example; a line near the
  image/asset notes about the `.de`/`.en` suffix.
- `clm info slide-format`: assets section (language-suffixed assets, the
  validator warning, the markdown `<video>` recommendation).
- `clm info migration`: behavior note (tagged assets are now language-scoped; code
  outputs no longer carry `img/` video/audio).
- `changelog.d/1031-dir-group-lang.added.md`, `changelog.d/1034-lang-suffixed-assets.added.md`,
  `changelog.d/1034-code-format-media.changed.md`.

## 4. Current Status

Phase 1 started 2026-10-03.

## 5. Next Steps

Finish Phase 1, then work through Phases 2–5 in order. One PR for both issues is
fine (they share the helper). Close #1031 and #1034 from it.

## 6. Key Files

| File | Role |
|---|---|
| `src/clm/core/utils/path_utils.py` | `output_specs`, new language/media helpers |
| `src/clm/core/course_files/{data_file,duplicated_image_file,shared_image_file}.py` | asset copy ops |
| `src/clm/core/provenance_manifest.py` | per-file, shared-image and dir-group enumeration (must mirror the copies) |
| `src/clm/core/course_spec.py` | `DirGroupSpec`, `CourseSpec.validate()` |
| `src/clm/core/dir_group.py`, `src/clm/core/course.py` (`process_dir_group_for_targets`) | dir-group copy |
| `src/clm/slides/spec_validator.py` | `_validate_dir_group_destinations`, `_validate_images` |

## 7. Landmines

- **Copy/manifest parity.** Any placement rule added to a copy class must be
  mirrored in `provenance_manifest.py`; route both through one helper.
- `DuplicatedImageFile` copies to `output_relative_path` (`img-generated/` collapsed
  to `img/`), not `relative_path`. Apply the language tag to the **file name**, which
  both paths share.
- `output_specs` silently drops languages other than `de`/`en`. The new filter
  composes with it and doesn't replace it.
