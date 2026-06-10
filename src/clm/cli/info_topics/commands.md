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
uses the canonical (group-qualified) names. The older flat names
(`clm normalize-slides`, `clm validate-slides`, etc.), deprecated since
CLM 1.6, were **removed in CLM 1.8** — use the group-qualified forms; see
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
| `--fail-on-error / --no-fail-on-error` | Exit with non-zero status when the build summary reports any cell/notebook error **or a dropped companion voiceover** (a `for_slide` with no matching `slide_id`, since CLM {version}). Defaults to **on** under `--http-replay=replay` (incl. CI) and **off** under all other replay modes. Override via `CLM_FAIL_ON_ERROR={1,true,yes,0,false,no}`. See "Exit codes" below. |
| `--fail-on-missing-xref / --no-fail-on-missing-xref` | Exit with non-zero status when a `clm:` cross-reference points at a topic not included in the build (issue #17). Defaults to **on** under `--http-replay=replay` (incl. CI) and **off** under all other replay modes (a missing target is then a warning and the link is dropped). Override via `CLM_FAIL_ON_MISSING_XREF={1,true,yes,0,false,no}`. See `clm info spec-files` → "Cross-references". |
| `--provenance-manifest / --no-provenance-manifest` | Write a `.clm-manifest.json` provenance index into each output root, mapping every output file to its source commit and owning section/topic (issue #208 — needed by the per-topic solution-release workflow). **On by default since CLM {version}**; `clm git` excludes it from every distributed repo. Pass `--no-provenance-manifest` to skip it. Always suppressed under `--snapshot` / `--verify-against` (it embeds a timestamp + commit, so it must not enter a byte-reproducibility baseline). |

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

#### Jinja `{% include %}` in slide source

Before a notebook is converted, its source is expanded as a Jinja
template (this is what makes the `# {{ header(…) }}` title macro work).
Two loaders back `{% include %}`, searched in this order:

1. The bundled per-language template directory inside the `clm` package
   (`src/clm/workers/notebook/templates_<prog_lang>/`) — the source of
   `macros.j2` and friends.
2. The notebook's own **topic siblings** — any non-image file sitting
   next to the slide file in its topic directory (since CLM {version}).

So a deck can render a sibling file verbatim, e.g. show a C++ header
that lives beside it:

````text
// ```cpp
// {% include "add.h" %}
// ```
````

Resolution notes:

- The include target is the sibling's path **relative to the topic
  directory** (forward slashes), matching how the file is shipped to
  the worker — the same set of files an `<include>` splices in are
  also includable this way.
- The bundled package directory is searched **first**, so a sibling can
  never shadow a bundled template: a sibling named `macros.j2` is
  ignored in favor of the shipped macros. Siblings only supply names the
  package does not already provide.
- Binary siblings (anything that is not valid UTF-8) are skipped — they
  cannot be Jinja templates.
- An include with no matching bundled template and no matching sibling
  still fails the build with `TemplateNotFound`, as before.

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

Slide files in split format — `<basename>.de.<ext>` and
`<basename>.en.<ext>`, produced by `clm slides split` — route directly
through the per-language pipeline: a `.de.<ext>` file is built only
for `lang=de` and a `.en.<ext>` file only for `lang=en`. No unify
step, no temporary file. Build output is byte-identical to building
the bilingual companion (same per-cell `lang` filter, same output
paths, same section index — split companions are treated as one
logical slot when numbering notebooks within a section).

The build detects three other shapes per slide family
(`slides_foo.<ext>`, `slides_foo.de.<ext>`, `slides_foo.en.<ext>` all share
a *family*) and the routing rule is:

- **Bilingual only** (`slides_foo.<ext>`, no companions) — fed to both
  DE and EN pipelines exactly as before.
- **Split pair** (`.de.<ext>` + `.en.<ext>`, no bilingual) — each file
  routes to its own per-language pipeline.
- **Dual-format conflict** (bilingual *and* at least one split
  companion present) — build refuses before any worker runs with
  category `split_slide_dual_format`. Resolve by running
  `clm slides unify` to merge or deleting the bilingual companion.
- **Half-pair** (only one of `.de.<ext>` / `.en.<ext>`) — build refuses
  before any worker runs with category `split_slide_half_pair`.
  Add the missing companion.

`clm validate <topic_dir>` (and `clm validate <course-spec>`)
additionally diffs the shared (no-`lang`) cells between a detected
split pair and emits a `pairing` error finding for any divergence —
the failure mode that silently produces different DE and EN output
for what was meant to be language-neutral material.

The sibling `header_de` / `header_en` macros (the split prerequisite)
ship in every language template — `templates_python` and the
cpp/csharp/java/typescript templates — so split decks work across all
supported prog_langs.

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
| Dropped companion voiceover (unmatched `for_slide`) — same policy as cell errors (since CLM {version}) | `1` under `--fail-on-error`, else `0` |
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

Since CLM {version}, **dropped companion voiceover** is treated the same
way. When a separated-voiceover companion (`voiceover_*.<ext>`) has a
`for_slide` that matches no `slide_id` in the slide it accompanies, that
narration is dropped from the built output — usually because a `slide_id`
was renamed out from under the companion. The build now reports each drop
as a `voiceover`-category error in the summary (it used to be a bare log
line), so it surfaces in the report and, under `--fail-on-error`, fails the
build. Fix the `for_slide` / `slide_id` mismatch (`clm voiceover inline`
then re-extract, or `clm slides sync`), or pass `--no-fail-on-error` to
tolerate it. `clm validate`'s #162 detectives catch the underlying
divergence earlier (pre-commit), before it reaches a build.

### `clm targets`

List output targets defined in a course spec file.

```
clm targets SPEC_FILE
```

### `clm export`

Group for the **course-document exports** — commands that turn a course spec
into a human-readable document: `outline`, `schedule`, and `summary`. (These
replace the former flat `clm outline` / `clm schedule` / `clm summarize`
top-level commands, which were removed.)

All three share a common option vocabulary:

| Option | Description |
|--------|-------------|
| `-L, --language [de\|en]` | Language of the generated document (defaults differ per command). |
| `-o, --output FILE` | Write to FILE (mutually exclusive with `-d`). |
| `-d, --output-dir DIR` | Write to DIR with auto-generated filenames (mutually exclusive with `-o`). |
| `--include-optional` | Include modules marked `optional="true"` (on a `<section>` or `<subsection>`). Off by default. |
| `--include-disabled[=marked\|merge]` | Include sections/subsections marked `enabled="false"`. Off by default (excluded). A bare `--include-disabled` (or `=marked`) tags them `(disabled)` — disabled whole sections are listed after the enabled ones in `outline`/`summary`. `=merge` folds them into the normal course flow, in declared order, with no marker. |

`optional="true"` and `enabled="false"` are **presentation-only** for these
commands — they never change the build, only what appears in the document. An
element that is both optional and disabled needs **both** flags to appear.

Because `--include-disabled` takes an optional value, give the value with `=`
(e.g. `--include-disabled=merge`) and keep `SPEC_FILE` first — a bare
`--include-disabled` placed immediately before the spec path would be parsed as
its value. In the structured outputs (`outline --format json`, `schedule
--format csv`) the disabled state stays recorded (`"disabled": true` /
`disabled` column) even under `=merge`; merge only changes the human-readable
placement and marker.

Split-language decks (`slides_x.de.py` + `slides_x.en.py`) are filtered to the
requested `-L` language across all three commands, so a split pair contributes a
single entry — the same per-language routing the build applies.

#### `clm export outline`

Generate a Markdown (or JSON) outline of a course.

```
clm export outline [OPTIONS] SPEC_FILE
```

| Option | Description |
|--------|-------------|
| `-o/-d/-L/--include-optional/--include-disabled` | Shared options (see `clm export` above; `-L` defaults to `en` for stdout/`-o`, both languages for `-d`). |
| `--format [markdown\|json]` | Output format (default: markdown). |
| `--sections-only` | Emit only section headings, omitting per-topic/slide entries within each section. |
| `--weekdays [never\|always]` | Show the `<subsection>` weekday/name groupings as bold labels (Markdown only). `never` (default): flatten every section's decks into plain bullets, so weeks read uniformly whether or not they declare subsections. `always`: group decks under their weekday/name label in every week (including disabled weeks under `--include-disabled`). |

A section may use the optional `<subsection>` layer (see `clm info spec-files`)
to group its decks under a weekday or label (`<section>` = week,
`<subsection>` = day). By default (`--weekdays never`) the outline ignores that
grouping and lists every deck as a flat bullet, so a section reads the same
whether or not it declares subsections. Pass `--weekdays always` to render each
subsection as an indented group instead: a bold weekday/label bullet with the
subsection's decks nested beneath it, after any bare (unscheduled) topics.
Hiding an optional/disabled subsection always hides its topics (they are not
demoted to bare bullets), regardless of `--weekdays`. `--include-disabled`
surfaces disabled subsections (and disabled sections) with a `(disabled)`
marker, reading their decks from disk and appending disabled whole sections
after the enabled ones; `--include-disabled=merge` instead interleaves them in
declared order with no marker. The JSON format always adds a `subsections`
array to each section that uses them (alongside the flat `topics` list) — it
carries the grouping as structured data and is unaffected by `--weekdays`.

Examples:

```bash
clm export outline course.xml
clm export outline course.xml -L de
clm export outline course.xml -d ./docs
clm export outline course.xml --format json
clm export outline course.xml --weekdays always           # group decks by weekday/label
clm export outline course.xml --include-optional
clm export outline course.xml --include-disabled          # roadmap weeks, tagged + appended
clm export outline course.xml --include-disabled=merge     # roadmap weeks folded into the flow
clm export outline course.xml --sections-only
```

#### `clm export schedule`

Export a **day-of-week deck listing** for certification (e.g. AZAV requires a
listing of which weekday each video/slide deck is presented, per week). The
listing is built from the spec's optional `<subsection weekday="...">` layer
(`<section>` = week, `<subsection>` = day; see `clm info spec-files`) resolved
against the decks discovered on disk.

```
clm export schedule [OPTIONS] SPEC_FILE
```

| Option | Description |
|--------|-------------|
| `-L, --language`, `--lang [de\|en]` | Language for deck titles/labels (default: `de`). Titles come from the matching-language `header`/`header_de`/`header_en` macro. |
| `-f, --format [md\|csv]` | Output format (default: `md`). |
| `-o, --output FILE` / `-d, --output-dir DIR` | Write to FILE / to a directory (filename `<course>-schedule-<lang>.<ext>`). |
| `--no-topic` | Omit the Topic column, leaving just day and video/slides — the columns a certification authority needs (applies to both `md` and `csv`). |
| `--include-optional` | Include modules marked `optional="true"` (on a `<section>` or `<subsection>`). |
| `--include-disabled[=marked\|merge]` | Surface disabled subsections/sections (read from disk). Bare/`=marked`: tagged `(disabled)`. `=merge`: no tag (weeks already appear in declared order). The CSV gains a trailing `disabled` column whenever disabled content is included (truthful even under `=merge`). |
| `--data-dir DIR` | Course data directory (contains `slides/`). Default: inferred from the spec location. |

Each listing is **single-language** — run once per language to produce both.
Deck order within a (week, day) is topic document order, then `slides_NNN_`
order within each topic, matching the build.

- **`--format md`** (default): one Markdown table per week, columns
  weekday / video (deck title) / topic (the Topic column is dropped with
  `--no-topic`). The weekday label appears on the first deck row of each day.
  Days are fixed-length by definition, so there are no durations/minutes. Empty
  days render a placeholder row. A subsection that spans several days
  (`weekday="mon,tue"`) renders a single joined label.
- **`--format csv`**: one row per deck, with header
  `week,week_title,weekday,video_title,topic,deck_file` (`topic` dropped with
  `--no-topic`; a trailing `disabled` column is added with `--include-disabled`).
  A multi-day subsection joins its tokens in the `weekday` cell (`mon,tue`,
  quoted by the CSV writer).

Only enabled subsections are listed by default; optional ones require
`--include-optional` and disabled ones `--include-disabled`. An excluded
optional `<section>` keeps its declared week number (so omitting an optional
Week 3 leaves Weeks 1, 2, 4, … rather than renumbering). Bare topics that sit
under no subsection do not appear in the listing (`clm validate` reports them as
an info finding).

Examples:

```bash
clm export schedule course.xml                  # German Markdown to stdout
clm export schedule course.xml -L en            # English listing
clm export schedule course.xml -f csv           # CSV (one row per deck)
clm export schedule course.xml --no-topic       # Day + video/slides only (cert authority)
clm export schedule course.xml --include-optional   # Add optional modules
clm export schedule course.xml --include-disabled   # Show disabled days, tagged
clm export schedule course.xml --include-disabled=merge   # Show disabled days, no tag
clm export schedule course.xml -o schedule.md   # Write to a file
clm export schedule course.xml -d ./docs        # Write into a directory
```

#### `clm export calendar`

*New in {version}.* Project the course **schedule onto a cohort's real calendar
dates**. Where `export schedule` is course-relative ("Week 3, Tuesday"), a
*calendar* maps the same ordered day-buckets onto actual dates for one cohort,
absorbing that cohort's holidays, delayed start, breaks, and catch-up. The
trainer maintains only a small hand-edited `release/<channel>.calendar.toml`
(see **Cohort calendar file** below); the dates are computed.

```
clm export calendar [OPTIONS] SPEC_FILE
```

| Option | Description |
|--------|-------------|
| `--channel NAME` | Cohort channel; resolves the calendar file as `<channel>.calendar.toml` beside the channel's ledger in `<release-channels>`. |
| `--calendar PATH` | Explicit calendar TOML (overrides `--channel`). |
| `-L, --language`, `--lang [de\|en]` | Language for deck titles and weekday labels (default: `de`). |
| `-f, --format [md\|csv\|ics]` | Output format (default: `md`). `ics` is the subscribable student feed. |
| `-o, --output FILE` / `-d, --output-dir DIR` | Write to FILE / to a directory (filename `<course>-calendar-<channel-or-lang>.<ext>`). |
| `--data-dir DIR` | Course data directory. Default: inferred from the spec location. |

- **`md`** — a date-ordered `Date | Content` table; multi-date spans show a date
  range and `insert` days show their label in italics.
- **`csv`** — one row per deck:
  `date,end_date,weekday,kind,label,video_title,topic,deck_file` (`insert` rows
  carry an empty deck triple).
- **`ics`** — one all-day `VEVENT` per assignment, spans using the exclusive-end
  `DTEND` convention, with **stable per-assignment UIDs** so re-exporting an
  updated calendar *updates* events in a subscribed client rather than
  duplicating them.

Projection **errors** (over-full segment, unknown pin/split ref, end overflow)
are printed to stderr and abort the export with a non-zero exit; fix the
calendar (or run `clm calendar check`) first. Warnings (free dates, stray
inserts) are printed but do not block.

```bash
clm export calendar course.xml --channel jan            # German Markdown
clm export calendar course.xml --channel jan -f ics     # student .ics feed
clm export calendar course.xml --calendar c.toml -L en -f csv
clm export calendar course.xml --channel jan -o jan.ics -f ics
```

##### Cohort calendar file (`release/<channel>.calendar.toml`)

A small, hand-edited TOML file holding only the *deltas* from the ideal plan.
Lives beside the channel's release ledger; it is **not** part of the spec.

```toml
start = 2026-03-02              # first teaching date (required)
end   = 2026-06-30              # last allowable teaching date (optional; checked)
pattern = ["mon", "tue", "wed"] # teaching weekdays; default = weekdays the spec uses

holidays = [
  2026-04-06,                                            # a single day
  {from = 2026-05-18, to = 2026-05-29, label = "Break"}, # an inclusive interval
]

# Ordered perturbations of the default 1-bucket-per-teaching-date mapping.
[[adjustments]]
merge = 2026-06-09   # collapse `count` buckets onto one date (catch up)
count = 2

[[adjustments]]
pin  = "control_flow"  # anchor the bucket containing this topic/deck id to a date
date = 2026-04-09

[[adjustments]]
insert = 2026-03-30    # a teaching date with no new video
label  = "Review & Q&A"

[[adjustments]]
split = "long_topic"   # spread a bucket across several dates (slow down)
dates = [2026-03-25, 2026-03-26]
```

A holiday removes a teaching date, so every later bucket slides one date
later automatically. `pin`/`split` reference a bucket by a **stable topic/deck
id** it contains (anchoring the whole day, not the single deck). Pins *segment*
the timeline; when more buckets fall between two pins than there are teaching
dates, the engine never guesses — `clm calendar check` reports the exact
deficit and you resolve it with an explicit `merge`.

### `clm calendar`

Work with a cohort's viewing calendar: validate it, show today's status, or
push it to Google Calendar (see `clm export calendar` for the file format).

#### `clm calendar check`

**Date-free validation** of a calendar against the course schedule. Reports
errors — unknown/ambiguous pin/split refs, over-full segments (with the exact
"merge ≥ N buckets" deficit), content overflowing `end` — and warnings (free
teaching dates before a pin, `insert`/`merge` dates that are not teaching
dates). Exits non-zero if there are errors, so it suits a pre-push hook.

```
clm calendar check [OPTIONS] SPEC_FILE      # --channel/--calendar/--data-dir
```

#### `clm calendar status`

Show **where a cohort is today** relative to the plan — the only now-relative
command. Defaults to the system date; pass `--as-of YYYY-MM-DD` for tests, dated
handouts, or what-if previews. Reports today's assignment (or the next one), its
plan coordinate (e.g. `W4 Tuesday`), the **drift** in days versus the ideal
(no-holiday, no-adjustment) calendar, and an upcoming lookahead.

```
clm calendar status [OPTIONS] SPEC_FILE     # --channel/--calendar/-L/--as-of/--data-dir
```

#### `clm calendar push`

*New in {version}.* **Mirror the cohort's calendar into a Google calendar.**
Students subscribe to that one shared calendar; unlike a subscribed `.ics`
URL, pushed changes propagate within minutes. Requires the `[gcal]` extra
(`pip install "coding-academy-lecture-manager[gcal]"`).

```
clm calendar push [OPTIONS] SPEC_FILE
```

| Option | Meaning |
|---|---|
| `--channel NAME` / `--calendar PATH` | Locate the cohort calendar TOML (as in `clm export calendar`). |
| `--calendar-id ID` | Target Google calendar id. Default: `calendar_id` from the TOML's `[google]` table. |
| `--credentials PATH` | Google credentials JSON (env: `CLM_GOOGLE_CREDENTIALS`). Either an OAuth "Desktop app" client — a browser consent flow runs once, then the token is cached — or a service-account key for a service account the calendar is shared with ("Make changes to events"). |
| `-L, --language` | Language for event titles (default `de`). |
| `--dry-run` | Print the insert/update/delete plan; change nothing. |
| `--data-dir DIR` | Course data directory (as elsewhere). |

The push only ever touches **CLM-managed events**: every event it creates is
tagged (via private extended properties) with the cohort namespace and the
same stable per-assignment UID the `.ics` export uses. Re-pushing after a
schedule change therefore updates events in place, deletes events whose
assignment disappeared, and never touches other events in the same calendar.
Events are all-day and marked free (transparent).

The optional `[google]` table in the cohort calendar TOML holds the target:

```toml
[google]
calendar_id = "abc123…@group.calendar.google.com"
```

A projection error (see `clm calendar check`) blocks the push.

```bash
clm calendar check  course.xml --channel jan
clm calendar status course.xml --channel jan -L en
clm calendar status course.xml --channel jan --as-of 2026-05-06
clm calendar push   course.xml --channel jan --dry-run
clm calendar push   course.xml --channel jan --credentials oauth-client.json
```

### `clm topic resolve`

*Removed in CLM 1.8: the flat alias `clm resolve-topic` no longer exists — use this group-qualified form.*

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

### `clm spec decks`

List the deck files a course spec actually pulls in — its "shipping set".

```
clm spec decks [OPTIONS] [SPEC_FILE]
```

Resolution mirrors the build exactly, which is the point of the command: a
`<topic>` resolves to a topic **directory** and CLM builds **every**
`slides_*.<ext>` in it. The directory name often differs from the deck filenames
(e.g. topic `properties` → `slides_properties.py` **and**
`slides_property_setters.py`), so a deck-filename-stem heuristic silently misses
decks. Module-bound `<topic>`/`<section>` references resolve in their module;
unbound topic IDs that match multiple modules are first-occurrence-wins (matching
the build), and the shadowed matches are reported in `--json` output.

| Option | Description |
|--------|-------------|
| `--all-specs DIR` | Resolve the union shipping set across every `*.xml` spec in `DIR`, annotating each deck with the spec(s) that reference it. Mutually exclusive with `SPEC_FILE`. |
| `--lang de\|en\|both` | Keep only decks serving this language. Bilingual decks (no `.de`/`.en` tag) serve both, so they always survive the filter; split halves are kept only for their own language. Default: `both`. |
| `--data-dir DIR` | Course data directory (contains `slides/`). Default: inferred from the spec file (its grandparent). |
| `--json` | Output as JSON (includes per-topic resolution, unresolved topics, and first-occurrence-shadowed duplicates). |

Topic references that resolve to no directory on disk are reported as a warning
(stderr) but do not fail the command.

Examples:

```bash
clm spec decks course-specs/python.xml
clm spec decks course-specs/python.xml --lang de --json
clm spec decks --all-specs course-specs/
```

### `clm slides referenced-by`

Reverse of `clm spec decks`: show which spec(s)/topic(s) pull a given deck into
their shipping set. A deck reachable from no spec is reported as `unreferenced` —
useful for spotting orphaned or superseded decks before a corpus-wide change.

```
clm slides referenced-by [OPTIONS] DECK
```

| Option | Description |
|--------|-------------|
| `--specs-dir DIR` | Directory of `*.xml` specs to search. Default: `<course-root>/course-specs/`. |
| `--data-dir DIR` | Course data directory (contains `slides/`). Default: inferred from the deck path (its `slides/` ancestor). |
| `--json` | Output as JSON. |

Examples:

```bash
clm slides referenced-by slides/module_x/topic_y/slides_intro.py
clm slides referenced-by slides_intro.py --specs-dir course-specs/
```

### `clm spec orphans`

*Added in CLM {version}.*

The inverse of `clm spec decks`: scan **every** spec in a course and report the
decks on disk that *no* spec pulls in, grouped by likely intent — so you can
archive the dead ones without deleting intentional alternates. Also surfaces
(and optionally removes) gitignored `.ipynb_checkpoints/` cache cruft.

```
clm spec orphans [OPTIONS] SPECS_DIR
```

`SPECS_DIR` is the directory of course spec `*.xml` files. Orphans are computed
against the **union** of every spec (a deck unreferenced by one spec may be
pulled in by another). The on-disk walk is extension-complete (`.py` / `.cpp` /
`.cs` / …), so a non-Python orphan is not silently missed.

| Option | Description |
|--------|-------------|
| `--slides-dir DIR` | The course's `slides/` directory. Default: `<specs-dir>/../slides`. |
| `--data-dir DIR` | Course data directory (contains `slides/`); alternative to `--slides-dir`. |
| `--kind superseded\|alternate\|unknown` | Show only orphans of this intent. |
| `--clean-checkpoints` | Delete the `.ipynb_checkpoints/` directories found (regenerable cache cruft). |
| `--json` | Emit a JSON report (`by_kind` counts + per-orphan `kind`/`reason`, plus `checkpoints`). |

Intent buckets (the distinction matters — blindly archiving a `_part1..5` series
would delete real content):

| Bucket | Markers | Meaning |
|---|---|---|
| `superseded` | `_old` / `_oldN` / `_bak` / `_backup` / `_orig` / `_deprecated` / `_copy` / `_vN` / trailing `_N` | usually safe to archive |
| `alternate` | `_partN` / `_short` / `_long` | probably intentional content — do **not** blindly archive |
| `unknown` | no recognizable marker | review before acting |

The exit code is always `0` — this is a report. Examples:

```bash
clm spec orphans course-specs/                                 # full orphan report
clm spec orphans course-specs/ --kind superseded               # just the archivable ones
clm spec orphans course-specs/ --clean-checkpoints             # report + delete checkpoint cruft
clm spec orphans course-specs/ --slides-dir ../other/slides --json
```

### `clm course gate`

Run the mechanical conversion passes over a course and report **readiness** —
how much of a corpus is cleared by tooling versus how much still needs a human.
Built for bringing a course up to a stricter validator (e.g. the 1.8 `slide_id`
gate) without hand-driving the passes one at a time.

```
clm course gate [OPTIONS] TARGET
```

`TARGET` is a course spec `.xml` (validates and fixes its shipping set) or a
slides directory. The gate runs the mechanical passes — `tag_migration`,
`workshop_tags`, `interleaving`, and content-derived `slide_id` minting — then
splits the remaining work into:

- **mechanical** — what the passes changed (or, in a dry run, *would* change);
- **needs-author** — what the normalizer **refused** to touch because a safe
  automatic fix doesn't exist: a `slide_id` with no derivable heading (hard
  refusal), a DE/EN pair whose code diverged too far to auto-interleave
  (`similarity_failure`), or a DE/EN cell-count mismatch (a missing translation).

| Option | Description |
|--------|-------------|
| `--apply` | Write the mechanical fixes and re-validate, reporting the residual. Without it, the gate is a **dry run**: it reports what *would* change and touches nothing on disk. |
| `--operations LIST` | Comma-separated passes to run (default: `tag_migration,workshop_tags,interleaving,slide_ids`). Valid names: `tag_migration`, `workshop_tags`, `interleaving`, `slide_ids`. |
| `--data-dir DIR` | Course data directory (contains `slides/`). Default: inferred from the target. |
| `--json` | Output as JSON (baseline rollup, mechanical change counts, the needs-author list, and the post-apply residual rollup). |

**Exit code:** non-zero when author work remains, or — after `--apply` — when a
residual error remains; zero when the course is mechanically clean (no author
work). This makes `clm course gate <spec>` usable as a conversion gate in CI:
a dry run that exits 0 means `--apply` will fully clear the corpus.

Examples:

```bash
clm course gate course-specs/python.xml            # dry-run readiness report
clm course gate course-specs/python.xml --apply    # fix mechanically + re-validate
clm course gate slides/module_100/ --apply
clm course gate course-specs/python.xml --json
```

### `clm slides search`

*Removed in CLM 1.8: the flat alias `clm search-slides` no longer exists — use this group-qualified form.*

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

`clm validate` is one unified command that validates **either** a course
specification **or** slide files, dispatching on the input type: an `.xml`
file → spec validation (this section); a `.py` file or a directory → slide
validation (see *`clm validate` (slides mode)* below). Override the inference
with `--kind`.

*Removed in CLM 1.8: the flat aliases `clm validate-spec` and
`clm validate-slides` no longer exist — both are folded into this single
`clm validate` command.*

Validate a course specification XML file for consistency.

```
clm validate [OPTIONS] SPEC_FILE
```

Checks that all referenced topic IDs resolve to exactly one existing
topic directory, that there are no duplicate topic references, and
that referenced dir-group paths exist.

> **Structure-OK is not decks-clean.** By default `clm validate <spec.xml>`
> validates only the spec *structure* (topic resolution, duplicates, dir-group
> paths) — it does **not** check the slide content of the decks the spec pulls
> in. So a passing spec validation does not mean the decks are free of
> missing-`slide_id` / adjacency / pairing errors. Use `--deep` to validate
> both.

| Option | Description |
|--------|-------------|
| `--kind [slides\|spec]` | Force a validator instead of inferring from the path (`.xml` → spec, `.py`/directory → slides). `--kind=spec` requires an `.xml` file. |
| `--data-dir DIR` | Course data directory (contains slides/) |
| `--json` | Output as JSON |
| `--include-disabled` | Also validate sections marked `enabled="false"`; each finding from a disabled section has `(disabled)` appended to its message (default: disabled sections are skipped) |
| `--check-workdays` | Warn (`missing_workday`) when a section that uses the day-of-week `<subsection>` layer leaves a Mon–Fri workday uncovered. Off by default (most courses do not fill all five days). Spec-only. |
| `--deep` | After structure validation, run the full slide validator on **every deck the spec pulls in** (its shipping set) and report both. Exits non-zero on a structure error or a deck-content error (`--fail-on` governs the deck-content threshold). Resolves decks with the same build-faithful semantics as `clm spec decks`. |
| `--summary` | Roll the deck-content findings up into a category/kind histogram with per-deck counts instead of a flat list (intended for corpus-scale validates that emit thousands of findings). On a spec, `--summary` implies `--deep`. |
| `--checks LIST`, `--fail-on [error\|warning]` | Slides-validator options (see slides mode); valid on a spec only together with `--deep`, where they apply to the deck-content pass. |

The `--summary` rollup has three axes: **by category** (`format`/`pairing`/`tags`
× severity — the validator's own categories, exact), **by kind** (a finer,
heuristic bucket derived from the message: `missing-slide_id`, `adjacency`,
`count-mismatch`, `start-completed`, `malformed-marker`, … with an `other`
fallback), and **by deck** (the decks with the most findings first).

Examples:

```bash
clm validate course-specs/python-basics.xml
clm validate course-specs/ml-azav.xml --json
clm validate course-specs/ml-azav.xml --include-disabled
clm validate course-specs/python.xml --deep              # structure + deck content
clm validate course-specs/python.xml --summary           # deep, rolled up
clm validate course-specs/python.xml --deep --fail-on warning
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

`clm validate slides/` (or `clm validate slides_foo.<ext>`) dispatches
to slide validation when the argument is a `.py` file or directory.

*Removed in CLM 1.8: the flat alias `clm validate-slides` no longer exists — use this group-qualified form.*

Validate slide files for format, tag, and pairing correctness. Runs deterministic
checks and extracts structured review material for content-quality checks.

```
clm validate [OPTIONS] PATH
```

| Option | Description |
|--------|-------------|
| `--kind [slides\|spec]` | Force a validator instead of inferring from the path (`.xml` → spec, `.py`/directory → slides). |
| `--checks TEXT` | Comma-separated checks: `format`, `pairing`, `tags`, `code_quality`, `voiceover`, `completeness` (CLI default: all deterministic) |
| `--quick` | Fast syntax-only check (format + tags + slide_ids). Useful for PostToolUse hooks |
| `--json` | Output as JSON |
| `--data-dir DIR` | Course data directory (contains slides/) |
| `--fail-on {error,warning}` | (since CLM {version}) Exit non-zero when findings reach this severity. Unset: legacy behavior (human output fails on errors; JSON exits 0). `error`: fail on errors in either mode. `warning`: fail on errors **or** warnings — the pre-commit-gate setting, so the cross-file `slide_id` / voiceover `for_slide` parity warnings block a commit. |
| `--summary` | (since CLM {version}) Roll findings up into a category/kind histogram with per-deck counts instead of a flat list — for corpus-scale validates that would otherwise print thousands of lines. |
| `--shipping-only` | (since CLM {version}) Directory only: restrict the walk to decks reachable from course specs (the shipping set), skipping archived / unreferenced decks so they don't drown the signal. |
| `--specs-dir DIR` | For `--shipping-only`: directory of `*.xml` specs to resolve the shipping set from. Default: `<course-root>/course-specs/`. |

`PATH` can be a single slide file, a topic directory, or a course spec XML file.

> `--shipping-only` resolves the shipping set with the same build-faithful logic
> as `clm spec decks`, and filters that resolved deck list to the decks under
> `PATH` — so it correctly includes non-`.py` decks (`.cs`, `.cpp`) that the
> plain directory walk currently misses.

Since CLM {version}, `clm validate slides/ --fail-on warning` is the
pre-commit gate: by default the `pairing` warnings (missing/divergent
`slide_id`, tag-parity asymmetry, the cross-file `slide_id` / voiceover
`for_slide` parity detectives) surface but exit 0, so a naive
`clm validate && git commit` lets them through. `--fail-on warning`
escalates the exit code so a hook fails on them; it governs the exit code
with `--json` too (without `--fail-on`, JSON mode always exits 0 for
backward compatibility).

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
| `slide`/`subslide` cell missing `slide_id` | `error` | An `error` since CLM 1.8 (was a `warning` through 1.7). Suggested fix: `clm slides assign-ids`. |
| DE/EN content/voiceover pair not adjacent (intervening lang-tagged cell) | `error` | An `error` since CLM 1.8 (was a `warning` through 1.7). Canonical layout: `[de] [en] [de voiceover] [en voiceover]`. Fix with `clm slides normalize`. |
| duplicate `slide_id` across slide groups | `error` | Group-aware: paired DE/EN cells sharing the EN-derived slug are not a duplicate. Bare-form comparison so `!intro` and `intro` collide. |
| voiceover/notes `slide_id` ≠ preceding `slide`/`subslide` anchor | `error` | Walk-back skips j2, code, shared (lang-less), and cross-language narrative cells. The j2 `header()` macro anchors `slide_id="title"` for narrative cells that follow it. |
| paired DE/EN slides carry mismatched bare `slide_id`s | `warning` | Suggested fix: `clm slides assign-ids --force`. |
| split pair `.de.py` / `.en.py` carry a different `slide_id` set or order | `warning` | **Cross-file** (issue #162): `slide_id` is the cross-language join key for voiceover `for_slide`, `clm slides unify`, and extract/inline. Route structural changes through `clm slides sync`; avoid per-file `clm slides assign-ids` on a split half. Runs on a directory/course validate, and on a single-file validate when the twin exists on disk. |
| split pair voiceover companions narrate different slides (`for_slide` set differs) | `warning` | **Cross-file** (issue #162, the both-language voiceover compatibility check): a narration cell's `for_slide` is the `slide_id` of the slide it covers, so the `.de` / `.en` companions (`voiceover_X.de.py` / `voiceover_X.en.py`) must narrate the same set of slides — otherwise one language ships with missing voiceover. A one-sided companion (one language has voiceover, the other none) is flagged too. Runs on a directory/course validate, and on a single-file validate when the twin exists on disk. |
| `slide_id` is not a valid kebab-case ASCII slug (≤30 chars) | `warning` | The leading `!` preserve marker is permitted and does not count toward the length cap. |

Since CLM {version}, the **bilingual** `pairing` sub-checks (DE/EN cell
count parity, per-pair tag/type consistency, and DE/EN adjacency) are
**skipped on single-language split files** (`*.de.py` / `*.en.py`) — a
split half legitimately carries cells of only one language, so these
checks would otherwise report a false `DE/EN cell count mismatch` on every
converted deck (issue #160). The per-file `slide_id` integrity checks (and
the `format` / `tags` groups) still run on split files unchanged, and the
cross-file shared-cell parity diff between a `.de.py` / `.en.py` pair is
still applied when validating a directory or course spec. Since CLM
{version} the cross-file **`slide_id` parity** check (issue #162) is applied
the same way — and additionally on a single-file validate when the twin
exists on disk, so the pre-commit gate and the PostToolUse path catch a
divergent join key. The companion **`for_slide` parity** check (the
both-language voiceover compatibility check) is applied alongside it, so a
split deck's `voiceover_X.de.py` / `voiceover_X.en.py` companions can't
silently narrate different slide sets. Bilingual decks (no `.de` / `.en`
suffix) are unaffected — the full pairing check still runs.

Since CLM {version}, the `tags` check group also verifies **workshop
scope** (issue #78). The `partial` output kind leaves a workshop's code
cells empty for live code-alongs; if the workshop scope is missing, the
build silently renders every code cell instead. A workshop is opened by
either a `workshop` tag or a slide-start cell whose `slide_id` begins with
`workshop-` (see `clm info spec-files`).

| Finding | Severity | Notes |
|---------|----------|-------|
| markdown `# Workshop …` heading with no workshop scope covering it | `warning` | Heading match is case-sensitive, tolerant of `#`-count and whitespace (`^#+\s*Workshop\b`). Continuation headings (e.g. `## Workshop (Continued)`) inside an already-open scope are *not* flagged. Suggested fix: add a `workshop` tag or a `workshop-…` slide_id. |

Since CLM {version}, the `format` check group also enforces **cell spacing**
(both `warning`s — non-breaking; `clm slides normalize` auto-fixes them):

| Finding | Severity | Notes |
|---------|----------|-------|
| cell is not separated from the previous cell by a blank line | `warning` | A blank line is required before every cell **except a j2 cell** — the title-header block (`# j2 … import header` immediately followed by `# {{ header(…) }}`) is tight-coupled and exempt. Cells run together are valid percent-format but render and diff poorly. Fix with `clm slides normalize`. |
| markdown cell body does not start with a blank comment line (`#`) | `warning` | A markdown cell should open `# %% [markdown]` / `#` / `# <content>`; the leading `#` is what makes content that starts with a bullet (or heading) render correctly. j2 cells (the title macro) are exempt; empty-body cells are skipped. Fix with `clm slides normalize`. |
| executable code appears before the first `%% ` cell marker (issue #253) | `warning` | (since CLM {version}) Code between the `# {{ header(…) }}` macro call and the first `# %%` cell has no cell marker, so jupytext folds it into the header cell. At build time it lands in the **title markdown** — silently dropped from a DE build (it rides the EN title in the bilingual macro) yet kept in the split DE half, so the bilingual and split builds diverge. A `warning`, not an `error`: the source still round-trips through `split`/`unify`. Fix with `clm slides normalize` (the `preamble_code` op wraps it in its own `# %%` cell). |

Quick mode (`--quick`) runs the slide_id checks because they walk cells
linearly and don't false-positive on in-progress edits. The workshop-scope
check runs in quick mode too. The DE/EN count/tag-mismatch checks remain
excluded from quick mode, as do the cell-spacing checks above (they would
fire on an in-progress markdown cell before the author has typed the
leading `#`).

Examples:

```bash
clm validate slides/module_010/topic_100_intro/slides_intro.py
clm validate slides/module_010/ --json
clm validate slides/module_010/topic_100_intro/ --quick
```

### `clm slides normalize`

*Removed in CLM 1.8: the flat alias `clm normalize-slides` no longer exists — use this group-qualified form.*

Normalize slide files by applying mechanical fixes: tag migration (`alt`→`completed`),
workshop tag insertion, DE/EN interleaving, slide ID auto-generation, **cell spacing**
(`cell_spacing`), and — since CLM {version} — **preamble-code wrapping**
(`preamble_code`).

The `cell_spacing` operation fixes the two formatting issues the `format`
validator now warns about: it inserts a blank line before every cell that lacks
one (except the tight-coupled j2 title-header block), and prepends a blank
comment line (`#`) to any markdown cell whose body starts directly with content.
It runs by default (part of `all`) and is idempotent.

The `preamble_code` operation (since CLM {version}) fixes issue #253: code that
sits between the `# {{ header(…) }}` macro call and the first `# %%` cell has no
cell marker of its own, so jupytext folds it into the header cell and — at build
time — into the **title markdown**, where it diverges between bilingual and split
builds. The op moves that code into its own `# %%` code cell (a shared,
language-neutral cell included in every build and copied verbatim to both split
halves), making the conversion render-neutral and finally executing the code as
code rather than rendering it as markdown text. It runs **first** (before the
other passes), is default-on (part of `all`), idempotent, and a strict no-op on
a conforming deck.

```
clm slides normalize [OPTIONS] PATH
```

| Option | Description |
|--------|-------------|
| `--operations TEXT` | Comma-separated operations: `preamble_code`, `tag_migration`, `workshop_tags`, `interleaving`, `slide_ids`, `cell_spacing`, `all` (default: `all`) |
| `--dry-run` | Preview changes without modifying files |
| `--canonicalize-start-completed` | Force `start`/`completed` cohesion pairs into the canonical DE/EN interleave, even when DE/EN code differs (e.g. localized identifiers). Run before `clm slides split` so `unify(split(deck)) == deck` holds byte-for-byte. Only affects the `interleaving` operation. |
| `--json` | Output as JSON |
| `--data-dir DIR` | Course data directory (contains slides/) |
| `--only bilingual\|split` | (since CLM {version}) Scope a **directory** run to only bilingual decks (no `.de`/`.en` tag) or only split halves — e.g. normalize the bilingual decks while leaving `.de`/`.en` pairs for `clm slides sync`. |
| `--exclude GLOB` | (since CLM {version}) Skip decks matching `GLOB`, matched against the full path **and** each path component (so `--exclude _archive` skips an `_archive/` directory). Repeatable. |
| `--shipping-only` | (since CLM {version}) Scope a directory run to decks reachable from course specs (the shipping set), skipping archived / unreferenced decks. |
| `--specs-dir DIR` | For `--shipping-only`: directory of `*.xml` specs. Default: `<course-root>/course-specs/`. |

The scoping options (`--only` / `--exclude` / `--shipping-only`) apply only to a
directory `PATH`; using them with a single file or a spec is an error. They
replace the old "run over everything, then `git checkout` the files you shouldn't
have touched" workaround.

Examples:

```bash
clm slides normalize slides/module_010/topic_100_intro/slides_intro.py
clm slides normalize slides/module_010/ --dry-run
clm slides normalize slides/module_010/ --operations tag_migration
clm slides normalize slides/module_010/ --operations slide_ids --json
# Fix only cell spacing (blank line between cells + markdown leading `#`).
clm slides normalize slides/module_010/ --operations cell_spacing
# Fix only preamble code (wrap code before the first cell into its own `# %%` cell)
clm slides normalize slides/module_010/ --operations preamble_code
# Pre-conversion: canonicalize start/completed order so the split round-trips exactly
clm slides normalize slides/module_010/topic_100_intro/ --operations interleaving --canonicalize-start-completed
# Scope: mint ids on bilingual decks only, skipping an _archive/ dir
clm slides normalize slides/ --operations slide_ids --only bilingual --exclude _archive
# Scope: only the decks that actually ship
clm slides normalize slides/ --shipping-only
```

### `clm slides assign-ids`

*Added in CLM {version}.*

Generate stable `slide_id` metadata for slide/subslide cells per the
EN-derived, kebab-case, ASCII policy. Cells in a DE/EN pair share the
same id (derived from the EN heading); voiceover/notes cells inherit
the id of the preceding slide.

> **Plumbing (since CLM {version}).** This command is **hidden** from
> `clm slides --help` and is intended for agents/scripts and one-off id fixes —
> it stays fully invocable by name. For everyday authoring, id minting happens
> inside the safe funnels and you do not need to call it directly:
> `clm slides sync` mints a shared id onto both halves of a split deck as it
> reconciles them, and `clm slides normalize` runs the same minting as one of
> its passes. Running assign-ids on a **single** split half can mint a
> divergent slug (#162) — prefer the funnels, or run it over a **directory**
> (EN-authority pair minting, see below).

Three-category policy:

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
- **code-derived** *(since CLM {version})* — a bare-expression code cell
  with no heading and none of the AST constructs above (e.g.
  `(1 + 1j) * (1 + 1j)`, `letters[0:3]`, `a == b`). **Refused by default**;
  opt in with `--accept-code-derived`, which slugs the cell's first real
  code line (`letters[0:3]` → `letters-0-3`). The scanner is
  comment-token-aware, so non-Python decks (`.cs`/`.cpp`/`.java`/`.ts`),
  which `ast` never parses, are completed too. Independent of
  `--accept-content-derived` — the bilingual→split conversion typically
  passes both.
- **no content** — cell where no extractor produces anything (empty
  cell, pure `<img>` without alt, pure-punctuation / `...` code,
  magic-only cells). **Hard refuse**; the author has to write
  `slide_id="…"` by hand, or pass `--llm-suggest` to let the LLM propose
  a title as a last resort.

Special cases:

- Title slides (j2 `header()` macro) anchor `slide_id="title"`
  automatically. No author input needed.
- An id prefixed with `!` (e.g. `slide_id="!intro"`) is the
  **preserve marker** — never regenerated, even under `--force`. The
  `!` is source-level only; references elsewhere use the bare form.
- **Split-file id consistency (since CLM {version}, issue #162).** `slide_id`
  is the cross-language join key, so the two halves of a split deck must agree
  on it. assign-ids keeps that automatically, two ways:
  - **Directory / course run** (`clm slides assign-ids slides/`) — a
    `*.de.py` / `*.en.py` pair is minted **EN-authority** across *both*
    halves at once: the slug derives from the EN heading and the same id is
    stamped on both, deterministic regardless of file order (the same policy
    as a bilingual file). A pair that is not byte-faithfully unifiable
    (divergent shared cells) falls back to the per-file path below.
  - **Single-file run** (`clm slides assign-ids slides_x.de.<ext>`) — when the
    twin exists on disk with a matching slide count, an **id-less** slide
    adopts the twin's `slide_id` for the positionally-corresponding slide
    instead of minting a divergent slug. When both halves are id-less the
    first-assigned half's slug wins (parity still holds; for EN-authority use
    the directory run or `clm slides sync`). Mismatched slide counts skip the
    reuse and leave the divergence for `clm validate`'s #162 detective.

```
clm slides assign-ids [OPTIONS] PATH
```

| Option | Description |
|--------|-------------|
| `--force` | Regenerate ids where the algorithm can produce one. `!`-prefixed ids and cells without a proposal are left untouched. |
| `--accept-content-derived` | Auto-accept proposals for the extractable category (no LLM). Bare-expression code cells and hard-refusal cells still refuse. |
| `--accept-code-derived` | (since CLM {version}) Auto-accept a first-code-line slug for bare-expression code cells the AST extractors can't name (`(1 + 1j) * (1 + 1j)` → `1-1j-1-1j`, `letters[0:3]` → `letters-0-3`). Comment-token-aware, so it works on non-Python decks (`.cs`/`.cpp`/`.java`/`.ts`). Genuinely empty / pure-punctuation / magic-only cells still refuse. Independent of `--accept-content-derived`. |
| `--llm-suggest` | Use the local LLM (Ollama, default model `qwen3:30b`) to propose a short title. Fires on both extractable cells (replacing the content-derived title when the LLM returns one) and on hard-refusal cells (last-resort fallback before refusing). Cached per `(content_hash, prompt_version, lang)` in the LLM cache. Falls back silently to refusal when Ollama is unreachable. |
| `--report-only`, `--dry-run` | List planned assignments and refusals without modifying any file. |
| `--llm-model TEXT` | Ollama model name (default: `qwen3:30b`). |
| `--ollama-url TEXT` | Base URL of the Ollama daemon (default: `$OLLAMA_URL` or `http://localhost:11434`). |
| `--llm-timeout SECONDS` | Per-call timeout (default: 120s — cold-load on a 30B model can exceed 60s). |
| `--cache-dir PATH` | Directory for the LLM cache. Lookup order: flag → `$CLM_CACHE_DIR` → `tool.clm.cache_dir` in `pyproject.toml` → `<cwd>/.clm-cache/`. |
| `--only bilingual\|split` | (since CLM {version}) Scope a **directory** run to only bilingual decks (no `.de`/`.en` tag) or only split halves — e.g. `--only bilingual` mints bilingual decks while leaving `.de`/`.en` pairs for `clm slides sync`. |
| `--exclude GLOB` | (since CLM {version}) Skip decks matching `GLOB`, matched against the full path **and** each path component (so `--exclude _archive` skips an `_archive/` dir). Repeatable. |
| `--shipping-only` | (since CLM {version}) Scope a directory run to decks reachable from course specs (the shipping set). |
| `--specs-dir DIR` | For `--shipping-only`: directory of `*.xml` specs. Default: `<course-root>/course-specs/`. |
| `--data-dir DIR` | Course data directory (contains `slides/`); used to resolve the `--shipping-only` scope. |
| `--report-refusals` | (since CLM {version}) Emit a hand-authoring **worklist** of the refusals (hard ones first) instead of the assignment listing — the cells that still need a `slide_id`. |
| `--context` | (since CLM {version}) With `--report-refusals`, include each refused cell's marker, body, and the nearest preceding `slide_id`/heading so you can author an id in place. Implies `--report-refusals`. |
| `--json` | Emit a JSON report instead of human-readable lines. |

The scoping options (`--only` / `--exclude` / `--shipping-only`) apply only to a
directory `PATH` and replace the old "run over everything, then `git checkout`
the files you shouldn't have touched" workaround. Split pairs are still detected
*within* the scoped set, so EN-authority parity minting across a `.de`/`.en` pair
is preserved; if only one half survives the filter, that half takes the per-file
twin-aware path and the absent twin is never written.

`--report-refusals` turns the run into a **worklist** for the cells that could
*not* be assigned automatically: hard refusals (no heading and no extractable
content — only a hand-authored id will do) sort first, then soft refusals
(extractable, carrying a proposed slug). Add `--context` to attach each refused
cell's marker line, full body, and the nearest preceding `slide_id`/heading so an
author or agent can write the id without opening the file. The worklist honors the
same scoping flags, and `--json` emits it as structured data. It replaces the
throwaway "dry-run JSON → script that re-extracts cell bodies and surrounding
context" step that course conversions repeatedly hand-rolled.

Exit codes: `0` clean, `1` soft refusals (extractable cells awaiting
author input), `2` at least one hard refusal.

Examples:

```bash
clm slides assign-ids slides/module_010/topic_100/slides_intro.py --report-only
clm slides assign-ids slides/module_010/ --accept-content-derived
# Fully automatable bilingual→split prep: also id bare-expression code cells
clm slides assign-ids slides/module_110_basics/ --accept-content-derived --accept-code-derived
clm slides assign-ids slides/module_010/topic_100/slides_intro.py --llm-suggest
clm slides assign-ids slides/module_010/ --force        # regenerate all derivable ids
# Scope: mint only the bilingual decks, leaving split pairs for `clm slides sync`
clm slides assign-ids slides/ --accept-content-derived --only bilingual --exclude _archive
# Scope: only the decks that actually ship
clm slides assign-ids slides/ --accept-content-derived --shipping-only
# Worklist of cells that still need a hand-authored id, with body + context
clm slides assign-ids slides/ --report-only --report-refusals --context
```

### `clm slides slug-report`

*Added in CLM {version}.*

After a bulk `clm slides assign-ids --accept-content-derived` mints thousands
of ids, **most are fine but a minority are low-information** — single generic
tokens (`data` / `true` / `value`), very short code-identifier-shaped slugs
(`cp` / `df` / `os`), or slugs that hit the 30-char cap and lost their trailing
words. `slug-report` flags just those so you review the minority instead of
scanning every id.

```bash
clm slides slug-report [OPTIONS] PATH
```

`PATH` is a directory of slide files **or** a course spec `.xml` (resolved to
the decks it pulls in, via the same build-faithful logic as `clm spec decks`).

| Option | Description |
|--------|-------------|
| `--min-severity low\|medium\|high` | Only show findings at or above this confidence (default `low` = all). `high` = very-short / generic only. |
| `--only bilingual\|split` | Scope a **directory** scan to only bilingual decks (no `.de`/`.en` tag) or only split halves. |
| `--exclude GLOB` | Skip decks matching `GLOB` (matched against the full path **and** each path component, so `--exclude _archive` skips an `_archive/` dir). Repeatable. |
| `--shipping-only` | Scope a directory scan to decks reachable from course specs (the shipping set). |
| `--specs-dir DIR` | For `--shipping-only`: directory of `*.xml` specs. Default: `<course-root>/course-specs/`. |
| `--data-dir DIR` | Course data directory (contains `slides/`); used for a spec `PATH` or `--shipping-only`. |
| `--json` | Emit a JSON report (per-finding issues + `by_severity` / `by_issue` histograms). |

Quality signals — a flag means "worth a look", **not** "wrong" (`introduction`
is a single token and perfectly good, so it's only `low`):

| Signal | Meaning | Severity |
|---|---|---|
| `very_short` | one token ≤ 3 chars (`cp` / `df` / `os`) | high |
| `generic` | one content-free token (`data` / `true` / `value`) | high |
| `possibly_truncated` | length hit the 30-char cap; trailing words likely lost | medium |
| `single_token` | one token (often fine, e.g. `introduction`) | low |

Only slide/subslide *start* cells are inspected (narrative cells inherit their
slide's id), and a bilingual deck's DE/EN twins — which share an id — yield a
single finding. The exit code is always `0`; this is a report.

Examples:

```bash
clm slides slug-report slides/module_010/                       # everything flagged
clm slides slug-report slides/ --min-severity high              # just the high-confidence ids
clm slides slug-report course-specs/python-course.xml --json    # only the decks that ship
clm slides slug-report slides/ --exclude _archive --shipping-only
```

### `clm slides coverage-report`

*Added in CLM {version}.*

Report **DE/EN completeness** per deck. Among count-mismatch validation errors,
two very different situations hide — a deck that exists in only one language
(needs *translation*, a big job) and a bilingual deck off by a cell or two (a
small *alignment* fix). This separates them by counting `lang="de"` vs
`lang="en"` slide cells per deck.

```
clm slides coverage-report [OPTIONS] PATH
```

`PATH` is a directory of slide files **or** a course spec `.xml` (resolved to
its shipping decks). Each deck unit is classified:

| Status | Meaning |
|---|---|
| `de_only` | DE present, EN missing — needs EN translation |
| `en_only` | EN present, DE missing — needs DE translation |
| `imbalanced` | both present, counts differ — an alignment fix (shown with `Δ`) |
| `balanced` | equal DE/EN counts (not listed unless `--status balanced`) |

Split `*.de.py` / `*.en.py` halves are scored as **one pair**; a half whose
twin is absent counts the missing language as zero (so a lone `.de.py` reads as
`de_only`). Only slide/subslide cells are counted — narrative (voiceover/notes)
cells inherit their slide, so one-language speaker notes don't skew the result.

| Option | Description |
|--------|-------------|
| `--status de_only\|en_only\|imbalanced\|balanced` | Show only decks with this status. |
| `--only bilingual\|split` | Scope a **directory** scan to only bilingual decks or only split halves. |
| `--exclude GLOB` | Skip decks matching `GLOB` (full path **and** each component; repeatable). |
| `--shipping-only` | Scope a directory scan to decks reachable from course specs. |
| `--specs-dir DIR` | For `--shipping-only`: directory of `*.xml` specs. Default: `<course-root>/course-specs/`. |
| `--data-dir DIR` | Course data directory (contains `slides/`). For a spec `PATH` or `--shipping-only`. |
| `--json` | Emit a JSON report (`by_status` counts + per-deck `de_cells`/`en_cells`/`delta`/`status`). |

The exit code is always `0` — this is a report. Examples:

```bash
clm slides coverage-report slides/module_010/                   # everything not balanced
clm slides coverage-report slides/ --status de_only             # just the untranslated decks
clm slides coverage-report course-specs/python-course.xml --json
clm slides coverage-report slides/ --exclude _archive --shipping-only
```

### `clm slides sync`

*Added in CLM {version}.*

Single-language authoring sync for split-format decks
(`<deck>.de.<ext>` / `<deck>.en.<ext>`, the layout produced by
`clm slides split`). After an author edits **one** half of a pair, this
command brings the *other* half into sync in a single pass: edits are
propagated, brand-new slides are translated and inserted, removed
slides are dropped, reorders are mirrored, and a shared `slide_id` is
minted onto both decks as it goes.

**Pairing guard (since CLM {version}).** Before anything is read or written,
sync checks that `DE_PATH` and `EN_PATH` are the two halves of **one** deck —
one `.de` half and one `.en` half of the same name (the routing prefix is not
required, so `apis.de.py` / `apis.en.py` is fine). A **swapped** order
(`<deck>.en.<ext>` first) is auto-corrected with a note; passing the **same file**
twice, **two same-language** halves, **two different decks**, or a path that is
**not a split half** at all (a bilingual or untagged file) is rejected with a
usage error before any LLM call or write. This closes the #162 footgun where a
mismatched pair could silently produce a divergent or no-op sync.

**Single-path form (since CLM {version}).** `EN_PATH` is **optional**: pass just
one half and the twin is derived from disk — `clm slides sync slides_x.de.<ext>`
syncs the pair. You may also pass the **bilingual deck stem** (`slides_x.py`, no
`.de`/`.en` tag) when it still exists on disk, and both halves are derived. The
derivation is prefix-agnostic (so `apis.de.py` works) and the resolved pair is
still run through the pairing guard above. A missing twin is a clear usage error
(exit 2) — sync never invents a translated half. To create a missing
other-language half from scratch, use **`clm slides translate`** (below); to split
an existing bilingual deck into halves, run `clm slides split` first. The two-path
form is unchanged.

**Batch mode (since CLM {version}).** `DE_PATH` may also be a **directory** —
every `.de`/`.en` deck pair under the tree is synced in one pass. Enumeration is
prefix-agnostic (un-prefixed decks like `apis.de.py` count too) and descends the
whole subtree; voiceover companions (`voiceover_*`) are ignored. A half with **no
twin** under the tree is **skipped with a warning**, never synced against a
phantom empty twin. The sweep **continues past a failing pair** (recording it as
errored) and the process exit code is the **worst** over all pairs (`0` clean <
`1` review < `2` error). A summary one-liner per pair plus a final rollup
(`N pair(s): X clean, Y review, Z errored`) is printed; `--dry-run` and
`--explain` behave as for a single pair, applied to each. A **writing** directory
run requires **`--yes`** (or an interactive confirm), since it writes to every
pair at once; `--dry-run` / `--explain` directory runs are unprompted.
`--interactive` is single-pair only (it walks one pair's proposals) and is
rejected with a directory. Do **not** pass a second path with a directory.
Since CLM {version} a sweep prints a `[i/N] <deck> …` **progress header** per
pair (stderr), and a writing run prints a short stderr tick per LLM call
(`· reconciling …` / `· translating …`) so a long sync is visibly alive;
progress is suppressed under `--json`.

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

**Tag-only edits (since CLM {version}).** Tags are language-independent, so a
synced pair carries identical tag sets per cell. A one-sided tag-only edit
(e.g. adding `keep`/`alt`) on an id'd cell, or on an **id-less localized**
cell, is **mirrored** to the twin — on both the watermark and the committed
(git-HEAD) baseline, and **also across a concurrent slide-group reorder**
(the twin is located reorder-invariantly via the baseline, by body hash).
Tag shapes sync cannot mirror are **errored, never silently dropped** (the
watermark holds and nothing is written): a tag edit on a **language-neutral**
cell (shared verbatim across the halves — apply the tag change to both halves
yourself, the bodies stay untouched), a tag edit among **byte-identical
duplicate** id-less cells (nothing can anchor which twin to retag), tags
changed on **both** twins, and an under-reorder tag edit coinciding with
other structural changes (add/remove) in the same pass — sync those in two
steps.

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

- **Code-only edits propagate — on the first sync too (Issue #269).** Editing
  *only* a language-neutral code (or markdown) cell on one side — no narrative or id
  change — is detected (the anchor diff sees which half drifted) and copied verbatim
  to the twin. This now also fires on the **cold-start (git `HEAD`) baseline** — the
  first sync of a freshly-split pair, before any watermark exists. Previously the
  neutral-cell diff ran only against a watermark, so such an edit on a first sync was
  silently dropped and reported "decks already consistent".
- **Id-less localized cell edits propagate (Issue #269).** A `lang=` cell with **no**
  `slide_id` (a one-off demo / output cell) edited on one side is re-translated onto
  the twin — both bare statements and named constructs (`def` / `class` / `import`),
  under either baseline. Previously an id-less-localized-only edit had no direction
  signal and was silently dropped.
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
- **The deck header is never silently dropped (Issue #269).** Sync does **not**
  auto-translate the j2 deck header (`{{ header_xx(…) }}`) — it is language-specific
  and each half keeps its own. But a header edited on **one** half only is now an
  **error** (the watermark holds, exit 2) telling you to update the other header (or
  run `clm slides translate`), instead of being reported "consistent". A header
  updated on **both** halves is accepted.
- **A shared-cell parity fail-safe guards the invariant (Issue #269).** After an
  otherwise-clean apply, the language-neutral cells of the two halves must be
  byte-identical (the `unify` invariant); if any still differ, sync **errors** and
  holds the watermark rather than report the decks consistent — so an
  un-propagatable shared-cell change is always surfaced, never silently banked.
  Since CLM {version} the error **names the diverging cell(s)** — the cell text
  present on one half but missing on the other (or the first out-of-order cell),
  and for id-less localized cells the slide group and cell kind — so you can
  locate the divergence without a manual diff.
- **A new slide group added next to a neutral cell is placed correctly (since CLM
  {version}).** Inserting a new id'd slide (a localized markdown cell plus its
  language-neutral code cells) right after a language-neutral or id-less neighbour
  used to land the new group in the wrong inter-group slot on the other half and
  trip the parity fail-safe above; sync now reconciles slide-group **order**
  against the propagation source, so the insertion propagates cleanly.
- **Reordering groups on one half while editing a neutral / id-less cell on the
  other is surfaced, not silently dropped (since CLM {version}, Issue #282).** If
  one half reorders slide groups (a *move*) while the other half independently
  edits a language-neutral or id-less-localized cell, the two changes flow in
  opposite directions and a single sync pass cannot apply both. (The reorder
  shuffles the source half's cell order, which the drift detectors would otherwise
  mistake for an edit — masking the real one on the other half, and for two or more
  reordered cells even auto-healing over it on disk.) Sync now **errors** and holds
  the watermark, leaving both halves untouched on disk, instead of overwriting the
  edit. Reconcile by hand (apply the edit and the reorder on the same half, or sync
  them in separate steps) and re-run.

Use `--explain` to see the anchor-level view (per-cell anchor + drift, the
propagation direction, drifted ids) for any pair.

**Translation conventions (glossary).** A brand-new slide on the add path is
translated by the same model `clm slides translate` uses, and it honors the same
**glossary** — a Markdown style note + term glossary appended to the translation
prompt (keep "Dictionary", address the reader with "Sie"). Because sync is
**bidirectional**, the glossary is resolved **per target language**: a new EN
slide translated to DE uses the **DE** conventions and a new DE slide translated
to EN uses the **EN** conventions. Each is auto-discovered as
`clm-glossary.<lang>.md` walking up from the deck (a `clm-glossary.de.md` next to
your slides is found automatically), or supplied explicitly with `--glossary-de` /
`--glossary-en`. A language with no glossary simply translates with no conventions
(unchanged from before). In **batch (`DIR`) mode** the translator is shared across
the sweep, so the glossary is resolved once from the directory root.

```
clm slides sync [OPTIONS] DE_PATH [EN_PATH]
clm slides sync [OPTIONS] DIR          # batch: sync every pair under DIR
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
| `--glossary-de PATH` | Translation conventions (Markdown: a style note + term glossary) for **German** targets — a brand-new EN slide translated to DE on the add path. Default: auto-discover `clm-glossary.de.md` walking up from the deck. |
| `--glossary-en PATH` | Translation conventions for **English** targets — a brand-new DE slide translated to EN on the add path. Default: auto-discover `clm-glossary.en.md` walking up from the deck. |
| `--verify-cold-pairs` / `--no-verify-cold-pairs` | **Bootstrap and reconcile split-pair `slide_id`s, gated by a cheap correspondence check (default on when an OpenRouter/OpenAI key is set).** A never-id'd cold pair is **minted** a shared id per slide; a half-id'd pair's id-less half **adopts** the id'd half's ids; a committed pair sharing some ids but giving one slide a *divergent* id on each half is **reconciled** — `sync` rewrites the divergent id so both halves share one (EN-authority), surfaced as a `reconcile` proposal (#228). Each is applied only after a cheap LLM (Haiku, via OpenRouter) confirms the two halves actually correspond. With `--no-verify-cold-pairs` (or no key) such a pair is **refused** instead — sync one direction at a time, or run `clm slides assign-ids`. |
| `--llm-recover` | **Opt into the bounded-LLM recovery tier (default off).** When the deterministic id-migration is stuck on an *ambiguous* drifted `slide_id` (a function renamed while a cell was split, an unresolvable tie), ask Claude (Opus, via OpenRouter) for a **validated, body-free** id↔cell alignment. Without this flag such a region is left untouched and re-surfaces next run. The model only ever sees content anchors (construct + hash + id), never cell source, and its map is validated (it can never drop a stable `slide_id`) before any header is written. Needs `$OPENROUTER_API_KEY` / `$OPENAI_API_KEY`. |
| `--recovery-model TEXT` | OpenRouter model for `--llm-recover` alignment (default: `anthropic/claude-opus-4`). |
| `--cache-dir PATH` | Directory holding the structural watermark. Lookup order: flag → `$CLM_CACHE_DIR` → `tool.clm.cache_dir` in `pyproject.toml` → `<cwd>/.clm-cache/`. |
| `--no-cache` | Do not read or write the watermark. Every run then re-derives its baseline from git `HEAD` and no synced state is persisted. |
| `--no-env-file` | Do not auto-load a `.env` file. By default sync loads the first `.env` found above each deck (without overriding already-set variables), so keys kept in the project `.env` reach the judge/translator. |
| `--yes`, `-y` | **Batch (`DIR`) only.** Confirm a writing directory run without the interactive prompt. A directory apply writes to every pair under the tree, so it is gated; `--dry-run` / `--explain` directory runs are unprompted. Ignored for a single pair. |
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

In **batch (`DIR`) mode** the `--json` report is instead an envelope
`{ "mode", "root", "exit_code", "pairs": [ … ] }`, where each entry of
`pairs` is exactly one single-pair object as above (so a tool can treat
`pairs[i]` like an individual `clm slides sync --json`); a pair that errored
appears as `{ "de_path", "en_path", "mode", "exit_code", "error" }`. A writing
batch with `--json` requires `--yes` (there is no prompt to fall back to).

Examples:

```bash
# Edit intro.de.py, then bring intro.en.py into sync (writes to the tree).
clm slides sync slides/topic/intro.de.py slides/topic/intro.en.py

# Single-path: pass one half, the twin is derived from disk.
clm slides sync slides/topic/intro.de.py

# Batch: preview every pair under a directory (unprompted, writes nothing).
clm slides sync slides/ --dry-run

# Batch: sync every pair under a directory (writing → needs --yes).
clm slides sync slides/ --yes

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

### `clm slides translate`

*Added in CLM {version}. Alias: `clm slides bootstrap`.*

Cold-start translation of a **single-language** deck into its other-language
split half. When an author has written only `slides_x.de.<ext>`, this synthesizes
`slides_x.en.<ext>` (and vice-versa) as a complete translation of the whole deck —
the one thing `clm slides sync` deliberately refuses to do (sync only fills
per-cell gaps inside an *already-existing* pair). After the twin exists, keep the
two halves in step with `clm slides sync`; run `clm slides unify` for a single
bilingual file.

**Code is mostly not translated — the `lang` tag decides.** A cell with **no**
`lang` attribute is *shared* and copied **byte-for-byte** into both halves; only
**`lang`-tagged** cells are translated. Idiomatic code carries no `lang` tag, so
it is copied verbatim; a code cell whose string literals / comments are shown to
the learner carries `lang=` and is translated through a prompt that keeps every
identifier byte-identical. This is the same model `clm slides sync` and the
validator already use — there is no new marker.

**Dispatch (idempotent by design).** If the other-language half is **absent**,
the deck is bootstrapped: the whole deck is translated, shared cells copied,
EN-authority shared `slide_id`s minted onto **both** halves, and the sync
watermark recorded. If the twin is **already present**, the command degrades to
an incremental `clm slides sync` (it never re-translates the whole deck). So
running `clm slides translate` twice is safe — the second run is a clean sync
no-op and the deck is never doubled. Use `--force` to re-bootstrap over an
existing twin.

**Direction** is inferred from the source half's `.de` / `.en` tag (`.de.py` →
produces `.en`). Override with `--to en|de`. The source **must** be one split
half: a bilingual deck stem (no tag) is rejected with a hint to run
`clm slides split` first.

**Voiceover companion in lockstep.** If the source half has a `voiceover_*`
companion, it is translated alongside the deck into the matching
`voiceover_*.<lang>.py` (in the same `voiceover/` subdir or sibling location),
preserving each cell's `for_slide` / `vo_anchor` anchors. An existing target
companion is left untouched unless `--force`.

**Key and `.env`.** Translation needs `$OPENROUTER_API_KEY` (or
`$OPENAI_API_KEY`); the command walks up from the deck and loads the first `.env`
it finds (skip with `--no-env-file`). On the **bootstrap** path a missing key is
a hard stop — the command exits `1` and writes nothing (a whole untranslated deck
is useless), unlike sync's per-cell defer. `--dry-run` uses no key and no LLM.

**Translation conventions (glossary).** Point `--glossary` at a Markdown file (a
style note plus a term glossary) to pin a target-language register and keep or
translate technical terms consistently across the deck; the text is appended to
the translation system prompt. If `--glossary` is omitted, the command
auto-discovers `clm-glossary.<target-lang>.md` walking up from the deck (the same
walk-up as `.env`), so a course keeps its glossary next to its slides and needs
no flag. The guidance is folded into the translation cache key: editing the
glossary invalidates affected entries by cache miss, while decks translated
without a glossary keep the bare key (no flag-day invalidation).

```
clm slides translate [OPTIONS] SOURCE
clm slides bootstrap [OPTIONS] SOURCE   # alias
```

| Option | Description |
|--------|-------------|
| `--to [en\|de]` | Target language. Default: the opposite of SOURCE's `.de`/`.en` tag. Override when a source mixes/omits `lang` tags. |
| `--dry-run` | Preview only: show the target path and how many cells would be translated vs copied (and the companion), and write nothing. Uses no LLM and no API key. |
| `--force` | Overwrite an existing twin (and its companion) by re-bootstrapping. Without it, an existing twin degrades to an incremental sync. |
| `--translation-model TEXT` | OpenRouter model used to translate the deck (default: `anthropic/claude-sonnet-4-6`). Needs `$OPENROUTER_API_KEY` / `$OPENAI_API_KEY`. |
| `--glossary PATH` | Translation conventions file (Markdown: a style note + term glossary) appended to the translation prompt. Default: auto-discover `clm-glossary.<target-lang>.md` walking up from SOURCE's directory. |
| `--provider [openrouter\|local]` | Edit-judge backend for the *delegated-sync* path (when the twin already exists); unused on the bootstrap path. Overridable with `$CLM_SYNC_PROVIDER`. |
| `--llm-model TEXT` | Model for the delegated-sync edit judge (default `anthropic/claude-sonnet-4-6` for openrouter). |
| `--cache-dir PATH` | Directory holding the translation + watermark caches. Lookup: flag → `$CLM_CACHE_DIR` → `tool.clm.cache_dir` → `<cwd>/.clm-cache/`. |
| `--no-cache` | Do not read or write the translation / watermark caches. |
| `--no-env-file` | Do not auto-load a `.env` file. |
| `--json` | Emit a JSON report instead of human-readable lines. |

Exit codes: `0` wrote the new half (or the delegated sync was clean), `1` the
delegated sync left something for review **or** no API key was available on the
bootstrap path (nothing written), `2` a hard error (the source is not a single
split half, or the deck could not be translated — nothing is written).

The `--json` report carries `action` (`bootstrapped` / `synced`), `source`,
`target`, `source_lang`, `target_lang`, the `companion` (`action`, `source`,
`target`) or `null`, `watermark_recorded`, and — for a bootstrap —
`cells_translated`, `cells_copied`, `ids_assigned`. `--dry-run --json` carries
`mode: "dry-run"`, the `action` that *would* run, and `cells_translatable` /
`cells_copied` counts.

Examples:

```bash
# Author wrote only slides_x.de.py — create the English half.
clm slides translate slides/topic/slides_x.de.py

# Preview without translating (no key needed).
clm slides translate slides/topic/slides_x.de.py --dry-run

# Force the direction (e.g. a source that mixes/omits lang tags).
clm slides translate slides/topic/slides_x.de.py --to en

# Re-bootstrap over an existing (e.g. stale) twin.
clm slides translate slides/topic/slides_x.de.py --force

# After bootstrapping, keep the halves in step, or merge to a bilingual file.
clm slides sync slides/topic/slides_x.de.py
clm slides unify slides/topic/slides_x.de.py
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

Split a bilingual `.py` slide file into `<basename>.de.<ext>` and
`<basename>.en.<ext>` companions. Cells with `lang="de"` go to the DE
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
escalating to error in CLM 1.8) — `unify` pairs adjacent DE/EN cells
by matching id. Since CLM {version} the parser recognises the deck's
own comment token (`# %%` for Python/Rust, `// %%` for C#/C++/Java/TS),
so split/unify and the rest of the authoring tooling work on every
supported prog_lang — the token is derived from the file extension.

**Preamble code (issue #253).** Since CLM {version}, `split` emits a `warning:`
(to stderr; does not fail) when SOURCE has executable code between the
`# {{ header(…) }}` macro call and the first `# %%` cell. The split itself stays
byte-identical (the code is copied to both halves), but such code folds into the
**title markdown** at build time and is **not render-neutral** between the
bilingual and split forms. `split` never rewrites the source — run
`clm slides normalize` (the `preamble_code` op) first to wrap the code in its own
`# %%` cell, then re-split.

**Voiceover companion.** If SOURCE has a sibling voiceover companion
(`slides_<name>.py` → `voiceover_<name>.<ext>`), `split` splits it in
lockstep into `voiceover_<name>.de.<ext>` / `voiceover_<name>.en.<ext>`,
routing each narration cell by its `lang` and preserving `for_slide` /
`vo_anchor` verbatim. Without this the companion would be orphaned — the
build would find no companion next to either split half. `--force`
covers overwriting existing companion halves, and the refusal is atomic
(if any deck or companion target exists without `--force`, nothing is
written). Splitting a deck that has no companion creates no
`voiceover_*` files.

### `clm slides unify`

The inverse of `clm slides split`. Combine `<basename>.de.<ext>` and
`<basename>.en.<ext>` into the bilingual `<basename>.<ext>` companion. Pairs
adjacent DE/EN cells by matching `slide_id`, treats shared cells as
alignment points (must be byte-identical between the two inputs —
divergent shared content is an error), and rebuilds the bilingual
`# {{ header("DE", "EN") }}` macro from the split forms.

```
clm slides unify [OPTIONS] DE_SOURCE EN_SOURCE
```

| Option | Description |
|--------|-------------|
| `--target FILE` | Explicit bilingual target path. Defaults to the basename shared by the two sources (e.g. `foo.de.<ext>` + `foo.en.<ext>` → `foo.<ext>`). |
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

**Voiceover companion.** If the pair has voiceover companions
(`voiceover_<name>.de.<ext>` / `voiceover_<name>.en.<ext>`), `unify` recombines
them in lockstep into `voiceover_<name>.<ext>` — the inverse of `split`'s
companion split, byte-identical. The recombined companion is written to the
**same directory** the split companions lived in (a `voiceover/` subdirectory
stays foldered; see `clm slides tidy`). `--force` also covers overwriting an
existing companion target.

### `clm slides tidy`

Relocate a topic's authoring **sidecars** between the flat and foldered
layouts. Sidecars are the files that are *not* core source and never reach
output: voiceover companions (`voiceover_*.<ext>`) and HTTP-replay cassettes
(`*.http-cassette.yaml`). `tidy` moves them into per-type subdirectories so a
topic directory holds only the `slides_*.<ext>` sources and genuine output
companions (`img/`, `drawio/`):

```
topic_070_rag_introduction/
├── cassettes/      ← *.http-cassette.yaml      (was: loose in the topic dir)
├── voiceover/      ← voiceover_*.<ext>         (was: loose in the topic dir)
├── drawio/  img/   ← output companions (unchanged)
└── slides_010_*.de.py  slides_010_*.en.py      ← core sources
```

`--layout sibling` flattens the sidecars back out. Both layouts are fully
supported everywhere (build, `extract`/`inline`/`sync`, `split`/`unify`,
`validate`); `tidy` is just the bulk reorganizer. The cassette folder is
`cassettes/`; the historical `_cassettes/` is still read and is **consolidated**
into `cassettes/` by `--layout subdir`.

```
clm slides tidy [OPTIONS] PATH
```

`PATH` may be a single slide/sidecar file, a topic directory, or a whole course
tree (walked recursively).

| Option | Description |
|--------|-------------|
| `--layout [subdir\|sibling]` | Target layout. `subdir` (default) moves sidecars into `voiceover/` / `cassettes/`; `sibling` flattens them back. |
| `--dry-run` | Print the planned moves/deletes without touching any file. |
| `--voiceover` / `--no-voiceover` | Include/exclude voiceover companions (default: include). |
| `--cassettes` / `--no-cassettes` | Include/exclude cassettes and the pruning of transient staging markers (default: include). |
| `--no-git` | Use plain file moves instead of `git mv` for tracked files. |
| `--json` | Emit a JSON report. |

Behavior:

- **Moves** use `git mv` for tracked files (history follows the file), falling
  back to a plain move for untracked files or outside a repo.
- **Transient** cassette staging markers (`*.http-cassette.yaml.staging-*` and
  their `.completed` companions) are **deleted**, not moved — they regenerate.
- A file already at its target is a **no-op** (the command is idempotent).
- A sidecar present in **both** layouts is a **conflict**: that one move is
  skipped (nothing is clobbered) and the command exits **2**. Reconcile the
  duplicate (`clm validate` flags it too) and re-run.
- A `voiceover/` / `cassettes/` / `_cassettes/` directory emptied by a flatten
  is removed.

Exit codes: `0` done (or dry-run printed); `2` one or more conflicts were
skipped.

```bash
clm slides tidy slides/module_550/topic_070 --dry-run
clm slides tidy slides/module_550/topic_070            # -> subdir layout
clm slides tidy slides --layout sibling                # flatten a whole tree
clm slides tidy slides/module_550 --no-cassettes       # voiceover companions only
```

### `clm slides language-view`

*Removed in CLM 1.8: the flat alias `clm language-view` no longer exists — use this group-qualified form.*

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

*Removed in CLM 1.8: the flat alias `clm suggest-sync` no longer exists — use this group-qualified form.*

Compare a slide file against git HEAD and detect asymmetric bilingual edits.
Suggests which cells need translation updates. Does not modify the file.

> **Plumbing (since CLM {version}).** This command is **hidden** from
> `clm slides --help` (it stays invocable by name and as the `suggest_sync` MCP
> tool). It is a read-only suggester for the pre-split **bilingual** layout
> (de/en cells co-located in one `.py`). For split-format decks
> (`<deck>.de.<ext>` / `<deck>.en.<ext>`) use **`clm slides sync`**, which reconciles
> the pair and writes the changes. Two `sync`-named commands on the everyday
> surface was a source of confusion — `sync` is the canonical funnel.

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

*Removed in CLM 1.8: the flat alias `clm extract-voiceover` no longer exists — use this group-qualified form.*

Extract voiceover and notes cells from a slide file to a companion
`voiceover_*.<ext>` file, linked via `slide_id`/`for_slide` metadata.
Content cells without `slide_id` get auto-generated IDs before extraction.

Since CLM {version}, that ID generation is **twin-aware** on a split half
(`*.de.py` / `*.en.py`): when the sibling exists on disk with a matching slide
count, an id-less slide adopts the twin's `slide_id` instead of minting a
divergent slug (the #162 defensive). This keeps `de_id == en_id` when you
extract the two halves separately, so their companions' `for_slide` sets agree
— without it, per-language extraction would mint independent slugs and one
language would silently ship with missing narration (which `clm validate`'s
#162 detectives now flag). Bilingual decks are unaffected.

Since CLM {version} (#242), a voiceover for the **title slide** — the one
generated by the j2 `header()` / `header_de()` / `header_en()` macro, which
carries no `slide_id` of its own — is anchored by the conventional
`for_slide="title"`. `extract` recognizes the title macro and stamps it; the
build merge and `clm voiceover inline` anchor it back to the title slide, so a
title greeting round-trips and builds in companion form exactly as it does
inline. Companions extracted before the fix (which carried `slide_id="title"`
with no `for_slide`) still merge — no re-extract needed. Since CLM {version}
(#246), the greeting's **exact position** within the opening segment is also
preserved: a greeting authored *before* the title slide's trailing `keep`/code
cells gets a title-macro `vo_anchor` (`tm:title#0`) so the merge restores it
right after the title slide rather than at the end of the title group. Legacy
companions with no `vo_anchor` keep the group-end placement.

**Paired extract (auto-pairing), since CLM {version}.** When `FILE` is a split
half (`<deck>.de.<ext>` / `<deck>.en.<ext>`) whose twin exists on disk, both
companions are extracted in **one op** by default: the two halves are first
minted with **EN-authority** `slide_id`s across both at once (the slug comes
from the EN heading, stamped identically on both halves), then each half is
extracted, and all writes commit atomically. This is stronger than extracting
the halves one at a time — the `for_slide` sets agree *by construction* and the
result is independent of which half you point at. The routing prefix is not
required, so `apis.de.py` / `apis.en.py` pairs too. Pass `--single` to extract
only `FILE`'s own companion (the legacy per-half behavior); `--both` forces the
paired form and errors if there is no twin. If the two halves are not
structurally alignable (divergent shared cells / mismatched cell count), the
paired extract **refuses** rather than risk divergence — reconcile them first
(e.g. `clm slides sync`). A bilingual deck (no `.de`/`.en` twin) always extracts
a single companion. The `--json` output for a paired extract carries
`"paired": true` and a `"companions"` array (one entry per half); a single
extract keeps the flat object.

Since CLM {version}, each extracted cell also records a `vo_anchor`
attribute identifying its **immediate predecessor cell** — `id:<slide_id>`
when that cell carries an id, otherwise `fp:<body-fingerprint>` — with a
trailing `#<n>` occurrence ordinal to disambiguate repeated cells in the
same slide group. `vo_anchor` lets `clm voiceover inline` restore each
voiceover to its **exact** original position rather than to the end of its
slide group. It is body-only and occurrence-qualified, so editing a
sibling cell's tags, inserting unrelated slides, or the build's blank-line
cleanup between extract and inline does not move the voiceover.

Since CLM {version} (#247), a j2 macro cell embedded *mid* slide-group — an
inline widget, say — is also an eligible anchor. A voiceover authored after
such a cell anchors to it (by body fingerprint, which is stable because the
companion merge runs *before* j2 expansion) and is restored to its slot
*after* the macro, rather than being hoisted in front of it. The title-slide
macro keeps its dedicated `tm:title#0` anchor (#246).

Since CLM {version}, extract **refuses to overwrite an existing companion**
unless `--force` is given (it raises rather than writing, leaving both files
untouched). The companion is *rebuilt* from the slide's current voiceover
cells, so a blind overwrite would discard anything that lives only in the
companion (hand-edits, or cells whose owning slide was removed). This
mirrors `clm slides split`'s `--force` contract.

```
clm voiceover extract [OPTIONS] FILE
```

| Option | Description |
|--------|-------------|
| `--force` | Overwrite an existing companion (rebuilds it from the slide's voiceover cells, discarding companion-only content). Without it, an existing companion is left untouched and the command errors. For a paired extract this is **all-or-nothing**: it refuses if *either* companion exists. |
| `--both` | Force the paired extract (both companions of a split deck). Auto-detected on a split half whose twin exists; passing `--both` errors if there is no twin. |
| `--single` | Extract only `FILE`'s own companion, even on a split half whose twin exists — opt out of the default auto-pairing. |
| `--layout [subdir\|sibling]` | Where to write the companion: `subdir` creates/uses a `voiceover/` folder; `sibling` writes next to the slide. Default: auto-detect an existing `voiceover/` folder, else sibling. See `clm slides tidy`. |
| `--dry-run` | Preview changes without modifying files |
| `--json` | Output as JSON |

Reading is always layout-agnostic — `inline`, `sync`, `validate`, and the build
find the companion whether it sits next to the slide or in a `voiceover/`
subdirectory. `--layout` only chooses where a **newly written** companion lands.

Examples:

```bash
clm voiceover extract slides_intro.py                 # bilingual: single companion
clm voiceover extract slides_intro.de.py              # split half: auto-pairs both companions
clm voiceover extract slides_intro.de.py --single     # split half: this half only
clm voiceover extract slides_intro.de.py --layout subdir   # write into voiceover/
clm voiceover extract slides_intro.py --dry-run
clm voiceover extract slides_intro.de.py --force
```

### `clm voiceover inline`

*Removed in CLM 1.8: the flat alias `clm inline-voiceover` no longer exists — use this group-qualified form.*

Inline voiceover cells from a companion `voiceover_*.<ext>` file back into the
slide file. The companion is deleted **only when every cell is placed**.

Since CLM {version}, each voiceover is re-inserted immediately after the
predecessor cell recorded in its `vo_anchor` (resolved within the owning
slide group only — it never crosses into another slide). If that anchor
cell was edited away or removed, inline falls back to the end of the
`for_slide` group and counts the cell as **relocated**; if the owning slide
is gone entirely (e.g. its `slide_id` was renamed), the cell is
**unmatched**. Both cases are reported rather than silently misplaced:

- **Unmatched cells are no longer dumped at the end of the slide.** Since
  CLM {version}, when any cell is unmatched the companion is **kept**,
  rewritten to hold exactly the unmatched remainder (with `for_slide` /
  `vo_anchor` intact), and the command **exits non-zero** — so a clean,
  recoverable source of truth always survives. Fix the slide `slide_id`(s)
  and re-run inline to place the rest. (Previously the companion was
  deleted unconditionally and the leftovers stranded at EOF — a data-loss
  footgun.)
- The text summary appends `N cell(s) relocated …` / `N cell(s) could not
  be matched …` / `companion … retained …`.
- `--dry-run` prints a per-cell placement line — `+` anchored, `!`
  relocated, `?` unmatched — with the target line, so you can confirm
  placement before writing.
- `--json` adds `relocated_cells`, `companion_retained`, and a `placements`
  array (each entry: `for_slide`, `anchor`, `status`, `after_line`,
  `after_header`).

```
clm voiceover inline [OPTIONS] FILE
```

| Option | Description |
|--------|-------------|
| `--dry-run` | Preview changes (incl. per-cell placement report) without modifying files |
| `--json` | Output as JSON (incl. `relocated_cells`, `companion_retained`, `placements`) |

Examples:

```bash
clm voiceover inline slides_intro.py
clm voiceover inline slides_intro.py --dry-run
```

### `clm authoring rules`

*Removed in CLM 1.8: the flat alias `clm authoring-rules` no longer exists — use this group-qualified form.*

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

The MCP server exposes 16 tools over stdio transport. Tool names mirror the
CLI verb-group structure (group-first); the flat pre-1.8 names
(`resolve_topic`, `validate_slides`, …) were renamed in CLM 1.8.

| Tool | Description |
|------|-------------|
| `topic_resolve` | Resolve topic ID or glob pattern to filesystem path |
| `slides_search` | Fuzzy search across topic names and slide titles |
| `course_outline` | Generate structured JSON course outline |
| `validate` | Validate a course spec (`.xml`) or slide files; dispatches on input type (override with the `kind` parameter). Replaces the former `validate_spec` + `validate_slides`. |
| `slides_normalize` | Apply mechanical fixes (tag migration, interleaving, slide IDs) |
| `slides_language_view` | Extract single-language view with line annotations |
| `slides_suggest_sync` | Detect asymmetric bilingual edits vs git HEAD |
| `voiceover_extract` | Move voiceover cells to a companion file; on a split half auto-pairs both companions (`both`/`single` params, `"paired"` JSON) |
| `voiceover_inline` | Merge voiceover cells back from companion file |
| `authoring_rules` | Look up merged authoring rules for a course |
| `voiceover_transcribe` | Transcribe a video through the voiceover artifact cache |
| `voiceover_identify_rev` | Identify which historical revision a recording was made against |
| `voiceover_compare` | Compare voiceover content between two slide files |
| `voiceover_backfill_dry` | Preview a backfill (identify-rev → sync-at-rev → port) without writing |
| `voiceover_cache_list` | List entries in the voiceover artifact cache |
| `voiceover_trace_show` | Read a voiceover merge-trace log and return entries as JSON |

All tools accept paths relative to the data directory or as absolute paths.
Most return JSON; `slides_language_view` returns annotated plain text.

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
| `--target` | all | Filter to specific output target. Also the only way to act on a non-distributed target (see below). |
| `--channel NAME` | all | Act on the named release-channel (cohort) repo instead of output targets (issues #208, #291). With several release streams, address a channel as `STREAM/CHANNEL` (e.g. `materials/2026-04`); a bare name works when unique. Mutually exclusive with `--target`. |
| `--all-channels` | all | Act on every release-channel (cohort) repo of every stream instead of output targets. Mutually exclusive with `--target`. |
| `--dry-run` | all | Show what would be done |

**Non-distributed targets (issue #292).** Without `--target`, `clm git` skips
any output target with `distribute="false"` — and, by default, any target named
as a `<release-channels source-target>` (a private build input for `clm
release`). Pass `--target NAME` explicitly to act on such a target anyway, or
set `distribute="true"` on it to restore wholesale distribution.

Examples:

```bash
clm git commit course.xml -m "Update slides"
clm git commit course.xml --amend              # amend, keep message
clm git commit course.xml --amend -m "new msg" # amend with new message
clm git push course.xml --force-with-lease     # safe force push
clm git sync course.xml -m "Weekly update"     # commit + push
clm git sync course.xml --amend                # amend + force push
clm git sync course.xml --force-with-lease -m "msg"  # commit + force push
clm git init course.xml --channel jan          # create one cohort repo
clm git sync course.xml --channel jan -m "Release functions"  # push a cohort
clm git status course.xml --all-channels       # status of every cohort repo
```

`git init` is idempotent — re-running it after creating remote repositories will
detect and add them as origin. The behavior matrix:

| | No local repo | Local repo exists |
|---|---|---|
| **No remote** | Create local-only repo | Skip (print remote URL if configured) |
| **Remote exists** | Clone/restore from remote | Add remote origin if missing |

**Release channels (`--channel` / `--all-channels`).** With these flags every
`clm git` subcommand operates on the per-cohort repositories declared in the
spec's `<release-channels>` block (see `clm info spec-files`) instead of the
`<output-targets>` repos — same init/status/commit/push/sync/reset behavior,
pointed at the cohort working trees. The private provenance manifest
(`.clm-manifest.json`) is always excluded from staging (and a copy a pre-exclusion
commit already tracked is purged on the next commit); the per-cohort frozen
manifest (`.clm-released.json`) is committed normally. The course must declare a
`<release-channels>` block or these flags error. Populating these working trees
is the job of `clm release sync`; `clm git --channel` then versions and
distributes them (and `clm git init --channel` creates each cohort repo once).

### `clm release`

Per-topic solution release to student cohorts (issue #208). After a topic's
workshop has been discussed, release that topic's full solution — and only that
topic's — into a cohort repository, **frozen** so later course edits never change
what a cohort already received. Channels are declared in the spec's
`<release-channels>` block(s) (`clm info spec-files`); the volatile per-topic
release state lives in a plain-text **ledger** (one topic id per line), never in
the spec.

A course may declare several `<release-channels>` blocks — one per release
*stream* (issue #291), e.g. `materials` fed by a `shared` target and
`solutions` fed by a `completed` target. Channels in a named stream are
addressed as `STREAM/CHANNEL` (e.g. `--channel materials/2026-04`); a bare
channel name keeps working when it is unique across streams.

| Subcommand | Description |
|------------|-------------|
| `release add SPEC_FILE TOPIC_IDS… --channel NAME` | Append topic ids to the channel's ledger (validated against the spec). |
| `release week SPEC_FILE SELECTORS… --channel NAME` | Append **every topic in the selected section(s)** to the ledger — a section-scoped `release add`. `SELECTORS` use the `build --only-sections` grammar (`id:`/`idx:`/`name:` prefixes, or a bare 1-based index / name substring). Section indices are disabled-inclusive; a selected-but-`enabled="false"` section is reported and skipped. |
| `release status SPEC_FILE --channel NAME` | Show released vs pending topics, and (with a resolvable `--dest`/`--channel`) frozen vs awaiting-sync. |
| `release sync SPEC_FILE --channel NAME` | Promote released-but-not-frozen topics from the built source into the cohort repo and freeze them. |
| `release provision SPEC_FILE [--channel NAME]` | Apply the spec's `<share-with>` declarations: share each channel repo into its GitLab access group(s) via the API (issue #294). Idempotent; needs `CLM_GITLAB_TOKEN`/`GITLAB_TOKEN` with `api` scope; repos must already exist on the remote. `--dry-run` previews. Channels without a parseable GitLab remote are skipped with a note. |

A channel can be addressed two ways: `--channel NAME` (resolves the ledger, the
frozen `--source` build root, and the `--dest` cohort repo from the spec's
`<release-channels>`), or by passing `--ledger` / `--source` / `--dest`
explicitly (which override resolution).

Key options for `release sync`:

| Option | Description |
|--------|-------------|
| `--channel NAME` | Resolve ledger/source/dest from the spec's `<release-channels>` (use `STREAM/CHANNEL` with several streams). |
| `--ledger PATH` | Channel release ledger (overrides `--channel` resolution). |
| `--source DIR` | Built frozen-source output root (must contain `.clm-manifest.json`). |
| `--dest DIR` | Cohort destination repository (created if absent). |
| `--language de\|en` | Promote only this language's files, re-rooted at the language directory (issue #293). Overrides the channel's `lang` attribute; requires `SPEC_FILE`; `--source` must point at the output-target root. |
| `--refreeze TOPIC` | Re-copy and re-freeze an already-frozen topic (e.g. a bug fix). Repeatable. |
| `--refreeze-all` | Re-copy and re-freeze every released topic. |
| `--push` | After promoting, commit and push the cohort repo (via `clm git`'s commit/push). The repo must already exist — run `clm git init … --channel` once first. |
| `-m, --message` | Commit message used by `--push` (default: a one-line summary of the sync). |
| `--dry-run` | Print the promotion plan; copy nothing. |

The source must be built with the provenance manifest (`clm build` writes it by
default since CLM {version}). Promotion copies bytes by manifest and records each
topic in the cohort's frozen manifest (`.clm-released.json`); a frozen topic is
never re-propagated unless you pass `--refreeze`.

**Partial manifests (issue #295).** A whole-course build that errors on some
topics still writes the manifest for the cleanly-built subset, recording the
failed topics. `release sync` promotes every green topic and reports the failed
ones as `skip-failed` (loudly, but exit 0) — they are never frozen, so they
promote automatically once a build succeeds for them. Builds whose errors
cannot be attributed to topics (and timed-out or `--only-sections` builds)
still write no manifest.

Examples:

```bash
clm build course.xml                                       # writes .clm-manifest.json
clm release add course.xml functions lists --channel jan   # release two topics to a cohort
clm release week course.xml "name:Week 1" --channel jan    # release a whole section's topics
clm release status course.xml --channel jan                # what's released vs pending/frozen
clm git init course.xml --channel jan                      # one-time: create the cohort repo
clm release sync course.xml --channel jan --push -m "Release functions, lists"
clm release sync course.xml --channel jan --dry-run        # preview promotion

# Two-stream setup (issue #291): materials before the session, solutions after.
clm release week course.xml idx:3 --channel materials/2026-04
clm release sync course.xml --channel materials/2026-04 --push
clm release week course.xml idx:3 --channel solutions/2026-04   # after the workshop
clm release sync course.xml --channel solutions/2026-04 --push
clm release provision course.xml                                # apply <share-with> group shares
```

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

#### `clm export summary`

Generate LLM-powered markdown summaries of course content. Requires
`clm[summarize]` extra. (Canonical name `summary`; `clm export summarize` is
kept as an alias.)

```
clm export summary [OPTIONS] SPEC_FILE
```

| Option | Description |
|--------|-------------|
| `--audience [client\|trainer]` | Target audience (required) |
| `--granularity [notebook\|section]` | Summary level (default: `notebook`) |
| `--style [prose\|bullets]` | Output formatting (default: `prose`) |
| `-L, --language [de\|en]` | Language for the summary structure (default: `en`) |
| `-o, --output FILE` / `-d, --output-dir DIR` | Shared output options (see `clm export`). |
| `--include-optional` | Include optional **whole sections** (gates sections only — a summary flattens each section to its notebooks and cannot drop optional *subsections*). |
| `--include-disabled[=marked\|merge]` | Summarize disabled whole sections too (read from disk). Bare/`=marked`: heading tagged `(disabled)`, appended after the enabled sections. `=merge`: interleaved in declared order with no marker. |
| `--model TEXT` | LLM model identifier |
| `--api-base TEXT` | Custom API base URL |
| `--no-cache` | Skip cache, re-generate all summaries |
| `--dry-run` | Show what would be summarized (no LLM calls) |
| `--no-progress` | Disable progress bar |

Examples:

```bash
clm export summary course.xml --audience client --dry-run
clm export summary course.xml --audience trainer -o summary.md
clm export summary course.xml --audience client -d ./docs
clm export summary course.xml --audience trainer --model openai/gpt-4o
clm export summary course.xml --audience client --style bullets
clm export summary course.xml --audience client --include-disabled=merge
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
| `--companion/--no-companion` | Force companion-file merge on/off (default: auto-detect based on whether a `voiceover_*.<ext>` companion exists, in a `voiceover/` subdir or next to SLIDES) |
| `--layout [subdir\|sibling]` | Where to create a **new** companion: `subdir` (a `voiceover/` folder) or `sibling`. Default: auto-detect an existing `voiceover/` folder, else sibling. Ignored when a companion already exists — it is updated in place. |
| `--propagate-to [de\|en]` | After merging `--lang`, translate the changes into the given target language and update its voiceover cells |

**Companion-file merge (auto-detected):**
- If a `voiceover_*.<ext>` companion file (as produced by `clm voiceover extract`)
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

#### `clm recordings drift`

Report which recordings have gone stale after slide edits. Each recorded
part stamps, at record time, the topic it records and that topic's
build-output digest (from the `.clm-manifest.json` provenance index). `drift`
re-reads the current manifest and compares: a part is `changed` when the
topic's built output differs from when it was recorded, `current` when it
matches, and `unknown` when it predates provenance stamping or its topic is
absent from the manifest (`unknown` is never reported as up to date).

```
clm recordings drift COURSE_ID [OPTIONS]
```

| Option | Description |
|--------|-------------|
| `--source PATH` | Built output root containing `.clm-manifest.json` (overrides the spec's default `output/` location) |
| `--manifest PATH` | Path to a specific `.clm-manifest.json` (overrides `--source` and the spec) |
| `--spec-file PATH` | Course spec XML; its default `output/` root is searched for the manifest |
| `--all` | Show every recorded part, not just the changed ones |
| `--json` | Emit machine-readable JSON |

The manifest is resolved in priority order: `--manifest` > `--source` >
`--spec-file`'s default `output/` root > the `spec_file` of the matching
`recordings.courses` config entry. By default only `changed` parts are
listed (the answer to "which videos must I re-record?"); pass `--all` to see
every part.

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
| `CLM_GITLAB_TOKEN` | GitLab API token (`api` scope) used by `clm release provision` for group shares; `GITLAB_TOKEN` is accepted as a fallback. |
| `CLM_LLM__MODEL` | Default LLM model for summarize (default: `anthropic/claude-sonnet-4-6`) |
| `CLM_LLM__API_KEY` | API key for LLM provider (or use `OPENAI_API_KEY`) |
| `CLM_LLM__API_BASE` | API base URL (e.g. `https://openrouter.ai/api/v1`) |
| `CLM_LLM__MAX_CONCURRENT` | Max parallel LLM calls (default: 3) |
| `CLM_LLM__TEMPERATURE` | LLM sampling temperature (default: 0.3) |
| `CLM_SYNC_PROVIDER` | Default edit-judge backend for `clm slides sync`: `openrouter` (default) or `local`. Overridden by `--provider`. |
| `CLM_SYNC__SHARED_DIVERGENCE` | How `clm slides sync` handles a language-neutral code cell edited *differently* on both decks: `auto-heal` (default) propagates the winning side (keyed direction, else newer file) and warns; `error` surfaces it and writes nothing so you resolve it by hand. |
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
| `CLM_HTTP_REPLAY_TRANSPORT` | HTTP-replay transport: `mitmproxy` (default since {version}; out-of-process proxy that matches repeated/concurrent identical requests) or `vcrpy` (in-process, consume-once — the pre-{version} default). Cassettes are **not** byte-compatible between the two — a pre-{version} course must re-record under mitmproxy (`--http-replay=refresh`) or pin `vcrpy` during the transition. |
| `CLM_HTTP_REPLAY_IGNORE_HOSTS` | Comma-separated list of request hosts that should pass through to the real network instead of being recorded into the cassette. Defaults to `api.smith.langchain.com` (LangSmith telemetry). Set to an empty string to disable the default. |
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
