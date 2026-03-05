# Changelog

All notable changes to CLM are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Fixed
- Forbid Markdown headings in trainer summaries to preserve heading hierarchy in
  generated summary documents.

## [1.1.2] - 2026-03-05

### Added
- **`clm summarize` command**: Generate LLM-powered markdown summaries of course content.
  Supports `--audience client|trainer`, `--style prose|bullets`, `--granularity notebook|section`,
  per-notebook caching, and configurable LLM models via litellm. Requires the `[summarize]` extra.
- **`--amend` flag for `clm git commit` and `clm git sync`**: Amend the previous commit
  instead of creating a new one. When used without `-m`, reuses the previous commit message
  (`--no-edit`). When used with `-m`, replaces the commit message.
- **`--force-with-lease` flag for `clm git push` and `clm git sync`**: Safe force push
  for rewritten history. `--amend` on `sync` implies `--force-with-lease` automatically.
  When force-pushing, the "remote is ahead" safety check is skipped.

### Fixed
- Bullet-style client output formatting in summarize command.

## [1.1.1] - 2026-03-05

### Added
- Automatic `.env` file loading: The `build` command now walks up the directory tree to
  find a `.env` file and loads it before spawning workers.

### Changed
- Reorganized optional dependencies: moved data-science packages from `[notebook]` to
  `[ml]` extra, organized by category.

### Fixed
- CLI help text formatting for multi-line examples.
- Suppressed `RequestsDependencyWarning` from the requests library.

## [1.1.0] - 2026-02-27

### Added
- **Remote URL template for git operations**: Trainers can now override the git remote
  URL pattern via a configurable template with placeholders (`{repository_base}`, `{repo}`,
  `{slug}`, `{lang}`, `{suffix}`). Set via `CLM_GIT__REMOTE_TEMPLATE` environment variable,
  `[git] remote_template` in TOML config, or `<remote-template>` in the course spec XML.
  Enables SSH access with custom host aliases (e.g., `git@github.com-cam:Org/{repo}.git`).

### Changed
- **Flatten speaker kind subfolder**: Speaker output no longer creates a redundant `Speaker/`
  subfolder in the output path. Paths are now `.../Html/Section/` instead of
  `.../Html/Speaker/Section/`, since speaker output has only one variant.

## [1.0.9] - 2025-11-29

### Added
- `clm info <topic>` command for version-accurate documentation that downstream agents
  can query at runtime. Topics: `spec-files`, `commands`, `migration`.

### Changed
- `<project-slug>` promoted to top-level course spec element (previously inside `<github>`).
  The old location still works but is deprecated and logs a warning.

## [1.0.8] - 2025-11-28

### Added
- `docker.io/` registry prefix for Podman compatibility.
- `.python-version` file for Arch Linux compatibility.

### Fixed
- `sanitize_path` no longer strips leading dots from path components.

## [1.0.7] - 2025-11-27

### Added
- LangSmith and Ragas to ML optional dependencies.
