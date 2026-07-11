---
name: resolve-issue
description: "Resolve a GitHub issue in the CLM repo end-to-end: investigate the issue thread and code, reproduce the problem, name the root cause, design and implement a fix with regression tests that fail before the fix, optionally run an adversarial review, and ship a PR that closes the issue. Use when asked to fix / resolve / investigate-and-fix issue #NNN. Args: the issue number, optionally followed by --review (force an adversarial review) or --no-review (skip it); with neither flag, decide via the risk heuristic in this skill after the diff exists."
---

# Resolve a GitHub issue end-to-end

Take an issue number from `$ARGUMENTS` (e.g. `616` or `#616`), plus optional
`--review` / `--no-review`. Work autonomously: investigate → root cause →
design → implement with regression tests → verify → (maybe) adversarial
review → ship. Only stop to ask when the issue itself is ambiguous in a way
that changes what "fixed" means.

## Phase 0 — Orient (before touching code)

1. **Read the whole issue, not just the title**:
   `gh issue view NNN --comments`. Comments often contain repro details,
   constraints, or a decision that supersedes the original description.
2. **Check for existing work** so you don't duplicate or collide:
   - `gh pr list --search "NNN" --state all` and
     `git branch -r | grep -i "issue-NNN"` — an abandoned branch may hold a
     partial fix or a failed approach worth knowing about.
   - Linked issues/PRs mentioned in the thread; `docs/claude/TODO.md` and
     `docs/claude/handovers/` for prior investigations.
3. **Branch worktree-safely** (never switch to literal `master`):
   `git fetch origin && git switch -C claude/issue-NNN-<slug> origin/master`.

## Phase 1 — Investigate and reproduce

- Read the relevant code before forming a theory. Use `clm info commands` /
  `clm info spec-files` when behavior questions arise — don't guess flags.
- **Reproduce the problem before changing anything.** The preferred repro is
  a failing test; a minimal script is acceptable when a test is impractical
  (then convert it into a test in Phase 3). If you cannot reproduce it,
  say so on the issue-investigation level and stop — do not "fix" what you
  cannot observe.
- **State the root cause explicitly** in one or two sentences ("X happens
  because Y") and distinguish it from the symptom. If the fix you're about
  to write addresses the symptom but not that sentence, you're patching the
  wrong layer.
- **Checkpoint — ask only here.** If the issue admits multiple defensible
  interpretations, the intended behavior is genuinely unclear, or the root
  cause implies a much larger change than the issue suggests, ask the user
  (AskUserQuestion) before designing. Otherwise proceed without asking.

## Phase 2 — Design

- Prefer the smallest change that eliminates the root cause. Check
  `docs/claude/design/` for decisions that constrain the fix.
- **Scope discipline**: fix this issue only. Adjacent problems you notice go
  into a comment on the issue, a new issue, or `docs/claude/TODO.md` — not
  into this diff.

## Phase 3 — Implement with regression tests

- **Test first, and watch it fail.** Write the regression test, run it
  against the unfixed code, and confirm it fails *for the root-cause reason*
  (not a typo or fixture error). A regression test that never failed proves
  nothing. Then implement the fix and watch it pass.
- Reference the issue in the test (docstring or comment: `Regression test
  for #NNN`), so future readers can find the context.
- Follow the marker strategy (`docs/developer-guide/testing.md`): keep
  regression tests in the fast suite unless they genuinely need `slow` /
  `docker` / `integration` markers.
- Cover the neighbors: if the bug was an edge case, add the adjacent edge
  cases (empty, Windows paths/CRLF, unicode, concurrent) while you're there.

## Phase 4 — Verify

- `uv run ruff check src/ tests/` and the fast suite (`pytest`); run the
  full relevant subset for the touched area if the fast suite doesn't cover
  it (`pytest -m "not docker"` when infrastructure is involved).
- If the change has a runtime surface, exercise it end-to-end once (the
  actual CLI command / build flow from the issue), not just the tests.

## Phase 5 — Adversarial review (decide now, with the diff in hand)

Flags override everything: `--review` → run it; `--no-review` → skip it.
Otherwise **run an adversarial review when ANY of these hold**:

- The diff touches `core/` or `infrastructure/` beyond a trivial change —
  especially the job queue, caching/cache keys, worker lifecycle, or
  persistence.
- The fix involves concurrency, path/encoding handling, security, or
  anything Windows/Unix-divergent.
- More than ~150 changed lines of non-test code.
- The root cause was subtle: your first theory was wrong, or the symptom
  and root cause lived in different layers.

**Skip it** for docs/test-only changes, mechanical renames, and small
isolated fixes whose regression test pins the behavior tightly.

State the decision and its one-line rationale either way. To run the
review, invoke the `code-review` skill (medium effort by default, high for
core/infrastructure changes), fix CONFIRMED findings, and re-run Phase 4.

## Phase 6 — Housekeeping and ship

- Changelog fragment `changelog.d/NNN-<slug>.fixed.md` (never edit
  `CHANGELOG.md`'s `[Unreleased]`).
- If a CLI command/flag, the spec format, or user-visible behavior changed,
  update the matching `src/clm/cli/info_topics/*.md` — and any affected
  `docs/` pages.
- Ship via the **ship-a-pr** skill (commit trailers, fast-suite push gate,
  CI-gated auto-merge). The PR body must contain `Fixes #NNN` so the merge
  closes the issue, and should include the root-cause sentence from
  Phase 1 — reviewers should not have to re-derive it.
