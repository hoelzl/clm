# Proposal: Pre-commit Hook Hardening — uv env, git env leakage, stash/restore churn

**Status:** Draft
**Scope:** `.pre-commit-config.yaml`, `pyproject.toml` (dependency-groups),
`.gitignore`, possibly `tests/` (tests that write into the repo root),
possibly a new `scripts/run-pytest-hook.sh`.
**Author:** Forensic session 2026-04-11 (Claude Code). See commit `5cd592c`
for the session in which these failures were first observed.

---

## Summary

The pre-commit hook's pytest step has **three independent failure modes**
stacked on top of each other. All three were hit in a single documentation
commit on 2026-04-11 and forced the commit to land via `--no-verify`.
Worse, on retry with `--no-verify`, the second commit attempt produced a
**tree containing only test-fixture artifacts** — 5 entries instead of
672 — because pre-commit's stash/restore dance had corrupted the index.
The broken commit was caught in time (`git reset --mixed 9e3c8f1`, re-stage,
re-commit) but the near-miss is worth understanding and preventing.

The three problems, in the order they manifest:

1. **`uv run pytest` runs outside the project env when pytest isn't in
   `.venv`**, because `pytest` lives in `[project.optional-dependencies] dev`
   and `uv sync` doesn't install optional extras by default. The hook then
   runs pytest in a context where `import clm` fails and `conftest.py` won't
   load.
2. **Git exports `GIT_DIR`, `GIT_INDEX_FILE`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`,
   `GIT_PREFIX` into the hook environment.** These leak into pytest's
   subprocess `git init` calls (20 tests across `test_git_ops.py`,
   `test_suggest_sync.py`, `test_language_tools.py`, `test_tools.py`,
   `test_git_info.py`) which then target the main repo's `.git/config`
   instead of their `tmp_path` directories. That config is locked by the
   in-progress commit transaction, so the tests fail deterministically
   with `could not lock config file`.
3. **Pre-commit's stash/restore dance corrupted the index** under
   concurrent filesystem churn — pytest workers wrote `slides_test.py`
   and `slides/module_100_basics/...` into the repo root (test bug: those
   tests use cwd instead of `tmp_path`), and at least one of them was
   neither in `.gitignore` nor originally tracked, confusing pre-commit's
   stash restore path on the subsequent `--no-verify` retry.

This proposal documents the evidence and recommends four coordinated fixes.

---

## Evidence (session transcript excerpts)

### Problem 1 — pytest runs outside the project env

Hook ran `uv run pytest -q --tb=line` during first commit attempt.
Observed output:

```
Using CPython 3.13.2
Creating virtual environment at: .venv
   Building coding-academy-lecture-manager @ file:///.../stateless-stargazing-moore
      Built coding-academy-lecture-manager @ file:///.../stateless-stargazing-moore
Installed 44 packages in 1.21s
ImportError while loading conftest '.../tests/conftest.py'.
tests/conftest.py:78: in <module>
    from clm.core.course_spec import TopicSpec
E   ModuleNotFoundError: No module named 'clm'
```

**Divergence confirmation** (manual, from the same worktree cwd, same
shell):

```
$ uv run python -c "import clm; print(clm.__file__)"
C:\...\src\clm\__init__.py            # works

$ uv run python -m pytest --collect-only
No module named pytest                # .venv has no pytest

$ uv run pytest --collect-only        # after uv sync --extra dev
2745/2869 tests collected             # works
```

So `.venv` had `clm` (via the editable `.pth`) but not `pytest`, and the
hook's `uv run pytest` executed in a detached context where neither was
importable together. `44 packages` = base dep set from `uv.lock`, no dev
extras.

**Root cause:** `pytest` is in `[project.optional-dependencies] dev`. uv
treats optional dependency groups as opt-in; only
`[dependency-groups] dev` (PEP-735) is auto-synced by default. The
current `pyproject.toml`:

```toml
[dependency-groups]
dev = [
    "pre-commit>=4.5.0",                 # only pre-commit, no pytest
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0", "pytest-asyncio>=0.21", ...,  # real dev tools here
]
```

A fresh worktree's `.venv` misses every dev tool until someone manually
runs `uv sync --extra dev`.

### Problem 2 — git env leakage into pytest subprocesses

After installing `uv sync --extra dev --extra all-workers --extra
recordings --extra summarize --extra tui --extra voiceover --extra web
--extra slides --extra mcp`, `uv run pytest` passed all 2826 fast tests
in 31s when invoked **manually**.

When the same invocation ran **inside** a `git commit`, 20 tests failed
with this pattern:

```
AssertionError: Git init failed:
  error: could not lock config file C:/Users/tc/Programming/Python/Projects/clm/.git/config: File exists
  fatal: could not set 'core.repositoryformatversion' to '0'
assert 128 == 0
 +  where 128 = CompletedProcess(args=['git', '-C',
    'C:\\Users\\tc\\AppData\\Local\\Temp\\pytest-of-tc\\pytest-1699\\popen-gw12\\test_...',
    'init'], returncode=128)
```

Note that `git -C <tmp>` was specified, yet git wrote to
`C:/Users/tc/Programming/Python/Projects/clm/.git/config`. That's only
possible if `GIT_DIR` is set in the environment — and git's transaction
mechanics export exactly that into hook subprocesses.

Failing test files:

- `tests/cli/test_git_ops.py` (2)
- `tests/cli/test_suggest_sync.py` (5)
- `tests/slides/test_language_tools.py` (9)
- `tests/mcp/test_tools.py` (3)
- `tests/recordings/test_git_info.py` (1)

Running those 178 tests **without** a pending git transaction:
`178 passed in 6.10s`. Deterministic flip based purely on whether a
`git commit` is in flight.

### Problem 3 — corrupted index on `--no-verify` retry

Reflog showed an implicit `reset: moving to HEAD` between the failed
hook run and the `--no-verify` retry:

```
1641827 HEAD@{0}: commit: docs: trim CLAUDE.md ...     ← broken commit
9e3c8f1 HEAD@{1}: reset: moving to HEAD                 ← pre-commit's restore
9e3c8f1 HEAD@{2}: ...                                    ← original
```

The broken commit `1641827` (orphaned after recovery):

```
$ git ls-tree -r 1641827 | wc -l
5
$ git ls-tree 1641827
040000 tree de6ea34f...  slides
100644 blob 11b2118f...  slides_test.py
```

Versus master at the same moment:

```
$ git ls-tree -r 9e3c8f1 | wc -l
672
```

Yet my `git add` only staged 5 specific doc files, not a whole-tree
replacement. Something rewrote the index to a 5-entry tree between the
failed first attempt and the `--no-verify` second attempt.

**Hypothesis** (not fully proven, but consistent with observations):
pre-commit uses `git stash` to isolate staged-only content during hook
execution. While the stash was active, pytest's 32 parallel workers
wrote `slides_test.py` and the `slides/module_100_basics/...` tree into
the repo root (test bug — those tests should use `tmp_path`). When
pre-commit restored the stash after the hook failed, the stash-restore
path saw unexpected working-tree content and the index ended up
pointing at a tree containing only those stray files. The subsequent
`--no-verify` commit committed that corrupted index without running any
hooks.

Recovery: `git reset --mixed 9e3c8f1` restored HEAD and index to master
while leaving the working tree's real edits intact, then re-staging the
5 files produced a correct commit.

---

## Proposed fixes

Each fix addresses exactly one problem and is independently landable.
All four together form a coherent hardening patch.

### Fix 1 — Move dev tools into `[dependency-groups]` so `uv sync` auto-installs them

**File:** `pyproject.toml`

```toml
[dependency-groups]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
    "pytest-cov>=4.0",
    "pytest-mock>=3.12.0",
    "pytest-timeout>=2.2.0",
    "pytest-xdist>=3.5.0",
    "mypy>=1.0",
    "ruff>=0.1.0",
    "respx>=0.20.0",
    "pre-commit>=4.5.0",
]
```

The old `[project.optional-dependencies] dev` can stay for
backward-compatibility (`pip install -e ".[dev]"` still works) or be
removed. Dependency-groups are PEP-735 and uv syncs the `dev` group by
default, so any fresh worktree that runs `uv sync` (or any `uv run`
that triggers a sync) will have pytest/ruff/mypy installed without
having to remember `--extra dev`.

**Effect:** eliminates Problem 1. A fresh `.venv` after `uv sync` will
have pytest in it, and `uv run pytest` will find it in the project env.

### Fix 2 — Unset leaking git env vars before running pytest in the hook

**New file:** `scripts/run-pytest-hook.sh`

```bash
#!/usr/bin/env bash
# Pre-commit pytest wrapper.
#
# Git exports GIT_DIR, GIT_INDEX_FILE, GIT_WORK_TREE, GIT_COMMON_DIR,
# GIT_PREFIX, GIT_OBJECT_DIRECTORY, and GIT_ALTERNATE_OBJECT_DIRECTORIES
# into every hook subprocess. Any test that shells out to `git init` in
# a tmp_path will inherit these variables and end up writing to the
# MAIN repo's .git directory instead — which is locked by the
# in-progress commit transaction. Clear them before invoking pytest.
#
# See docs/proposals/PRE_COMMIT_HOOK_HARDENING.md for context.
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_COMMON_DIR GIT_PREFIX \
      GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES

exec uv run --group dev pytest "$@"
```

Make executable: `chmod +x scripts/run-pytest-hook.sh` (git tracks the
exec bit on non-Windows; on Windows the `bash scripts/...` invocation
below works regardless).

**File:** `.pre-commit-config.yaml` — update the pytest hook entry:

```yaml
  - repo: local
    hooks:
      - id: pytest
        name: pytest (fast)
        entry: bash scripts/run-pytest-hook.sh -q --tb=line
        language: system
        files: ^(src|tests)/
        types: [python]
        pass_filenames: false
        always_run: false    # was: true (see Fix 4)
```

**Windows note:** `bash` resolves to Git Bash (shipped with Git for
Windows); pre-commit already relies on it for shell-based hooks, so no
new dependency. If portability to environments without bash becomes a
concern, a Python wrapper (`scripts/run_pytest_hook.py`) works equally
well — the unset pattern is `os.environ.pop(k, None)`.

**Effect:** eliminates Problem 2. Pytest's subprocess `git init` calls
see a clean environment and write to their `tmp_path` dirs as intended.

### Fix 3 — Add test-artifact names to `.gitignore` (defense in depth)

**File:** `.gitignore` — append:

```
# Test artifacts that sometimes leak into the repo root
# (caused Problem 3 of docs/proposals/PRE_COMMIT_HOOK_HARDENING.md).
# Once those tests are fixed to use tmp_path, these entries can go away.
/slides_test.py
/clm_cache.db
/clm_jobs.db
```

**Effect:** even if a misbehaving test writes into the repo root, git
won't pick it up, and pre-commit's stash/restore sees a clean working
tree for those paths. Does not fix the underlying test bug.

**Separate follow-up:** grep the test suite for writes to the repo
root that should be `tmp_path`-based:

```bash
git grep -n '"slides_test' tests/
git grep -n "'slides/" tests/ | grep -v tmp_path
git grep -n 'os.chdir' tests/
git grep -n 'Path.cwd()' tests/
```

Each hit is a candidate for a focused fix. This is the **right** way
to kill Problem 3 at the root; the `.gitignore` entries are only a
safety net.

### Fix 4 — Only run pytest hook when Python files change

**File:** `.pre-commit-config.yaml`

Change `always_run: true` to `always_run: false` on the pytest hook
(already shown in Fix 2's snippet). Combined with the existing
`files: ^(src|tests)/` filter, this means the pytest hook is skipped
entirely when a commit touches only docs, markdown, or config files.

**Effect:** docs-only commits become instant (no 30s pytest run),
drastically reducing the attack surface for Problem 3 (pre-commit
stash/restore is only invoked when there's something to isolate, and
only when pytest is actually going to run). Does not weaken correctness
— a docs-only commit cannot break Python tests.

---

## Verification plan

After applying all four fixes, verify in order:

1. **Problem 1** — Fresh `.venv` bootstrap:
   ```bash
   rm -rf .venv
   uv sync
   uv run pytest --collect-only -q 2>&1 | tail -5
   ```
   Expected: all tests collect, no `ModuleNotFoundError`. Confirms pytest
   is in `.venv` after `uv sync` without any `--extra` flag.

2. **Problem 2** — Hook env leakage:
   ```bash
   # Touch a Python file so the hook actually runs
   touch src/clm/__init__.py
   git add src/clm/__init__.py
   git commit -m "test commit"
   ```
   Expected: hook runs pytest, all 2826 tests pass (previously 20 of them
   failed with `could not lock config file`). Restore with
   `git reset --soft HEAD^` and `git restore --staged src/clm/__init__.py`.

3. **Problem 3** — Concurrent filesystem churn:
   Harder to reproduce directly. The indirect verification is that the
   tests that wrote `slides_test.py` into the repo root are fixed to use
   `tmp_path`. Before declaring this done, run:
   ```bash
   git status --porcelain | grep -E '^\?\? (slides_test\.py|slides/)'
   ```
   after a full `uv run pytest` run. Expected: no output (no
   artifacts left in the repo root).

4. **Fix 4** — Docs-only commit speed:
   ```bash
   touch README.md
   git add README.md
   time git commit -m "test"
   ```
   Expected: commit completes in <2s. Hook skipped entirely (`pytest
   (fast).........Skipped`). Undo with `git reset --soft HEAD^ && git
   restore --staged README.md`.

5. **Full test suite** — belt and braces:
   ```bash
   uv run pytest -q --tb=line
   ```
   Expected: 2826 passed, 1 skipped (same baseline as this session).

---

## Scope and non-goals

**In scope:**
- `.pre-commit-config.yaml` pytest hook changes
- `pyproject.toml` dep-group migration
- `scripts/run-pytest-hook.sh` new file
- `.gitignore` defense-in-depth additions

**In scope but separate follow-up:**
- Finding and fixing the tests that write into the repo root instead
  of `tmp_path`. This is worth doing but is independent of the hook
  hardening and can land as its own commit. See the Fix 3 "Separate
  follow-up" grep patterns.

**Out of scope:**
- Moving the mypy hook — same `uv run` pattern, but mypy doesn't spawn
  subprocesses that touch git, so Problem 2 doesn't affect it. It still
  benefits from Fix 1 (dependency-group migration) automatically.
- Moving the ruff hooks — same note. Ruff is pure static analysis; no
  git subprocess.
- Changing the test runner (e.g., to run outside the worktree). Too
  invasive and doesn't solve the root causes.
- Splitting the fast suite further — the current 30s is fine once
  Fix 4 makes it run only on Python-touching commits.

---

## Open questions

1. **Does uv's `dependency-groups` sync behavior apply on every `uv run`,
   or only on explicit `uv sync`?** I believe it's every `uv run` in a
   project (uv auto-syncs before running), but worth confirming before
   relying on it for Fix 1.

2. **Should the `[project.optional-dependencies] dev` group stay for
   `pip install -e ".[dev]"` compatibility?** I'd keep it as a mirror
   (both groups list the same packages) for users who aren't on uv.
   Decide during implementation.

3. **Is the hypothesized root cause of Problem 3 actually right?** We
   don't have a captured index-state mid-stash-restore. The
   `reset: moving to HEAD` entry in the reflog is consistent with
   pre-commit's restore path, but a deeper investigation would require
   instrumenting pre-commit or strace-ing the commit. Fix 4 makes this
   moot in practice by removing almost all opportunities for the race
   to trigger, so landing the fix without closing this question is fine
   — but if Problem 3 recurs after Fix 4, it's worth the instrumentation.

---

## Related

- Commit `5cd592c` (2026-04-11, docs: trim CLAUDE.md): the commit that
  hit all three problems in sequence and inspired this proposal.
- `docs/proposals/WORKER_CLEANUP_RELIABILITY.md`: separate hardening
  proposal for kernel/worker cleanup, unrelated but similar spirit.
- `.pre-commit-config.yaml`: the hook config that needs updating.
- `pyproject.toml`: the dep-groups that need migrating.
