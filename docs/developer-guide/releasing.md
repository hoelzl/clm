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

## Step 3: Cut the release

`bump-my-version` updates the version across eight files and commits it with the
message `Bump version X.Y.Z → A.B.C` — the message the release workflow keys on.
There are two ways to land that bump commit on `master`; both trigger the same
automated pipeline (Step 4).

**Merge-driven (recommended).** Treat the bump like any other change and review
it in a PR:

```bash
git switch -c claude/release-A.B.C
uv run bump-my-version bump patch   # or minor / major; creates the bump commit
git push -u origin claude/release-A.B.C
# open a PR, get CI green, then merge it with a MERGE COMMIT (not squash/rebase)
```

When the PR merges, the push to `master` carries the `Bump version …` commit and
the workflow releases. **Merge with a merge commit, not squash or rebase** — the
workflow recognises the release by finding the `Bump version …` commit in the
push, and squash/rebase rewrites it into a differently-worded commit.

`bump-my-version` is configured with `tag = false`, so it creates **only** the
`Bump version …` commit — no local tag. The `vA.B.C` tag is created on the server
by the Release workflow, after the CI-green gate, so a red commit is never tagged.
There is nothing to `git push --tags`; doing so is unnecessary and not part of the
flow.

**Direct (no PR).** If review isn't needed, push the bump commit straight to
master:

```bash
uv run bump-my-version bump patch
git push        # pushes the bump commit; do NOT push the tag
```

**Explicit tag (fallback).** You can still release by pushing a tag directly —
useful to re-release or to tag a specific commit. Since the bump no longer
creates a local tag, make it yourself first, then push it:

```bash
git tag vX.Y.Z      # on the commit whose in-tree version is X.Y.Z
git push origin vX.Y.Z
```

Preview a bump without changing anything: `uv run bump-my-version bump patch
--dry-run --verbose`.

## Step 4: What the Release workflow does

Either trigger runs `.github/workflows/release.yml`, which:

1. **`resolve`** — decides whether to release. A tag push always proceeds; a
   `master` push proceeds only when it introduced a `Bump version …` commit and
   the `vX.Y.Z` tag does not already exist. The version is read from
   `src/clm/__version__.py`.
2. **`require-ci`** — waits for the CI workflow to finish on this commit and
   requires it to be **green** before anything is published. CI triggers on the
   `master` push (not on tags); the gate polls for that run on the same commit
   SHA and aborts if it failed or never ran. This enforces "CI green on the
   released commit" mechanically — including the Docker integration job, which a
   standalone release job could not easily reproduce.
3. **`publish`** — creates and pushes the `vX.Y.Z` tag if it doesn't exist yet
   (only *after* the gate, so a red commit is never tagged), builds a clean
   sdist + wheel, verifies the built version matches the tag, **publishes to
   PyPI via Trusted Publishing (OIDC)**, and **creates the GitHub Release** from
   the matching `## [X.Y.Z]` CHANGELOG section.

Every external action is **idempotent** — the tag, the PyPI upload, and the
GitHub Release are each skipped if they already exist, so a re-run (or a stray
duplicate trigger) never double-publishes.

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
