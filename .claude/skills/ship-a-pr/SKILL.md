---
name: ship-a-pr
description: "Ship a finished change in the CLM repo as a PR, the autonomous way: branch → commit → push (fast-suite gate) → PR → CI-gated auto-merge. Use when a feature/fix/docs change is complete and ready to land, especially from a git worktree. Encodes the CLM-specific landmines: a worktree must NEVER switch to literal master (reset its OWN branch off origin/master); commit/PR trailers; changelog goes in changelog.d/ NOT the CHANGELOG [Unreleased] section; update the matching `clm info` topic for any CLI/spec/behavior change; the pre-push hook runs the fast suite (cap xdist workers to dodge contention flakes); never edit files during a backgrounded push; auto-merge is CI-gated; open a PR fresh off master (don't stack) to avoid the retarget-no-CI gap."
---

# Ship a CLM change as a PR

The autonomous flow for landing finished work. Commit, push, and open PRs
**without waiting for a go-ahead** — only genuinely destructive ops (force-push
to a shared branch, history rewrites, pushes to `master` itself) warrant asking.

## Before you commit (the easy-to-forget bits)

- **Changelog**: never edit `CHANGELOG.md`'s `[Unreleased]` section (it caused
  constant merge conflicts). Add a fragment `changelog.d/<pr-or-issue>-<slug>.<type>.md`
  (`type` = added | changed | deprecated | removed | fixed | security) with the
  finished bullet(s). See `changelog.d/README.md`.
- **Info topics**: if you changed a CLI command/flag, the spec format, or
  user-visible behavior, update the matching `src/clm/cli/info_topics/*.md`
  (`commands.md` / `spec-files.md` / `migration.md`). Downstream agents rely on
  these; stale topics produce wrong output.
- **Tests + lint**: the pre-commit hook runs ruff + mypy; make sure they pass.

## Branching (worktree-safe)

**A worktree must NEVER switch to literal `master`** — `master` is checked out in
the main repo and git forbids it in two worktrees. To start fresh work off the
latest master content, reset onto `origin/master`:

```
git fetch origin
git switch -C claude/issue-NNN-slug origin/master      # fresh branch off master
```

When work depends on nothing unmerged, **branch off `origin/master`, don't stack**
on another open PR's branch — a stacked PR that gets auto-retargeted when its base
merges does **not** fire a CI run, leaving it BLOCKED with no checks.

## Commit

Make focused commits. **End every commit message with the two trailers your
environment specifies** (the `Co-Authored-By:` line and the `Claude-Session:` URL
given in your session instructions). If a hook **rejects** a commit, the commit
did not happen — fix, re-stage, make a **new** commit; never `--amend` a
hook-rejected commit.

## Push (the fast-suite gate)

The **pre-push** hook runs the fast test suite (~72s). Cap xdist workers to avoid
unrelated contention flakes on a busy machine:

```
PYTEST_XDIST_AUTO_NUM_WORKERS=4 git push -u origin claude/issue-NNN-slug
```

**Never edit files while a push is running in the background** — a concurrent edit
makes the pre-commit/pre-push hook abort (`files were modified by this hook`).
Push a clean tree in the foreground, or wait for a backgrounded push to finish
before touching anything. **Never `--no-verify`** unless the user explicitly asks.

## PR + auto-merge

```
gh pr create --base master --head <branch> --title "…" --body-file -   # heredoc body
gh pr merge <N> --merge --auto                                          # CI-gated auto-merge
```

- End the **PR body** with the generation trailer your environment specifies.
- Auto-merge is **CI-gated** (the repo has a "Require CI green" ruleset +
  merge-commit-only), so `--merge --auto` waits for green — `BLOCKED` immediately
  after arming is normal (waiting on checks), not a failure.
- A PR opened **fresh off master** fires its CI `pull_request` run normally;
  confirm with `gh run list --branch <branch> --limit 1`.

## If CI never starts

Usually the retarget-no-CI gap: the PR's base was auto-changed (a base PR merged)
without a new push, so no `pull_request` event fired. Fix by rebasing onto current
master and force-pushing (`--force-with-lease`), which re-triggers CI.

## Don't ask permission for

Committing, pushing, opening PRs, arming auto-merge when the work is complete. Do
ask before: force-pushing a shared branch, history rewrites, or pushing to
`master` directly.
