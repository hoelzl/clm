---
name: clm-release
description: "Cut a release of the CLM PACKAGE to PyPI (bump the version, publish coding-academy-lecture-manager). Use when asked to release/publish CLM, bump its version (patch/minor/major), or ship a new clm version to PyPI. This is the PYTHON PACKAGE release, NOT publishing course content to a cohort (that is course-release). Publishing is AUTOMATED — landing a `Bump version …` commit on master (via merge commit) triggers .github/workflows/release.yml, which gates on CI-green then publishes via OIDC and creates the GitHub Release. Your job is docs + CHANGELOG + version bump; never run `uv publish` / `gh release create` by hand. Source of truth: docs/developer-guide/releasing.md."
---

# Release the CLM package to PyPI

**Publishing is automated.** Landing a `Bump version X.Y.Z → A.B.C` commit on
`master` (a **merge commit**, or a pushed `vX.Y.Z` tag) triggers
`.github/workflows/release.yml`, which **gates on CI being green for that commit**,
then publishes to PyPI via OIDC Trusted Publishing and creates the GitHub Release.
Your job is the **docs + CHANGELOG + version bump**; the workflow does the rest.

**Source of truth: `docs/developer-guide/releasing.md`.** Read it for the full
procedure; this skill is the ordered checklist + the hard guardrails.

## The hard rules (never violate)

- **Never publish without updating documentation first.**
- **Never publish if any local test fails** (`uv run pytest -m "not docker"`).
- **Never publish unless CI is green for the released commit.**
- **Never `uv publish` / `gh release create` by hand** — the workflow owns
  publishing. (A manual fallback exists in releasing.md *only* if the workflow is
  unavailable, and only after confirming CI is green on the tagged commit.)
- **Merge the bump PR with a MERGE COMMIT, not squash/rebase** — the workflow
  recognises the release by finding the `Bump version …` commit in the push;
  squash/rebase rewrites it and the release won't trigger.
- **Do not push tags.** `bump-my-version` is `tag = false`; the server creates the
  `vX.Y.Z` tag *after* the CI-green gate, so a red commit is never tagged.

## The ordered steps

1. **Docs first** (commit before the bump so they're in the release commit):
   - **CHANGELOG**: `python scripts/collect_changelog.py X.Y.Z` folds the
     `changelog.d/` fragments into a `## [X.Y.Z]` section. Then **cross-check
     every merged PR is represented**: `git log <last-tag>..HEAD --merges` vs the
     generated section; backfill any PR that merged without a fragment.
   - README, AGENTS.md (guardrails only), `clm info` topics (if spec/CLI/migration
     changed), `docs/user-guide` + `docs/developer-guide` as relevant.
2. **Test**: `uv run pytest -m "not docker"` — all must pass (Docker tests are
   CI-only). If a test fails, fix the root cause; do not loop-retry.
3. **Bump** (merge-driven, recommended):
   ```
   git switch -c claude/release-A.B.C
   uv run bump-my-version bump patch        # or minor / major
   git push -u origin claude/release-A.B.C
   # open PR → CI green → merge with a MERGE COMMIT
   ```
   Preview first with `uv run bump-my-version bump patch --dry-run --verbose`.
4. **Watch the release workflow**:
   ```
   gh run watch "$(gh run list --workflow=Release --limit 1 --json databaseId --jq '.[0].databaseId')"
   ```
   It resolves → waits for CI green → tags + builds + publishes to PyPI + creates
   the GitHub Release (all idempotent — a re-run never double-publishes).

## If a hook rejects a commit

The commit did **not** happen — fix the issue, re-stage, make a **new** commit.
Never `--amend` a hook-rejected commit.

## Escalate to the user

- Any release where CI is not green, or where you cannot confirm docs reflect the
  shipped code.
- A major version bump (breaking changes) — confirm scope/timing first.
- The manual fallback path (hand `uv publish`) — only with explicit instruction.
