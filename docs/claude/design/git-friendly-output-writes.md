# CLM Design — Git-Friendly Output Writes

Status: **proposed** (2026-05-16, design only — no implementation has
started). Targets a future minor release (likely 1.5.0). Replaces the
"wipe and rebuild" output strategy with hash-aware writes plus a
post-build stray-file sweep so that course output directories stay
fast under git.

Motivation captured in:
- [`git-friendly-output-writes-gemini-conversation.md`](git-friendly-output-writes-gemini-conversation.md)
  — the source Gemini conversation that prompted this design. The three
  strategies Gemini proposed (rsync staging, `git ls-files` manifest,
  pure-plumbing) are evaluated against the chosen approach in
  [Alternatives considered](#alternatives-considered) below.
- This design extends the [`OutputWriteRegistry`](../shared-source-includes-and-output-dedup.md#feature-2-output-write-deduplication)
  introduced in PR #64 — same registry, new consumer at the disk-write boundary.

## Problem

CLM's current build flow is hostile to git in the output tree:

1. `git_dir_mover` (`src/clm/cli/git_dir_mover.py:12-74`) finds every
   nested `.git` under each output root and `shutil.move()`s it to a
   tempdir under the context manager's `__enter__`.
2. `build.py:802-806` then runs `shutil.rmtree(root_dir, ignore_errors=True)`
   over the entire output tree.
3. Everything is regenerated from scratch; mtimes/inodes change for every
   file even when content is unchanged.
4. `__exit__` moves the `.git` directories back.

The net effect on a repo containing the output: git's stat-cache is
invalidated for every tracked file, so the next `git status` re-hashes
the entire tree. On the AZAV ML course (~thousands of files) this turns
sub-second `git status` into multi-minute rebuilds of the index.

Compounding factor: notebook filenames carry a serial-number prefix
(`notebook_file.py:271`, `f"{self.number_in_section:02} {sanitized_title}{ext}"`)
re-assigned per build (`section.py:26-28`). Inserting one topic shifts
the prefix of every later notebook in the section, producing genuine
renames even when content is unchanged.

CLM today does have two partial mitigations:
- `--keep-directory` (`build.py:1130`) skips both the `.git` move and
  the rmtree.
- `--incremental` (`build.py:961`) implies `keep_directory` and additionally
  skips disk writes on database cache hits (`sqlite_backend.py:116-122`).

Both are opt-in. `--incremental` correctness depends on the existing on-disk
state matching the cache exactly; any drift (stale files from old names,
hand-edits, partial wipes) produces silent inconsistency. Neither is safe
to make the default in its current form.

## Goals

- **G1.** Default build flow does not invalidate git's stat-cache for
  unchanged content. After two consecutive builds with no spec changes,
  `git status` in the output tree is sub-second.
- **G2.** No content drift relative to today's "wipe and rebuild" output —
  any file produced by a clean rebuild must also be produced (with the
  same content) by the new flow.
- **G3.** No stale files. Renames, reorders, and deletions in the spec
  must remove the obsolete files from the output tree, with the same
  guarantees the rmtree-then-rebuild flow provides today.
- **G4.** Build the feature on top of the existing `OutputWriteRegistry`
  rather than introducing a parallel mechanism.
- **G5.** Old behavior remains available behind an opt-in flag (`--clean`),
  for emergency recovery and parity with current scripts that depend on it.

## Non-Goals

- Coupling CLM to git as a source of truth. The output tree is sometimes
  shipped to consumers (students, instructors) who do not have a git
  repo at all. The design must work identically whether the output dir
  is a git repo, untracked, or anywhere else.
- Bypassing the working tree (Gemini's Strategy 3 — `git hash-object`
  plumbing). Worker subprocesses (`jupyter nbconvert`, the PlantUML JAR,
  `drawio.exe`) write to disk; intercepting those writes is a worker-layer
  rewrite out of scope for this feature.
- Persisting a cross-build manifest. The stray-file sweep operates on
  the current build's registry plus a filesystem walk; no separate
  state file is required.
- Solving the serial-number renumbering itself. Git detects renames by
  content similarity; what matters for performance is *not modifying
  unchanged files*, which this design delivers.

## Design

Four pieces, ordered by dependency.

### D1. Per-write destination check at every output write site

There are four registry-aware write call sites today:

| # | Site | Location | Current behavior |
|---|---|---|---|
| 1 | `LocalOpsBackend.copy_file_to_output` | `local_ops_backend.py:53-113` | Calls `record_write(content_source=…)`. DEDUP outcome skips the `shutil.copyfile`. Other outcomes always copy. |
| 2 | `LocalOpsBackend._register_dir_group_writes` | `local_ops_backend.py:147-` | Post-hoc registration after `shutil.copytree`. No skip path; files already on disk. |
| 3 | `SqliteBackend.execute_operation` cache-replay | `sqlite_backend.py:115-187` | On DB cache hit, calls `record_write(content=…)`. DEDUP outcome skips `atomic_write_bytes`. Other outcomes always write. |
| 4 | `SqliteBackend` worker-readback | `sqlite_backend.py:478-498` | Worker subprocess has already written the file. Registry registration is informational only. |

Sites 1 and 3 are the high-leverage targets — sites 2 and 4 happen
after the bytes are already on disk and cannot be skipped without
restructuring the worker layer.

**Add a `WriteOutcome.UNCHANGED_ON_DISK` variant** to
`output_write_registry.py:98-114`. The registry doesn't decide this
itself (it doesn't stat the disk); instead, callers check on demand
using a new helper:

```python
class OutputWriteRegistry:
    def is_destination_identical(
        self,
        output_path: Path,
        *,
        content: bytes | None = None,
        content_source: Path | None = None,
    ) -> bool:
        """Return True iff output_path exists on disk and its content
        is byte-identical to the supplied content / file. Cheap-path
        first: stat size mismatch → False without hashing. Used by
        write call sites to skip the actual disk write so mtime is
        preserved and git's stat-cache stays valid."""
```

**Cheap-path comparison order** (each step short-circuits to "differs"):
1. `output_path.exists()` — no → not identical.
2. Source size vs. `output_path.stat().st_size` — mismatch → not identical.
3. Hash both (reuse `_HASH_READ_CHUNK`, `BLAKE2b-128`, same algo as
   `record_write`). The expensive step, but only reached when sizes match.

Above `_resolve_hash_limit_bytes()` (currently 50 MB by default), fall
back to the existing behavior — write unconditionally. The "skip" is an
optimization, not a correctness boundary, so it's safe to give up on
huge files.

**Caller change at site 1** (`local_ops_backend.py`, after the existing
DEDUP branch around line 71):

```python
if write_result.outcome == WriteOutcome.FIRST_WRITE:
    if self.output_write_registry.is_destination_identical(
        abs_output, content_source=copy_data.input_path
    ):
        logger.debug(f"Disk-skip: {abs_output} already has identical content")
        return  # don't shutil.copyfile, preserve mtime
```

**Caller change at site 3** (`sqlite_backend.py`, around line 168 before
`atomic_write_bytes`):

```python
if not skip_write:
    if self.output_write_registry.is_destination_identical(
        output_file, content=content_bytes
    ):
        logger.debug(f"Disk-skip: {output_file} already has identical content")
    else:
        atomic_write_bytes(output_file, content_bytes)
```

**Sites 2 and 4 are left unchanged.** Worker-readback (4) cannot help
us — the worker has already written. Dir-group (2) uses `shutil.copytree`
which doesn't easily support per-file skip; it's a minority of writes
and we accept the cost. If profiling shows dir-groups are hot, we can
revisit by replacing `_copy_dir_group_to_output_sync` with a walk that
goes through `copy_file_to_output` per file.

### D2. End-of-build stray-file sweep

Once all stages complete, the build's `OutputWriteRegistry` holds the
complete set of paths the build *intended* to populate. Anything under
a build-owned root that's not in that set is stray.

Add a new function in `src/clm/cli/output_sweep.py` (new module):

```python
def sweep_stray_files(
    root_dirs: Iterable[Path],
    registry: OutputWriteRegistry,
    *,
    keep_patterns: Iterable[str] = DEFAULT_KEEP_PATTERNS,
    dry_run: bool = False,
) -> SweepReport:
    """Walk each root_dir recursively. For each file not in the
    registry's entries and not matching keep_patterns, delete it.
    After the file pass, remove empty directories bottom-up.
    Returns counts and a list of deleted paths for the build summary."""
```

**Default `keep_patterns`** — paths the sweep must never touch:
- `.git/**` (covers nested `.git` directories anywhere under each root —
  see EC9 for the full nested-repo handling)

That is the entire allow-list. The output directory's governing
principle is **everything under a build-owned root must be produced by
`clm build`** — authors do not hand-edit, hand-place, or commit
auxiliary files (`.gitignore`, `README.md`, editor caches, `.DS_Store`,
`__pycache__`, etc.) into the output tree. The sweep enforces that
principle.

Specifically, files matching `SKIP_DIRS_FOR_OUTPUT` /
`SKIP_DIRS_PATTERNS` / `SKIP_OUTPUT_FILE_GLOBS` from `path_utils.py`
are **not** excluded from the sweep. Those patterns are auto-generated
junk (caches, OS metadata) or content explicitly withheld from
students; if they appear under the output tree, they were placed by
mistake or by manual intervention, and the sweep should remove them.

If a course genuinely needs a file like `.gitignore` at the root of
its output (e.g., to ignore a sibling build-artifact directory), the
right answer is to teach CLM to generate it as part of the build —
not to special-case the sweep.

**Image-path handling.** The registry deliberately skips `img/`
paths (`output_write_registry.py:17-20`) because `ImageRegistry`
owns those. Without intervention the sweep would treat every image
as stray. Two options:

- **Option A.** Extend `ImageRegistry` to expose a `tracked_paths`
  property; the sweep takes the union of registry paths + image
  registry paths as "expected".
- **Option B.** Hardcode `img/**` into `keep_patterns`.

**Recommend Option A.** It correctly handles the case where an image
is *removed* from the spec — option B would silently keep removed
images forever. Stale-image cleanup is a real bug currently masked by
the rmtree.

**Invocation point.** In `build.py:_run_stages` (around line 762, after
`build_reporter.report_output_writes(...)` and before `finish_build`),
call `sweep_stray_files(root_dirs, backend.output_write_registry)`
and feed the result into the build reporter. Skip the sweep when
`--only-sections` mode is active — that mode has its own narrower
cleanup scope (`build.py:770-782`), and a sweep over the full root
would delete files for non-selected sections.

**Empty-directory cleanup.** After the file pass, walk each root_dir
bottom-up; remove any directory that is now empty (excluding the root
itself and any `.git/` directory). This catches the case where an
entire section was renamed `01 Intro` → `01 Introduction`: every
file under the old `01 Intro/` is stray and gets deleted, then the
empty dir itself gets removed.

### D3. Default behavior change

In `build.py:802-806`, today's logic is:

```python
with git_dir_mover(root_dirs, config.keep_directory):
    for root_dir in root_dirs:
        if not config.keep_directory:
            shutil.rmtree(root_dir, ignore_errors=True)
```

Flip the default. `keep_directory` becomes the implicit normal mode;
the wipe-and-restore branch becomes opt-in:

```python
if config.clean:
    # Legacy / emergency-recovery path. Wipes output, preserves
    # nested .git, regenerates from scratch. Strictly slower than
    # the default but useful when the on-disk state is corrupt.
    with git_dir_mover(root_dirs):
        for root_dir in root_dirs:
            shutil.rmtree(root_dir, ignore_errors=True)
        course.precreate_output_directories()
        await _run_stages()
else:
    # New default: do not wipe, do not move .git. Hash-aware writes
    # (D1) preserve mtimes for unchanged files; stray-file sweep
    # (D2) at end of build removes orphans.
    course.precreate_output_directories()
    await _run_stages()
    # sweep runs inside _run_stages' finally block — see D2
```

**CLI surface:**
- Add `--clean` flag (replaces today's implicit default).
- Keep `--keep-directory` as a no-op alias that warns it's now the default
  (one release of deprecation, remove in 1.6).
- `--incremental` remains as-is but now buys less — it implies `--no-sweep`
  on top of the new default, since `--incremental` users explicitly
  trust the on-disk state.
- Add `--no-sweep` for users who want hash-aware writes but no stray
  cleanup (useful when iterating on a single section and you don't
  want orphans from other sections deleted).

**Config struct.** Add `clean: bool` and `sweep: bool` to `BuildConfig`
(`build.py:104-`). Default `clean=False`, `sweep=True`.

### D4. Status of `git_dir_mover`

Keep the module, keep the implementation. It still runs under `--clean`.
Mark its public surface as "internal, used only by `--clean`" in the
docstring; flag in the migration info topic
(`src/clm/cli/info_topics/migration.md`) that the default no longer
wipes output. No deletion in this release.

## Edge cases and open questions

| # | Case | Resolution |
|---|---|---|
| EC1 | First build of a course (empty output tree). | Hash-aware check sees no destination → writes normally. Sweep walks an empty tree → no-op. Zero overhead vs. today. |
| EC2 | User has hand-edited a generated file or hand-placed an auxiliary file (`.gitignore`, `README.md`, editor cache, …). | Sweep deletes it. This is the governing principle, not an edge case — the output tree is exclusively CLM's. `--no-sweep` exists for users who want to iterate without orphan cleanup but does not change the principle. |
| EC3 | Worker writes a slightly different output for byte-identical input (timestamp metadata in `.ipynb`, etc.). | The hash check correctly sees a difference → writes. Mtime updates. Git sees a real change. This is correct, just not free. Worth measuring how often it triggers. |
| EC4 | Same path written twice in one build with identical content (the existing DEDUP case). | Unchanged. Registry's existing DEDUP outcome already short-circuits. Stray sweep sees the path in `entries` → keeps it. |
| EC5 | `--only-sections` mode. | Skip the sweep entirely (already noted in D2). `--only-sections` has its own scoped cleanup at `build.py:770-782`. Hash-aware writes (D1) still apply. |
| EC6 | Large file (above `CLM_OUTPUT_DEDUP_HASH_LIMIT_MB`). | `is_destination_identical` returns False → write unconditionally. Same behavior as today for large files. |
| EC7 | Sweep runs but a stage errored out partway. | The registry only contains entries from writes that were *attempted*. If a stage fails before its writes, the sweep would mistakenly delete files from prior successful builds. **Mitigation:** skip the sweep when `build_reporter` has logged any stage-fatal errors. Add `SweepReport.skipped_due_to_errors` for the summary. |
| EC8 | Race with watch mode (`--watch`). | Watch mode re-enters `process_course_with_backend` per file change. The sweep would delete files for sections not in the current iteration. **Resolution:** disable the sweep when `config.watch` is set. Watch mode is dev-time iteration; correctness is provided by the next full build. |
| EC9 | Nested git repos. | The sweep excludes `.git/**` everywhere. A nested git repo's tracked files outside `.git/` would still be candidates for deletion if not in the registry. **Resolution:** if a directory contains a `.git/` entry, treat the entire directory as opaque (skip the subtree). Mirrors how `SKIP_DIRS_PATTERNS` already treats nested vcs dirs. |
| EC10 | Symlinks under the output root. | Walk with `follow_symlinks=False`. If a symlink isn't in the registry, delete the link only (not the target). |

**Resolved D1.** Hash-aware writes do **not** apply at site 4
(worker-readback). Restoring mtime after the worker subprocess has
written is fragile (atomic temp+rename changes inodes; `utime` works
on Windows/Linux but requires care) and the savings depend on workers
producing byte-identical output, which is uncommon. Re-evaluate only
if profiling shows worker-readback as a meaningful contributor to
git-status latency.

**Resolved D2.** The registry does **not** persist across builds.
The end-of-build walk is O(files-on-disk), comparable to one
`git status`, and cross-build state introduces correctness risk
(stale manifest after manual edits or interrupted builds) for a
small performance win.

**Resolved D3.** `--keep-directory` emits a deprecation warning for
one release ("now default, will be removed in 1.6") and is removed
in the following minor release. The flag is documented and ecosystem
scripts may reference it, so silent no-op is unacceptable.

## Test plan

### Unit

- `tests/core/test_output_write_registry.py` — extend with
  `is_destination_identical` cases:
  - dest does not exist → False
  - dest exists, size differs → False (without hashing — verify by
    spying on the hash function)
  - dest exists, size matches, content differs → False
  - dest exists, content identical → True
  - dest above size limit → False (no skip, write proceeds)
- `tests/cli/test_output_sweep.py` (new) — sweep semantics:
  - empty registry, empty tree → no-op
  - registry has paths, tree has stray file → file deleted
  - tree has `.git/` → preserved
  - tree has `.gitignore` at root → preserved
  - tree has nested `.git` repo → entire repo preserved
  - tree has stray symlink → link removed, target untouched
  - tree has empty dir left over → removed bottom-up
  - sweep skipped when stage errors recorded
  - sweep skipped in `--only-sections` mode
  - sweep skipped in `--watch` mode

### Integration

- `tests/cli/test_build_command.py` — extend:
  - **The git-friendliness test.** Build a small course twice into the
    same output dir. Capture `(path, mtime, inode)` of every output
    file after build 1. After build 2, assert that ≥95% of files have
    identical mtime+inode (the survivors of hash-aware skip). The
    threshold accounts for files whose content legitimately changes
    (e.g., notebook metadata timestamps if any).
  - **Stale-file removal test.** Build course A with sections S1, S2.
    Modify spec to remove S2. Rebuild. Assert S2's output directory
    is gone after sweep.
  - **Rename test.** Section title changes `01 Intro` → `01 Introduction`.
    Rebuild. Assert old dir is gone, new dir is populated.
  - **`--clean` flag works.** Asserts old wipe-and-restore path still
    runs and produces identical output to default flow.
- `tests/cli/test_build_only_sections.py` — assert sweep is *not*
  invoked under `--only-sections`.

### End-to-end (slow, opt-in)

- `tests/e2e/test_git_friendliness.py` (new, `slow` marker) — initialize
  an empty git repo over a real course output, run two builds, capture
  `git status` wall time on the second run. Assert it completes under
  some threshold (5 s for a course of N files; pick threshold by
  measuring current `--incremental` runs).

### Smoke (manual, documented)

- Build AZAV ML (`machine-learning-azav.xml`) into the existing
  `D:\CLM\Recordings` tree. Verify `git status` is sub-second.
- Insert a topic mid-section; rebuild; verify only the renamed files
  show up in `git status`, not the entire section.

## Implementation phasing

Three PRs, each independently testable and revertable.

**PR 1 — Hash-aware writes (D1).** Adds `is_destination_identical`,
`WriteOutcome.UNCHANGED_ON_DISK` (unused initially, in case we want
to plumb it back), and the two skip sites in
`local_ops_backend.py` and `sqlite_backend.py`. Behind a feature flag
`CLM_HASH_AWARE_WRITES=1` initially so we can A/B perf before flipping.
~300 LOC + 200 LOC tests.

**PR 2 — Stray-file sweep (D2).** Adds `src/clm/cli/output_sweep.py`,
extends `ImageRegistry` with `tracked_paths`, wires the sweep into
`_run_stages`' finally block. Sweep gated on a new BuildConfig field
`sweep: bool` that defaults to False initially. ~250 LOC + 300 LOC
tests.

**PR 3 — Default flip (D3, D4).** Flips defaults, adds `--clean` and
`--no-sweep` flags, updates info topics (`commands.md` and
`migration.md`), deprecates `--keep-directory` with warning, updates
`CHANGELOG.md`. ~150 LOC + 100 LOC tests.

After PR 3 lands and bakes for a release, a follow-up can remove the
feature flag and `--keep-directory` alias.

## Alternatives considered

**A1. rsync-style staging (Gemini's Strategy 1).** Build into a tempdir,
then `rsync --delete` into the live tree. Considered and rejected:
2× disk I/O during build (write to temp, then sync to final), and
on Windows there is no native `rsync` — we'd reimplement it. The
hash-aware approach gets the same outcome (preserved mtimes for
unchanged files) at 1× I/O. Kept as a fallback if the hash-aware
approach hits unforeseen integration problems with the worker layer.

**A2. `git ls-files` manifest (Gemini's Strategy 2).** Use git as the
source of truth for what should exist. Rejected per the non-goals:
output is sometimes shipped to non-git consumers, and git-only logic
makes the build path bimodal.

**A3. Plumbing-only (Gemini's Strategy 3).** Discussed and rejected
in non-goals. Would require rewriting the worker subprocesses to
stream content back rather than write to disk.

**A4. Make `--incremental` the default.** Tempting but unsound. Incremental
mode skips disk writes on cache hits *without verifying* the on-disk
file is correct. Any drift (partial wipe, hand-edit, interrupted
prior build) produces silent inconsistency. Hash-aware writes give us
the same speed with a stat+hash safety check.

**A5. Per-section sweep instead of per-root sweep.** Sweep only inside
`precreate_output_directories`' set. Rejected because section
renames leave stale directories *outside* the new precreate set —
exactly the case we need to handle.

## Estimated size

| Component | LOC src | LOC tests |
|---|---|---|
| `is_destination_identical` + `WriteOutcome.UNCHANGED_ON_DISK` | 80 | 150 |
| Site 1, site 3 skip paths | 40 | 50 |
| `output_sweep.py` (sweep + report struct) | 180 | 250 |
| `ImageRegistry.tracked_paths` | 30 | 50 |
| Build wiring (`_run_stages`, BuildConfig) | 60 | 50 |
| CLI flags + deprecation warning | 80 | 50 |
| Info-topic updates | 30 | — |
| **Total** | **~500** | **~600** |

Larger than a quick fix, smaller than the PR #64 baseline (which was
~1200 LOC). Two months of intermittent work or three weeks dedicated.

## References

- `src/clm/cli/git_dir_mover.py` — current `.git`-move logic.
- `src/clm/cli/commands/build.py:104-`, `:419-`, `:700-830`, `:1130-` —
  build entry points, BuildConfig, root_dir computation, flag wiring.
- `src/clm/core/output_write_registry.py` — registry to extend.
- `src/clm/infrastructure/backends/local_ops_backend.py:53-180` — write
  sites 1 and 2.
- `src/clm/infrastructure/backends/sqlite_backend.py:100-200`,
  `:478-498` — write sites 3 and 4.
- `src/clm/core/course_files/notebook_file.py:271`,
  `src/clm/core/section.py:26-28` — serial-number naming (root cause
  of rename churn, not addressed here).
- `src/clm/core/image_registry.py` — sibling registry for `img/` paths;
  needs `tracked_paths` extension for the sweep.
- `src/clm/cli/info_topics/commands.md`,
  `src/clm/cli/info_topics/migration.md` — info topics to update on
  default flip (per CLAUDE.md rule).
- [`shared-source-includes-and-output-dedup.md`](shared-source-includes-and-output-dedup.md)
  — design of the registry this feature extends.
