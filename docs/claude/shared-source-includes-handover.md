# Handover — Shared-Source Includes & Output-Write Dedup

Companion to
[`docs/claude/design/shared-source-includes-and-output-dedup.md`](design/shared-source-includes-and-output-dedup.md)
(locked 2026-05-10). Tracks implementation progress across two PRs.

## PRs

- **PR 1**: Feature 1 — `<include>` element + `clm sync-includes` CLI.
  Branch: `claude/shared-source-includes`. Worktree: `curious-twirling-owl`.
- **PR 2**: Feature 2 — `OutputWriteRegistry` + collision warning.
  Branch: TBD. Starts after PR 1 merges.

## PR 1 — Feature 1 phases

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | Spec parsing (`IncludeSpec`, parse `<include>` on `<topic>`/`<section>`) | [x] | Done 2026-05-10. `src/clm/core/course_spec.py`: `IncludeSpec` dataclass, `_parse_includes`, `_normalize_include_path`, `SectionSpec.includes_for(topic)`. Validates: empty source rejected, `..` in source/`as` rejected, absolute paths rejected, duplicate `as_path` rejected, Windows separators normalized to forward slashes. 12 new tests in `tests/core/course_spec_test.py`. Fast suite green (4593 pass). |
| 2 | File discovery (`DirectoryTopic.build_file_map` virtual splice) | [x] | Done 2026-05-10. `course_file.py`: added `source_origin: Path \| None`, `source_path` property, `from_virtual()` classmethod. `topic.py`: added `ResolvedInclude` dataclass, `Topic.includes` field, `add_virtual_file()`, `apply_includes()`. Real local files shadow virtual ones (warning `include_shadowed_by_local`). Skips `__pycache__`, `.venv`, etc. during recursion. Updated read sites: `copy_file.py`, `process_notebook.py`, `convert_drawio_file.py`, `convert_plantuml_file.py` now use `source_path`. 6 new tests in `tests/core/topic_test.py`. Fast suite green (4599 pass). |
| 3 | Build-pipeline integration (per-section default propagation, override key = `as`) | [ ] | Touches `src/clm/core/course.py`. |
| 4 | Validation (`include_source_missing`, `include_target_collision`, `include_shadowed`, `include_dependencies` info) | [ ] | Touches `src/clm/cli/commands/validate_spec.py` or wherever the existing checks live. |
| 5 | `clm sync-includes` CLI command (`copy` default; `symlink`, `hardlink`, `--remove`, `.clm-include` marker) | [ ] | New file under `src/clm/cli/commands/`. |
| 6 | Docs: `info_topics/spec-files.md`, `info_topics/commands.md`, `docs/user-guide/spec-file-reference.md`, `CHANGELOG.md` | [ ] | Per CLAUDE.md "Info Topics Maintenance Rule" — version-accurate, `{version}` placeholder. |
| 7 | Smoke test: migrate ML AZAV `topic_040_gradio_intro` and `topic_041_gradio_deep_dive` per design doc; full build + diff against pre-migration | [ ] | Course repo: `C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\`. Don't commit the course-repo migration in this PR — record the recipe and confirm it works locally. |
| 8 | Pre-PR: `pytest -m "not docker"`, `ruff check`, `ruff format`, `mypy` | [ ] | Per CLAUDE.md release rules. |

## PR 2 — Feature 2 phases

| # | Phase | Status | Notes |
|---|---|---|---|
| 1 | `OutputWriteRegistry` module + content hashing helper | [ ] | Probably under `src/clm/core/`. |
| 2 | Hook into `backend.copy_file_to_output()` and the notebook output writer | [ ] | Skip paths owned by `ImageRegistry`. |
| 3 | `BuildReporter` integration (counts + JSON `output_conflicts` key) | [ ] | Existing reporter at `src/clm/cli/build_reporter.py`. |
| 4 | Tests (unit + integration with synthetic two-topic collision) | [ ] | |
| 5 | Docs + CHANGELOG | [ ] | |
| 6 | Pre-PR checks | [ ] | |

## Key code surface (citations — frozen at design time)

- Spec parsing: `src/clm/core/course_spec.py` (`parse_sections`, `parse_dir_groups` at line 828).
- Topic resolution: `src/clm/core/topic_resolver.py:60` (`build_topic_map`), `src/clm/core/course.py:540` (`_build_sections`).
- File discovery: `src/clm/core/topic.py:110` (`DirectoryTopic.build_file_map`, `add_files_in_dir`).
- CourseFile types: `src/clm/core/course_file.py:25,90` (base + `_find_file_class`).
- Output write: `src/clm/core/operations/copy_file.py:20`, `backend.copy_file_to_output()`.
- Notebook other-files copy: `src/clm/workers/notebook/notebook_processor.py:1529` (`write_other_files_sync`).
- Docker mounts: `src/clm/infrastructure/workers/worker_executor.py:147` (`/source` mount).
- Image registry (don't double-warn): `src/clm/core/image_registry.py:62`.

## Decisions log

- 2026-05-10: design locked, open questions resolved (simple inheritance,
  warn-but-allow on topic-source includes, disallow outside-root,
  no-persist registry, copy-default sync, no `dedup="silent"` yet).
- 2026-05-10: PR split confirmed (Feature 1 first, Feature 2 second).

## Migration recipe (PR 1 smoke test, mirrored from design doc)

In `course-specs/machine-learning-azav.xml`:

```xml
<topic id="gradio_intro">
  <include source="examples/SimpleChatbot/src/simple_chatbot"
           as="simple_chatbot"/>
</topic>
<topic id="gradio_deep_dive">
  <include source="examples/SimpleChatbot/src/simple_chatbot"
           as="simple_chatbot"/>
</topic>
```

Then:

1. `clm sync-includes course-specs/machine-learning-azav.xml --remove`
   (delete physical copies marked with `.clm-include`).
2. `clm sync-includes course-specs/machine-learning-azav.xml`
   (re-materialize as copies; or `--mode=symlink` if author has admin).
3. `clm build` and diff against a pre-migration build.

## Out-of-scope, captured for future

- `--strict` flag promoting `output_path_conflict` warnings to errors.
- Cross-spec sharing (include in spec A pulling from spec B).
- Auto-installation of an include's `pyproject.toml` dependencies into
  the worker environment.
- `<dir-group dedup="silent">` attribute (only if the warning becomes
  noisy in practice).
