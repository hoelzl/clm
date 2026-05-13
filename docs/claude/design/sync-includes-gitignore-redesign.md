# CLM Design — `sync-includes` Gitignore Handling Redesign

Status: **proposed** (2026-05-13). Resolves blocker **B2** in
[`python-course-include-migration.md`](../python-course-include-migration.md).
Companion to the parent feature design at
[`shared-source-includes-and-output-dedup.md`](shared-source-includes-and-output-dedup.md).

## Background

`clm sync-includes --gitignore` (shipped on master via PR #61, **not yet
released**) writes per-topic `.gitignore` files into each topic directory
that received a materialized `<include>`. Each file contains the include's
`as` path plus `.clm-include` (the ledger filename):

```
# slides/module_550_ml_azav/topic_040_gradio_intro/.gitignore
# Added by `clm sync-includes --gitignore`
simple_chatbot
.clm-include
```

The implementation is in
[`src/clm/cli/commands/sync_includes.py:501-538`](../../../src/clm/cli/commands/sync_includes.py)
(`_apply_gitignore`).

## Problem

Per-topic `.gitignore` files written by CLM **leak into build output**.
`is_ignored_file_for_course` (`src/clm/infrastructure/utils/path_utils.py:169`)
already filters `.clm-include` (added in PR1.7a) but treats `.gitignore` as a
normal file. The smoke test that caught the `.clm-include` leak didn't
exercise `--gitignore`, so the leak is still latent.

Adding `.gitignore` to `SKIP_FILE_NAMES` unconditionally would silently swallow
any author's hand-written topic-level `.gitignore` — a broader behavior change
than is justified. See the parent handover archive's
"Out-of-scope, captured for future" section
([`shared-source-includes-handover-archive.md:746-756`](../shared-source-includes-handover-archive.md)).

Two solutions were considered:

- **(a) Tag-the-ledger.** Write a marker line into the generated
  `.gitignore`; have `is_ignored_file_for_course` open the file and skip only
  marker-bearing ones. Preserves the `--gitignore` UX but adds per-file I/O
  to a hot path and special-cases `.gitignore` in core filtering logic.
- **(b) Move the ignore declaration out of CLM.** Stop writing `.gitignore`
  at all; the author manages a single course-root `.gitignore` once. No
  leak surface, no special case in `path_utils`.

This design chooses **(b)**.

## Design

### Principle

`clm sync-includes` writes only what it owns and can clean up via
`--remove`: materialized include targets and the `.clm-include` ledger.
**`.gitignore` is the author's file. CLM does not write to it.**

CLM's job is to *help* the author write the right entries — by suggesting
patterns derived from each run.

### CLI surface change

The `--gitignore` flag is **removed** and replaced with `--print-gitignore`.
Since PR #61 has not been released, this is a clean break with no deprecation
shim.

| Old | New |
|---|---|
| `--gitignore` (writes `.gitignore` files into topic dirs) | *(removed)* |
| *(no equivalent)* | `--print-gitignore` (prints suggested patterns to stdout, exits 0) |

`--print-gitignore` is mutually exclusive with `--remove` (nothing to suggest
when removing). Combined with `--dry-run`, it still prints the suggestions
(they're computed from spec + ledger state, not from materialization
side-effects).

### Pattern generation

Given a run's set of materialized entries (current ledger entries, indexed
by `(topic_dir, as_path)`):

1. Collect the unique `as_path` values across all topics. For each `as`,
   emit one pattern anchored at the course root using the convention
   `slides/**/<as>/`. The trailing slash matches a directory; `**/` allows
   the `as` directory to sit at any depth under `slides/`.
2. Emit one universal pattern for the ledger: `**/.clm-include`. Included
   even when no materializations happened in this run, so authors can
   bootstrap from a fresh repo with `clm sync-includes spec.xml --print-gitignore`
   before the first materialization.
3. Sort patterns deterministically (ledger pattern first, then `as` patterns
   alphabetically).

Example output for the AZAV ML spec post-migration (two topics, one `as`
name `simple_chatbot`):

```
# Added by `clm sync-includes --print-gitignore`
# Materialized include targets and per-topic ledgers.
**/.clm-include
slides/**/simple_chatbot/
```

Authors append this to `<course-root>/.gitignore` once. Re-running with
`--print-gitignore` produces the same output — paste-safe because gitignore
tolerates duplicate patterns.

#### Why path-anchored patterns, not bare names

A bare `simple_chatbot/` in the course-root `.gitignore` would also match
`examples/SimpleChatbot/src/simple_chatbot/` — the canonical source — and
exclude it from git. That's exactly wrong: the canonical copy must be
tracked. Anchoring with `slides/**/` confines the pattern to materialized
targets.

#### Edge case: `as` paths with separators

`IncludeSpec.as_path` permits nested paths (e.g., `as="vendor/pkg"`). The
generator must emit the deepest directory segment with the prefix preserved:
`slides/**/vendor/pkg/`. Validate that `as_path` contains no glob
metacharacters (`*`, `?`, `[`); reject the spec at parse time if it does.
This validation lives in `CourseSpec._parse_include` and is independent of
this redesign — it's a small hardening item.

### Summary-line nudge in the normal flow

When `clm sync-includes` runs *without* `--print-gitignore` and *with*
at least one materialization or refresh, the human summary ends with one
extra line:

```
2 created.
Tip: run `clm sync-includes <spec> --print-gitignore` for suggested .gitignore rules.
```

When nothing was materialized, no tip is shown (no gitignore-relevant state
changed). When `--remove` is used, no tip is shown.

This nudge is one short line. Authors who've already configured their
`.gitignore` can ignore it; first-time users get a clear next step.

### Ledger format — unchanged

`.clm-include` keeps its current JSON shape
([`sync_includes.py:48-110`](../../../src/clm/cli/commands/sync_includes.py)).
No new fields. Suggested patterns are derived on demand from `as_path`
entries — single source of truth, no drift risk.

### `is_ignored_file_for_course` — unchanged

`.clm-include` stays in `SKIP_FILE_NAMES`. `.gitignore` is NOT added.
Authors who keep a hand-written topic-level `.gitignore` (e.g., to exclude
a `_drafts/` subdir) keep that capability untouched.

### Course-template snippet (docs-only)

The user guide gains a one-line snippet for new course repos to drop into
their starter `.gitignore`:

```
# CLM: per-topic include ledgers (managed by `clm sync-includes`)
**/.clm-include
```

The `as`-name-specific patterns remain per-course because the names are
author-chosen. `clm sync-includes --print-gitignore` is the canonical way to
discover what to add.

## Implementation

Changes are localized to `src/clm/cli/commands/sync_includes.py` plus tests
and docs.

### Code

1. Delete `_apply_gitignore` and its call site
   ([`sync_includes.py:258-263`, `501-538`](../../../src/clm/cli/commands/sync_includes.py)).
2. Replace the `--gitignore` Click option with `--print-gitignore` (same
   `is_flag=True`).
3. Add `_print_gitignore_suggestions(entries: Iterable[tuple[Path, str]])`
   that builds and emits the pattern block to stdout.
4. In `sync_includes_cmd`:
   - If `--print-gitignore` and `--remove`: raise `UsageError`.
   - If `--print-gitignore`: skip the normal summary; emit only the pattern
     block; exit 0 even if `summary.materialized == 0` (so the ledger
     pattern is still emitted for bootstrap).
   - Otherwise, after `_print_summary`, emit the one-line tip when
     `summary.materialized + summary.refreshed > 0`.
5. Pattern generation in a pure helper `_compute_gitignore_patterns(as_paths:
   Iterable[str]) -> list[str]` so it's directly unit-testable without I/O.

### Tests (`tests/cli/test_sync_includes.py`)

Replace the existing `TestSyncIncludesGitignore` class entirely. New tests:

1. **`test_print_gitignore_emits_expected_patterns`** — spec with two
   topics sharing one `as`, run `--print-gitignore`, assert stdout contains
   exactly:
   ```
   **/.clm-include
   slides/**/<as>/
   ```
   (plus the comment header lines). Assert no `.gitignore` file was created
   anywhere under `tmp_path`.
2. **`test_print_gitignore_with_no_includes`** — spec with no
   `<include>` elements; assert stdout still emits the `**/.clm-include`
   pattern (bootstrap path) and nothing else.
3. **`test_print_gitignore_multiple_as_names_sorted`** — two `as`
   values (`pkg_a`, `pkg_b`); patterns emitted in deterministic
   alphabetical order.
4. **`test_print_gitignore_rejects_combination_with_remove`** — exit
   code != 0, error message names both flags.
5. **`test_summary_tip_shown_when_materializations_happened`** —
   normal run (no flag); stdout summary ends with the `Tip:` line.
6. **`test_summary_tip_absent_when_nothing_materialized`** — spec with no
   `<include>` elements; no tip line.
7. **`test_summary_tip_absent_for_remove`** — `--remove`; no tip.
8. **`test_no_dotgitignore_files_ever_written`** — exercises every flag
   combination (`--mode=copy`, `--mode=symlink`, `--remove`, `--dry-run`,
   `--print-gitignore`) and walks `tmp_path` afterward asserting zero
   `.gitignore` entries. This is the regression guard for B2.
9. **`test_compute_gitignore_patterns_unit`** — pure-function unit test for
   `_compute_gitignore_patterns` covering empty input, single `as`, nested
   `as` (`vendor/pkg`), and deduplication across topics.

### Build-output smoke (integration)

Add or extend an integration test under `tests/integration/` that builds a
course with at least one materialized include and asserts zero `.gitignore`
files appear under the output root across student/trainer/speaker variants.
The existing PR1.7a smoke test for `.clm-include` is the template.

### Docs updates

| File | Change |
|---|---|
| `src/clm/cli/info_topics/commands.md:266,297` | Replace `--gitignore` row + example with `--print-gitignore` description and example using shell redirection (`clm sync-includes spec.xml --print-gitignore >> .gitignore`). |
| `docs/user-guide/spec-file-reference.md:602,~605-625` | Replace the `--gitignore` example with `--print-gitignore`; add a short paragraph on the recommended one-time setup and the universal `**/.clm-include` line. |
| `src/clm/cli/info_topics/spec-files.md` | No change (does not mention gitignore today). |
| `CHANGELOG.md` | Unreleased section: note the `--gitignore` → `--print-gitignore` swap as a behavior change (breaking if anyone used the unreleased flag). |

### `python-course-include-migration.md` updates (after this lands)

When this redesign ships, the migration doc's reactivation criteria need
adjusting:

- **B2** is marked resolved; "passing test that confirms `clm sync-includes
  --gitignore` produces zero `.gitignore` files" becomes "passing test
  confirming `clm sync-includes` writes zero `.gitignore` files under any
  flag combination."
- **Step 4** (migration plan) `--gitignore` flag use is replaced with the
  one-time author setup: paste `**/.clm-include` and `slides/**/simple_chatbot/`
  into `<course-root>/.gitignore`. Or run `clm sync-includes
  course-specs/machine-learning-azav.xml --print-gitignore >> .gitignore`.
- **Step 5** smoke check for `.gitignore` leaks remains as a regression
  guard — same assertion, different mechanism.

## Why this over option (a)

| Concern | (a) Tag-the-ledger | (b) Move declaration out |
|---|---|---|
| Touches `path_utils` hot path | Yes (open + read marker per `.gitignore`) | No |
| Adds CLM-managed scattered files | Yes (one per topic) | No |
| Preserves `--gitignore` muscle memory | Yes | No (rename to `--print-gitignore`) |
| Risk of swallowing author's own `.gitignore` | Low (marker check) but non-zero | Zero |
| Number of new test surfaces | ~3 (marker write, marker detect, marker absence) | ~9 (above, half are tiny pure-function asserts) |
| Closes the leak surface entirely | Yes (with marker check) | Yes (no file written) |
| New behavior to document | Marker line + detection rule | One CLI flag rename + a snippet |

Option (b) wins on conceptual cleanliness (CLM stops touching files it
shouldn't own), simpler `path_utils`, and zero risk to author-written
ignore files. Option (a)'s only advantage — preserving the `--gitignore`
verb — is mooted by the fact that the flag is unreleased.

## Out of scope

- **Auto-editing the course-root `.gitignore`.** Tempting (`--write-gitignore
  <path>` would append to a chosen file), but: it reintroduces the "CLM
  writes the author's `.gitignore`" pattern this redesign is exiting, plus
  it can't easily handle `.gitignore` files split across submodules or
  `git config core.excludesFile`. `--print-gitignore` + shell redirection
  covers 95% of cases in one line.
- **Detection of "already added" entries.** Could grep the course-root
  `.gitignore` and suppress patterns already present. Skipped: gitignore
  semantics (anchoring, negation, parent overrides) are non-trivial to
  match against, and the duplicate-paste case is harmless.
- **Cleaning up obsolete patterns.** When an `<include>` is removed from a
  spec, the matching pattern in `.gitignore` is now stale. CLM doesn't
  manage author files, so removal is the author's job. The pattern is
  inert if no matching path exists, so leaving it costs nothing.
- **`.gitattributes` integration.** Same reasoning as `.gitignore`: author's
  file, not CLM's.

## Reactivation triggers

This design is ready to implement once approved. Once shipped:

- The PythonCourses migration's B2 blocker clears
  ([`python-course-include-migration.md`](../python-course-include-migration.md)).
- B1 (topic-ID-before-children XML wrinkle) still blocks the migration
  independently and is tracked separately.
