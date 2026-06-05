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
   **Verify every merged PR since the last release is represented** — a PR can
   merge without adding its own `[Unreleased]` entry (e.g. 1.8.0's cell-spacing
   feature shipped with none and had to be backfilled at cut time). Cross-check
   `git log <last-tag>..HEAD --merges` against the `[Unreleased]` section, then
   rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and leave a fresh empty
   `## [Unreleased]` above it. `bump-my-version` does **not** touch the
   CHANGELOG, so this rename is manual.
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

## Step 3: Bump the version and push

```bash
# Bump the version (creates the commit + tag across 8 files)
uv run bump-my-version bump patch  # or minor / major

# Push the bump commit to master (this triggers CI), then push the tag
git push
git push --tags
```

`bump-my-version` is configured in `[tool.bumpversion]` in `pyproject.toml`
and automatically updates the version in eight files, creates a commit with
the message `Bump version X.Y.Z → A.B.C`, and tags that commit `vA.B.C`.

Preview what would change without modifying anything:

```bash
uv run bump-my-version bump patch --dry-run --verbose
```

## Step 4: The automated release (CI gate → PyPI → GitHub Release)

Pushing the `vX.Y.Z` tag triggers the **Release** workflow
(`.github/workflows/release.yml`), which does the rest automatically:

1. **Waits for the CI workflow to finish on the tagged commit and requires it
   to be green** before doing anything else. CI triggers on the `master` push
   from Step 3 (it does **not** trigger on tags); the release workflow polls for
   that run on the same commit SHA and aborts if it failed or never ran. This
   enforces the "CI green on the tagged commit" rule mechanically — including
   the Docker integration job, which a standalone release job could not easily
   reproduce.
2. Builds a clean sdist + wheel and **verifies the built version matches the
   tag**.
3. **Publishes to PyPI via Trusted Publishing (OIDC)** — no stored token.
4. **Creates the GitHub Release**, with notes taken from the matching
   `## [X.Y.Z]` CHANGELOG section.

Watch it:

```bash
gh run watch "$(gh run list --workflow=Release --limit 1 --json databaseId --jq '.[0].databaseId')"
```

Nothing else is required for a normal release. The rest of this section is the
one-time setup and the manual fallback.

### One-time setup: PyPI Trusted Publishing

The publish step authenticates to PyPI via GitHub OIDC, so there is no API token
to store or rotate. Configure it once on PyPI, under the project's
[publishing settings](https://pypi.org/manage/project/coding-academy-lecture-manager/settings/publishing/):

- **Owner:** `hoelzl`  ·  **Repository:** `clm`
- **Workflow name:** `release.yml`
- **Environment name:** `pypi`

The `publish` job declares `environment: pypi`; add protection rules (required
reviewers, etc.) to that environment in the repo settings if you want a manual
approval gate before any upload.

### Manual fallback

If the workflow is unavailable, publish by hand — but only after confirming CI
is green on the tagged commit:

```bash
rm -rf dist/*            # dist/ accumulates old artifacts; uv publish uploads all of dist/
uv build
uv publish              # needs a PyPI token configured locally
awk -v v=X.Y.Z 'BEGIN{h="## [" v "]"} index($0,h)==1{f=1;next} f&&index($0,"## [")==1{exit} f' \
  CHANGELOG.md > notes.md
gh release create vX.Y.Z --title "CLM X.Y.Z" --notes-file notes.md --verify-tag
```

`--verify-tag` refuses to create the release if the tag was never pushed.

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
