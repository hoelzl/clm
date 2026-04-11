# Releasing CLM

This document describes the CLM release procedure. It is the canonical
reference — `CLAUDE.md` only keeps the load-bearing guardrails (the
"Rules" section at the bottom of this file).

**IMPORTANT**: Before publishing a release, you **MUST** update documentation,
run the local test suite, and verify CI passes. These steps are ordered — do
not skip ahead.

## Step 1: Update documentation

Before bumping the version, make sure all documentation reflects the current
state of the code:

1. **CHANGELOG.md** — Add an entry for the new version with a summary of
   changes (Added / Changed / Fixed / Removed, following Keep a Changelog).
2. **README.md** — Update if there are new features, changed commands, or
   altered setup instructions.
3. **CLAUDE.md** — Update only if there are new **guardrails** or
   **behavioral conventions** that session-start instructions need to carry.
   Do NOT re-add reference material (commands, env vars, class lists, feature
   notes) — those belong in their canonical homes listed in the CLAUDE.md
   documentation map.
4. **`clm info` topics** (`src/clm/cli/info_topics/*.md`) — Update if the spec
   file format, CLI commands, or migration steps have changed. Downstream
   agents in course repositories rely on these being current; stale info
   topics produce incorrect output. See the "Info topics" section of
   `CLAUDE.md` for the hard requirement.
5. **`docs/user-guide/`** / **`docs/developer-guide/`** — Update the relevant
   pages for any user-facing or developer-facing changes. Env var additions
   go in `docs/user-guide/configuration.md`. New modules or architectural
   changes go in `docs/developer-guide/architecture.md`.

Commit all documentation updates **before** the version bump so they are
included in the release commit.

## Step 2: Run local tests (non-Docker)

Docker-marked tests require CI-built images (`lite-test`, `test` tags) that
are not available locally. Run the local test suite excluding Docker tests:

```bash
uv run pytest -m "not docker"
```

All non-Docker tests must pass before proceeding. This runs unit, integration,
and e2e tests and takes about 2 minutes with `pytest-xdist` parallelism.

## Step 3: Bump version, build, and push to CI

```bash
# Bump the version (creates commit + tag across 7 files)
uv run bump-my-version bump patch  # or minor / major

# Build the package (sdist + wheel in dist/)
uv build

# Push commit and tags to trigger CI
git push && git push --tags
```

`bump-my-version` is configured in `[tool.bumpversion]` in `pyproject.toml`
and automatically updates the version in seven files, creates a commit with
the message `Bump version X.Y.Z → A.B.C`, and tags that commit `vA.B.C`.

Preview what would change without modifying anything:

```bash
uv run bump-my-version bump patch --dry-run --verbose
```

## Step 4: Verify CI passes

Wait for the GitHub Actions CI pipeline to complete. CI runs the **full**
test suite including Docker tests (it builds the `lite-test` images from
scratch).

```bash
gh run list --limit 5
gh run view <run-id>
```

Do not proceed to publish until CI is green on the tagged commit.

## Step 5: Publish to PyPI

Only after CI has passed for the tagged commit:

```bash
uv publish
```

The `uv build` output in `dist/` (sdist + wheel) is what gets uploaded.

---

## Rules for Claude Code

These rules are mirrored verbatim in `CLAUDE.md` because they are
guardrails, not procedures:

- **Never publish a release without updating documentation first.**
- **Never publish a release if any local test fails.**
- **Never publish unless CI has passed for the tagged commit.**
- Use `pytest -m "not docker"` for local pre-release testing (Docker tests are
  validated in CI, not locally).
- If tests fail, fix the root cause and re-run — do not retry in a loop.
- Create a new commit if a hook fails; never `--amend` the failed commit, as
  the commit did not happen and `--amend` would rewrite the previous one.
