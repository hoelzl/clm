# Changelog

All notable changes to CLM are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Remote URL template for git operations**: Trainers can now override the git remote
  URL pattern via a configurable template with placeholders (`{repository_base}`, `{repo}`,
  `{slug}`, `{lang}`, `{suffix}`). Set via `CLM_GIT__REMOTE_TEMPLATE` environment variable,
  `[git] remote_template` in TOML config, or `<remote-template>` in the course spec XML.
  Enables SSH access with custom host aliases (e.g., `git@github.com-cam:Org/{repo}.git`).

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
