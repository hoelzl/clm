# CLM {version} — CLI Command Reference

## Global Options

```
clm [OPTIONS] COMMAND [ARGS]...
```

| Option | Description |
|--------|-------------|
| `--version` | Show version and exit |
| `--cache-db-path PATH` | Path to cache database (default: `clm_cache.db`) |
| `--jobs-db-path PATH` | Path to job queue database (default: `clm_jobs.db`) |

## Commands

The CLI is organised into a small top-level surface plus verb groups
(`slides`, `topic`, `authoring`, `voiceover`). The reference below
uses the canonical (group-qualified) names; older flat names like
`clm normalize-slides`, `clm validate-slides`, etc. still work as
deprecated aliases and print a one-line migration hint on each
invocation. The aliases will be removed in CLM 1.7; see
`clm info migration` for the full rename table.

### `clm build`

Build a course from a spec file.

```
clm build [OPTIONS] SPEC_FILE
```

Key options:

| Option | Description |
|--------|-------------|
| `-d, --data-dir DIR` | Source data directory |
| `-o, --output-dir DIR` | Override where build output is written. For specs with `<output-targets>`, each target is re-rooted to `<DIR>/<target.name>/` (same layout `--snapshot` produces and what the regular spec-driven build writes). For specs without output-targets, DIR becomes a single collapsed output tree. |
| `--snapshot DIR` | Capture build output to DIR as a verification baseline. Identical layout to `--output-dir DIR` plus three safety guards: DIR must not exist or be empty, mutually exclusive with `--output-dir` and `--verify-against`, and prints a confirmation line after the build. See "Snapshot / verify" below. |
| `--verify-against DIR` | Build, then byte-compare the output tree against the snapshot at DIR. Exits non-zero on any diff. `.html` is skipped by default (kernel-execution noise). See "Snapshot / verify" below. |
| `--include-html` | With `--verify-against`: include `.html` files using hex-address normalization. |
| `--strict-verify` | With `--verify-against`: byte-compare every file, no normalization or skipping. |
| `-w, --watch` | Watch for changes and auto-rebuild |
| `--watch-mode [fast\|normal]` | `fast` = notebooks only; `normal` = all formats |
| `--ignore-cache` | Reprocess all files (still updates cache) |
| `--clear-cache` | Clear cache before building |
| `--clean` | Wipe each output root and regenerate from scratch (legacy flow; preserves nested `.git/`). Use for emergency recovery from a corrupted output tree. The default no longer wipes — see "Git-friendly output writes" below. |
| `--no-sweep` | Disable the post-build stray-file sweep. Useful when iterating on a single section and you don't want orphans from other sections deleted. |
| `--incremental` | Keep directories, only write newly processed files (skip cached ones). Implies `--no-sweep`. |
| `--keep-directory` | **Deprecated** (CLM {version}, will be removed in 1.7). Keeping the output tree is now the default; this flag is a no-op alias. |
| `--only-sections TEXT` | Comma-separated selector tokens; rebuild only those sections and leave unselected section output untouched. Dir-group processing is skipped in this mode. See "Iterating on a single section" below. |
| `--workers [direct\|docker]` | Worker execution mode |
| `--notebook-workers N` | Number of notebook workers |
| `--plantuml-workers N` | Number of PlantUML workers |
| `--drawio-workers N` | Number of Draw.io workers |
| `--max-workers N` | Hard cap on effective worker count per type. Applied on top of automatic CPU/RAM-derived caps. Also settable via the `CLM_MAX_WORKERS` environment variable. Use to keep an oversized spec file (e.g. an 18-worker course override) from saturating a small dev laptop. |
| `--notebook-image TEXT` | Docker image for notebook workers |
| `-O, --output-mode [default\|verbose\|quiet\|json]` | Progress output mode |
| `-L, --language [de\|en]` | Generate only one language |
| `--speaker-only` | Generate only the private (notes-bearing) outputs — both `trainer` and `recording` kinds. Skips public outputs (`code-along`, `completed`, `partial`). |
| `-T, --targets TEXT` | Comma-separated target names from spec |
| `--image-mode [duplicated\|shared]` | Image storage strategy |
| `--image-format [png\|svg]` | Image output format |
| `--inline-images` | Embed images as base64 in notebooks |
| `--http-replay [replay\|once\|new-episodes\|refresh\|disabled]` | HTTP replay record mode for topics with `http-replay="yes"` in the spec. `replay` requires a cassette (strict, CI default); `once` records on first run, replays thereafter (strict on new requests); `new-episodes` replays recorded requests and records any new ones (local default); `refresh` re-records every run; `disabled` bypasses replay. Defaults to `replay` when `CI=true`, else `new-episodes`. Also settable via `CLM_HTTP_REPLAY_MODE`. |
| `--fail-on-error / --no-fail-on-error` | Exit with non-zero status when the build summary reports any cell or notebook error. Defaults to **on** under `--http-replay=replay` (incl. CI) and **off** under all other replay modes. Override via `CLM_FAIL_ON_ERROR={1,true,yes,0,false,no}`. See "Exit codes" below. |
| `--fail-on-missing-xref / --no-fail-on-missing-xref` | Exit with non-zero status when a `clm:` cross-reference points at a topic not included in the build (issue #17). Defaults to **on** under `--http-replay=replay` (incl. CI) and **off** under all other replay modes (a missing target is then a warning and the link is dropped). Override via `CLM_FAIL_ON_MISSING_XREF={1,true,yes,0,false,no}`. See `clm info spec-files` → "Cross-references". |

Examples:

```bash
clm build course.xml
clm build course.xml -w --watch-mode fast
clm build course.xml --workers docker -T students,solutions
clm build course.xml --clear-cache -L en
clm build course.xml --only-sections w03
clm build course.xml --only-sections w03,w04 -w
clm build course.xml --only-sections "Week 03"
```

#### Iterating on a single section

`--only-sections` is a dev-time iteration flag for large courses. It
rebuilds only the selected sections and leaves every other section's
output directory untouched — much faster than a full clean-and-rebuild
on a 20+ week course.

Selector syntax (comma-separated tokens):

- **Bare tokens** try in order: exact `id` match → 1-based index →
  case-insensitive substring match on either the German or English
  section name. First strategy that yields ≥1 match wins.
- **Prefixed tokens** force one strategy: `id:w03`, `idx:3`,
  `name:"Woche 03"`.
- Section indices are 1-based and count **all** sections in declared
  order, including disabled ones — toggling `enabled="false"` does not
  renumber the sections that follow.

Selector errors:

- Empty token or whitespace-only value → error, not silent full build.
- Zero matches → error with a listing of all available sections.
- Ambiguous bare substring (e.g. `"Introduction"` matching two sections)
  → error; disambiguate with a prefixed form.
- A mixed list containing disabled sections → skip each disabled
  section with a warning and build the rest.
- A selection that matches *only* disabled sections → error.

What `--only-sections` does **not** do:

- It does **not** run dir-group processing. Dir-groups produce the
  final shipping state of a course; run a full build when you need
  them.
- It does **not** detect section renames. If you rename a section,
  `--only-sections <new-name>` will warn that the old output directory
  is missing — run a full build once to clean up the stale name.
- It does **not** modify other sections' output directories, the
  top-level course files (README, `pyproject.toml`, etc.), or any git
  metadata.

#### Output-write deduplication and conflict warnings

`clm build` records every output write to a per-build registry keyed
by absolute output path. Two write semantics surface in the build
summary:

- **Identical-content re-writes are deduplicated.** When multiple
  topics produce the same output path with byte-identical content
  (common for `<include>`-shared files and the C# course's repeated
  `NUnitTestRunner.cs`), only the first write touches disk. The
  others are counted as dedups and surfaced as
  `N duplicate output writes deduplicated` in the human summary and
  `output_dedup_count: N` in the JSON summary.
- **Differing-content writes to the same output path are flagged.**
  The build proceeds (last writer wins, preserving previous
  behavior) and emits one warning per conflicting output path
  (category `output_path_conflict`, severity `medium`) naming the
  first and last writers. The JSON summary records each conflict
  under the `output_conflicts` key as
  `{output_path, first_writer, last_writer, first_hash, last_hash,
  conflict_count}`.

Image paths under an `img/` segment are owned by the existing
`ImageRegistry` collision channel (category `image_collision`) and
are skipped by the output-write registry — no double-warning.

Tunable via the `CLM_OUTPUT_DEDUP_HASH_LIMIT_MB` environment
variable (default 50 MB; see `docs/user-guide/configuration.md` →
Performance). Files larger than the limit skip hashing and are
reported as a single `output_large_file_collision_count` summary
value rather than per-event warnings.

#### Git-friendly output writes

Starting in CLM {version}, `clm build` no longer wipes the output
tree at the start of every build. Two mechanisms keep the tree
correct without invalidating git's stat-cache:

- **Hash-aware writes.** Before writing a file, the build checks
  whether the destination already holds byte-identical content. If
  so, the write is skipped — mtime/inode are preserved, and a
  subsequent `git status` over the output tree stays sub-second.
- **Post-build stray-file sweep.** Anything under a build-owned
  root that the build did not write (e.g. orphans from a renamed
  section, a removed topic) is deleted in a sweep after all stages
  complete. The sweep only spares nested `.git/` directories;
  hand-placed auxiliary files (`.gitignore`, `README.md`, editor
  caches) under an output root are treated as stray and removed.

The sweep is skipped automatically under:

- `--clean` — already regenerates the whole tree.
- `--only-sections` — has its own narrower cleanup scope.
- `--watch` — event-driven rebuilds only populate the changed file.
- `--incremental` — incremental users explicitly trust the on-disk
  state, so a sweep would delete files cache replay decided not to
  re-emit.
- After fatal stage errors — the registry is incomplete, so sweeping
  could remove files from prior successful builds.

`--no-sweep` opts out manually (useful when iterating on a single
section and you don't want orphans from other sections deleted).

If you need the legacy wipe-and-rebuild flow — emergency recovery
from a corrupted output tree, or a script that depends on a clean
build — use `--clean`. Nested `.git/` directories are preserved
across the wipe.

#### Split-source build routing

Slide files in split format — `<basename>.de.py` and
`<basename>.en.py`, produced by `clm slides split` — route directly
through the per-language pipeline: a `.de.py` file is built only
for `lang=de` and a `.en.py` file only for `lang=en`. No unify
step, no temporary file. Build output is byte-identical to building
the bilingual companion (same per-cell `lang` filter, same output
paths, same section index — split companions are treated as one
logical slot when numbering notebooks within a section).

The build detects three other shapes per slide family
(`slides_foo.py`, `slides_foo.de.py`, `slides_foo.en.py` all share
a *family*) and the routing rule is:

- **Bilingual only** (`slides_foo.py`, no companions) — fed to both
  DE and EN pipelines exactly as before.
- **Split pair** (`.de.py` + `.en.py`, no bilingual) — each file
  routes to its own per-language pipeline.
- **Dual-format conflict** (bilingual *and* at least one split
  companion present) — build refuses before any worker runs with
  category `split_slide_dual_format`. Resolve by running
  `clm slides unify` to merge or deleting the bilingual companion.
- **Half-pair** (only one of `.de.py` / `.en.py`) — build refuses
  before any worker runs with category `split_slide_half_pair`.
  Add the missing companion.

`clm validate <topic_dir>` (and `clm validate <course-spec>`)
additionally diffs the shared (no-`lang`) cells between a detected
split pair and emits a `pairing` error finding for any divergence —
the failure mode that silently produces different DE and EN output
for what was meant to be language-neutral material.

Phase 6 is Python-only today, mirroring Phase 5's scope: the
sibling `header_de` / `header_en` macros only ship in
`templates_python/macros.j2`. Phase 8 adds them to the
cpp/csharp/java/typescript templates.

#### Snapshot / verify

`--snapshot` and `--verify-against` implement byte-level migration
verification. Use them when you need to confirm that an applied
change produced exactly the same build output as a pre-change
baseline.

Capture a baseline:

```bash
clm build course.xml --snapshot baseline/ --ignore-cache
```

Apply the migration (slide_id rollout, language-split conversion,
mechanical normalize, etc.), then verify:

```bash
clm build course.xml --output-dir out/ --verify-against baseline/ --ignore-cache
```

`--verify-against` exits non-zero if any non-skipped file differs.

**Specs with `<output-targets>`** (e.g. `shared`/`trainer`/`speaker`):
both `--snapshot DIR` and `--output-dir DIR` write each target to
`<DIR>/<target.name>/...` — a spec with `shared`, `trainer`, and
`speaker` targets lays down `<DIR>/shared/...`, `<DIR>/trainer/...`,
`<DIR>/speaker/...`. `--verify-against DIR` then compares each
target's actual `output_root` against the matching `<DIR>/<name>/`
subtree and surfaces diffs prefixed with the target name (e.g.
`trainer/de/a.py`). The verify build picks up the layout
automatically — whether you pass `--output-dir DIR` or rely on the
spec's declared target paths, `--verify-against` walks per-target.
Specs without `<output-targets>` (minimal specs) keep the
single-tree behavior: `--snapshot DIR`, `--output-dir DIR`, and
`--verify-against DIR` all operate on `<DIR>` as one flat tree.

**HTML is skipped by default** because rendered HTML uses live kernel
execution, and any slide whose code path is non-deterministic
(`random.choice(...)`, `print(some_object)` for a class without
`__repr__`, error-and-print stream interleaving) produces different
HTML each run. The `.ipynb`, `.py`, `.png`, and other artifacts are
byte-deterministic post-CLM 1.5; verifying those is what most
migrations actually need.

Two opt-in modes raise the strictness:

- `--include-html` re-enables HTML comparison but normalizes hex
  memory addresses (`<__main__.Foo at 0x2733c2b8ad0>` →
  `<__main__.Foo at 0xADDR>`). Other content diffs still surface.
- `--strict-verify` compares every file raw, with no normalization
  and no skipping. Implies `--include-html`. Useful for
  reproducibility audits where any cross-run variance is suspect.

`--ignore-cache` is recommended on both sides: a stale cache can mask
the very diffs the verify is meant to detect. The cache requires
complete HTTP-cassette coverage for sections that make LLM calls.

#### Exit codes

Starting in CLM {version}, `clm build` exits non-zero when the build
summary reports any cell or notebook error, **by default under
`--http-replay=replay`** (the CI-strict mode). This closes a gap where
CI and pre-commit hooks could not gate on cell failures — the build
summary listed the errors, but the process still exited 0.

| Condition | Exit code |
|---|---|
| Build completed cleanly | `0` |
| `--verify-against` diff (existing behavior, unchanged) | `1` |
| Cell/notebook errors under `--http-replay=replay` (new default) | `1` |
| Cell/notebook errors under non-replay modes | `0` (opt in with `--fail-on-error`) |
| Cell/notebook errors with `--no-fail-on-error` (any mode) | `0` |
| Image filename collisions | non-zero `SystemExit` (existing, unchanged) |
| Second `SIGTERM` while shutting down | `1` (existing, unchanged) |

Precedence for the exit-on-error policy:

1. Explicit `--fail-on-error` or `--no-fail-on-error` on the CLI.
2. `CLM_FAIL_ON_ERROR={1,true,yes,0,false,no}` environment variable.
3. Replay-mode default (on for `replay`, off for `once`,
   `new-episodes`, `refresh`, `disabled`).

CI implications: because `_resolve_http_replay_mode` returns `replay`
when `CI=true`, the default policy means CI builds will now exit
non-zero on cell errors automatically. If a CI job needs the legacy
behavior, pass `--no-fail-on-error` or set `CLM_FAIL_ON_ERROR=0`.

Watch mode does **not** exit on cell errors — `clm build --watch`
keeps looping; only one-shot builds drive exit-code policy.

The check fires **before** `--verify-against` comparison, so CI logs
show the cell error as the cause rather than a downstream verification
diff. If both a cell error and a verify diff are present, the cell
error wins.

### `clm targets`

List output targets defined in a course spec file.

```
clm targets SPEC_FILE
```

### `clm outline`

Generate a Markdown outline of a course.

```
clm outline [OPTIONS] SPEC_FILE
```

| Option | Description |
|--------|-------------|
| `-o, --output FILE` | Write to file (mutually exclusive with `-d`) |
| `-d, --output-dir DIR` | Write both languages to directory |
| `-L, --language [de\|en]` | Language selection |
| `--format [markdown\|json]` | Output format (default: markdown) |
| `--include-disabled` | Include sections marked `enabled="false"` with a `(disabled)` marker (default: omitted). Topics that resolve to slide files on disk show the H1 header (each slide rendered as its own bullet); unresolvable topics fall back to the topic id |
| `--sections-only` | Emit only section headings, omitting per-topic/slide entries within each section |

Examples:

```bash
clm outline course.xml
clm outline course.xml -L de
clm outline course.xml -d ./docs
clm outline course.xml --format json
clm outline course.xml --include-disabled
clm outline course.xml --sections-only
```

### `clm topic resolve`

*Deprecated alias: `clm resolve-topic` (removed in 1.7).*

Resolve a topic ID to its filesystem path.

```
clm topic resolve [OPTIONS] TOPIC_ID
```

| Option | Description |
|--------|-------------|
| `--course-spec FILE` | Scope resolution to topics in this course spec |
| `--data-dir DIR` | Course data directory (contains slides/) |
| `--module NAME` | Restrict resolution to topics in the named module directory (e.g., `module_545_ml_azav_cohort_2026_04`). Use this when a topic ID exists in multiple modules — for example, a frozen-cohort archive that shares topic IDs with the live module. |
| `--json` | Output as JSON |

Examples:

```bash
clm topic resolve what_is_ml
clm topic resolve "decorators*"
clm topic resolve intro --course-spec course-specs/python.xml
clm topic resolve intro --module module_545_ml_azav_cohort_2026_04
```

### `clm slides search`

*Deprecated alias: `clm search-slides` (removed in 1.7).*

Fuzzy search across topic names and slide file titles.

```
clm slides search [OPTIONS] QUERY
```

| Option | Description |
|--------|-------------|
| `--course-spec FILE` | Limit search to topics in this course spec |
| `--data-dir DIR` | Course data directory (contains slides/) |
| `--language [de\|en]` | Search titles in this language only |
| `--max-results N` | Maximum results to return (default: 10) |

Examples:

```bash
clm slides search decorators
clm slides search "RAG introduction" --language en
clm slides search lists --course-spec course-specs/python.xml
```

### `clm validate` (spec mode)

`clm validate course.xml` dispatches to spec validation when the
argument is an `.xml` file.

*Deprecated alias: `clm validate-spec` (removed in 1.7).*

Validate a course specification XML file for consistency.

```
clm validate [OPTIONS] SPEC_FILE
```

Checks that all referenced topic IDs resolve to exactly one existing
topic directory, that there are no duplicate topic references, and
that referenced dir-group paths exist.

| Option | Description |
|--------|-------------|
| `--data-dir DIR` | Course data directory (contains slides/) |
| `--json` | Output as JSON |
| `--include-disabled` | Also validate sections marked `enabled="false"`; each finding from a disabled section has `(disabled)` appended to its message (default: disabled sections are skipped) |

Examples:

```bash
clm validate course-specs/python-basics.xml
clm validate course-specs/ml-azav.xml --json
clm validate course-specs/ml-azav.xml --include-disabled
```

### `clm sync-includes`

Materialize `<include>` declarations from a course spec onto the
filesystem. The build pipeline splices includes virtually, so `clm build`
never needs this command; it exists for *local* notebook execution
(VS Code, `jupyter lab`), where Python's import system requires the
included package to physically sit next to the slide file.

```
clm sync-includes [OPTIONS] SPEC_FILE
```

For each topic that declares (or inherits) one or more `<include>`
elements, the command creates the materialization under
`<topic-dir>/<as>`. A small JSON ledger at `<topic-dir>/.clm-include`
records exactly which paths the command created, so `--remove` can
delete only those paths — untracked files in the topic dir are never
touched.

| Option | Description |
|--------|-------------|
| `--data-dir DIR` | Course data directory (contains `slides/` and include sources). Default: inferred from the spec file location. |
| `--mode [copy\|symlink\|hardlink]` | How to materialize each include (default: `copy`). `copy` is the most portable. `symlink` is faster and avoids drift but requires admin or Developer Mode on Windows — falls back to `copy` per-include on `OSError`. `hardlink` is per-file and filesystem-local; falls back to per-file `copy` when the filesystem refuses (e.g., cross-device). |
| `--remove` | Delete previously-synced materializations. Only paths recorded in each topic's `.clm-include` ledger are removed; untracked files are left in place. |
| `--print-gitignore` | Print suggested `.gitignore` patterns for every declared `<include>` (and the `.clm-include` ledger) to stdout, then exit. The command never writes `.gitignore` files itself — paste the output into your course-root `.gitignore` once. Idempotent; safe to redirect with `>> .gitignore`. Cannot be combined with `--remove`. |
| `--dry-run` | Print what would happen without modifying the filesystem. |

Behavior notes:

- **Default mode is `copy`** because it works without any platform setup
  and survives moving topic directories between machines. Switch to
  `--mode=symlink` for an in-place workflow that always reflects edits
  to the canonical source.
- **Switching modes is supported.** Re-running with a different
  `--mode` deletes the previous materialization at each target and
  recreates it.
- **Untracked targets are protected.** If `<topic-dir>/<as>` already
  exists and was not created by `sync-includes` (no matching ledger
  entry), the command leaves it untouched and emits a `shadowed`
  warning — mirroring the build-time "local file wins" rule.
- **Required vs optional sources.** A missing source on an
  `optional="true"` include is silently skipped; a missing required
  source emits a warning and the command exits with status 1 after
  processing the rest of the spec.
- **Unresolved topics are skipped.** If a topic that declares includes
  fails to resolve to exactly one directory under `slides/`, its
  includes are skipped with a warning pointing at
  `clm validate` for diagnosis.

Examples:

```bash
clm sync-includes course-specs/ml-azav.xml
clm sync-includes course-specs/ml-azav.xml --mode=symlink
clm sync-includes course-specs/ml-azav.xml --remove
clm sync-includes course-specs/ml-azav.xml --print-gitignore >> .gitignore
clm sync-includes course-specs/ml-azav.xml --dry-run
clm sync-includes course-specs/ml-azav.xml --data-dir /path/to/course
```

### `clm validate` (slides mode)

`clm validate slides/` (or `clm validate slides_foo.py`) dispatches
to slide validation when the argument is a `.py` file or directory.

*Deprecated alias: `clm validate-slides` (removed in 1.7).*

Validate slide files for format, tag, and pairing correctness. Runs deterministic
checks and extracts structured review material for content-quality checks.

```
clm validate [OPTIONS] PATH
```

| Option | Description |
|--------|-------------|
| `--checks TEXT` | Comma-separated checks: `format`, `pairing`, `tags`, `code_quality`, `voiceover`, `completeness` (CLI default: all deterministic) |
| `--quick` | Fast syntax-only check (format + tags + slide_ids). Useful for PostToolUse hooks |
| `--json` | Output as JSON |
| `--data-dir DIR` | Course data directory (contains slides/) |

`PATH` can be a single slide file, a topic directory, or a course spec XML file.

Since CLM {version}, the **`voiceover` coverage check is opt-in** (issue #176).
Voiceover is now optional per deck, so the check — which reports a gap for every
slide / nontrivial code cell that lacks a voiceover cell — is **never** part of a
default, "all", or "review" bundle. It runs **only** when you name it explicitly:
`--checks voiceover` (or include it in a longer list). This applies everywhere:
the CLI default was already deterministic-only, and the MCP `validate_slides`
tool / the `validate_file`/`validate_directory`/`validate_course` library
functions now exclude `voiceover` from their `checks=None` default too. The other
review checks (`code_quality`, `completeness`) still run by default.

The `pairing` check group covers DE/EN cell count, tag consistency,
adjacency, and — since CLM {version} — **`slide_id` metadata**:

| Finding | Severity | Notes |
|---------|----------|-------|
| `slide`/`subslide` cell missing `slide_id` | `warning` | Will become an `error` in CLM 1.7. Suggested fix: `clm slides assign-ids`. |
| duplicate `slide_id` across slide groups | `error` | Group-aware: paired DE/EN cells sharing the EN-derived slug are not a duplicate. Bare-form comparison so `!intro` and `intro` collide. |
| voiceover/notes `slide_id` ≠ preceding `slide`/`subslide` anchor | `error` | Walk-back skips j2, code, shared (lang-less), and cross-language narrative cells. The j2 `header()` macro anchors `slide_id="title"` for narrative cells that follow it. |
| paired DE/EN slides carry mismatched bare `slide_id`s | `warning` | Suggested fix: `clm slides assign-ids --force`. |
| `slide_id` is not a valid kebab-case ASCII slug (≤30 chars) | `warning` | The leading `!` preserve marker is permitted and does not count toward the length cap. |

Since CLM {version}, the **bilingual** `pairing` sub-checks (DE/EN cell
count parity, per-pair tag/type consistency, and DE/EN adjacency) are
**skipped on single-language split files** (`*.de.py` / `*.en.py`) — a
split half legitimately carries cells of only one language, so these
checks would otherwise report a false `DE/EN cell count mismatch` on every
converted deck (issue #160). The per-file `slide_id` integrity checks (and
the `format` / `tags` groups) still run on split files unchanged, and the
cross-file shared-cell parity diff between a `.de.py` / `.en.py` pair is
still applied when validating a directory or course spec. Bilingual decks
(no `.de` / `.en` suffix) are unaffected — the full pairing check still runs.

Since CLM {version}, the `tags` check group also verifies **workshop
scope** (issue #78). The `partial` output kind leaves a workshop's code
cells empty for live code-alongs; if the workshop scope is missing, the
build silently renders every code cell instead. A workshop is opened by
either a `workshop` tag or a slide-start cell whose `slide_id` begins with
`workshop-` (see `clm info spec-files`).

| Finding | Severity | Notes |
|---------|----------|-------|
| markdown `# Workshop …` heading with no workshop scope covering it | `warning` | Heading match is case-sensitive, tolerant of `#`-count and whitespace (`^#+\s*Workshop\b`). Continuation headings (e.g. `## Workshop (Continued)`) inside an already-open scope are *not* flagged. Suggested fix: add a `workshop` tag or a `workshop-…` slide_id. |

Quick mode (`--quick`) runs the slide_id checks because they walk cells
linearly and don't false-positive on in-progress edits. The workshop-scope
check runs in quick mode too. The DE/EN count/tag-mismatch checks remain
excluded from quick mode.

Examples:

```bash
clm validate slides/module_010/topic_100_intro/slides_intro.py
clm validate slides/module_010/ --json
clm validate slides/module_010/topic_100_intro/ --quick
```

### `clm slides normalize`

*Deprecated alias: `clm normalize-slides` (removed in 1.7).*

Normalize slide files by applying mechanical fixes: tag migration (`alt`→`completed`),
workshop tag insertion, DE/EN interleaving, and slide ID auto-generation.

```
clm slides normalize [OPTIONS] PATH
```

| Option | Description |
|--------|-------------|
| `--operations TEXT` | Comma-separated operations: `tag_migration`, `workshop_tags`, `interleaving`, `slide_ids`, `all` (default: `all`) |
| `--dry-run` | Preview changes without modifying files |
| `--canonicalize-start-completed` | Force `start`/`completed` cohesion pairs into the canonical DE/EN interleave, even when DE/EN code differs (e.g. localized identifiers). Run before `clm slides split` so `unify(split(deck)) == deck` holds byte-for-byte. Only affects the `interleaving` operation. |
| `--json` | Output as JSON |
| `--data-dir DIR` | Course data directory (contains slides/) |

Examples:

```bash
clm slides normalize slides/module_010/topic_100_intro/slides_intro.py
clm slides normalize slides/module_010/ --dry-run
clm slides normalize slides/module_010/ --operations tag_migration
clm slides normalize slides/module_010/ --operations slide_ids --json
# Pre-conversion: canonicalize start/completed order so the split round-trips exactly
clm slides normalize slides/module_010/topic_100_intro/ --operations interleaving --canonicalize-start-completed
```

### `clm slides assign-ids`

*Added in CLM {version}.*

Generate stable `slide_id` metadata for slide/subslide cells per the
EN-derived, kebab-case, ASCII policy. Cells in a DE/EN pair share the
same id (derived from the EN heading); voiceover/notes cells inherit
the id of the preceding slide. Three-category policy:

- **headed** — slug from the first markdown heading. Always assigned.
- **extractable** — headingless but with one of:
  - a first bullet, prominent bold line, or `<img alt="…">`,
  - a first non-empty prose line (HTML tags and inline markdown
    stripped, trailing terminal punctuation dropped),
  - in a code cell: top-level `class`, `def`, assignment, `import`/
    `from-import`, or method call (AST-based; precedence in that
    order),
  - in a DE/EN pair: when the EN slug source has none of the above
    but the DE sibling does, the slug derives from the DE sibling
    (transliterated to ASCII).
  **Refused by default**; opt in with `--accept-content-derived` or
  `--llm-suggest`.
- **no content** — cell where no extractor produces anything (empty
  cell, pure `<img>` without alt, unparsable code, magic-only cells).
  **Hard refuse**; the author has to write `slide_id="…"` by hand,
  or pass `--llm-suggest` to let the LLM propose a title as a last
  resort.

Special cases:

- Title slides (j2 `header()` macro) anchor `slide_id="title"`
  automatically. No author input needed.
- An id prefixed with `!` (e.g. `slide_id="!intro"`) is the
  **preserve marker** — never regenerated, even under `--force`. The
  `!` is source-level only; references elsewhere use the bare form.

```
clm slides assign-ids [OPTIONS] PATH
```

| Option | Description |
|--------|-------------|
| `--force` | Regenerate ids where the algorithm can produce one. `!`-prefixed ids and cells without a proposal are left untouched. |
| `--accept-content-derived` | Auto-accept proposals for the extractable category (no LLM). Hard-refusal cells still refuse. |
| `--llm-suggest` | Use the local LLM (Ollama, default model `qwen3:30b`) to propose a short title. Fires on both extractable cells (replacing the content-derived title when the LLM returns one) and on hard-refusal cells (last-resort fallback before refusing). Cached per `(content_hash, prompt_version, lang)` in the LLM cache. Falls back silently to refusal when Ollama is unreachable. |
| `--report-only`, `--dry-run` | List planned assignments and refusals without modifying any file. |
| `--llm-model TEXT` | Ollama model name (default: `qwen3:30b`). |
| `--ollama-url TEXT` | Base URL of the Ollama daemon (default: `$OLLAMA_URL` or `http://localhost:11434`). |
| `--llm-timeout SECONDS` | Per-call timeout (default: 120s — cold-load on a 30B model can exceed 60s). |
| `--cache-dir PATH` | Directory for the LLM cache. Lookup order: flag → `$CLM_CACHE_DIR` → `tool.clm.cache_dir` in `pyproject.toml` → `<cwd>/.clm-cache/`. |
| `--json` | Emit a JSON report instead of human-readable lines. |

Exit codes: `0` clean, `1` soft refusals (extractable cells awaiting
author input), `2` at least one hard refusal.

Examples:

```bash
clm slides assign-ids slides/module_010/topic_100/slides_intro.py --report-only
clm slides assign-ids slides/module_010/ --accept-content-derived
clm slides assign-ids slides/module_010/topic_100/slides_intro.py --llm-suggest
clm slides assign-ids slides/module_010/ --force        # regenerate all derivable ids
```

### `clm slides sync`

*Added in CLM {version}.*

Single-language authoring sync for split-format decks
(`<deck>.de.py` / `<deck>.en.py`, the layout produced by
`clm slides split`). After an author edits **one** half of a pair, this
command brings the *other* half into sync in a single pass: edits are
propagated, brand-new slides are translated and inserted, removed
slides are dropped, reorders are mirrored, and a shared `slide_id` is
minted onto both decks as it goes.

**Default behavior changed in CLM {version}: the command now writes to
the working tree.** A bare `clm slides sync de en` applies the agreed
changes (it no longer just prints a diff). Nothing is committed —
review the result with `git diff`, the design's primary review surface.
Use `--dry-run` to preview without writing. See the migration guide
(`clm info migration`) for the full before/after.

**Per-cell direction (no `--source-lang`).** Direction is decided per
cell by diffing each deck against a structural **watermark** — the
last-synced deck state, recorded only on a successful apply, so it is
immune to the author's git-commit cadence. Different cells can flow in
different directions in the same pass. A cell with **no** `slide_id` is,
by construction, *added since the last sync* (a commit never runs
assign-ids), so new slides are detected even after the editing deck is
committed. When no watermark exists yet, the baseline falls back to each
deck's git `HEAD`, then to the id-less-as-new heuristic alone; the
no-silent-no-op summary states which baseline was used.

**Conflicts are isolated, never guessed.** A cell edited on *both* decks
since the last sync (or removed on one and edited on the other) is
surfaced as a `conflict`: both decks are left untouched and it is listed
in the summary. Resolve it with `--interactive` (`[d]e-wins` /
`[e]n-wins`) or by editing one side and re-running.

**Two LLMs.** Edits are reconciled by a judge whose backend you pick
with `--provider`: **`openrouter`** (the default — Claude Sonnet via
OpenRouter, fast) or **`local`** (the Ollama daemon — offline but
slower). Set a persistent default with `$CLM_SYNC_PROVIDER`. The judge
model is `--llm-model` (default `anthropic/claude-sonnet-4-6` for
openrouter, `qwen3:30b` for local). The OpenRouter backend needs
`$OPENROUTER_API_KEY` (or `$OPENAI_API_KEY`). Brand-new slides are
always translated by an OpenRouter model (`--translation-model`, default
`anthropic/claude-sonnet-4-6`), which needs the same key; without a key,
add proposals defer. When the judge backend is unavailable (Ollama
unreachable, or no OpenRouter key), edit proposals are recorded as
errors (exit 2) rather than guessed.

**`.env` is loaded automatically.** Before resolving the judge and
translator, sync walks up from each deck's directory and loads the first
`.env` it finds (without overriding already-exported variables), so
`$OPENROUTER_API_KEY` / `$OPENAI_API_KEY` kept in the project `.env` (the
usual course-repo layout) are picked up. Pass `--no-env-file` to skip
this; `--dry-run` never loads `.env` (it uses no LLM).

Cells synced: **all** sync-relevant cells, not only narrative markdown:

- markdown `slide` / `subslide` cells and narrative `voiceover` / `notes`
  cells (reconciled by the judge);
- **auxiliary markdown** carrying a `slide_id` but no narrative tag (an
  `alt` solution note, an untagged explanatory cell) — twinned/translated
  like narrative;
- **code cells**: a **language-neutral** code cell (no `lang=`) is copied
  **verbatim** across both halves; a **localized** code cell (`lang=` —
  e.g. one whose string literals are shown to the learner) is **twinned and
  translated**, keeping the code itself byte-identical. New slides bring
  their code along, and code an author moves between slide groups follows.

A localized code cell with a `slide_id` is reconciled per cell (its body
re-translated on an edit); language-neutral and id-less code is propagated
structurally, so it is **not** minted a `slide_id`.

**Content-anchor sync (Issue #190).** Cell identity is tracked in the watermark
by a **content anchor** (`hand slide_id > construct slug > content hash`), never
written into the file, so a deck stays id-light yet syncs precisely:

- **Code-only edits propagate.** Editing *only* a language-neutral code cell on
  one side — no narrative or id change — is now detected (the anchor diff sees
  which half drifted) and copied verbatim to the twin. Previously such an edit was
  silently dropped.
- **Unchanged localized code is never re-translated.** When a slide group is
  rebuilt for a sibling's sake, an unchanged id-less localized code cell is spliced
  verbatim by its anchor instead of being re-translated — no churn, no LLM spend.
- **A drifted `slide_id` is migrated back.** If you split an id'd code cell (e.g.
  add an `import` above a `def`, leaving the id on the import half), the id is
  moved back onto the cell whose construct it names and a fresh slug is minted on
  the orphan — one targeted header write each, no LLM, symmetric across both decks
  (`de_id == en_id` is preserved).
- **A neutral cell edited *differently* on both decks auto-heals with a warning**
  (the winning side is the one with a keyed direction, else the newer file). Set
  `CLM_SYNC__SHARED_DIVERGENCE=error` to surface it as an error and write nothing
  instead. A neutral cell edited *incompatibly* on both decks (different cells) is
  an error — never silently reverted.
- **Genuinely ambiguous id realignment** (a function renamed *while* a cell was
  split, an unresolvable tie) is left untouched and re-surfaces next run, unless
  you opt in with `--llm-recover` (above).

Use `--explain` to see the anchor-level view (per-cell anchor + drift, the
propagation direction, drifted ids) for any pair.

```
clm slides sync [OPTIONS] DE_PATH EN_PATH
```

| Option | Description |
|--------|-------------|
| `--dry-run` | Classify only: print the plan and write nothing. (The default, without this flag, writes the agreed changes to the working tree.) |
| `--explain` | Diagnostic: print the **content-anchor diff** — each cell's anchor (`id:` / `construct:` / `hash:`) and whether it is unchanged / edited / new / removed vs the watermark, the neutral-cell propagation direction, and any drifted `slide_id`s (id-migration candidates) — then the plan, and write nothing. A read-only superset of `--dry-run` for understanding *why* a cell did or did not sync. Mutually exclusive with `--interactive` and `--json`. |
| `--interactive` | Walk each proposal and choose `[a]pply / [s]kip / [q]uit` (`[d]e-wins / [e]n-wins` for a conflict) before a single atomic apply. Mutually exclusive with `--dry-run` and `--json`. |
| `--provider [openrouter\|local]` | Backend for the edit-reconciliation judge: `openrouter` (Claude Sonnet via OpenRouter, the default — needs `$OPENROUTER_API_KEY` / `$OPENAI_API_KEY`) or `local` (the Ollama daemon — offline, slower). Overridable with `$CLM_SYNC_PROVIDER`. |
| `--llm-model TEXT` | Model for the edit-reconciliation judge. Default depends on `--provider`: `anthropic/claude-sonnet-4-6` (openrouter) or `qwen3:30b` (local). |
| `--ollama-url TEXT` | Base URL of the Ollama daemon (only used with `--provider local`; default: `$OLLAMA_URL` or `http://localhost:11434`). |
| `--llm-timeout SECONDS` | Per-call timeout for the edit judge. Provider-aware default: 120s for `openrouter` (fast hosted model), 300s for `local` (a large local reasoning model can spend minutes "thinking"). |
| `--translation-model TEXT` | OpenRouter model used to translate brand-new slides for the add path (default: `anthropic/claude-sonnet-4-6`). Needs `$OPENROUTER_API_KEY` / `$OPENAI_API_KEY`; adds defer when absent. |
| `--llm-recover` | **Opt into the bounded-LLM recovery tier (default off).** When the deterministic id-migration is stuck on an *ambiguous* drifted `slide_id` (a function renamed while a cell was split, an unresolvable tie), ask Claude (Opus, via OpenRouter) for a **validated, body-free** id↔cell alignment. Without this flag such a region is left untouched and re-surfaces next run. The model only ever sees content anchors (construct + hash + id), never cell source, and its map is validated (it can never drop a stable `slide_id`) before any header is written. Needs `$OPENROUTER_API_KEY` / `$OPENAI_API_KEY`. |
| `--recovery-model TEXT` | OpenRouter model for `--llm-recover` alignment (default: `anthropic/claude-opus-4`). |
| `--cache-dir PATH` | Directory holding the structural watermark. Lookup order: flag → `$CLM_CACHE_DIR` → `tool.clm.cache_dir` in `pyproject.toml` → `<cwd>/.clm-cache/`. |
| `--no-cache` | Do not read or write the watermark. Every run then re-derives its baseline from git `HEAD` and no synced state is persisted. |
| `--no-env-file` | Do not auto-load a `.env` file. By default sync loads the first `.env` found above each deck (without overriding already-set variables), so keys kept in the project `.env` reach the judge/translator. |
| `--json` | Emit a JSON report instead of human-readable lines. |

Exit codes: `0` clean (every change applied, or nothing to do, with no
errors), `1` something is left for review (a skipped proposal or an
unresolved conflict), `2` a structural error (classifier error, missing
target cell, or the edit LLM is unavailable).

A run also surfaces structural issues the classifier will not turn into
a proposal — for example a duplicate `slide_id` whose original cannot be
identified (`error`), or cell order that drifted on both decks
(`warning`, order not propagated). Any issue holds the whole watermark
so the signal is never silently baselined.

The JSON report carries `mode` (`dry-run` / `apply` / `interactive`),
`exit_code`, a `plan` block (`baseline_source`, per-kind `counts`,
`in_sync`, the `proposals`, and `issues`), an `apply` block (per-kind
`applied` counts, `in_sync`, `deferred`, `watermark_recorded`, and
`errors`) — `null` under `--dry-run` — and a `walker` block of
accept/skip/defer counters under `--interactive`. These counters are the
pilot accept-rate instrumentation.

Examples:

```bash
# Edit intro.de.py, then bring intro.en.py into sync (writes to the tree).
clm slides sync slides/topic/intro.de.py slides/topic/intro.en.py

# Preview the plan first — write nothing.
clm slides sync intro.de.py intro.en.py --dry-run

# Understand the anchor-level view (per-cell drift, direction, drifted ids).
clm slides sync intro.de.py intro.en.py --explain

# Let an LLM realign a genuinely ambiguous split (opt-in, off by default).
clm slides sync intro.de.py intro.en.py --llm-recover

# Walk each proposal, resolving conflicts as you go.
clm slides sync intro.de.py intro.en.py --interactive

# Use the offline local Ollama judge instead of OpenRouter.
clm slides sync intro.de.py intro.en.py --provider local

# Machine-readable plan for tooling.
clm slides sync intro.de.py intro.en.py --dry-run --json

# Stateless run: ignore/leave the watermark, baseline off git HEAD.
clm slides sync intro.de.py intro.en.py --no-cache
```

### `clm slides coverage`

*Added in CLM {version}.*

Check whether each slide's bullets are covered by the voiceover that
follows it. A local LLM (Ollama) is asked to judge per-language;
verdicts are cached so re-runs over an unchanged deck cost nothing.
Findings are emitted at `warning` severity (slated for promotion to
`error` in a future release once the false-positive rate against
real decks is known — same option-B rollout pattern Phase 3 uses for
the missing-slide_id rule).

Per-language: a paired DE/EN slide produces two independent checks
(DE slide vs. DE voiceover, EN slide vs. EN voiceover) cached as
separate rows. Bullets with no voiceover at all are reported as
warnings without consulting the LLM. Non-bulleted slides (heading-
only, image-only, code-only) are skipped silently — there is
nothing to cover. Workshop slides (cells inside a `workshop` /
`end-workshop` scope) are also skipped — workshop exercise slides
intentionally have no voiceover, and flagging them drowns the
report in known-OK findings. The run summary reports the count of
excluded workshop slides so the skip is visible.

```
clm slides coverage [OPTIONS] PATH
```

| Option | Description |
|--------|-------------|
| `--llm-model TEXT` | Ollama model name (default: `qwen3:30b`). |
| `--ollama-url TEXT` | Base URL of the Ollama daemon (default: `$OLLAMA_URL` or `http://localhost:11434`). |
| `--llm-timeout SECONDS` | Per-call timeout (default: 120s — cold-load on a 30B local model can exceed 60s). |
| `--cache-dir PATH` | Directory for the LLM cache. Lookup order: flag → `$CLM_CACHE_DIR` → `tool.clm.cache_dir` in `pyproject.toml` → `<cwd>/.clm-cache/`. |
| `--report-only` | Skip cache writes; reads still happen. Useful for measuring the current cache hit rate without persisting fresh verdicts. |
| `--dump` | Print a readable text dump of cached verdicts instead of running a coverage check. PATH is ignored. Combine with `--json` for machine output. |
| `--json` | Emit a JSON report. |

Exit codes: `0` no findings, `1` at least one warning or error.

When Ollama is not reachable the command still works in cache-only
mode: cached verdicts surface, fresh pairs are reported as skipped,
no LLM calls are made. This makes coverage safe to invoke from
PostToolUse hooks even on machines where the local daemon is offline.

Examples:

```bash
clm slides coverage slides/module_010/topic_100/slides_intro.py
clm slides coverage slides/module_010/                      # sweep a whole module
clm slides coverage slides/module_010/ --report-only        # don't update the cache
clm slides coverage --dump                                  # inspect cached verdicts
clm slides coverage --dump --json | jq .                    # machine-readable dump
```

The first run on a fresh deck calls the LLM once per (slide, lang)
pair; subsequent runs over the unchanged deck use the cache and make
zero LLM calls. Editing one bullet's wording invalidates only that
pair's cache entry — the rest of the deck stays cached.

### `clm slides split`

Split a bilingual `.py` slide file into `<basename>.de.py` and
`<basename>.en.py` companions. Cells with `lang="de"` go to the DE
file, `lang="en"` to the EN file, and shared cells (no `lang` — j2
directives, language-neutral code) are copied verbatim to both. The
bilingual `# {{ header("DE", "EN") }}` macro call is rewritten into
sibling-macro form `# {{ header_de("DE") }}` (DE file) /
`# {{ header_en("EN") }}` (EN file), and the matching `# j2 from
'macros.j2' import header` directive is rewritten in parallel so each
file imports only the macro it uses.

The companion sibling macros `header_de(title_de)` and
`header_en(title_en)` are defined in
`src/clm/workers/notebook/templates_python/macros.j2` alongside the
existing two-arg `header(title_de, title_en)`. The macros render
byte-identical DE/EN cell text to the bilingual macro on a per-language
basis, so a split-format build produces the same per-language notebooks
as a bilingual build of the same content.

```
clm slides split [OPTIONS] SOURCE
```

| Option | Description |
|--------|-------------|
| `--force` | Overwrite existing `.de.py` / `.en.py` companions if present |
| `--report-only`, `--dry-run` | Compute the split and report what would be written without modifying files |
| `--json` | Emit a JSON report |

Examples:

```bash
clm slides split slides_intro.py
clm slides split slides_intro.py --report-only
clm slides split slides_intro.py --force      # overwrite stale companions
```

Round-trip with `clm slides unify` is byte-identical:
`unify(*split(deck.py)) == deck.py`. Hard prerequisite: every slide
carries a valid `slide_id` (Phase 3 enforces this with a warning,
escalating to error in CLM 1.7) — `unify` pairs adjacent DE/EN cells
by matching id. Currently Python-only: the slide parser recognises
only `# %%` cell boundaries; non-Python prog_langs are deferred.

### `clm slides unify`

The inverse of `clm slides split`. Combine `<basename>.de.py` and
`<basename>.en.py` into the bilingual `<basename>.py` companion. Pairs
adjacent DE/EN cells by matching `slide_id`, treats shared cells as
alignment points (must be byte-identical between the two inputs —
divergent shared content is an error), and rebuilds the bilingual
`# {{ header("DE", "EN") }}` macro from the split forms.

```
clm slides unify [OPTIONS] DE_SOURCE EN_SOURCE
```

| Option | Description |
|--------|-------------|
| `--target FILE` | Explicit bilingual target path. Defaults to the basename shared by the two sources (e.g. `foo.de.py` + `foo.en.py` → `foo.py`). |
| `--force` | Overwrite an existing target file if present |
| `--report-only`, `--dry-run` | Compute the unified text and report what would be written without modifying files |
| `--json` | Emit a JSON report |

Examples:

```bash
clm slides unify slides_intro.de.py slides_intro.en.py
clm slides unify slides_intro.de.py slides_intro.en.py --target out.py
clm slides unify slides_intro.de.py slides_intro.en.py --report-only
```

Divergent shared cells fail with `error: shared cell content
diverges …`. The same divergence is surfaced by
`clm validate <topic_dir>` and refused at build time — see
"Split-source build routing" under `clm build` above for the
build-time gate.

### `clm slides language-view`

*Deprecated alias: `clm language-view` (removed in 1.7).*

Extract a single-language view of a bilingual slide file. Each cell is
preceded by an `[original line N]` annotation so edits can be mapped back.

```
clm slides language-view FILE {de|en} [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--include-voiceover` | Include voiceover cells |
| `--include-notes` | Include speaker-notes cells |

Examples:

```bash
clm slides language-view slides_intro.py de
clm slides language-view slides_intro.py en --include-voiceover
clm slides language-view slides_intro.py en --include-notes
```

### `clm slides suggest-sync`

*Deprecated alias: `clm suggest-sync` (removed in 1.7).*

Compare a slide file against git HEAD and detect asymmetric bilingual edits.
Suggests which cells need translation updates. Does not modify the file.

```
clm slides suggest-sync [OPTIONS] FILE
```

| Option | Description |
|--------|-------------|
| `--source-language [de\|en]` | The language that was edited (auto-detected if omitted) |
| `--json` | Output as JSON |

Examples:

```bash
clm slides suggest-sync slides_intro.py
clm slides suggest-sync slides_intro.py --source-language de --json
```

### `clm voiceover extract`

*Deprecated alias: `clm extract-voiceover` (removed in 1.7).*

Extract voiceover and notes cells from a slide file to a companion
`voiceover_*.py` file, linked via `slide_id`/`for_slide` metadata.
Content cells without `slide_id` get auto-generated IDs before extraction.

Since CLM {version}, each extracted cell also records a `vo_anchor`
attribute identifying its **immediate predecessor cell** — `id:<slide_id>`
when that cell carries an id, otherwise `fp:<body-fingerprint>` — with a
trailing `#<n>` occurrence ordinal to disambiguate repeated cells in the
same slide group. `vo_anchor` lets `clm voiceover inline` restore each
voiceover to its **exact** original position rather than to the end of its
slide group. It is body-only and occurrence-qualified, so editing a
sibling cell's tags, inserting unrelated slides, or the build's blank-line
cleanup between extract and inline does not move the voiceover.

```
clm voiceover extract [OPTIONS] FILE
```

| Option | Description |
|--------|-------------|
| `--dry-run` | Preview changes without modifying files |
| `--json` | Output as JSON |

Examples:

```bash
clm voiceover extract slides_intro.py
clm voiceover extract slides_intro.py --dry-run
```

### `clm voiceover inline`

*Deprecated alias: `clm inline-voiceover` (removed in 1.7).*

Inline voiceover cells from a companion `voiceover_*.py` file back into the
slide file, deletes the companion file after successful inlining.

Since CLM {version}, each voiceover is re-inserted immediately after the
predecessor cell recorded in its `vo_anchor` (resolved within the owning
slide group only — it never crosses into another slide). If that anchor
cell was edited away or removed, inline falls back to the end of the
`for_slide` group and counts the cell as **relocated**; if the owning slide
is gone entirely, the cell is **unmatched** and appended at the end. Both
cases are reported rather than silently misplaced:

- The text summary appends `N cell(s) relocated …` / `N cell(s) could not
  be matched …`.
- `--dry-run` prints a per-cell placement line — `+` anchored, `!`
  relocated, `?` unmatched — with the target line, so you can confirm
  placement before writing.
- `--json` adds `relocated_cells` and a `placements` array (each entry:
  `for_slide`, `anchor`, `status`, `after_line`, `after_header`).

```
clm voiceover inline [OPTIONS] FILE
```

| Option | Description |
|--------|-------------|
| `--dry-run` | Preview changes (incl. per-cell placement report) without modifying files |
| `--json` | Output as JSON (incl. `relocated_cells` and `placements`) |

Examples:

```bash
clm voiceover inline slides_intro.py
clm voiceover inline slides_intro.py --dry-run
```

### `clm authoring rules`

*Deprecated alias: `clm authoring-rules` (removed in 1.7).*

Look up merged authoring rules (common + course-specific) for a course.
Reads per-course `.authoring.md` files from the `course-specs/` directory.

```
clm authoring rules [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--course-spec TEXT` | Course spec path or slug (e.g. `machine-learning-azav`) |
| `--slide-path PATH` | Path to a slide file; resolves to the course(s) containing it |
| `--data-dir DIR` | Course data directory (contains course-specs/, slides/) |
| `--json` | Output as JSON |

At least one of `--course-spec` or `--slide-path` must be provided.

Examples:

```bash
clm authoring rules --course-spec python-basics
clm authoring rules --slide-path slides/module_010/topic_100_intro/slides_intro.py
clm authoring rules --course-spec python-basics --json
```

### `clm mcp`

Start the MCP server for AI-assisted slide authoring.

```
clm mcp [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--data-dir DIR` | Course data directory (default: `CLM_DATA_DIR` or cwd) |
| `--log-level TEXT` | Log level for stderr output |

The MCP server exposes 11 tools over stdio transport:

| Tool | Description |
|------|-------------|
| `resolve_topic` | Resolve topic ID or glob pattern to filesystem path |
| `search_slides` | Fuzzy search across topic names and slide titles |
| `course_outline` | Generate structured JSON course outline |
| `validate_spec` | Validate course specification XML |
| `validate_slides` | Validate slide files (format, tags, pairing) |
| `normalize_slides` | Apply mechanical fixes (tag migration, interleaving, slide IDs) |
| `get_language_view` | Extract single-language view with line annotations |
| `suggest_sync` | Detect asymmetric bilingual edits vs git HEAD |
| `extract_voiceover` | Move voiceover cells to companion file |
| `inline_voiceover` | Merge voiceover cells back from companion file |
| `course_authoring_rules` | Look up merged authoring rules for a course |

All tools accept paths relative to the data directory or as absolute paths.
Most return JSON; `get_language_view` returns annotated plain text.

### `clm status`

Show CLM system status (workers, databases, configuration).

```
clm status
```

### `clm info`

Show version-accurate CLM documentation for agents and users.

```
clm info [TOPIC]
```

Without a topic argument, lists available topics. With a topic, displays
the full documentation for that topic.

Examples:

```bash
clm info                # List available topics
clm info spec-files     # Spec file format reference
clm info commands       # CLI command reference
clm info migration      # Breaking changes and migration guide
```

### `clm completion`

Emit a shell completion (tab-completion) activation script.

```
clm completion SHELL [--install-hint]
```

`SHELL` is one of `bash`, `zsh`, `fish`, or `powershell`. Bash/Zsh/Fish use
Click's native completion generator; **PowerShell** support is provided by
CLM (Click has no native PowerShell completion) via a
`Register-ArgumentCompleter` script that reuses Click's completion protocol,
so PowerShell gets the same context-aware command, option, and value
completions as the POSIX shells.

Pass `--install-hint` to print instructions for making completion permanent
in that shell's profile, instead of the script itself.

Examples:

```bash
# Bash / Zsh — enable for the current session
eval "$(clm completion bash)"

# Fish — install into the completions directory
clm completion fish > ~/.config/fish/completions/clm.fish
```

```powershell
# PowerShell — enable for the current session
clm completion powershell | Out-String | Invoke-Expression

# PowerShell — make it permanent (appends to your $PROFILE)
clm completion powershell >> $PROFILE

# Show install instructions for any shell
clm completion powershell --install-hint
```

### `clm cassette`

Inspect and repair HTTP-replay cassettes.

| Subcommand | Description |
|------------|-------------|
| `cassette doctor` | Detect (and optionally repair) orphan chain-pointing interactions |

#### `clm cassette doctor [SPEC-FILE]`

Detects *chain-orphan* interactions in canonical `*.http-cassette.yaml`
files — a chat-completion response whose text is substantial enough to be a
"chain edge" yet appears in no other interaction's request body. These are
almost always a chain-opener whose chain-closer was never recorded: the
canonical-poisoning failure mode that the completion-marker fix (issue #115)
prevents going *forward* but cannot retroactively repair, plus the
`try/except`-swallowed-closer case the marker logic structurally cannot catch.

Walks every `*.http-cassette.yaml` under the spec's source tree (the course
root, resolved as `clm build` does). When `SPEC-FILE` is omitted, the current
working directory is walked instead — convenient for repairing a single topic
directory in place.

Detection is intentionally simple (substring match — no fuzzy or LLM-based
matching): for each interaction the chat-completion content is extracted
(`choices[].message.content` for non-streaming JSON; accumulated
`delta.content` for streaming SSE), and any content at least `--min-text-len`
characters long is treated as a chain-edge candidate. If no other
interaction's request body embeds that text as a substring, the interaction is
flagged.

| Option | Description |
|--------|-------------|
| `--fix` | Rewrite cassettes to drop chain-orphan interactions (via the atomic-write path), so the next build re-records them. Default off — diagnostic only. |
| `--min-text-len N` | Minimum extracted-content length (chars) to treat as a chain-edge candidate. Default: `50`. |
| `--json` | Emit a machine-readable JSON report on stdout instead of the text report. |

`--fix` only guarantees the orphan entry is gone; it does **not** guarantee
the next recording produces a correct chain — the author still has to
re-record (e.g. with `--http-replay=refresh` or `new-episodes`). Cassettes
that fail to load are reported as skipped and never rewritten.

```bash
clm cassette doctor course-specs/python-course.xml          # report only
clm cassette doctor course-specs/python-course.xml --fix    # repair
clm cassette doctor course-specs/python-course.xml --json   # CI gate
clm cassette doctor                                          # walk cwd
```

### `clm config`

Manage CLM configuration files.

| Subcommand | Description |
|------------|-------------|
| `config init` | Create an example configuration file |
| `config locate` | Show configuration file locations |
| `config show` | Show current configuration values |

### `clm db`

Database management commands.

| Subcommand | Description |
|------------|-------------|
| `db stats` | Show database statistics |
| `db prune` | Prune old jobs, events, and cache entries |
| `db vacuum` | Compact databases |
| `db clean` | Combined prune + vacuum (with confirmation) |

`db prune` options:

| Option | Description |
|--------|-------------|
| `--completed-days N` | Days to keep completed jobs (default: keep all) |
| `--failed-days N` | Days to keep failed jobs (default: keep all) |
| `--events-days N` | Days to keep worker events (default: 30) |
| `--cache-versions N` | Cache versions to keep per file (default: 1) |
| `--dry-run` | Show what would be deleted |
| `--remove-missing` | Remove entries for source files no longer on disk |

`db clean` options (same retention flags as `db prune`, then vacuums):

| Option | Description |
|--------|-------------|
| `--completed-days N` | Days to keep completed jobs (default: keep all) |
| `--failed-days N` | Days to keep failed jobs (default: keep all) |
| `--events-days N` | Days to keep worker events (default: 30) |
| `--cache-versions N` | Cache versions to keep per file (default: 1) |
| `--force` | Skip confirmation prompt |
| `--remove-missing` | Remove entries for source files no longer on disk |

### `clm delete-database`

Delete CLM databases (job queue and/or cache).

### `clm docker`

Build and push CLM Docker images.

| Subcommand | Description |
|------------|-------------|
| `docker build` | Build Docker images for CLM workers |
| `docker build-quick` | Quick rebuild using local cache |
| `docker cache-info` | Show build cache information |
| `docker list` | List available services and images |
| `docker pull` | Pull images from Docker Hub |
| `docker push` | Push images to Docker Hub |

### `clm git`

Manage git repositories for course output directories.

| Subcommand | Description |
|------------|-------------|
| `git init SPEC_FILE` | Initialize git repos in output directories (idempotent — re-run to add remotes) |
| `git status SPEC_FILE` | Show status of all output repos |
| `git commit SPEC_FILE` | Stage and commit changes |
| `git push SPEC_FILE` | Push commits to remote |
| `git sync SPEC_FILE -m MSG` | Commit and push in one operation |
| `git reset SPEC_FILE` | Reset to remote tracking branch |

Key options for `git commit`, `git push`, and `git sync`:

| Option | Commands | Description |
|--------|----------|-------------|
| `-m, --message` | commit, sync | Commit message (required unless `--amend`) |
| `--amend` | commit, sync | Amend previous commit instead of creating new one |
| `--force-with-lease` | push, sync | Safe force push (implied by `--amend` on sync) |
| `--target` | all | Filter to specific output target |
| `--dry-run` | all | Show what would be done |

Examples:

```bash
clm git commit course.xml -m "Update slides"
clm git commit course.xml --amend              # amend, keep message
clm git commit course.xml --amend -m "new msg" # amend with new message
clm git push course.xml --force-with-lease     # safe force push
clm git sync course.xml -m "Weekly update"     # commit + push
clm git sync course.xml --amend                # amend + force push
clm git sync course.xml --force-with-lease -m "msg"  # commit + force push
```

`git init` is idempotent — re-running it after creating remote repositories will
detect and add them as origin. The behavior matrix:

| | No local repo | Local repo exists |
|---|---|---|
| **No remote** | Create local-only repo | Skip (print remote URL if configured) |
| **Remote exists** | Clone/restore from remote | Add remote origin if missing |

### `clm jobs`

Manage CLM jobs.

### `clm workers`

Manage CLM workers.

| Subcommand | Description |
|------------|-------------|
| `workers list` | List registered workers |
| `workers cleanup` | Delete stale worker DB rows (does not kill processes) |
| `workers reap` | Kill surviving worker processes + trees *and* clean DB rows |

`workers reap` is the self-service recovery command for crashed or
task-killed builds that left `python -m clm.workers.*` processes
running. It:

1. Marks in-flight job rows as failed (same as a clean pool shutdown would).
2. Scans for surviving worker processes via `psutil`.
3. Matches each one against `--jobs-db-path` (via the worker's `DB_PATH`
   env var) and kills its whole process tree.
4. Cleans up stale worker rows (same as `workers cleanup`).

| Option | Description |
|--------|-------------|
| `--jobs-db-path PATH` | Path to the job queue DB (default: `clm_jobs.db`) |
| `--dry-run` | Show what would be reaped without killing anything |
| `--force` | Skip the confirmation prompt |
| `--all` | Also reap processes whose env is unreadable or `DB_PATH` does not match (dangerous across worktrees) |

Unmatched processes are listed but not killed by default, so running
`reap` from one worktree cannot accidentally kill workers from another.
Use `--all` only when you are sure every surviving worker belongs to
you.

```bash
clm workers reap --dry-run         # preview
clm workers reap --force           # reap this worktree's orphans
clm workers reap --all --force     # emergency cross-worktree cleanup
```

### `clm summarize`

Generate LLM-powered markdown summaries of course content. Requires `clm[summarize]` extra.

```
clm summarize [OPTIONS] SPEC_FILE
```

| Option | Description |
|--------|-------------|
| `--audience [client\|trainer]` | Target audience (required) |
| `--granularity [notebook\|section]` | Summary level (default: `notebook`) |
| `--style [prose\|bullets]` | Output formatting (default: `prose`) |
| `-L, --language [de\|en]` | Language for outline structure (default: `en`) |
| `-o, --output FILE` | Write output to file |
| `-d, --output-dir DIR` | Write to directory with auto-generated filename |
| `--model TEXT` | LLM model identifier |
| `--api-base TEXT` | Custom API base URL |
| `--no-cache` | Skip cache, re-generate all summaries |
| `--dry-run` | Show what would be summarized (no LLM calls) |
| `--no-progress` | Disable progress bar |

Examples:

```bash
clm summarize course.xml --audience client --dry-run
clm summarize course.xml --audience trainer -o summary.md
clm summarize course.xml --audience client -d ./docs
clm summarize course.xml --audience trainer --model openai/gpt-4o
clm summarize course.xml --audience client --style bullets
```

### `clm voiceover`

Synchronize video recordings with slide files to generate speaker notes.
Requires `clm[voiceover]` extra.

**Group-level cache flags** (accepted by every `voiceover` subcommand):

| Option | Description |
|--------|-------------|
| `--cache-root PATH` | Override the cache location (default: `./.clm/voiceover-cache`) |
| `--no-cache` | Disable the artifact cache for this invocation |
| `--refresh-cache` | Force recomputation and overwrite existing cache entries |

The cache stores intermediate pipeline artifacts (transcripts, transitions,
timelines, alignments) keyed by video and slide-file fingerprints, so
repeat invocations skip the expensive ASR/detection steps when inputs are
unchanged. Manage the cache with `clm voiceover cache list/prune/clear`.

#### `clm voiceover sync`

Full pipeline: transcribe one or more video parts, detect transitions, match
slides, and merge voiceover cells in the .py file. By default, existing
voiceover content is preserved and transcript additions are merged in using
a single-pass LLM call that also filters recording noise (greetings,
self-corrections, code-typing dictation). Use `--overwrite` to replace
existing voiceover cells instead of merging.

Multiple video parts are processed independently and merged into a single
timeline using running offsets — no on-disk concatenation.

```
clm voiceover sync SLIDES VIDEO... --lang {de|en} [OPTIONS]
```

**Note:** The argument order is `SLIDES` first, then one or more `VIDEO` files.
Part ordering is authoritative — pass parts in the order they should be stitched.

**Glob expansion:** A positional `VIDEO` argument containing `*`, `?`, or `[`
is expanded relative to the current working directory, with matches sorted
using natural-numeric comparison (`Teil 2.mp4` before `Teil 10.mp4`). This
makes quoted globs work identically on POSIX and Windows shells. A glob with
no matches is an error. Literal and glob arguments can be mixed; the ordering
between arguments is preserved.

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Video language (`de` or `en`) (required) |
| `--polish-level [verbatim\|light\|standard\|heavy\|rewrite]` | How aggressively to clean up the transcript (default: `standard`). `verbatim` keeps transcript as-is without any LLM call. |
| `--mode [verbatim\|polished]` | **Deprecated** — use `--polish-level` instead. `polished` maps to `standard`; `verbatim` is unchanged. Emits a `DeprecationWarning`. |
| `--overwrite` | Overwrite existing voiceover cells instead of merging (old behavior) |
| `--whisper-model TEXT` | Whisper model size (default: `large-v3`) |
| `--backend [faster-whisper\|cohere\|granite]` | Transcription backend (default: `faster-whisper`) |
| `--device [auto\|cpu\|cuda]` | Device for transcription (default: `auto`) |
| `--tag TEXT` | Cell tag for inserted cells: `voiceover` (default) or `notes` |
| `--slides-range TEXT` | Slide range to update (e.g. `5-20`) |
| `--dry-run` | Show unified diff without writing changes |
| `-o, --output PATH` | Output file |
| `--keep-audio` | Keep extracted audio files |
| `--model TEXT` | LLM model for merge/polished mode (default: `anthropic/claude-sonnet-4-6` via OpenRouter) |
| `--transcript PATH` | Skip ASR; load precomputed transcript JSON (single-part only) |
| `--alignment PATH` | Skip ASR, detection, matching; load precomputed alignment JSON |
| `--companion/--no-companion` | Force companion-file merge on/off (default: auto-detect based on whether `voiceover_*.py` exists next to SLIDES) |
| `--propagate-to [de\|en]` | After merging `--lang`, translate the changes into the given target language and update its voiceover cells |

**Companion-file merge (auto-detected):**
- If a `voiceover_*.py` companion file (as produced by `clm voiceover extract`)
  exists next to `SLIDES`, sync reads baseline voiceover from the companion
  (keyed by `for_slide` → `slide_id`) and writes merged output back to the
  companion. The slide file itself is left untouched.
- Companion mode requires a stable `slide_id` on every slide being merged.
  If any slide is missing one, sync errors out with the exact fix command
  (run `clm voiceover extract` to auto-generate ids, or pass `--no-companion`
  to merge inline).
- `--no-companion` forces inline merge even if a companion exists; `--companion`
  forces companion mode (companion file is created on first write if missing).

**Merge behavior (default):**
- Existing voiceover cells are read as baseline; transcript additions are
  integrated while preserving all baseline content.
- Recording noise (greetings, self-corrections, code-typing dictation,
  operator asides) is filtered from the transcript.
- If the transcript contradicts a baseline bullet, the bullet is rewritten
  and logged in a structured rewrite report.
- `--dry-run` emits a unified diff showing exactly what would change.
- A JSONL trace log is written to `.clm/voiceover-traces/` on every run.
- `--mode verbatim` without `--overwrite` is an error (verbatim has no
  noise filter, so merging raw transcript would be unsafe).

**Overwrite behavior (`--overwrite`):**
- Old behavior: voiceover cells are replaced entirely with transcript content.
- `--mode verbatim --overwrite` writes raw transcript without LLM cleanup.

**Cross-language propagation (`--propagate-to`):**
- After the source-language merge completes, a second LLM pass translates
  the merge deltas into the target language and updates the target-language
  voiceover cells. The target language is authoritative for its own
  content — untouched target bullets are preserved.
- Only slides where the source merge produced a real change trigger a
  propagation call. No-op merges (empty transcript, merged == baseline)
  skip propagation entirely.
- Monolingual slides (no target-language variant) are skipped with an
  info log; propagation never synthesizes a new target-language slide.
- Works in both inline and companion modes, reading/writing the same
  `--tag` in the target language.
- `--propagate-to` cannot combine with `--overwrite` (combination is
  rejected with an error) and must differ from `--lang`.
- `--dry-run` with `--propagate-to` emits two unified diffs, one per
  language, each scoped to the voiceover cells that changed.
- Trace-log entries for propagation calls carry `kind: "propagate"`
  and a `source_trace_id` pointer to the matching source-language merge
  call. Langfuse spans are tagged `voiceover-sync`, `propagate`, plus
  both languages.

```bash
# Translate the merge changes into English voiceover cells too.
clm voiceover sync slides.py "Teil *.mp4" --lang de --propagate-to en
# Dry-run emits both the de diff and the en diff.
clm voiceover sync slides.py "Teil *.mp4" --lang de --propagate-to en --dry-run
```

#### `clm voiceover transcribe`

Extract transcript from a video file.

```
clm voiceover transcribe VIDEO [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Video language (`de` or `en`) |
| `--whisper-model TEXT` | Whisper model size (default: `large-v3`) |
| `--backend [faster-whisper\|cohere\|granite]` | Transcription backend (default: `faster-whisper`) |
| `--device [auto\|cpu\|cuda]` | Device for transcription (default: `auto`) |
| `-o, --output PATH` | Output file |

#### `clm voiceover detect`

Detect slide transitions in a video using frame differencing.

```
clm voiceover detect VIDEO [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-o, --output PATH` | Output file |

#### `clm voiceover identify`

Match video frames to slides using OCR + fuzzy matching.

```
clm voiceover identify VIDEO SLIDES --lang {de|en} [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Video language (`de` or `en`) (required) |
| `-o, --output PATH` | Output file |

#### `clm voiceover identify-rev`

Identify which historical revision of a slide file a recording was made
against. Walks the git history of the slide file, builds a fingerprint
from the OCR of the video's keyframe transitions, and ranks each
candidate revision by fuzzy longest-common-subsequence similarity.
Revisions at the boundary of a narrative-heavy commit run (likely
recording-session markers) receive a multiplicative prior.

Used standalone as a diagnostic, or as the first step of the backfill
pipeline before `clm voiceover sync` is run against a specific revision.

```
clm voiceover identify-rev SLIDE_FILE VIDEO... --lang {de|en} [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Video language (`de` or `en`) (required) |
| `--top N` | How many top-ranked revisions to display (default: 5) |
| `--since TEXT` | `git log --since` filter (e.g. `'6 months ago'`, `'2025-01-01'`) |
| `--limit N` | Maximum number of commits to score, most recent first (default: 50) |
| `--json` | Emit machine-readable JSON instead of a table |

If the top-ranked revision scores below ~0.6 the command prints a
warning suggesting you force a specific revision downstream. Re-using
the transitions cache (written by `detect`/`sync`/`identify`) keeps
repeated runs fast.

#### `clm voiceover sync-at-rev`

Middle step of the backfill pipeline. Exports SLIDE_FILE as it existed
at `--rev` to a scratch location via `git show` (never touches the
working tree) and runs the full `sync` pipeline against that historical
version plus the supplied VIDEO parts. Output is written to `--output`.

```
clm voiceover sync-at-rev SLIDE_FILE VIDEO... --rev SHA -o PATH --lang {de|en} [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--rev TEXT` | Git revision (SHA, tag, or branch) to export SLIDE_FILE at (required) |
| `-o, --output PATH` | Sync output path; must not equal SLIDE_FILE (required) |
| `--lang TEXT` | Video language, `de` or `en` (required) |
| `--polish-level [verbatim\|light\|standard\|heavy\|rewrite]` | How aggressively to clean up the transcript (default: `standard`) |
| `--mode {polished,verbatim}` | **Deprecated** — use `--polish-level` instead |
| `--overwrite` | Overwrite existing voiceover cells instead of merging |
| `--whisper-model TEXT` | Whisper model size (default: `large-v3`) |
| `--backend TEXT` | Transcription backend (`faster-whisper`/`cohere`/`granite`) |
| `--device {auto,cpu,cuda}` | Device for transcription |
| `--tag TEXT` | Cell tag to write: `voiceover` (default) or `notes` |
| `--dry-run` | Parse and report without running the LLM merge |
| `--keep-audio` | Keep extracted audio files for debugging |
| `--model TEXT` | Override the LLM merge model |
| `--transcript PATH` | Skip ASR; load transcript JSON |
| `--alignment PATH` | Skip ASR + detection + matching; load alignment JSON |
| `--scratch-dir PATH` | Use this directory for the exported slide file (default: fresh `.clm/voiceover-backfill/<topic>-<ts>/`) |

Use `clm voiceover backfill` to chain Step 1 (identify-rev), this
command (Step 2), and Step 3 (port-voiceover) in one shot.

#### `clm voiceover backfill`

One-shot wrapper that extracts voiceover content from old recordings
onto the current SLIDE_FILE. Chains `identify-rev` → `sync-at-rev` →
`port-voiceover`. **Patch-by-default:** writes a unified diff to
`.clm/voiceover-backfill/<topic>-<ts>/port.patch` and prints it;
`--apply` is required to mutate the working-copy SLIDE_FILE.

```
clm voiceover backfill SLIDE_FILE VIDEO... --lang {de|en} [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Video language, `de` or `en` (required) |
| `--rev TEXT` | Skip identify-rev and use this revision directly |
| `--top N` | How many top-ranked candidates to display (default: 5) |
| `--auto` | Pick the top-ranked revision automatically (Step 1) |
| `--force-rev` | Accept the identified rev even if its score is below the threshold |
| `--dry-run` | Print the diff only; do not write `port.patch` |
| `--apply` | Mutate SLIDE_FILE with the ported voiceover (default: patch-only) |
| `--keep-scratch` | Retain `.clm/voiceover-backfill/<topic>-<ts>/` on exit |
| `--tag TEXT` | Cell tag: `voiceover` (default) or `notes` |
| `--whisper-model TEXT` | Whisper model size (default: `large-v3`) |
| `--backend TEXT` | Transcription backend (`faster-whisper`/`cohere`/`granite`) |
| `--device {auto,cpu,cuda}` | Device for transcription |
| `--model TEXT` | Override the LLM model for the polish + port steps |
| `--api-base TEXT` | Override the LLM API base URL |

Without `--rev`, Step 1 scores candidate revisions and displays them.
If `--auto` is also set, the top-ranked candidate is used; otherwise
the command exits with the table shown and asks you to rerun with
`--rev <sha>`. Scores below ~0.6 require `--force-rev` to proceed.

The scratch directory is kept when a patch was written (so you can
re-apply or audit it later) and deleted otherwise, unless
`--keep-scratch` forces it to stick around. The most recent patch is
also copied to
`.clm/voiceover-backfill/<topic>/latest.patch` (one directory level
above the timestamped scratch) so the "just show me the most recent
diff" lookup is a predictable read.

#### `clm voiceover port-voiceover`

Port polished voiceover content from one slide file onto another,
file-to-file. Typical use: after running `clm voiceover sync` against
a historical revision exported to a scratch location, port the
resulting voiceover cells onto the current HEAD slide file.

Slide matching uses `slide_id` metadata as the primary key, falling
back to fuzzy title match and then content fingerprint when titles
collide. Slides present only on one side are reported but never
edited. The LLM merges prior bullets around any voiceover already
present on the target; baseline content is preserved unless directly
contradicted.

```
clm voiceover port-voiceover SOURCE TARGET --lang {de|en} [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Slide language (`de` or `en`) (required) |
| `--dry-run` | Print a unified diff instead of writing TARGET |
| `--tag TEXT` | Cell tag to read/write: `voiceover` (default) or `notes` |
| `--model TEXT` | Override the LLM model (default: `anthropic/claude-sonnet-4-6`) |
| `--api-base TEXT` | Override the LLM API base URL |

Prefer `clm voiceover backfill` when you want one-shot extraction plus
porting in a single command with automatic git-revision detection;
`port-voiceover` is the file-to-file primitive that `backfill` composes.

#### `clm voiceover compare`

Evaluate bullet-level differences between two slide-file revisions
without modifying either one. Read-only sibling to `port-voiceover`:
the LLM labels each bullet on each side as `covered`, `rewritten`,
`added`, `dropped`, or `manual_review`. Useful for auditing a port
(`source = sync-at-rev output`, `target = slides@HEAD` after a port)
or for reviewing how voiceover drifted between two hand-edited
revisions.

```
clm voiceover compare SOURCE TARGET --lang {de|en} [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Slide language (`de` or `en`) (required) |
| `--format {table,json,markdown}` | Output format (default: table on stdout) |
| `--json` | Shorthand for `--format json` |
| `-o, --output PATH` | Write the report to this path (default: stdout) |
| `--model TEXT` | Override the LLM model (default: `anthropic/claude-sonnet-4-6`) |
| `--api-base TEXT` | Override the LLM API base URL |

Slide matching uses the same `slide_id`/title/content pipeline as
`port-voiceover`. Slides present on only one side appear in the
report under their `new_at_head` / `removed_at_head` bucket with no
LLM call. The JSON schema mirrors the in-memory `CompareReport`:
top-level `status_totals` and `kind_totals`, plus a `slides[]` list
with per-slide outcomes. `--format markdown` renders the same data
as a human-readable report (summary table + per-bucket sections
grouped by `dropped` / `added` / `rewritten` / `manual_review`).

#### `clm voiceover compare-from-inventory`

Compare a slide file against its historical recording, using a
`video_to_slide_mapping.json` inventory to locate the video(s).
Composes `identify-rev` → `sync-at-rev` → `compare` into one call so
per-topic shell wrappers are unnecessary.

```
clm voiceover compare-from-inventory SLIDE_FILE --inventory PATH --lang {de|en} [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--inventory PATH` | Path to the inventory JSON (required) |
| `--lang TEXT` | Recording language (`de` or `en`) (required) |
| `--rev SHA` | Skip `identify-rev` and use this revision directly |
| `--auto/--no-auto` | Pick the top-ranked rev automatically (default: `--auto`) |
| `--force-rev` | Accept the top rev below the confidence threshold |
| `--top N` | How many candidate revisions to score in `identify-rev` (default: 5) |
| `--format {table,json,markdown}` | Output format (default: table) |
| `--json` | Shorthand for `--format json` |
| `-o, --output PATH` | Write the report to this path (default: stdout) |
| `--model TEXT` | Override the judge LLM model |
| `--api-base TEXT` | Override the LLM API base URL |
| `--whisper-model TEXT` | Whisper model size (default: `large-v3`) |
| `--backend {faster-whisper,cohere,granite}` | Transcription backend |
| `--device {auto,cpu,cuda}` | Device for transcription |
| `--keep-scratch` | Retain the scratch directory on exit |

The inventory's `matched_slide` field may be relative or absolute; it
is resolved against the directory containing the inventory JSON.
Multi-part recordings (several inventory rows pointing at the same
slide file) are passed to `sync-at-rev` in inventory order.

#### `clm voiceover report`

Re-render a saved `compare --json` report in a different format
without re-running the LLM judge. The JSON is the canonical artifact;
this command just reshapes it.

```
clm voiceover report REPORT.json [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--format {markdown,json,table}` | Output format (default: `markdown`) |
| `-o, --output PATH` | Write the rendered report to this path (default: stdout) |

#### `clm voiceover extract-training-data`

Extract training data from a voiceover merge trace log. Reads a JSONL trace
log produced by `clm voiceover sync` and correlates each entry with the
current slide file state to produce training triples suitable for fine-tuning.

```
clm voiceover extract-training-data TRACE_LOG [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--base-dir PATH` | Project root for resolving slide file paths (default: inferred from trace log location) |
| `--tag TEXT` | Cell tag to read from slide files: `voiceover` (default) or `notes` |
| `--no-check-git` | Skip `git_head` reachability check |
| `-o, --output PATH` | Output file (default: stdout) |

Output fields per JSONL line: `input.baseline`, `input.transcript`,
`llm_output`, `human_final`, `delta_vs_llm` (empty = no hand edits, valid
positive training example).

#### `clm voiceover cache`

Inspect and manage the voiceover artifact cache.

```
clm voiceover cache list
clm voiceover cache prune --max-age-days DAYS
clm voiceover cache clear [--yes]
```

`list` groups entries by kind (`transcripts`, `transitions`, `timelines`,
`alignments`) and shows the on-disk path and size. `prune` removes entries
older than the given number of days. `clear` removes every entry (prompts
for confirmation unless `--yes` is passed).

#### `clm voiceover trace show`

Render a trace log (`.clm/voiceover-traces/<stem>-<ts>.jsonl`) in a
human-readable summary table, or dump the raw entries with `--json`.

```
clm voiceover trace show PATH [--json]
```

The trace log schema is documented in `docs/claude/voiceover-design.md`
(schema tag `clm.voiceover.trace/1`).

Examples:

```bash
clm voiceover sync slides.py video.mp4 --lang de
clm voiceover sync slides.py video.mp4 --lang de --dry-run
clm voiceover sync slides.py "Teil 1.mp4" "Teil 2.mp4" "Teil 3.mp4" --lang de
clm voiceover sync slides.py "Teil *.mp4" --lang de
clm voiceover sync slides.py video.mp4 --lang de --overwrite
clm voiceover sync slides.py video.mp4 --lang de --overwrite --mode verbatim
clm voiceover sync slides.py video.mp4 --lang de --slides-range 5-20 --dry-run
clm voiceover sync slides.py video.mp4 --lang de --no-companion
clm voiceover extract-training-data .clm/voiceover-traces/slides_intro-20260412-012020.jsonl
clm voiceover extract-training-data trace.jsonl -o training.jsonl --no-check-git
clm voiceover transcribe video.mp4 --lang de -o transcript.txt
clm voiceover detect video.mp4 -o transitions.txt
clm voiceover identify video.mp4 slides.py --lang de
clm voiceover identify-rev slides.py part1.mp4 part2.mp4 --lang de
clm voiceover identify-rev slides.py recording.mp4 --lang en --top 10 --json
clm voiceover port-voiceover /tmp/slides-at-abc123.py slides.py --lang de --dry-run
clm voiceover port-voiceover old.py new.py --lang en
clm voiceover sync-at-rev slides.py video.mp4 --rev abc1234 --lang de -o /tmp/synced.py
clm voiceover backfill slides.py video.mp4 --lang de --auto
clm voiceover backfill slides.py video.mp4 --lang en --rev abc1234 --apply
clm voiceover compare /tmp/slides-at-abc123.py slides.py --lang de
clm voiceover compare old.py new.py --lang en --json -o report.json
clm voiceover compare old.py new.py --lang en --format markdown -o report.md
clm voiceover compare-from-inventory slides/foo/slides.py \
    --inventory planning/video_to_slide_mapping.json --lang de --json -o report.json
clm voiceover report report.json -o report.md
clm voiceover cache list
clm voiceover cache prune --max-age-days 30
clm voiceover --no-cache sync slides.py video.mp4 --lang de
clm voiceover trace show .clm/voiceover-traces/slides_intro-20260412-012020.jsonl
```

### `clm polish`

Polish existing speaker notes in slide files using an LLM. Removes filler words,
fixes grammar, and preserves technical terms. Requires `clm[summarize]` extra (openai).

```
clm polish SLIDES --lang {de|en} [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--lang TEXT` | Language of notes (`de` or `en`) (required) |
| `--polish-level [verbatim\|light\|standard\|heavy\|rewrite]` | How aggressively to edit notes (default: `standard`). `verbatim` returns notes unchanged without any LLM call. |
| `--slides-range TEXT` | Slide range to polish (e.g. `5-10`) |
| `--dry-run` | Show polished text without writing |
| `-o, --output PATH` | Output file |
| `--model TEXT` | LLM model identifier |

Examples:

```bash
clm polish slides.py --lang de
clm polish slides.py --lang en --slides-range 5-10 --dry-run
clm polish slides.py --lang de --polish-level heavy -o polished.py
clm polish slides.py --lang de --model openai/gpt-4o -o polished.py
```

### `clm recordings`

Manage video recordings for educational courses. Provides audio processing,
recording-to-lecture assignment, and status tracking.

#### `clm recordings check`

Check that the dependencies required by the **active processing backend**
(`recordings.processing_backend`) are available. The set of checks is
backend-aware:

- `onnx` (default): `ffmpeg`, `ffprobe`, and `onnxruntime` — the local
  DeepFilterNet3 pipeline.
- `external`: `ffmpeg` and `ffprobe` only. CLM muxes the externally produced
  `.wav` (e.g. from iZotope RX 11), so `onnxruntime` is **not** required.
- `auphonic`: neither `ffmpeg` nor `onnxruntime` is required (the cloud
  backend is video-in/video-out with no local mux). Instead, `check`
  verifies that `recordings.auphonic.api_key` is non-empty and performs a
  read-only API round-trip (`AuphonicClient.list_presets()`) to confirm the
  credentials and connectivity.

The output table header shows which backend was checked. The command exits
non-zero if any required dependency is missing or the Auphonic check fails.

```
clm recordings check [--offline]
```

Options:

- `--offline`: For the `auphonic` backend, skip the API connectivity
  round-trip and only validate that an API key is configured. No effect for
  the `onnx`/`external` backends.

#### `clm recordings process`

Process a single recording through the audio pipeline (DeepFilterNet3 ONNX + FFmpeg filters).

```
clm recordings process INPUT_FILE [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-o, --output PATH` | Output file (default: auto-named `*_final.mp4`) |
| `-c, --config PATH` | Config JSON file |
| `--keep-temp` | Keep intermediate files for debugging |

#### `clm recordings batch`

Batch-process all recordings in a directory. Skips files that already have output.

```
clm recordings batch INPUT_DIR [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `-o, --output-dir DIR` | Output directory (default: `INPUT_DIR/processed`) |
| `-c, --config PATH` | Config JSON file |
| `-r, --recursive` | Search subdirectories |

#### `clm recordings status`

Show recording status for a course, including per-lecture recording state.

```
clm recordings status COURSE_ID
```

#### `clm recordings compare`

Generate an A/B audio comparison HTML page with embedded audio players
and blind test mode.

```
clm recordings compare VERSION_A VERSION_B [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--label-a TEXT` | Label for version A (default: "Version A") |
| `--label-b TEXT` | Label for version B (default: "Version B") |
| `--original PATH` | Original unprocessed file (optional) |
| `-o, --output PATH` | Output HTML file (default: `comparison.html`) |
| `--start FLOAT` | Start time in seconds |
| `--duration FLOAT` | Duration in seconds (default: 60) |

#### `clm recordings assemble`

Mux paired video + audio files in `to-process/`, write results to `final/`,
and archive originals to `archive/`.

```
clm recordings assemble ROOT_DIR [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--raw-suffix TEXT` | Override raw file suffix (default: from config or `--RAW`) |
| `--dry-run` | Show pending pairs without assembling |

Examples:

```bash
clm recordings assemble ~/Recordings
clm recordings assemble ~/Recordings --dry-run
```

#### `clm recordings serve`

Start the recordings web dashboard (HTMX + SSE). Provides file watcher
controls, job status, lecture assignment, and OBS integration.
Requires `clm[web]` extra.

```
clm recordings serve ROOT_DIR [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--host TEXT` | Host to bind to (default: `127.0.0.1`) |
| `--port INT` | Port to bind to (default: `8008`) |
| `--spec-file PATH` | CLM course spec XML for lecture listing |
| `--obs-host TEXT` | OBS WebSocket host (default: from config) |
| `--obs-port INT` | OBS WebSocket port (default: from config) |
| `--obs-password TEXT` | OBS WebSocket password |
| `--no-browser` | Do not auto-open browser |

Examples:

```bash
clm recordings serve ~/Recordings
clm recordings serve ~/Recordings --spec-file course.xml
clm recordings serve ~/Recordings --obs-host 192.168.1.5 --port 9000
```

#### `clm recordings backends`

List available processing backends and their capabilities.

```
clm recordings backends
```

#### `clm recordings submit`

Submit a file to the configured processing backend. For synchronous
backends (onnx, external), this blocks until completion; for
asynchronous backends (auphonic), it returns once the upload finishes
and processing starts.

```
clm recordings submit INPUT_FILE [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--root DIR` | Recordings root (defaults to config) |
| `--request-cut-list` | Ask the backend to produce a cut list (Auphonic only) |
| `--title TEXT` | Metadata title override |

#### `clm recordings jobs list`

List recording processing jobs from the on-disk store.

```
clm recordings jobs list [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--root DIR` | Recordings root (defaults to config) |
| `--all` | Include terminal jobs (completed/failed) |
| `-n / --limit` | Max number of jobs to show (default: 20) |

#### `clm recordings jobs cancel`

Cancel an in-flight job by ID (prefix matches accepted).

```
clm recordings jobs cancel JOB_ID [OPTIONS]
```

#### `clm recordings auphonic preset sync`

Create or update the managed ``CLM Lecture Recording`` preset in the
user's Auphonic account. Idempotent.

#### `clm recordings auphonic preset list`

List all presets in the authenticated Auphonic account.

Examples:

```bash
clm recordings check
clm recordings process raw.mkv
clm recordings process raw.mkv -o final.mp4 --keep-temp
clm recordings batch ~/Recordings -o ~/Processed -r
clm recordings status python-basics
clm recordings compare izotope.mp4 onnx.mp4 --label-a "iZotope RX" --label-b "DeepFilterNet3 ONNX"
clm recordings assemble ~/Recordings
clm recordings assemble ~/Recordings --dry-run
clm recordings serve ~/Recordings --spec-file course.xml
clm recordings backends
clm recordings submit topic--RAW.mp4 --root ~/Recordings
clm recordings jobs list --root ~/Recordings --all
clm recordings jobs cancel a3b4e56f --root ~/Recordings
clm recordings auphonic preset sync
clm recordings auphonic preset list
```

### `clm monitor`

Launch real-time TUI monitoring dashboard. Requires `clm[tui]` extra.

For each busy **notebook** worker the dashboard renders a second line with
per-cell visibility:

```
⚙ slides_010_langchain_basics (recording, html, en) [1m 23s]
    cell 47/92  in-cell 00:47  idle 00:47  last: Epoch 2/3 - loss: 0.21
```

The fields are sourced from the `worker_heartbeats` table in `clm_jobs.db`
(populated by the notebook worker before each cell, and on every
stdout/stderr stream chunk). `clm status` shows the same data in both
table and JSON formats. Non-notebook workers (PlantUML, Draw.io) do not
publish a heartbeat and do not get the second line.

### `clm serve`

Start web dashboard server. Requires `clm[web]` extra.

### `clm zip`

Create and manage ZIP archives of course output.

| Subcommand | Description |
|------------|-------------|
| `zip create SPEC_FILE` | Create ZIP archives of output directories |
| `zip list SPEC_FILE` | List directories that would be archived |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PLANTUML_JAR` | Path to PlantUML JAR file |
| `DRAWIO_EXECUTABLE` | Path to Draw.io executable |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `CLM_MAX_CONCURRENCY` | Max concurrent operations (default: 50) |
| `CLM_DATA_DIR` | Default data directory for MCP server (contains slides/, course-specs/) |
| `CLM_GIT__REMOTE_TEMPLATE` | Git remote URL template (e.g., `git@github.com-cam:Org/{repo}.git`) |
| `CLM_GIT__REMOTE_PATH` | Default remote path between base URL and repo name (e.g., GitLab group) |
| `CLM_LLM__MODEL` | Default LLM model for summarize (default: `anthropic/claude-sonnet-4-6`) |
| `CLM_LLM__API_KEY` | API key for LLM provider (or use `OPENAI_API_KEY`) |
| `CLM_LLM__API_BASE` | API base URL (e.g. `https://openrouter.ai/api/v1`) |
| `CLM_LLM__MAX_CONCURRENT` | Max parallel LLM calls (default: 3) |
| `CLM_LLM__TEMPERATURE` | LLM sampling temperature (default: 0.3) |
| `CLM_SYNC_PROVIDER` | Default edit-judge backend for `clm slides sync`: `openrouter` (default) or `local`. Overridden by `--provider`. |
| `OPENROUTER_API_KEY` | OpenRouter API key for `clm slides sync` (edit judge + new-slide translation); falls back to `OPENAI_API_KEY`. |
| `CLM_RECORDINGS__OBS_OUTPUT_DIR` | Directory where OBS saves recordings |
| `CLM_RECORDINGS__ACTIVE_COURSE` | Currently active course ID |
| `CLM_RECORDINGS__AUTO_PROCESS` | Auto-process recordings when detected (default: false) |
| `CLM_RECORDINGS__ROOT_DIR` | Root directory for recording workflow (to-process/, final/, archive/) |
| `CLM_RECORDINGS__RAW_SUFFIX` | Suffix for raw recording filenames (default: `--RAW`) |
| `CLM_RECORDINGS__PROCESSING_BACKEND` | Processing backend: `onnx` (default), `external`, `auphonic` |
| `CLM_RECORDINGS__STABILITY_CHECK_INTERVAL` | Seconds between file-size polls (default: `2.0`) |
| `CLM_RECORDINGS__STABILITY_CHECK_COUNT` | Consecutive identical polls = stable (default: `3`) |
| `CLM_RECORDINGS__OBS_HOST` | OBS WebSocket host (default: `localhost`) |
| `CLM_RECORDINGS__OBS_PORT` | OBS WebSocket port (default: `4455`) |
| `CLM_RECORDINGS__OBS_PASSWORD` | OBS WebSocket password (default: empty) |
| `CLM_RECORDINGS__PROCESSING__DEEPFILTER_ATTEN_LIM` | DeepFilterNet attenuation limit (default: 35.0) |
| `CLM_RECORDINGS__PROCESSING__SAMPLE_RATE` | Audio sample rate (default: 48000) |
| `CLM_RECORDINGS__PROCESSING__LOUDNORM_TARGET` | Loudness target in LUFS (default: -16.0) |
| `CLM_RECORDINGS__AUPHONIC__API_KEY` | Auphonic API key (required when `processing_backend = "auphonic"`) |
| `CLM_RECORDINGS__AUPHONIC__PRESET` | Optional managed preset name (empty = inline algorithms) |
| `CLM_RECORDINGS__AUPHONIC__POLL_TIMEOUT_MINUTES` | Max minutes per Auphonic job (default: 120) |
| `CLM_RECORDINGS__AUPHONIC__REQUEST_CUT_LIST` | Request cut list on every production (default: `false`) |
| `CLM_RECORDINGS__AUPHONIC__BASE_URL` | API base URL override (default: `https://auphonic.com`) |
| `CLM_MAX_WORKERS` | Cap effective worker count per build invocation (empty/zero/negative = no cap) |
| `CLM_HTTP_REPLAY_MODE` | Default HTTP replay record mode for `clm build` (one of `replay`, `once`, `new-episodes`, `refresh`, `disabled`). Overridden by `--http-replay`. Defaults to `replay` when `CI=true`, else `new-episodes`. |
| `CLM_HTTP_REPLAY_IGNORE_HOSTS` | Comma-separated list of request hosts that vcrpy should let pass through to the real network instead of recording into the cassette. Defaults to `api.smith.langchain.com` (LangSmith telemetry). Set to an empty string to disable the default. |
| `CLM_HTTP_REPLAY_CELL_TIMEOUT_SECONDS` | Default per-cell execution timeout (seconds) applied to HTTP-replay-engaged jobs only (any `--http-replay` mode but `disabled`), so a replay-layer hang fails as a clean cell timeout instead of stalling to the build-level job timeout (issue #143). `CLM_CELL_TIMEOUT_SECONDS` overrides it; set to `0` to opt out. Default `600`. |
| `CLM_HTTP_REPLAY_TRACE` | Set to `1` to enable the forensic trace harness for HTTP-replay diagnostics. Off by default; writes per-invocation trace bundles under `$CLM_HTTP_REPLAY_TRACE_DIR`. See `docs/claude/design/http-replay-trace.md`. |
| `CLM_HTTP_REPLAY_TRACE_DIR` | Root directory for trace bundles when `CLM_HTTP_REPLAY_TRACE=1`. Defaults to `./clm-http-replay-traces`. |
| `CLM_HTTP_REPLAY_TRACE_VERBOSE` | When tracing is on, include extra per-event detail (default off). Accepts `1`/`true`/`yes`. |
| `CLM_HTTP_REPLAY_TRACE_MAX_BODY_BYTES` | Cap on bytes recorded for the head/tail body excerpts in trace events (default: implementation-defined). |
| `CLM_FAIL_ON_ERROR` | Override the default exit-on-cell-error policy for `clm build`. Accepts `1`/`true`/`yes` or `0`/`false`/`no`. Overridden by `--fail-on-error` / `--no-fail-on-error`. See `clm build` → "Exit codes". |
| `CLM_FAIL_ON_MISSING_XREF` | Override the default exit-on-missing-cross-reference policy for `clm build` (issue #17). Accepts `1`/`true`/`yes` or `0`/`false`/`no`. Overridden by `--fail-on-missing-xref` / `--no-fail-on-missing-xref`. See `clm info spec-files` → "Cross-references". |
| `LANGFUSE_HOST` | Langfuse server URL (or `LANGFUSE_BASE_URL`); enables LLM call tracing when set with keys below |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key for LLM tracing |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key for LLM tracing |
