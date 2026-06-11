# Changelog

All notable changes to CLM are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

Unreleased changes are collected as fragment files in [`changelog.d/`](changelog.d/)
and folded into this file by `scripts/collect_changelog.py` at release time.

## [1.12.0] - 2026-06-11

### Added

- `clm validate`: a deck meant to be fully narrated can opt into the
  (default-off, #176) voiceover coverage check per deck with a
  `# clm: voiceover-coverage` header directive (#178) — a default
  validate run then coverage-checks that deck only, while an explicit
  `--checks`/`checks=[…]` list is still honored verbatim.

- `clm slides sync` cold-start mint/adopt deferrals are no longer an
  opaque count (#231): when the correspondence verifier rejects DE/EN
  slide pairs, the output (and `--json` via `apply.cold_deferrals`) names
  each rejected pair's index and both headings plus a
  `clm slides validate` hint, and verifier-unavailable / safe-abort /
  plan-error / race deferrals state their reason.

- `clm slides assign-ids` automates three more cold-start fixes (#233):
  display expressions (`data[:5]`, `result["choices"]`,
  `response.headers["Content-Type"]`) and `for` loops are now
  content-derived AST extractions instead of hard refusals; an alt-less
  `<img src="…">` proposes a slug from the image filename stem
  (`img-robots-playing-checkers`) instead of hard-refusing (multi-line
  `<img>` tags no longer leak attribute fragments into prose extraction);
  and voiceover/notes cells carrying `<deck-stem>-cell-N` conversion
  placeholder ids are re-pointed to the preceding slide on the normal
  inherit pass, without `--force`.

- `clm slides normalize` gained a `placeholder_start` operation (issue #233
  item 4a): a code cell tagged `start` whose body is only a solution
  placeholder (`# Your solution here`, `pass`, `...`) followed by a markdown
  `completed`/`alt` cell is demoted to a plain cell, and an already-promoted
  markdown `completed` partner is renamed back to `alt`. Placeholder `start`
  cells paired with a code `completed` cell are left untouched. Runs before
  `tag_migration` (which otherwise promotes the adjacent markdown `alt` to
  `completed`), is part of `all`, and is idempotent.

- `clm slides split` now warns (without failing or rewriting the source) when
  the bilingual source contains `slide`/`subslide` cells missing a `slide_id`,
  pointing at `clm slides assign-ids`. Lightweight substitute for the deferred
  `split --assign-ids` proposal (#255): the documented
  `assign-ids --accept-content-derived --accept-code-derived` → `split`
  pipeline already guarantees id-complete halves; the warning catches the
  forgotten-first-step case at split time instead of one `validate` later.

- **`clm calendar push` — mirror a cohort's viewing calendar into Google
  Calendar.** Students subscribe to one shared Google calendar and pushed
  schedule changes propagate within minutes (no `.ics` hosting, no feed-refresh
  lag). The push only touches CLM-managed events: each event is tagged (private
  extended properties) with the cohort namespace and the same stable
  per-assignment UID the `.ics` export uses, so re-pushing updates events in
  place, deletes vanished ones, and never disturbs other events in the same
  calendar. Credentials (`--credentials` / `CLM_GOOGLE_CREDENTIALS`) accept an
  OAuth "Desktop app" client (one-time browser consent, cached token) or a
  service-account key, auto-detected; the target comes from `--calendar-id` or
  a new optional `[google] calendar_id` table in the cohort calendar TOML.
  `--dry-run` previews the insert/update/delete plan. Requires the new `[gcal]`
  extra (`google-api-python-client`, `google-auth`, `google-auth-oauthlib`).

- **Evergreen release files — `<evergreen>` patterns and `clm release sync
  --evergreen`.** Skeleton (global) files matching an evergreen glob pattern
  are exempt from the release freeze: every sync re-copies a matching file
  whose built content differs from the cohort's copy — for files that are
  *meant* to change over a cohort's lifetime (a NEWS file, announcements),
  which previously froze with the rest of the skeleton after the first sync
  and could never be updated. Patterns are declared on `<release-channels>`
  (inherited by every channel; channel-level entries are additive) or passed
  per-invocation with the repeatable `--evergreen` option, and match
  destination-relative POSIX paths (re-rooted paths for `lang`-scoped
  channels). Evergreen is skeleton-only by design: patterns matching
  topic-owned files are warned about and ignored (topic content still changes
  only via `--refreeze`), and the comparison is stateless (destination hash
  vs. manifest hash) so the `.clm-released.json` format is unchanged and
  re-runs are idempotent.

- **`clm run` — spec-defined task sequences.** A new `<tasks>` block in the
  course spec declares named sequences of clm commands (e.g. a `pre-release`
  task that regenerates calendar/outline exports and then builds, so the
  output never ships stale files). `clm run pre-release course.xml` executes
  the steps in order; `clm run course.xml` lists the spec's tasks; `--dry-run`
  previews the resolved commands. Steps are clm commands only (no shell — that
  is what makes tasks portable across machines), support a `{spec}`
  placeholder, run as subprocesses in the same Python environment, and are all
  validated (placeholders + command existence) before the first one executes.
  The first failing step aborts the task with its exit code. `clm validate`
  checks declared tasks too. `python -m clm` now works (new module entry
  point). See `clm info spec-files` / `clm info commands` and
  `docs/user-guide/tasks.md`.

- `clm build` now reports what it is doing after the last build stage instead
  of appearing to hang: stale-output sweep, database cleanup/VACUUM, worker
  shutdown, HTTP-replay cassette merging, and provenance-manifest writing each
  print a progress message.

- **Shared release destinations.** Channels of *different* release streams may
  now declare the same `path`, releasing e.g. materials and solutions into a
  single cohort repository on independent per-topic timelines (#325). Frozen
  manifests are now per stream (`.clm-released.<stream>.json`; a matching
  legacy `.clm-released.json` is adopted and renamed automatically on the next
  sync), skeleton files already present at the destination are kept rather
  than overwritten (presence-as-frozen), `clm release sync` refuses to promote
  when the sharing streams' builds claim a topic-owned path with differing
  content (byte-identical static files, e.g. project scaffolding, are
  allowed), spec validation
  requires sharing channels to agree on `lang`, and `clm git --all-channels` /
  `clm release provision` treat a shared destination as one repository. See
  `clm info releases` ("Shared destination") and `clm info migration`.

- New `clm cache explain SOURCE_FILE --spec SPEC` (#328): read-only, per-deck
  view of the execution-cache key components (hashed topic siblings, template
  fingerprint, worker image identity, execution flags), the resulting hashes,
  and the hit/miss state of every cache layer with stored-at timestamps,
  ending in a per-artifact "replays / skips / will execute" verdict — the
  one-screen answer to "why did this deck replay stale output?" that the
  #321 diagnosis lacked.

### Changed

- `clm validate`: the `'completed' tag without a preceding 'start' cell`
  error now points at a `keep`-tagged preceding code cell when present
  ("did you mean 'start'?") — the recurring incremental-build mis-tag
  found during cold-start conversions (#233 item 4b).

- **Changelog entries are now fragment files in `changelog.d/`.** PRs no
  longer edit the `[Unreleased]` section of `CHANGELOG.md` (concurrent PRs
  inserting at the same lines made changelog merge conflicts near-universal);
  each PR instead adds `changelog.d/<pr-or-issue>-<slug>.<type>.md` with the
  finished markdown bullet. At release time the new
  `scripts/collect_changelog.py` folds all fragments (plus any stray
  hand-written `[Unreleased]` entries) into a `## [X.Y.Z]` section, grouped
  Added/Changed/Deprecated/Removed/Fixed/Security, and deletes them.
  Conventions in `changelog.d/README.md`; release procedure updated in
  `docs/developer-guide/releasing.md`.

- **Breaking: command-tree regrouping (#310).** The single-command groups
  `topic`, `spec`, and `authoring` were merged into the domain groups
  `course` and `slides`, and the remaining stray top-level commands moved
  into their natural groups — a clean break with no deprecation aliases:
  `clm targets`/`clm sync-includes`/`clm spec decks|orphans`/`clm topic
  resolve` → `clm course targets|sync-includes|decks|orphans|resolve-topic`;
  `clm authoring rules` → `clm slides rules`; `clm polish` →
  `clm slides polish`; `clm delete-database` → `clm db delete`;
  `clm export calendar` → `clm calendar generate` (the whole cohort-calendar
  lifecycle now lives in one group: `generate` → `check` → `status` →
  `push`); `clm voiceover port-voiceover` → `clm voiceover port`. The
  synonym pairs `slides translate`/`bootstrap` and `export
  summary`/`summarize` still work but are listed once in `--help`. The top
  level shrinks from 31 to 26 entries. See `clm info migration` for the full
  table.

- **Internal: command modules mirror the command tree.** Finding a command's
  definition is now mechanical: flat `clm <cmd>` lives in
  `commands/<cmd>.py`; `clm <group> <cmd>` lives in
  `commands/<group>/<cmd>.py` (package groups `slides/`, `course/`,
  `export/`) or `commands/<group>.py` (single-file groups, e.g. `db.py`,
  `git.py`, `calendar.py`). Groups register their own subcommands where
  they are defined; `main.py` is just the top-level manifest. No
  user-visible change, but `clm.cli.commands.*` import paths moved —
  external code importing them must follow the renames.

- CLI startup is ~4x faster: `clm.cli.main` now loads command modules lazily
  (`LazyGroup`), and the `clm`/`clm.core`/`clm.infrastructure` package inits
  resolve their convenience exports via PEP 562 instead of importing the whole
  core/infrastructure stack on every invocation. `from clm import Course` and
  `from clm.cli.main import BuildConfig`-style imports keep working unchanged.

- Finished job rows are now pruned at build end by default: completed jobs are
  kept 7 days and failed jobs 30 days (previously both were kept forever, which
  made the jobs database — and `clm monitor` startup — grow without bound).
  Job rows are diagnostic only; the results/execution caches live in separate
  tables, so this never causes re-execution. Set
  `CLM_RETENTION__COMPLETED_JOBS_RETENTION_DAYS` /
  `CLM_RETENTION__FAILED_JOBS_RETENTION_DAYS` to tune.

### Fixed

- `clm slides assign-ids` no longer mints collision-suffixed slide ids that
  exceed the 30-character slug cap (and that `clm slides validate` then
  rejected) — the base slug is trimmed at a word boundary before the `-N`
  dedup suffix is appended (#233).

- The voiceover transcribe CLI tests no longer leak a
  `.clm/voiceover-cache/transcripts/` directory into the working directory
  `pytest` was invoked from — they now isolate the transcript cache under
  `tmp_path` via `--cache-root` (#235).

- **Provenance manifest no longer records `.git` (and other never-copied
  paths) from output trees (issue #302).** The manifest's dir-group walk now
  applies the same ignore filter as the build's dir-group copy, so a `.git`
  left inside an output target (e.g. by `clm git init`) no longer enters the
  skeleton as 1000+ topic-less entries that `clm release sync` would then
  copy into a cohort repo — for a language-scoped channel landing at the repo
  root and clobbering the destination's real `.git`. As defense in depth,
  `clm release sync` now refuses to copy any manifest path containing a
  `.git`/`.svn`/`.hg` segment (with a warning), so a polluted manifest from
  an older build can never overwrite a destination repo's VCS metadata.

- `clm monitor` / `clm status` no longer take many seconds to start against a
  large jobs database: schema v9 adds indexes on `jobs(status, completed_at)`
  and `jobs(completed_at)` (existing databases migrate automatically), and the
  monitor's activity query restricts its un-indexable `COALESCE` sort to an
  index-friendly candidate set instead of sorting every finished job ever
  recorded.

- Underscore-prefixed directories under `slides/` (e.g. `_archive/`, `_drafts/`)
  are now invisible to module/topic discovery, to the recursive deck walks behind
  the `clm slides` batch tools and `clm slides sync`, and to `clm course orphans`
  — previously an archived module under `slides/_archive/` participated in topic
  resolution and could silently shadow a live topic ID via first-occurrence-wins,
  shipping retired decks in its place. A spec binding `module="_archive"` now
  fails validation with `unknown_module`. The legacy `_cassettes/` sidecar inside
  a topic is unaffected (#318).

- `clm build` notebook caches no longer replay stale execution results when
  a dependency changes with unchanged deck text (#321): the cache keys now
  cover every topic sibling shipped to the kernel (C++ `#include` headers,
  Jinja `{% include %}` targets, runtime data files), a fingerprint of the
  bundled Jinja templates (`macros.j2` etc.) plus the CLM version, the
  worker execution environment (`direct`, or the configured Docker image
  reference — a cache populated under one worker image is no longer
  replayed under another; pin versioned tags rather than `:latest` for
  exact invalidation), and the per-topic `evaluate=` / `skip-errors=`
  flags. The HTTP-replay cassette remains deliberately excluded
  (record-after-run miss loop). The first build after upgrading re-executes
  everything once (key schema change).
- Cached-issue replay actually works for notebook jobs again (#321): stored
  errors/warnings were keyed under `str(tuple)` output metadata while
  lookups used the colon-joined form, so a cache hit never re-surfaced the
  warnings/errors recorded for that content. Both cache layers (database
  result cache and job-level cache) now replay stored issues on a hit; the
  job-level path previously dropped them entirely.
- A successful build of a deck now clears errors/warnings stored by earlier
  runs of the same content (#321): previously a transient failure's stored
  error would have been replayed on every later cache hit, and repeated
  `--ignore-cache` runs accumulated duplicate warnings.
- `clm build --output-mode verbose` now prints an explicit
  `↻ Replayed from cache` line for every file served from a cache instead
  of executed (#321) — replayed output is freshly timestamped and was
  previously indistinguishable from executed output.

- Docker source-mount workers no longer discard the host's voiceover-merged
  notebook payload by re-reading the raw slide file from the mount (#324).
  The payload's `data` is now the canonical input in both Direct and Docker
  modes, so (1) companion narration reaches docker-built output and (2) the
  worker-side `execution_cache_hash` agrees with the host's again, restoring
  Stage-4 cache replay for voiceover decks built with docker workers.

## [1.11.0] - 2026-06-10

### Added

- **Multiple release streams per cohort (issue #291).** A course can declare
  several `<release-channels>` blocks — one per release *stream*, each fed by
  its own `source-target` (e.g. `materials` from a `shared` target released
  before each session, `solutions` from a `completed` target released after
  the workshop). With more than one block each needs a unique `name`; channels
  are addressed as `STREAM/CHANNEL` (`--channel materials/2026-04`) across
  `clm release`, `clm git --channel/--all-channels`, and `clm calendar`, with
  bare names still accepted when unique. Derived channel repo names gain the
  stream segment (`{slug}-{channel}-{stream}`); spec validation rejects
  duplicate stream/channel names and channels sharing a destination or ledger
  across streams.
- **Language-scoped release channels (issue #293).** `<channel lang="de">`
  promotes only that language's files, re-rooted so the cohort repo's root is
  the language directory — matching per-language distribution repos like
  `…/machine-learning-azav-de` — and appends `-{lang}` to the derived repo
  name. `clm release sync --language de|en` overrides per invocation. A
  channel without `lang` keeps receiving every built language root (now
  documented as the defined default).
- **Declarative GitLab access-group shares (issue #294).** `<share-with
  access="reporter">students/azav-ml/ml-2026-04</share-with>` on a `<channel>`
  (or on the block, inherited by every channel) declares which GitLab groups a
  channel repo is shared into; the new `clm release provision` applies the
  shares via the GitLab API (idempotent, token from
  `CLM_GITLAB_TOKEN`/`GITLAB_TOKEN`, safe no-op for non-GitLab remotes,
  `--dry-run` preview). Repo creation itself stays push-to-create/manual.
- **`clm git` skips release build-source targets (issue #292).** Without
  `--target`, `clm git init/status/commit/push/sync/reset` no longer creates
  or manages repos for output targets that only feed a release stream (named
  as a `<release-channels source-target>`), nor for targets with an explicit
  `distribute="false"` — those are private build inputs whose content reaches
  students only through `clm release sync`. An explicit `--target NAME` still
  selects such a target, and `distribute="true"` restores wholesale
  distribution for a release source that is also pushed directly.
- **A failing deck no longer blocks all releases (issue #295).** A
  whole-course build with topic-attributable errors now writes the provenance
  manifest for the cleanly-built subset, excluding the failed topics' entries
  and recording them (`partial: true`, `failed_topics`). `clm release sync`
  promotes every green topic and refuses the failed ones (`skip-failed`,
  never frozen, loud warning) until a build succeeds for them. Errors that
  cannot be attributed to a topic — and timed-out, `--watch`, or
  `--only-sections` builds — still suppress the manifest entirely.
- **Cohort viewing calendars (`clm export calendar`, `clm calendar check` /
  `status`)** project a course's schedule onto one cohort's real calendar dates
  (issue #283). Where `clm export schedule` is course-relative ("Week 3,
  Tuesday"), a *calendar* maps the same ordered day-buckets onto actual dates
  for a cohort, absorbing that cohort's holidays, delayed start, multi-week
  breaks, and catch-up days. The trainer maintains only the deltas in a small
  hand-edited `release/<channel>.calendar.toml` (start/end, weekly teaching
  pattern, single-or-interval holidays, and ordered `merge`/`split`/`insert`/
  `pin` adjustments) beside the channel's release ledger; the per-video dates
  are computed. A holiday removes a teaching date so later content slides
  automatically; `pin`s anchor a day to a date and segment the timeline, and an
  over-full segment is reported with the exact "merge ≥ N buckets" deficit
  rather than silently redistributed. `clm export calendar` renders Markdown,
  CSV, or a subscribable `.ics` feed (stable event UIDs, so a re-export updates
  events in place); `clm calendar check` validates a calendar (date-free,
  non-zero exit on errors); `clm calendar status [--as-of DATE]` shows where a
  cohort is today, its plan coordinate, and the drift in days versus the ideal
  plan. See `clm info commands`.
- **`clm export outline --weekdays [never|always]`** controls whether a
  section's `<subsection>` weekday/name groupings are rendered as bold labels
  in the Markdown outline. The default is `never`: every section's decks are
  flattened into plain bullets, so weeks read uniformly whether or not they
  declare subsections (previously, weeks that declared subsections rendered
  weekday groups while weeks that did not — and disabled weeks under
  `--include-disabled=merge`, which ignored subsections entirely — rendered
  flat, an inconsistent mix). Pass `--weekdays always` to group decks under
  their weekday/name label in every week, disabled weeks included. The JSON
  format is unaffected — it always carries the grouping as a structured
  `subsections` array.
- **Two new `clm slides sync` test oracles** close the architecture review's
  remaining coverage gaps (issue #289 P4): `tests/slides/test_sync_non_python.py`
  drives the engine end-to-end on a C# (`//`-token) split pair for the first
  time — neutral verbatim copy, id-less re-translation, add-with-insert (the
  built twin must carry the `// %%` header family), tag mirroring, and the
  neutral tag-drift alert, on both baselines — and
  `tests/slides/test_sync_corpus_mutation.py` (slow/integration, corpus-gated
  like the no-op backstop) is the corpus' first **positive propagation
  oracle**: scripted one-sided mutations of real PythonCourses decks per
  change-type (neutral edit/add, id-less localized edit, companion remove,
  tag-only retag, judge-reconciled edit), asserting each is propagated to the
  other half or alerted — never silently dropped — on pairs selected per
  target cell class and verified post-sync-clean.

### Changed

- **The course-document commands `outline`, `schedule`, and `summarize` moved
  under a new `clm export` group** (`clm export outline` / `clm export schedule`
  / `clm export summary`), and the flat top-level forms were removed (no
  deprecation alias). `summarize` was renamed to the noun `summary`, with
  `clm export summarize` kept as an alias. The three commands now share a
  consistent option vocabulary: `--include-optional` and `--include-disabled`
  are available on all three (both **off by default**, so an outline/summary
  that previously listed `optional="true"` modules now hides them unless the
  flag is given), `clm export schedule` gained `-d/--output-dir`, and
  `-L/--language` is the canonical spelling everywhere (`schedule` keeps
  `--lang` as an alias). The MCP `course_outline` tool is unchanged (it still
  shows optional content). See `clm info migration`.
- **`--include-disabled` now takes an optional value** on all three `clm export`
  commands: a bare `--include-disabled` (or `=marked`) keeps the previous
  behaviour (disabled content tagged `(disabled)`, disabled whole sections
  listed after the enabled ones in `outline`/`summary`), while
  `--include-disabled=merge` folds disabled content into the normal course flow
  — in declared order, with no marker — so a roadmap spec reads like a finished
  course. Structured outputs (`outline --format json`, `schedule --format csv`)
  keep the disabled state recorded even under `=merge`.
- **`clm export outline` / `clm export summary` now filter split-companion decks
  to the requested language.** When a topic ships split `slides_x.de.py` +
  `slides_x.en.py` companions, a `-L de` outline/summary listed *both* the
  German and the English title (and the JSON `slides` array carried both),
  because the section-flat, JSON-slide, summary, and disabled-topic enumerations
  skipped the `output_language_filter` the subsection path already applied. All
  enumerations now filter split companions to the requested language (via
  `output_language_filter` for the built course and the `.de`/`.en` filename
  suffix for disabled topics read from disk), so a split pair contributes a
  single entry — matching the build's per-language routing. Bilingual decks are
  unaffected.
- **`clm slides sync` parity errors now name the diverging cells**, instead of a
  generic "a change to a shared cell was not propagated". The shared-cell error
  lists the cell text present on one half but missing on the other (or the first
  out-of-order cell), and the id-less-localized error points at the slide group
  and cell kind — so the divergence can be located without a manual diff.
- **`clm slides sync` shows progress while it runs.** A directory (batch) sweep
  prints a `[i/N] <deck> …` header per pair, and a writing run prints a short
  stderr tick per LLM call (`· reconciling …` / `· translating …`) so a long
  sync is visibly alive. Progress goes to stderr and is suppressed under
  `--json` (stdout stays pure JSON).
- **The mitmproxy transport now records and replays a per-request response
  *sequence*** instead of collapsing a repeated request to its first response.
  A non-deterministic endpoint (a temperature>0 LLM, or OpenRouter routing the
  same request to different providers) answers an identical request differently
  on successive calls; when a *later* request embeds an earlier response (e.g.
  `summarize | translate`, where `translate` carries the generated summary), the
  old first-seen-wins dedup dropped every response after the first, so the
  downstream request matched nothing on replay and failed with `clm_replay_miss`.
  Recording now keys dedup on `(request, response)` so distinct responses to the
  same request are kept in order, and replay serves them in recorded order via a
  per-request cursor — sticking on the last match once exhausted, so a genuinely
  repeatable request never misses and a single-entry cassette still serves
  repeatably (unchanged). The host-side fold gained `preserve_sequence=True`
  (mitmproxy only; the vcrpy path keeps the deduped fold). Decks affected before
  this fix: `chains_and_lcel/slides_020`, `prompt_templates/slides_010`,
  `langgraph_intro/slides_010`.

### Fixed

- **`clm slides sync` now mirrors a tag-only edit on an id-less localized cell
  across a concurrent slide-group reorder** (issue #285). Live positional
  pairing is unsound under a reorder, so the Tier C retag mirror used to
  decline — silently before #289, with an error after #290. The baseline
  provides a sound, reorder-invariant join instead: the drifted cell is found
  by unique body hash against its own baseline (a tag-only edit never changes
  the body), its baseline *position* indexes the twin (the two localized
  streams are positional twins at the last sync, verified on the recorded
  rows), and the twin's baseline hash locates it in the current, reordered
  stream. The tag mirrors and the reorder applies in the same clean pass.
  Anything the route cannot anchor still **errors** rather than guessing or
  dropping: byte-identical duplicate bodies (detected via per-group tag
  multisets — previously these slipped silently to the validator even in the
  alert path), tags drifted on both twins, misaligned baseline streams, or a
  pass that also adds/removes cells (a remove would shift the positions the
  retag applier targets — sync in two steps).
- **`clm slides sync`'s structural pass no longer calls the translator inside
  its region rebuild** (issue #289 P2, completing the resolve-then-apply
  redesign's last deferred follow-up). The translations a rebuild needs are
  pre-resolved into the run's shared outcome cache (the same cache the add
  walks use, so a deferred add's outcome is already there when the rebuild
  reaches for the cell); the rebuild itself is mechanical, with the documented
  inline fallback for a cache miss. Unchanged id-less cells stay on the
  verbatim-reuse path (never translated). Behavior-preserving — the full
  slides suite is green unchanged. The opt-in `--llm-recover` tier remains
  inline **by decision** (it fires only when the deterministic id-migration is
  stuck, which cannot be known before execute without simulating that tier;
  it is already cached, validated, and safe-aborting) — recorded in
  `docs/claude/design/sync-plan-resolve-apply.md`.
- **`clm slides sync`'s baseline plumbing is unified, and the deterministic
  id-migration now also runs on a committed (git-HEAD) baseline** (issue #289
  P1). Both baseline sources now produce one representation (`BaselineBundle`,
  the membership-widened watermark shape — git HEAD is re-derived through the
  exact chokepoints a watermark recording uses) consumed by a single code path
  for every baseline aspect: the keyed diff, the neutral / id-less / header
  drift detectors, tag mirroring, and the apply engine's anchor-reuse and
  id-migration passes (which now read the same rows the classifier diffed
  against, carried on the plan). This deletes the per-aspect parallel git-HEAD
  derivations whose coverage gaps produced the #269 silent drops and the #289
  git-HEAD tag drop — the two sources can no longer diverge in coverage by
  construction. User-visible improvement: the `def-my-fun` drifted-id
  migration (and the opt-in `--llm-recover` tier) previously read only the
  watermark, so on the *first* sync of a committed pair a split id'd cell was
  not re-united with its construct; it now migrates identically on both
  baselines.
- **`clm slides sync` no longer silently drops a one-sided tag-only edit**
  (issue #289, found by the sync architecture review in
  `docs/claude/sync-engine-architecture-assessment.md`). Three tag-channel
  gaps — all of which previously reported *"decks already consistent"* and
  advanced the watermark over the divergence, permanently hiding it from
  later syncs — are closed:
  - a tag-only edit on an **id-less localized** cell is now **mirrored** on a
    committed (git-HEAD) baseline too, not only against a watermark: the
    Tier C retag classifier re-derives the baseline rows + tag sets from the
    committed text, so the first sync of a freshly-split committed pair
    carries a `keep`/`alt` tag change across;
  - a tag-only edit on a **language-neutral (shared)** cell — whose tags must
    match across the halves, since a neutral cell is shared verbatim header
    included — now **errors** (the body-hash detectors are blind to it, and
    sync has no neutral-retag mirror yet): the watermark holds and nothing is
    written. A combined body+tag edit still propagates both via the structural
    rebuild's verbatim header copy, with no false alert. The post-apply
    shared-cell parity fail-safe also compares tag sets now;
  - a tag-only edit on an id-less localized cell **while the other half
    reorders slide groups** (issue #285) now **errors** instead of vanishing:
    positional tag mirroring is unsound across a reorder, so the drift is
    detected order-blind (hash-keyed against each half's own baseline) and the
    watermark holds.
  A new channel-coverage meta-test pins the class: every channel the sync
  watermark records (body hash, tags, header, order, identity, construct — per
  partition) must name a live detector or fail-safe, so a future recorded
  channel can no longer ship unconsumed the way shared-partition tags did.
- **`clm slides sync` no longer errors when a new slide group is inserted next
  to a language-neutral cell.** Adding a new id'd slide (a localized markdown
  cell + its following language-neutral code cells) right after a neutral cell —
  e.g. a `slide_id`-carrying code cell with no `lang=` — made sync place the new
  group in the wrong inter-group position on the other half and then fail with
  `language-neutral (shared) cells differ …` / `id-less localized cells … placed
  differently …`, writing nothing. The id-carrying add path anchors a new group
  only beside cells it can name by `(slide_id, role)`, so a neutral / id-less
  neighbour was skipped; the structural pass rebuilds each group's *contents* but
  never reordered *groups*, so the misplacement survived as a parity error. Sync
  now reconciles slide-group **order** against the propagation source after the
  structural pass (committing only a reorder that reproduces the source's group
  and `(slide_id, role)` order exactly), so such an insertion propagates cleanly.
- **`clm slides sync` no longer silently drops (or destructively overwrites) a
  one-sided edit made while the other half reorders slide groups.** When one half
  reordered slide groups (a `move`) while the other half independently edited a
  language-neutral (no `lang=`) or id-less-localized cell, the two changes flowed
  in opposite directions. The reorder permutes the source half's neutral/id-less
  cell *sequence*, which the positional drift detectors misread as a "drift" —
  masking the target half's edit. The run reported the decks consistent and
  advanced the watermark while the edit was lost from **both** halves; for two or
  more reordered neutral cells the positional shift was even misclassified as a
  same-cell divergence and **auto-healed**, overwriting the edit on disk (Issue
  #282). Because a group reorder makes positional pairing unsound, sync now
  **errors** whenever one half reorders slide groups while the other half has any
  unreconciled neutral / id-less change (an edit, add, remove, or cross-group
  reassignment), holding the watermark and leaving both halves untouched on disk so
  the change is preserved. A neutral edit applied **identically** to both halves
  alongside a reorder still merges cleanly (the halves agree, so nothing is lost).
  Reconcile a genuine conflict by hand (apply the reorder and the edit on the same
  half, or sync them in separate steps) and re-run.

## [1.10.0] - 2026-06-08

### Changed

- **mitmproxy is now the default HTTP-replay transport** (issue #165), replacing
  the in-process vcrpy path. The out-of-process proxy matches repeated and
  concurrent identical requests that vcrpy's consume-once model mishandles — e.g.
  a LangChain chain invoked many times with the same body, or `RunnableParallel`
  fan-out — which previously made such decks impossible to strict-replay. Opt back
  into vcrpy with `CLM_HTTP_REPLAY_TRANSPORT=vcrpy`. **Cassettes are not
  byte-compatible between the two transports**, so existing vcrpy cassettes must be
  re-recorded under mitmproxy before strict replay passes (vcrpy stays installed —
  the mitmproxy addon serializes cassettes in vcrpy's on-disk format). Starting the
  proxy is gated on the course actually having an `http-replay` notebook, so a
  replay-free build never spawns `mitmdump`.
- **Dropped Python 3.11 support** (`requires-python` is now `>=3.12`). mitmproxy,
  the new default replay transport, requires Python 3.12+; `mitmproxy>=12,<13` is
  added to the `replay` extra. The Docker worker base images (drawio, notebook /
  notebooklite, plantuml) were bumped from `python:3.11-slim` to `python:3.12-slim`
  to match the new floor — on 3.11 `pip install ./clm` now fails the
  `requires-python` check.

### Added

- **The `[ml]` extra now bundles `deepagents>=0.6.0` and `psycopg[binary]>=3.2`.**
  `deepagents` (`create_deep_agent` on LangGraph) backs the AI-Agents-II deck and
  `psycopg` is the PostgreSQL driver for the Docker/Postgres deployment decks, so
  installing `[ml]` (or `[all]`) makes those decks build out of the box.
- **`clm slides sync` honors the translation glossary on the new-slide path**
  (follow-up to PR #264). The `--glossary` translation conventions — a style note
  plus a term glossary appended to the translation prompt — were wired into
  `clm slides translate` / `bootstrap` but not into the incremental `clm slides
  sync` add path, so a brand-new slide translated *during a sync* ignored the
  course's conventions. `sync` now resolves a glossary too. Because sync is
  **bidirectional** (a new EN slide flows to DE and a new DE slide flows to EN in
  the same pass), it resolves the conventions **per target language**: a new EN
  slide translated to DE uses the **DE** glossary, a new DE slide translated to EN
  uses the **EN** glossary. Each is auto-discovered as `clm-glossary.<lang>.md`
  walking up from the deck, or supplied explicitly with `--glossary-de` /
  `--glossary-en`; a language with no glossary translates with no conventions, as
  before. Batch (`DIR`) runs resolve one glossary from the directory root (the
  translator is shared across the sweep). The glossary-discovery helpers are now
  shared by both commands (`clm.slides.glossary`).

### Fixed

- **`clm slides sync` no longer silently drops one-sided edits to shared /
  id-less / header cells (Issue #269).** Sync's promise is that editing one half
  of a split deck carries *all* changes to the other half, and that it never
  reports "decks already consistent" while a change was in fact dropped. Several
  classes of edit violated that — each was silently lost, the run reported "0
  changes — decks already consistent", and the watermark advanced over the
  divergence (permanently baselining the loss). All are now propagated, or alerted
  when they cannot be:
  - **Language-neutral code/markdown cells on the first sync.** The neutral-cell
    drift diff ran only against a watermark, so the **cold-start (git `HEAD`)
    baseline** — the first sync of a freshly-split pair — missed a one-sided edit,
    add, or removal of a shared cell entirely. The git-HEAD baseline now supplies
    the shared-cell sequence, so cold-start syncs detect and propagate them (and
    surface a both-sides divergence) exactly like the watermark path.
  - **Id-less localized cells** (a `lang=` cell with no `slide_id`). A one-sided
    body edit had no propagation direction, so it was dropped under *both*
    baselines. A drift detector now feeds the structural pass a direction; both
    bare statements (hash-anchored) and named constructs (`def`/`class`/`import`)
    are re-translated onto the twin. A genuine both-sides id-less edit with no other
    direction is surfaced rather than guessed.
  - **The j2 deck header.** A one-sided header edit (a retitle, or a should-be-
    identical neutral arg) was reported "consistent". Sync still does not
    auto-translate the header, but a one-sided change is now an **error** that holds
    the watermark and tells you to update the other header (or run `clm slides
    translate`).
  - **Honest reporting + fail-safe.** The summary no longer says "0 changes —
    decks already consistent" on a run that propagated a structural change (it names
    the direction), the apply outcome counts a new `structural` figure, and a
    post-apply parity check errors if the two halves' neutral cells still differ —
    so an un-propagatable shared-cell change can never be silently banked.
- **`clm slides translate`'s delegated-sync path now selects the glossary
  per-language too.** When the twin already exists, `translate` degrades to the
  bidirectional sync engine; it previously applied the single target-language
  glossary to *both* add directions, so a reverse-direction new slide got the
  wrong-language conventions. It now resolves a per-language map there (parity with
  `clm slides sync`); the cold-start bootstrap path is single-direction and
  unchanged.
- **The dedicated deck-title translation prompt no longer strips terminal
  punctuation.** The `title` role prompt (PR #264) told the model to drop trailing
  punctuation, which silently mangled a legitimately punctuated title (e.g.
  `header_de("Was ist neu?")` → "What's New?"). It now preserves the source title's
  terminal `?`/`!` (while still forbidding the stray leading `# ` / quotes that were
  the actual bug being fixed).

## [1.9.2] - 2026-06-08

### Added

- **`clm slides translate` / `bootstrap` glossary guidance (`--glossary`)**
  (PR #264). The translator now accepts an optional translation conventions file
  — a Markdown style note plus a term glossary — that is appended to the
  translation system prompt, so a course can pin a formal register and keep or
  translate technical terms consistently across a whole deck. Supply it with
  `--glossary PATH`, or let the command auto-discover `clm-glossary.<target-lang>.md`
  by walking up from the deck's directory (the same walk-up used for `.env`). The
  guidance text is folded into the translation cache key (a fingerprint on the
  `translate-v1` prompt version): a different glossary keys a different cache
  entry and editing the glossary invalidates affected entries by cache miss,
  while decks translated without a glossary keep the bare `v1` key (no flag-day
  invalidation). See `clm info commands`.

### Fixed

- **Deck titles are now translated correctly** (PR #264). The
  `header_<lang>("…")` deck title was being translated through the markdown-prose
  prompt, which announces that every line is `#`-prefixed — so the model added a
  stray `# ` and left the title itself untranslated (e.g.
  `header_de("# Your First Web Service")`). A dedicated `title` translation role
  now translates the bare title phrase and forbids any prefix or surrounding
  quotes.

## [1.9.1] - 2026-06-07

### Added

- **Day-of-week scheduling: `<subsection>` spec layer + `clm schedule`**
  (issue #261). A `<section>`'s `<topics>` may now group `<topic>`s into
  optional `<subsection weekday="mon">…</subsection>` elements (`<section>` =
  week, `<subsection>` = day) with an optional `<name>` label override and
  `enabled="false"`. The layer is purely additive: `clm build` flattens
  subsections away, so a spec with subsections builds **byte-identically** to
  the same spec with the wrappers removed — no output-dir or topic-resolution
  changes. A new top-level **`clm schedule`** command exports the certification
  day-listing from the resolved course in Markdown (one table per week:
  weekday / video / topic) or CSV (one row per deck), single-language via
  `--lang` (default `de`). `clm outline` renders subsections indented under
  their section (with `--include-disabled` surfacing disabled ones), and
  `clm validate` adds four advisory subsection checks (duplicate weekday,
  out-of-order weekdays, empty day, and bare-topics-mixed-with-subsections).
  See `clm info spec-files` and `clm info commands`.
- **`clm slides assign-ids --accept-code-derived`** — a deterministic, opt-in
  fallback that mints a `slide_id` for bare-expression code subslides the AST
  extractors can't name (`(1 + 1j) * (1 + 1j)` → `1-1j-1-1j`, `letters[0:3]` →
  `letters-0-3`), by slugifying the cell's first real code line. Previously
  these hard-refused, with the non-deterministic LLM (`--llm-suggest`) as the
  only non-manual escape — so the bilingual→split conversion needed a human to
  hand-author ids on both split halves. The first-code-line scanner is
  comment-token-aware, so it also completes non-Python decks
  (`.cs`/`.cpp`/`.java`/`.ts`), which `ast` can never parse. It is independent
  of `--accept-content-derived` (separately gated, so the content-derived
  minting funnels never start emitting opaque code-line slugs); the conversion
  pipeline passes both. Genuinely empty / pure-punctuation / magic-only cells
  still refuse. (#251)
- **`clm slides normalize` gains a `preamble_code` operation** (default-on) that
  fixes issue #253: executable code between the `# {{ header(…) }}` macro call and
  the first `# %%` cell has no cell marker, so jupytext folds it into the header
  cell — and, at build time, into the **title markdown**. In the bilingual
  `header(de, en)` macro that code rides the EN title (silently dropped from a DE
  build); in a split `.de.py` half it rides the DE title (kept), so the bilingual
  and split builds diverge on the DE side and the conversion is not
  render-neutral. The new op moves the code into its own shared `# %%` code cell —
  included identically in every build and copied verbatim to both split halves —
  so the builds become byte-identical and the code is finally executed as code
  rather than rendered as markdown text. It runs first among the normalize passes,
  is idempotent, and is a no-op on a conforming deck.
- **Jinja `{% include %}` in slide source now resolves against the topic's own
  siblings** (PR #258). A deck that does `{% include "add.h" %}` to show a file
  sitting next to it previously failed with `TemplateNotFound`, because the Jinja
  environment only loaded the bundled `templates_<prog_lang>/` package. The loader
  is now a `ChoiceLoader`: the bundled package is tried **first** (so `macros.j2`
  and friends can never be shadowed by a same-named sibling), then the notebook's
  topic siblings as a fallback — a `FileSystemLoader` on `source_dir` in Docker
  source-mount mode and/or a `DictLoader` decoded from `payload.other_files` in
  direct mode (non-UTF-8 siblings are skipped). When a deck has no siblings the
  plain package loader is used unchanged, so existing courses are unaffected. See
  the new "Jinja `{% include %}` in slide source" section under `clm build` in
  `clm info commands`.

### Changed

- **`clm validate` (`format` group) and `clm slides split` now warn about
  preamble code** (issue #253). `validate` emits a `warning`-severity finding and
  `split` prints a non-fatal `warning:` to stderr (it never rewrites the source,
  so the byte-identical round-trip is preserved). Both point at `clm slides
  normalize` for the fix. Top-of-file code *before* the `# j2` import line (a true
  file preamble, e.g. a leading `import os`) is already render-neutral and is not
  flagged.
- **CI/release workflows upgraded to Node.js 24-based actions.** GitHub is
  forcing JavaScript actions onto Node.js 24 (Node.js 20 is deprecated and
  removed from runners in September 2026). `actions/checkout`, `setup-python`,
  `setup-java`, `astral-sh/setup-uv`, `codecov/codecov-action`, and
  `docker/setup-buildx-action` are bumped to their current Node-24 majors. No
  behavioral change to CI or the release pipeline. A grouped monthly Dependabot
  config (`.github/dependabot.yml`) now keeps the actions current so the next
  runtime deprecation arrives as a routine PR.
- **`bump-my-version` no longer creates a local `vX.Y.Z` tag** (`tag = false` in
  `[tool.bumpversion]`). The Release workflow already creates the authoritative
  tag on the server after the CI-green gate; dropping the local tag removes a
  must-never-push footgun and the post-release tag-reconciliation step. Release
  procedure docs updated accordingly.

### Fixed

- **Notebook-worker output is now written with LF newlines on every platform**
  (PR #260). All notebook-worker outputs (executed `.ipynb`, jupytext `.py`, and
  the HTML body) go through one `open(..., "w")` write that, lacking a `newline=`
  argument, let Python's universal-newline translation rewrite every `\n` to
  `os.linesep` — CRLF on Windows Direct workers, LF on Docker/Linux. So a Windows
  `clm build` produced CRLF working-tree files, yielding trailing-`^M` diffs and
  "CRLF will be replaced by LF" warnings in course repos that normalize to
  `eol=lf`. The write now pins `newline="\n"`, matching the convention used
  elsewhere in the codebase, so output is byte-identical regardless of host OS.
- **Pool-shutdown orphan jobs are no longer mis-blamed on the user** (PR #259).
  A worker-pool shutdown race could leave a valid job (e.g. a drawio diagram)
  stamped with the orphan sentinel; the drawio categorizer matched none of its
  specific patterns and fell through to `error_type="user"` ("Check your DrawIO
  diagram for errors"). Because user errors are persisted to `processing_issues`
  (and the content hash never changes), the stale error was then **replayed on
  every subsequent cached build**. `categorize_job_error` now checks for the
  orphan sentinel *before* the per-job-type dispatch and returns an
  `infrastructure` / `orphaned_job` error with re-run guidance — infrastructure
  errors are not cached, so the transient failure stays out of the replay store
  and the false diagram-blame disappears. The central placement also covers
  notebook and plantuml orphans from the same race.

## [1.9.0] - 2026-06-06

### Added

- **The slide authoring tooling now works on C#/C++/Java/TypeScript decks, not
  just Python.** CLM's *build* has long been multi-language, but the authoring
  tooling — `slides split` / `unify`, `sync`, `voiceover extract` / `inline`,
  `assign-ids`, `normalize`, `validate`, `coverage` / `lang-coverage` /
  `language-view`, deck discovery, and `translate` / `bootstrap` — hardcoded the
  Python `#` comment token and `.py` extension. The deck's comment token (`//`
  for the c-family, `#` for Python) and extension are now threaded through every
  authoring consumer, resolved per file from the extension via
  `comment_token_for_path()`. The `//`-family languages (C#, C++, Java,
  TypeScript) get the same split/sync/voiceover authoring workflow Python already
  had, including `.de`/`.en` split companions that preserve the deck extension
  (`*.de.cs`, `*.en.cpp`). The default everywhere stays `#`, so **Python output
  is byte-identical**. This completes the multi-language authoring migration whose
  structural prerequisite (the header-line-less title convention) shipped in
  1.8.4. Investigation + validation:
  `docs/claude/multi-language-authoring-tooling-investigation.md` §10.

### Changed

- **`sync translate` prompts are now language-aware.** A code cell in a `//`-deck
  is translated as that language (e.g. C# is translated as C#, not "runnable
  Python"), and the model is instructed to preserve `// ` comment prefixes. The
  programming language folds into the translation-cache key for non-Python decks,
  so cached Python translations are unaffected.
- **`--help` text and `clm info` topics use a language-neutral `<ext>`
  placeholder** instead of implying `.py`-only, and two now-inaccurate
  "Python-only" claims in the info topics were corrected.

### Fixed

- **The build no longer silently drops voiceover on `//`-family decks.** The
  host-side voiceover companion merge and the authoring `voiceover` writers were
  Python-token-only, so a `voiceover_*.cs` / `*.cpp` companion was never merged
  into the built notebook. The merge, the companion writers, and `extract` /
  `inline` are now token-aware, and the `SKIP_OUTPUT` globs were broadened so
  `voiceover_*.{cs,cpp,java,ts}` companions never leak into output.
- **`assign-ids` / `normalize` / `validate` now discover `.cs` / `.cpp` / `.java`
  / `.ts` decks.** Deck discovery (`rglob` + `is_slides_file`) and `validate`
  kind-inference were Python-extension-only; the `//`-family decks are now found
  and dispatched.
- **Comment-prefix stripping removes the literal prefix, not a character set.**
  `_strip_comment_prefix` previously used `lstrip("// ")`, which strips *any*
  leading `/` or space — turning `// /usr/bin` into `usr/bin` and collapsing a C#
  `///` doc comment. It now strips the exact `// ` prefix once.

## [1.8.4] - 2026-06-06

### Changed

- **C#/C++/Java/TypeScript decks now use the Python "header-line-less" title
  convention.** The `header` macro in `templates_{cpp,csharp,java,typescript}`
  now emits its own leading `%% [markdown] lang="de"` cell boundary (like
  `templates_python` already did), so a deck's title is written as a standalone
  `// {{ header("DE", "EN") }}` j2 call with **no** authored `// %% [markdown]`
  wrapper cell. This makes one title convention across all languages, which is
  the structural prerequisite for the multi-language authoring tooling (split
  decks, `voiceover extract/inline`, `assign-ids`, `normalize`, `sync`). It also
  fixes a latent bug in `//`-family decks that used a *neutral* wrapper
  (`// %% [markdown] tags=["slide"]`): the German title content lived in a
  language-neutral cell and therefore leaked into the **English** build (the EN
  slides showed two titles); each language now has exactly one title.
  **Migration:** existing `//`-family course decks must drop the wrapper line —
  run `python scripts/reformat_header_convention.py <slides-dir> --apply` (see
  `clm info migration`). A reformatted deck *requires* this release, so reformat
  in lockstep with bumping the course's CLM pin. Python (`#`) decks are
  unchanged. Investigation + validation: `docs/claude/multi-language-authoring-tooling-investigation.md` §10.

### Added

- **`header_de` / `header_en` sibling macros for the `//`-family templates**
  (C#/C++/Java/TypeScript), mirroring `templates_python` — the per-language
  title macros that split decks (`*.de.cs` / `*.en.cpp`) use.
- **`scripts/reformat_header_convention.py`** — migrates `//`-family decks to the
  header-line-less convention (dry-run by default; handles the simple,
  neutral-wrapper, `clang-format`-wrapped and split-cell shapes; idempotent).
- **`scripts/verify_header_reformat.py`** — corpus invariant checker: reformats
  every deck, builds it with the current macro, and asserts exactly one title
  slide per language. Verified across all 560 real decks (cpp 302, cs 131,
  java 78, ts 49): 0 outliers, 0 violations.

### Fixed

- **A voiceover authored after a mid-slide-group j2 cell now keeps its exact
  position across `clm voiceover extract` → build / `inline` (#247).** A j2
  macro cell (header `# {{ … }}` / `# j2 …` — e.g. an inline widget) embedded
  between a slide's content and a following voiceover was an invisible anchor
  barrier: `extract`'s predecessor-walk skipped j2 cells, so the voiceover was
  anchored to the content cell *above* the macro and the build merge / `inline`
  re-inserted it *before* the macro — content-preserving but not byte-identical,
  and reported clean (no relocation, nothing unmatched). A mid-group j2 cell is
  now an eligible positional anchor, matched by its body fingerprint (stable
  because the companion merge runs *before* j2 expansion), so the voiceover
  returns to its authored slot after the macro. The title-slide macro keeps its
  dedicated `tm:title#0` anchor (#246), and legacy companions with no
  `vo_anchor` are unaffected.
- **A title-slide greeting voiceover now keeps its exact position across
  `clm voiceover extract` → build / `inline` (#246).** Follow-up to #242, which
  fixed the title greeting being *dropped*; this fixes it being *reordered*.
  `extract` stamped the title voiceover with `for_slide="title"` but **no
  `vo_anchor`** (its backward predecessor-walk skips the slide_id-less j2 title
  macro), so on merge the greeting was appended at the **end** of the title
  slide's cell group. When the greeting was authored *before* the title slide's
  trailing `keep`/code cells, the built notebook therefore moved it after those
  cells — content-preserving but not byte-identical to the inline build.
  `extract` now records a title-macro anchor (`vo_anchor="tm:title#0"`) for a
  title greeting with no content predecessor, and the merge / `inline` resolve it
  to the title macro cell, restoring the greeting immediately after the title
  slide. A greeting authored *after* a title-slide content cell anchors to that
  cell as usual (`fp:`), and legacy companions with no `vo_anchor` keep the
  previous group-end placement, so already-built decks are unaffected.
- **A `.c` source file no longer crashes programming-language resolution.**
  `EXTENSION_TO_PROG_LANG` mapped `.c` to a `"c"` language that has no entry in
  the worker's `prog_lang_utils` config (no jinja/jupytext/kernel), so any code
  path that resolved a `.c` slide path through to `prog_lang_utils` raised
  `ValueError: Unsupported language: c`. `.c` now resolves to `cpp` (the xcpp
  kernel compiles C as C++); the dead `"c"` reverse mapping is removed.

## [1.8.3] - 2026-06-06

### Fixed

- **A title-slide greeting voiceover now survives `clm voiceover extract` (#242).**
  The title slide is generated by the j2 `header` / `header_de` / `header_en`
  macro and carries no `slide_id` of its own; the greeting voiceover attaches by
  the `slide_id="title"` convention. Previously `extract` wrote the companion
  cell with no `for_slide` (its backward owner-walk skipped the j2 macro), so the
  build merge dropped the narration — a hard error under `--fail-on-error`/CI —
  and `clm voiceover inline` stranded it. Both sides now recognize the title
  macro (reusing `is_title_macro_cell` / `TITLE_SLIDE_ID`): `extract` stamps
  `for_slide="title"`, and the build merge / `inline` anchor a `for_slide="title"`
  (or legacy `slide_id="title"` with no `for_slide`) voiceover to the title slide,
  matching the inline-build output byte-for-byte. Companions extracted before the
  fix merge without a re-extract. Adding `slide_id` to the macros (the issue's
  alternative) is neither necessary nor sufficient — the merge runs before j2
  expansion and the worker strips `slide_id`/`for_slide` from output.
- **`load_worker_config` no longer poisons the global config singleton (#223).**
  Applying CLI `--workers` overrides mutated `get_config().worker_management` —
  a process-global singleton — in place, so a build/test that resolved to Docker
  mode permanently flipped `default_execution_mode` to `"docker"` for every later
  call in the process; a subsequent override-free build then built a Docker
  executor with no image and raised `ValueError: Docker execution mode requires
  'image'`. Order-dependent under `pytest-xdist`, so it surfaced as a rare CI
  flake. The loader now deep-copies (`model_copy(deep=True)`) before applying
  overrides, so each call is self-contained and the shared default can't be
  poisoned.

### Added

- **`clm slides sync` now reconciles a committed mismatched-id twin instead of
  only refusing it (#228, strategy B).** When a committed split pair shares a
  `slide_id` but gave one slide a *different* id on each half (e.g. a per-half
  `assign-ids`), sync previously refused the ambiguous both-directions bucket to
  avoid silently doubling the slide (#226). With a correspondence verifier
  available (the default `--verify-cold-pairs` when `$OPENROUTER_API_KEY` /
  `$OPENAI_API_KEY` is set), sync now cross-pairs the suspects by content
  correspondence (the cheap Haiku verifier) and, for a confirmed twin, **rewrites**
  the divergent id so both halves share one (EN-authority) — no manual fix needed.
  Leftover suspects with no confirmed twin use a direction-guarded hybrid:
  single-direction → cross-add the genuinely-distinct slide; both-direction →
  defer. No provider or an unconfirmed pair → refuse, exactly as before (never
  bakes a wrong id). Surfaced as a `reconcile` proposal in the plan / `--json` /
  dry-run.

## [1.8.2] - 2026-06-06

### Changed

- **Releases can now be cut merge-driven, not only by an explicit tag.** The
  release workflow also triggers on a `master` push that introduces a
  `Bump version …` commit (e.g. merging a bump PR with a merge commit): it
  gates on CI, then creates the `vX.Y.Z` tag and publishes. Pushing a `vX.Y.Z`
  tag directly still works as a fallback. Every external action (tag, PyPI
  upload, GitHub Release) is idempotent, so a re-run never double-publishes. See
  `docs/developer-guide/releasing.md`.

## [1.8.1] - 2026-06-05

### Changed

- **Docker worker images fetch their large build inputs at build time instead
  of vendoring them as git LFS objects (#239).** `deno` (v2.1.1), `ijava`
  (v1.3.0), and the Draw.io `.deb` (v24.7.5) are now pulled from their pinned
  GitHub release URLs and verified with a `sha256sum -c` guard inside the
  Dockerfiles, mirroring the existing micromamba pattern, and the three LFS
  binaries (plus an unused orphan `packages-microsoft-prod.deb`) were removed.
  The CI `test` matrix also stops pulling `docker/**` LFS objects it never used
  (`git lfs pull --exclude="docker/**"`). This cuts the repo's LFS bandwidth and
  storage cost; building the worker images is unchanged for users (the
  `docker-test` CI job builds all three and validates the checksums).
- **Releases are now cut by an automated tag-triggered workflow**
  (`.github/workflows/release.yml`). Pushing a `vX.Y.Z` tag waits for CI to be
  green on that commit, then builds, publishes to PyPI via Trusted Publishing
  (OIDC), and creates the GitHub Release from the CHANGELOG. See
  `docs/developer-guide/releasing.md`.

## [1.8.0] - 2026-06-05

### Added

- **`clm spec decks` and `clm slides referenced-by` — spec→deck resolution.**
  `clm spec decks <spec.xml>` lists the decks a course spec actually pulls in
  (its "shipping set"), mirroring the build's resolution exactly: a `<topic>`
  resolves to a topic *directory* and **every** `slides_*.py` in it is a deck.
  A deck-filename-stem heuristic silently misses decks (a topic dir name often
  differs from its deck filenames), which is what motivated this command. Module
  bindings resolve in their module; unbound duplicate IDs are first-occurrence-wins
  (the shadowed matches surface in `--json`). `--lang de|en|both` filters split
  halves (bilingual decks always survive); `--all-specs DIR` emits the union
  shipping set across every spec annotated with the referencing spec(s).
  `clm slides referenced-by <deck.py>` is the reverse lookup (or reports
  `unreferenced`). First of the course-conversion tooling gaps documented in
  `docs/claude/course-conversion-tooling-gaps.md`.
- **`clm validate <spec> --deep`, `--summary`, and `--shipping-only` — deep /
  scoped validation with a category rollup.** `clm validate <spec.xml>` validates
  only the spec *structure* (topic resolution); it does **not** check the slide
  content of the referenced decks, so "spec validates OK" never meant the decks
  were clean. `--deep` now runs the full slide validator on every deck the spec
  pulls in (resolved with the same build-faithful logic as `clm spec decks`) and
  reports structure + content together, failing on either. `--summary` rolls
  findings up into a category/kind histogram with per-deck counts (by-category is
  exact; by-kind — `missing-slide_id`, `adjacency`, `count-mismatch`,
  `start-completed`, … — is a heuristic message bucket with an `other` fallback)
  instead of a flat list of thousands of lines; on a spec it implies `--deep`.
  `--shipping-only` restricts a directory validate to the decks reachable from
  course specs (`--specs-dir`, default `<course-root>/course-specs/`), skipping
  archived / unreferenced decks — and, because it filters the resolved shipping
  set rather than walking, it correctly covers non-`.py` decks (`.cs`, `.cpp`)
  that the plain directory walk misses. Second of the course-conversion tooling
  gaps. New public helper `clm.slides.validator.validate_files` validates an
  explicit deck list with the same per-pair parity as a directory walk.
- **`clm course gate` — corpus readiness orchestrator.** Runs the mechanical
  conversion passes (`tag_migration`, `workshop_tags`, `interleaving`,
  content-derived `slide_id` minting) over a spec's shipping set (or a directory),
  then splits the remaining work into **mechanical** (what the passes changed /
  would change) versus **needs-author** (what the normalizer *refused* to touch:
  a `slide_id` with no derivable heading, a DE/EN pair whose code diverged too far
  to auto-interleave, or a DE/EN cell-count mismatch). Default is a dry run that
  writes nothing; `--apply` writes the fixes and re-validates, reporting the
  residual. Exits non-zero while author work (or a post-apply residual error)
  remains, so it gates a conversion in CI — turning a validator bump into
  `clm course gate <spec> --apply`. Third of the course-conversion tooling gaps;
  the report a conversion agent previously hand-built. New `spec` and `course`
  command groups accompany these tools.
- **`clm slides assign-ids` / `clm slides normalize` gained `--only`, `--exclude`,
  and `--shipping-only` scoping.** A directory run can now be restricted to part
  of the corpus: `--only bilingual|split` (touch only bilingual decks, or only
  `.de`/`.en` split halves — e.g. mint bilingual decks while leaving split pairs
  for `clm slides sync`), `--exclude GLOB` (skip decks matching a glob, matched
  against the full path *and* each path component, so `--exclude _archive` skips
  an `_archive/` dir; repeatable), and `--shipping-only` (`--specs-dir`, default
  `<course-root>/course-specs/`) to touch only decks reachable from specs. This
  replaces the "run over everything, then `git checkout` the files you shouldn't
  have touched" workaround. Split pairs are still detected within the scoped set,
  so EN-authority parity minting is preserved. Fourth of the course-conversion
  tooling gaps. New public helpers `clm.slides.assign_ids.assign_ids_in_files`,
  `clm.slides.normalizer.normalize_files`, and module `clm.slides.deck_scope`.
- **`clm slides assign-ids --report-refusals [--context]` — a hand-authoring
  worklist.** Hard refusals (a slide with no heading and no extractable content)
  can only be cleared by hand-authoring a `slide_id`, and to write a good one you
  need the cell's body and where it sits in the deck. `--report-refusals` emits a
  worklist of the cells that could not be assigned — hard refusals first, then
  soft (extractable, with a proposed slug) — instead of the assignment listing.
  `--context` (which implies `--report-refusals`) attaches each refused cell's
  marker line, full body, and the nearest preceding `slide_id`/heading so an
  author or agent can fill the id without opening the file. Honors the same
  scoping flags; `--json` emits it structured. Replaces the throwaway "dry-run
  JSON → re-extract cell bodies and context with a script" step. Fifth of the
  course-conversion tooling gaps. New module `clm.slides.refusal_report`
  (`build_refusal_worklist` / `render_worklist` / `worklist_to_dict`).
- **`clm slides slug-report` — flag low-quality content-derived slugs.** A bulk
  `assign-ids --accept-content-derived` mints thousands of ids; most are fine but
  a minority are low-information — single generic tokens (`data` / `true` /
  `value`), very short code-identifier-shaped slugs (`cp` / `df` / `os`), or slugs
  that hit the 30-char cap and lost their trailing words. `slug-report` classifies
  each `slide_id` by cheap, source-independent heuristics and lists just the
  flagged minority, with a `--min-severity low|medium|high` cutoff (`high` =
  very-short / generic). `PATH` is a directory (with the same `--only` /
  `--exclude` / `--shipping-only` scoping as `assign-ids`) or a spec `.xml`
  (resolved to its shipping decks). Only slide-start cells are inspected and a
  bilingual deck's DE/EN twins yield one finding; `--json` adds `by_severity` /
  `by_issue` histograms. Exit code is always `0` (it's a report). Sixth of the
  course-conversion tooling gaps. New module `clm.slides.slug_quality`
  (`classify_slug` / `scan_slug_quality` / `render_report` / `report_to_dict`).
- **`clm spec orphans` — decks reachable from no spec, plus cruft.** The inverse
  of `clm spec decks`: scan every spec in a course and report the decks on disk
  that no spec pulls in, grouped by likely intent so dead decks can be archived
  without deleting intentional alternates — `superseded` (`_old` / `_bak` /
  `_orig` / `_vN` / trailing `_N`, usually safe to archive), `alternate`
  (`_partN` / `_short` / `_long`, probably-intentional content), and `unknown`
  (no marker — review). Orphans are computed against the **union** of every spec
  (a deck unreferenced by one spec may be pulled in by another), and the on-disk
  walk is extension-complete (`.py` / `.cpp` / `.cs` / …) so a non-Python orphan
  is not silently missed. Also surfaces gitignored `.ipynb_checkpoints/` cache
  cruft, with `--clean-checkpoints` to delete it; `--kind` filters to one bucket;
  `--json` adds `by_kind` counts. Exit code is always `0` (it's a report).
  Seventh of the course-conversion tooling gaps. New module
  `clm.core.spec_orphans` (`find_orphans` / `classify_orphan` /
  `find_checkpoint_dirs` / `render_report` / `report_to_dict`).
- **`clm slides coverage-report` — DE/EN completeness per deck.** Among
  count-mismatch validation errors, a deck that exists in only one language
  (needs translation) and a bilingual deck off by a cell or two (an alignment
  fix) are very different jobs. This separates them by counting `lang="de"` vs
  `lang="en"` slide cells per deck and classifying each as `de_only` /
  `en_only` / `imbalanced` (shown with a `Δ`) / `balanced`. Split `*.de.py` /
  `*.en.py` halves are scored as one pair — a half whose twin is absent counts
  the missing language as zero — and only slide/subslide cells are counted, so
  one-language speaker notes don't skew the result. `PATH` is a directory (with
  the same `--only` / `--exclude` / `--shipping-only` scoping as `assign-ids`)
  or a spec `.xml`; `--status` filters to one bucket; `--json` adds `by_status`
  counts. Exit code is always `0` (it's a report). Eighth of the
  course-conversion tooling gaps. New module `clm.slides.lang_coverage`
  (`count_languages` / `classify_counts` / `scan_coverage` / `render_report` /
  `report_to_dict`).
- **`clm validate` / `clm slides normalize` enforce cell spacing (#238).**
  `clm validate` gained two default-on `format` warnings: a cell that is not
  separated from the previous one by a blank line, and a markdown cell whose
  body does not open with a blank comment line (`#`) before its content — both
  render and diff poorly otherwise. The canonical j2 title-header block is
  exempt (its two directives are intentionally tight-coupled), and the checks
  run on the raw, whitespace-preserving cells since the parsed `Cell` model
  strips inter-cell blank lines. `clm slides normalize` gained a matching
  default-on `cell_spacing` operation that inserts the missing blank line and
  promotes/adds the leading `#`, so `normalize` clears both warnings
  mechanically.

### Removed

- **The flat top-level CLI aliases were removed (1.8 milestone, #158).**
  The flat command names deprecated since CLM 1.6 — `clm normalize-slides`,
  `clm language-view`, `clm suggest-sync`, `clm search-slides`,
  `clm resolve-topic`, `clm authoring-rules`, `clm validate-slides`,
  `clm validate-spec`, `clm extract-voiceover`, `clm inline-voiceover` —
  no longer exist. Use the verb-grouped invocations (`clm slides normalize`,
  `clm topic resolve`, `clm validate`, `clm voiceover extract`, …). See
  `clm info migration`.
- **`clm build --keep-directory` was removed (#158).** It had been a no-op
  alias since keeping the output tree became the default. Drop it; use
  `--clean` to opt into the legacy wipe-and-restore flow.

### Changed

- **`clm validate`: missing `slide_id` and DE/EN non-adjacency are now
  errors (#158).** A `slide`/`subslide` cell missing a `slide_id`, and a
  DE/EN content/voiceover pair separated by an intervening language-tagged
  cell, escalated from `warning` to `error` (the two-release deprecation
  window that began in 1.6/1.7 closed in 1.8). Fix missing ids with
  `clm slides assign-ids` (or `clm slides sync` for a split deck) and
  non-adjacency with `clm slides normalize`. This fails the pre-commit gate
  and the PostToolUse hook until cleared.
- **MCP tool names aligned to the CLI verb-group scheme (#158).** The MCP
  server's tools were renamed group-first with no aliases:
  `resolve_topic → topic_resolve`, `search_slides → slides_search`,
  `normalize_slides → slides_normalize`,
  `get_language_view → slides_language_view`,
  `suggest_sync → slides_suggest_sync`,
  `extract_voiceover → voiceover_extract`,
  `inline_voiceover → voiceover_inline`,
  `course_authoring_rules → authoring_rules`. `validate_spec` and
  `validate_slides` are consolidated into a single `validate` tool that
  dispatches on input type, mirroring the CLI. `course_outline` and the
  `voiceover_*` family are unchanged. Update `.mcp.json`, CLAUDE.md /
  AGENTS.md tool tables, and agent prompts.

### Fixed

- **`clm slides sync` no longer re-surfaces a clean edit as a phantom conflict
  when a tag-only conflict also warns (#202).** A both-decks tag conflict (the
  `both` branch of the #198/#200/#201 retag paths — the same role-preserving tag
  changed on *both* halves) used to hold the **whole-deck** watermark: any clean
  edit applied in the same pass reached disk but was never baselined, so it
  re-appeared as a spurious `conflict` on every subsequent run until the unrelated
  tag conflict was resolved by hand (loud and lossless, but non-idempotent). A
  tag-only conflict touches no cell *body*, so sync now scopes the hold to just
  that one cell's tags — pinning them at the old baseline so the conflict still
  re-surfaces — while the partial watermark advance banks the co-applied edit (and
  every other reconciled cell). Both the id-carrying (#200, keyed by
  `(slide_id, role)`) and id-less (#201, keyed by position) `both` paths are fixed
  together. Structural warnings (both-decks reorder, ambiguous de/en state,
  shared-cell auto-heal) still hold the whole watermark, as before.

## [1.7.0] - 2026-06-04

### Added

- **`clm slides sync DIR` — directory batch mode (§8a).** Pass a directory and
  every `.de`/`.en` deck pair under the tree is synced in one pass. Enumeration is
  prefix-agnostic (un-prefixed decks like `apis.de.py` count too) and descends the
  whole subtree; voiceover companions (`voiceover_*`) are ignored. A half with **no
  twin** under the tree is **skipped with a warning**, never synced against a
  phantom empty twin. The sweep **continues past a failing pair** (recording it as
  errored) and the process exit code is the **worst** over all pairs (`0` clean <
  `1` review < `2` error); a per-pair one-liner plus a `N pair(s): X clean, Y
  review, Z errored` rollup is printed. A **writing** directory run requires
  **`--yes`** (or an interactive confirm) since it writes to every pair at once;
  `--dry-run` / `--explain` directory runs are unprompted. `--interactive` stays
  single-pair only. `--json` over a directory returns an envelope
  `{ "mode", "root", "exit_code", "pairs": [ … ] }`, each `pairs` entry being one
  single-pair object. Additive — passing a single file or a pair is unchanged.
- **`clm slides sync` accepts a single path (§8 single-path contract).** `EN_PATH`
  is now optional: pass one half (`clm slides sync slides_x.de.py`) and the twin is
  derived from disk, or pass the bilingual deck stem (`slides_x.py`, when it still
  exists on disk) to derive both halves. Derivation is prefix-agnostic (`apis.de.py` works) and the resolved pair
  still runs through the pairing guard; a missing twin is a clear usage error (sync
  never invents a translated half). The two-path form is unchanged.
- **`clm voiceover extract` auto-pairs on a split half (§8 paired extract).**
  When the target is a split half (`<deck>.de.py` / `<deck>.en.py`) whose twin
  exists on disk, both companions are extracted in one op: the two halves are
  first minted with **EN-authority** `slide_id`s across both at once, then each
  is extracted, and all writes commit atomically — so the two companions'
  `for_slide` sets agree by construction (closing the footgun where extracting
  each half by hand could mint divergent slugs). `--single` opts out (legacy
  single-half behavior), `--both` forces the paired form, and a structurally
  non-alignable pair is **refused** rather than risk divergence. The `--json`
  output for a paired extract carries `"paired": true` + a `"companions"` array;
  the MCP `extract_voiceover` tool gains matching `both` / `single` params. (This
  is a behavior change to a bare `extract <deck>.de.py` — see `clm info
  migration`.)
- **`clm slides sync` now mirrors a tag-only edit across split halves
  (#198).** Adding or removing a role-preserving tag (`keep`, `alt`, …) on
  one half of a split deck used to be silently dropped — the body-hash
  classifier reported the cell as in sync and the other half was never
  updated. Sync now emits a dedicated `retag` proposal when a matched cell's
  tag set drifted from the watermark baseline on **exactly one** side, and
  mirrors it onto the twin with a header-only rewrite (no LLM — tags are
  language-independent); a both-sides change is surfaced as a warning rather
  than guessed. Tier C extends this to **id-less localized cells** (a `lang=`
  code/markdown cell with no `slide_id`, e.g. the `response = llm.invoke(...)`
  cell the report hit): they have no per-cell `(slide_id, role)` key, so they
  are paired by position in their language's cell stream (the membership-widened
  watermark identity, #190 item 3) and guarded by a per-cell body-hash anchor so
  a reorder or body edit never mis-mirrors a tag. `clm validate` already flags a
  committed tag asymmetry it can no longer attribute to one side.
- **`clm slides sync --provider {openrouter,local}`** (and the
  `$CLM_SYNC_PROVIDER` default) to choose the edit-reconciliation judge
  backend. The edit judge now **defaults to Claude Sonnet via OpenRouter**
  (much faster than the local Ollama model that was the only option before);
  pass `--provider local` for the offline Ollama judge. The OpenRouter backend
  needs `$OPENROUTER_API_KEY` (or `$OPENAI_API_KEY`); without a key, edits are
  recorded as errors (exit 2) instead of guessed. `--llm-model`'s default now
  depends on `--provider` (`anthropic/claude-sonnet-4-6` for openrouter,
  `qwen3:30b` for local). The OpenRouter client construction is shared with the
  new-slide translator. A step toward per-purpose model configurability
  (#167).
- **`clm completion <shell>`.** New command that emits a shell completion
  activation script for `bash`, `zsh`, `fish`, or `powershell`. Bash/Zsh/Fish
  use Click's native completion generator; **PowerShell** support (the gap in
  Click's built-in completion) is provided by CLM via a
  `Register-ArgumentCompleter` script that reuses Click's completion protocol,
  so PowerShell gets the same context-aware command, option, and value
  completions as the POSIX shells. Pass `--install-hint` to print per-shell
  instructions for making completion permanent. (Closes #22.)
- **`clm cassette doctor` command.** New offline tool to detect (and with
  `--fix`, repair) *chain-orphan* interactions in canonical HTTP-replay
  cassettes (issue #125). A chain-orphan is a chat-completion response whose
  extracted text (`choices[].message.content`, or accumulated streaming
  `delta.content`) is at least `--min-text-len` characters (default `50`) yet
  appears in no other interaction's request body — almost always a
  chain-opener whose closer was never recorded. This covers the two residual
  cases the issue-#115 completion-marker fix cannot: cassettes poisoned
  *before* that fix landed, and the `try/except`-swallowed chain-closer case
  the marker logic structurally cannot catch. The command walks every
  `*.http-cassette.yaml` under the spec's source tree (or the current
  directory when the `SPEC-FILE` argument is omitted), reports per cassette
  (URI, request-body fingerprint, response excerpt), and supports `--json`
  for machine-readable output. `--fix` rewrites cassettes via the existing
  atomic-write helper so the next build re-records the broken chain; it is
  diagnostic-only by default. Detection is deliberately a substring match —
  fuzzy/LLM matching and auto-repair correctness guarantees are out of scope.
- **Cross-references between notebooks (issue #17).** Link from one notebook
  to another with the `[text](clm:topic-id)` Markdown scheme. CLM resolves
  the `clm:` href at build time to the correct relative path to the
  same-variant (language, kind, format) target notebook, surviving renames
  and reordering. An optional `/notebook-stem` disambiguator selects one
  deck inside a directory topic that contains several slide notebooks;
  without it CLM resolves deterministically and emits a
  `cross_reference_ambiguous` warning. Per-format behavior: `html`/`notebook`
  get working links, `code` drops the link (text only), `jupyterlite` is
  deferred (link text left verbatim). A reference to a topic not included in
  the build is reported as `cross_reference_target_missing` — a hard error
  under `--http-replay=replay` (CI-strict) and a warning + dropped link
  otherwise, controlled by `--fail-on-missing-xref / --no-fail-on-missing-xref`
  and `CLM_FAIL_ON_MISSING_XREF` (mirrors `--fail-on-error`). `clm validate-spec`
  also reports missing and ambiguous cross-references. v1 limitations:
  no `#anchor`/sub-section targets, no cross-course references. See
  `clm info spec-files` → "Cross-references".
- **`clm slides normalize --canonicalize-start-completed`.** New opt-in
  flag for the interleaving operation. By default the normalizer leaves a
  `start`/`completed` cohesion pair (`[DE_start, DE_completed, EN_start,
  EN_completed]`) untouched when the DE and EN code differ — the
  content-similarity gate can't confirm the pairing, so it reports a
  `similarity_failure` review item. This is the layout that breaks the
  byte-identical `unify(split(deck)) == deck` round-trip, because
  `clm slides unify` always emits the canonical interleave
  (`[DE_start, EN_start, DE_completed, EN_completed]`). With the flag,
  such pairs are paired *structurally* (by `start`/`completed` tag and
  position rather than content) and forced into the canonical interleave,
  so a deck can be normalized before a split and round-trip exactly.
  Default-off preserves the cohesion layout for decks that are not being
  converted. Available on the CLI and the `normalize_slides` MCP tool.
- **`clm validate` workshop-scope check (issue #78).** The `tags` check
  group now warns when a markdown cell carries a `# Workshop …` heading
  (any `#`-count / whitespace, case-sensitive `^#+\s*Workshop\b`) but no
  workshop scope covers it. Without a scope — opened by a `workshop` tag or
  a slide-start cell whose `slide_id` starts with `workshop-` — the
  `partial` output kind silently renders every code cell instead of leaving
  the exercise empty for the code-along. Continuation headings such as
  `## Workshop (Continued)` inside an already-open scope are not flagged.
  Runs in `--quick` mode as well.

- **Per-cell timing instrumentation in the notebook worker (issue #143).**
  `TrackingExecutePreprocessor` now logs a `cell N/total begin` line before
  and a `cell N/total done in Xs` line after every cell at DEBUG, plus an
  INFO `slow cell` line for cells slower than
  `CLM_SLOW_CELL_LOG_THRESHOLD_SECONDS` (default 60s). When a build later
  times out, the last `begin` line with no matching `done` line pinpoints
  the stuck cell. New opt-in `CLM_CELL_TIMEOUT_SECONDS` env var sets
  nbclient's per-cell `timeout` so a hung cell raises a normal cell error
  instead of blocking the whole build until the job timeout fires (the
  build worker previously always ran cells with `timeout=None`, unlike a
  direct `jupyter execute --timeout=120`).

### Changed

- **Split-deck command-surface hardening (§8 safety + hygiene).**
  - **`clm slides sync` pairing guard.** Before any read or write, sync now
    verifies the two paths are the two halves of one deck (one `.de`, one `.en`,
    same name — the routing prefix is not required, so `apis.de.py` /
    `apis.en.py` works). A **swapped** order is auto-corrected with a stderr
    note; the **same file** twice, **two same-language** halves, **two different
    decks**, or a path that is **not a split half** are rejected with a usage
    error (exit 2) before any LLM call. Closes the #162 footgun where a
    mismatched pair could silently produce a divergent or no-op sync.
  - **`clm slides assign-ids` and `clm slides suggest-sync` are now plumbing
    (hidden).** Both are removed from `clm slides --help` but stay fully
    invocable by name (and `suggest-sync` remains the `suggest_sync` MCP tool).
    Per-file `assign-ids` on a single split half is the #1 silent #162 break;
    for everyday authoring let the funnels mint ids — `clm slides sync` (split
    decks) and `clm slides normalize`. `suggest-sync` is the read-only
    bilingual-file suggester, superseded by `sync` for split decks.
- **The `voiceover` slide-coverage check is now opt-in (issue #176).**
  Course-authoring policy changed: voiceover is optional per deck, so the
  coverage check — which reports a gap for every slide / nontrivial code cell
  lacking a voiceover cell — is no longer part of any default, "all", or
  "review" bundle. It runs **only** when named explicitly (`--checks voiceover`
  on the CLI, or `checks=["voiceover"]` via the MCP `validate_slides` tool /
  the `validate_file`/`validate_directory`/`validate_course` library
  functions). The library/MCP `checks=None` default now resolves to a new
  `DEFAULT_CHECKS` bundle (`format`, `pairing`, `tags`, `code_quality`,
  `completeness`) instead of `ALL_CHECKS`; `voiceover` stays a valid name so it
  can still be requested on demand. This removes the false-positive flood the
  MCP review path produced on voiceover-less decks. The CLI default (already
  deterministic-only) and the `--quick` hook path are unchanged.
- **Hardened the HTTP-replay vcrpy fork against silent breakage (issue #143,
  toward #165).** The `[replay]` extra now pins `vcrpy>=8.1.1,<8.2` (was an
  unbounded `>=6.0.0`). The notebook HTTP-replay bootstrap forks vcrpy 8.1.x
  *internals* — notably the issue #143 connection-leak fix, which reinstalls
  vcrpy's `_vcr_handle_request`/`_vcr_handle_async_request` verbatim plus an
  explicit `close()`. An unvalidated vcrpy bump could silently change those
  functions and resurrect the connection-pool deadlock; the tight pin makes
  such a bump fail loudly at resolution time, and a new in-kernel guard fails
  the build early if a kernel resolves vcrpy outside the validated 8.1.x line
  or is missing the forked-internals symbols. A new pin-guard test
  (`tests/workers/notebook/test_http_replay_vcr_pin_guard.py`) detects upstream
  drift, and CI now installs the `[replay]` extra so those tests run there.
  Ready-to-submit upstream patches that would let the fork be retired are in
  `docs/claude/vcrpy-upstream-patches.md`.
- **Worker payloads are reconstructed by total deserialization, not a
  hand-listed field set (issue #17 follow-up).** The notebook worker now
  rebuilds its `NotebookPayload` via `Payload.from_job_payload`, which
  `model_validate`s the entire serialized dict (symmetric with the host's
  `model_dump`), so a newly added payload field can never again be silently
  dropped at the worker boundary — the failure mode that disabled
  cross-references. A malformed job missing a required descriptor field now
  fails loudly instead of being coerced to defaults, and the
  `(kind, prog_lang, language, format)` metadata extraction is centralized in
  `notebook_metadata_tags_from_payload` (used by the worker and the
  result/cache bookkeeping), removing previously divergent fallback defaults.
  A structural round-trip test tied to `NotebookPayload.model_fields` guards
  the invariant for every current and future field.
- **`clm slides sync` is now the single-language authoring command and writes
  by default** (issue #166). Edit **one** half of a split deck
  (`<deck>.de.py` / `<deck>.en.py`) and one pass brings the other half into
  sync: edits are propagated, brand-new slides are translated (OpenRouter
  Claude Sonnet, `--translation-model`) and inserted, removed slides are
  dropped, reorders are mirrored, and a shared `slide_id` is minted onto both
  decks. **Breaking:** the default flipped from dry-run to writing the working
  tree — a bare `clm slides sync de en` now applies changes (nothing is
  committed; review with `git diff`). Pass `--dry-run` for the old preview.
  Direction is now decided **per cell** by diffing each deck against a new
  ordered, per-language structural watermark (`sync_watermarks`, recorded only
  on a successful apply), so the global `--source-lang` flag and the
  `sync_snapshots`-based direction inference are **removed**; a cell edited on
  both decks is isolated as a `conflict` rather than guessed. The legacy
  `--apply` / `--trivial` flags are **removed** (the default already applies;
  use `--interactive` to gate proposals). Edits are still reconciled by the
  local Ollama judge; when Ollama is unreachable, edits are recorded as errors
  (exit 2). See `clm info migration` for the full before/after.
- **`clm recordings check` is now backend-aware** (issue #33). The command
  reads `recordings.processing_backend` and only checks the dependencies the
  active backend actually needs, and the output table header shows which
  backend was checked:
  - `onnx` (default): unchanged — `ffmpeg`, `ffprobe`, `onnxruntime`.
  - `external`: `ffmpeg` + `ffprobe` only; `onnxruntime` is no longer
    required (the externally produced `.wav` is just muxed via ffmpeg).
  - `auphonic`: no longer requires `ffmpeg`/`onnxruntime` (cloud
    video-in/video-out, no local mux). Instead verifies that the API key is
    non-empty and performs a read-only `AuphonicClient.list_presets()`
    round-trip to confirm credentials and connectivity. A new `--offline`
    flag skips the network call and validates config shape only.

  Previously `check` always reported "All dependencies found" based solely
  on `ffmpeg` + `onnxruntime`, giving a false green for the `auphonic`
  backend (e.g. with an unset or rejected API key) and a false red for
  `external` (missing `onnxruntime` it never uses).

### Fixed

- **`clm slides sync` now propagates code cells and auxiliary markdown, not
  only narrative markdown.** The single-language sync previously classified and
  propagated *only* `slide` / `subslide` / `voiceover` / `notes` markdown; every
  code cell and every untagged / `alt` markdown cell was silently dropped — a
  workshop rewrite would leave the translated heading sitting over the **old**
  code, and a new slide's runnable code never reached the other half. Sync now
  handles the full cell set: a **language-neutral** code cell (no `lang=`) is
  copied **verbatim** across both halves; a **localized** code cell (`lang=`,
  with a `slide_id`) is **twinned and re-translated** on an edit (only its
  string literals/comments change — the code stays byte-identical); an id-less
  localized code cell is translated; auxiliary markdown (`alt` or untagged,
  carrying a `slide_id`) syncs like narrative; a new slide brings its code
  along; and code an author **moves between slide groups** follows. A new
  structural pass rebuilds the cell order of each *changed* slide group from the
  edited side, leaving untouched groups byte-for-byte intact. Also implements
  **id-carrying adds** (a new slide/cell authored with a `slide_id` already on
  it, present on one side only) — translated and inserted under the same id
  rather than deferred.
- **`clm slides sync` now loads the project `.env`.** It checked only the
  process environment for the OpenRouter/OpenAI key, so a key kept in `.env`
  (the usual course-repo layout, read by notebooks via `load_dotenv()`) was
  invisible and every brand-new-slide translation silently deferred. Sync now
  walks up from each deck's directory and loads the first `.env` it finds
  (without overriding already-exported variables) before resolving the
  judge/translator. Add `--no-env-file` to opt out; `--dry-run` never loads it.
- **A transient edit-judge / translation failure no longer drops a cell.** A
  single timeout or rate-limit on a hosted call previously errored the cell
  outright and the run proceeded with a partial result; the OpenRouter judge and
  translator now retry with bounded exponential backoff, and the **local**
  `--llm-timeout` default is raised to 300s (a large local reasoning model can
  legitimately spend minutes on a substantial cell, which the 120s default
  starved). A persistently-unavailable backend is still surfaced as an error,
  never guessed.
- **Cross-references (`clm:`) now actually render as working links
  ([#17](https://github.com/hoelzl/clm/issues/17)).** Two defects made the
  feature a no-op end-to-end as first merged: (1) the notebook worker
  reconstructed its payload from a hand-listed set of fields and silently
  dropped `cross_references` (and `svg_available_stems` / `inline_images`), so
  the resolved href map never reached the rewrite step and every `clm:` link
  shipped verbatim; the worker now deserializes the whole payload via
  `model_validate`, so no field can be dropped again. (2) Resolved hrefs were
  not URL-encoded, but CLM output filenames are `"{NN} {title}{ext}"` and the
  embedded space is not a valid Markdown link destination — renderers
  (nbconvert, JupyterLab/VS Code) left `[text](02 Foo.html)` as literal text
  rather than an anchor; hrefs are now percent-encoded
  (`02%20Foo.html`). Added a Markdown→HTML rendering regression test and a
  payload round-trip test.
- **`clm validate` no longer false-errors on split slide files
  ([#160](https://github.com/hoelzl/clm/issues/160)).** The bilingual DE/EN
  `pairing` sub-checks — cell-count parity, per-pair tag/type consistency, and
  DE/EN adjacency — are now skipped on single-language split halves
  (`*.de.py` / `*.en.py`), detected via the same `.de`/`.en` stem logic the
  build-time split routing uses. A `.de.py` legitimately contains only German
  cells, so the old unconditional check reported a spurious
  `DE/EN cell count mismatch: N German, 0 English` on every converted deck,
  burying real findings as the language-split migration proceeds. The
  applicable checks are unchanged: `format`, `tags`, the per-language review
  checks, and the per-file `slide_id` integrity checks still run on split
  files, and the cross-file shared-cell parity diff between a `.de.py` /
  `.en.py` pair is still applied. Bilingual decks (no `.de`/`.en` suffix) are
  unaffected — the full pairing check still runs.
- **HTTP-replay builds no longer deadlock on a `.batch()` cell
  ([#143](https://github.com/hoelzl/clm/issues/143)).** vcrpy 8.1.x's httpcore
  stub reads the response body and swaps `response.stream` for a buffered
  `ByteStream` but never `close()`s the original httpcore `Response`, so every
  recorded request leaked one pooled connection. A LangChain `.batch()` /
  `RunnableParallel` burst then exhausted httpcore's connection pool and the
  worker threads blocked forever in `wait_for_connection`, hanging the Stage-3
  HTML build until the build-level job timeout fired (and, before
  [#157](https://github.com/hoelzl/clm/issues/157), doing so silently). The
  HTTP-replay bootstrap now reinstalls vcrpy's sync and async httpcore stubs
  with an explicit `close()`/`aclose()` before the stream swap, returning the
  connection to the pool; the recorded bytes are unchanged, so cassettes stay
  byte-identical. As a defense-in-depth safety net, replay-engaged jobs now also
  default a generous per-cell timeout (`CLM_HTTP_REPLAY_CELL_TIMEOUT_SECONDS`,
  default `600`s; set `0` to opt out, `CLM_CELL_TIMEOUT_SECONDS` overrides) so
  any *future* replay-layer hang fails as a clean cell timeout instead of
  stalling to the job timeout. The strategic follow-up — replacing in-process
  vcrpy with an out-of-process transport — is tracked in
  [#165](https://github.com/hoelzl/clm/issues/165).
- **Split and bilingual builds now produce byte-equivalent output
  ([#133](https://github.com/hoelzl/clm/issues/133)).** The notebook
  processor strips jupytext's `lines_to_next_cell` cell metadata from build
  output. That field is a layout artifact jupytext records when the physical
  blank-line count between two cells differs from its PEP 8 lookahead
  heuristic. Because the heuristic depends on the *identity* of the next
  physical cell, a `clm slides split` single-language deck and the original
  bilingual deck recorded the field differently for cells whose neighbouring
  cell was a same-language markdown cell (split) versus an other-language
  code clone later filtered out (bilingual). The two forms have the same
  surviving cells, so the divergent metadata caused spurious failures in the
  byte-equivalence gate (and an extra trailing newline in the `.py`/`.html`).
  The field carries no semantic meaning for executed `.ipynb`/HTML output and
  source files are untouched — only build output is normalized.
- **`clm build` now exits non-zero when worker jobs time out (issue #143,
  sub-bug A).** A build where one or more jobs do not complete within
  `max_wait_for_completion_duration` (default 1,200s) previously could
  print "Build completed successfully" and exit 0 even though jobs were
  still pending and the output tree was incomplete. `wait_for_completion`
  now records one infrastructure error per stuck job in the build summary,
  flags the summary as timed-out, and the build exits 1 unconditionally —
  independent of `--fail-on-error`, because a timeout means the output is
  incomplete.
- **Resilient log rotation on Windows (issue #143, sub-bug B).** The main
  build log now uses `ResilientRotatingFileHandler`, which tolerates the
  Windows "file in use by another process" (`WinError 32`) error that the
  stock `RotatingFileHandler.doRollover()` raised when worker subprocesses
  shared the log file. A locked rollover is now skipped (and logged once at
  DEBUG) instead of flooding the console with a traceback per log record.
- **`clm db vacuum` / `clm db clean` now actually reclaim disk space on the
  jobs database (issue #144).** The jobs DB runs in WAL mode, where a plain
  `VACUUM` rewrites the database into write-ahead-log pages rather than the
  main `.db` file; without a truncating checkpoint the on-disk file size was
  unchanged, so the command reported `Reclaimed: 0 MB` even when gigabytes of
  freed space were available (a raw `sqlite3 VACUUM` on the same file shrank
  it from 2.9 GB to 541 MB). `JobQueue.vacuum()` now issues
  `PRAGMA wal_checkpoint(TRUNCATE)` after `VACUUM` so the freed pages are
  folded back into the main file immediately. The CLI also re-stats the file
  only after the connection is closed and warns when the database had free
  pages before vacuum but the file size did not change. The cache database
  was unaffected (it does not use WAL).

## [1.6.2] - 2026-05-26

### Added

- **Forensic trace harness for HTTP-replay cassette diagnostics.**
  Off-by-default instrumentation that captures three telemetry streams
  per build to localize cassette misses (force_reset races, partial
  captures, matcher false-negatives): socket-level connect events via
  `sys.addaudithook`, non-invasive wrappers on `vcrpy`'s cassette and
  `force_reset` paths, and host-side cassette lifecycle events (seed,
  merge decisions, dedup counts, completion-marker writes, orphan
  sweeps). Each worker subprocess writes its own
  `worker-<pid>.jsonl`; host writes `host.jsonl` plus a manifest with
  build metadata. Bodies are redacted as head+tail+sha+length with
  `repr()` escaping so CR/LF differences are visible during forensics.
  Enable with `CLM_HTTP_REPLAY_TRACE=1`; configure output via
  `CLM_HTTP_REPLAY_TRACE_DIR` (default `./clm-http-replay-traces`),
  `CLM_HTTP_REPLAY_TRACE_VERBOSE`, and
  `CLM_HTTP_REPLAY_TRACE_MAX_BODY_BYTES`. A new
  `scripts/analyze_http_replay_trace.py` cross-references the streams
  and classifies remote socket connects into matched / bypassed /
  race-candidate buckets (the latter being the issue-129 fingerprint).
  When the env var is unset, the bootstrap is byte-identical to before
  and no trace dir is created. Design:
  `docs/claude/design/http-replay-trace.md`.
- **`CLM_HTTP_REPLAY_IGNORE_HOSTS` env var + default LangSmith
  passthrough.** New env var controls which request hosts vcrpy should
  let pass through to the real network instead of recording into the
  cassette. Defaults to `api.smith.langchain.com` (LangSmith
  telemetry — see Fixed below for why). Comma-separated; set to an
  empty string to disable the default.
- **`scripts/strip_cassette_hosts.py` — one-shot cassette cleanup by
  request host.** Companion to the new `ignore_hosts` default: walks
  a directory tree for `*.http-cassette.yaml`, loads each via vcrpy's
  persister, drops any interaction whose request host matches the
  configured list (default: `api.smith.langchain.com`), and rewrites
  the cassette using vcr's own serializer so the on-disk format stays
  consistent with what CLM produces. Hosts are configurable via
  repeated `--host` flags; `--dry-run` reports without writing. Skips
  cassettes vcrpy can't load (corrupt YAML, format drift) so a single
  bad file doesn't abort a course-wide cleanup. Exit code 0 on
  success (whether or not anything was stripped), 1 if any load/save
  failed, 2 on argument errors.

### Changed

- **`.gitattributes` now pins LF line endings via `eol=lf`.** The
  prior `* text=auto` rule combined with the Windows default
  `core.autocrlf=true` checked out 246 text files (`.md`, `.yml`,
  `.json`, `.xml`, …) as CRLF while the index stored them as LF.
  Every `git status` / `git commit` on Windows emitted "LF will be
  replaced by CRLF" warnings, and tools that rewrite files as LF
  (ruff, pre-commit) caused unnecessary churn. The catch-all rule now
  carries `eol=lf` so the worktree matches the index byte-for-byte on
  every platform; `.bat`, `.cmd`, and `.ps1` are pinned to CRLF
  defensively. Index bytes are unchanged for existing files; the next
  checkout re-smudges the 246 affected files from CRLF to LF.

### Fixed

- **Cassettes no longer grow on every no-op rebuild.** Two distinct
  bugs, both LangSmith-shaped, conspired to add entries on every
  rebuild of LangChain slides even when the slide source was
  unchanged:

  1. **Telemetry traffic was being captured.** LangSmith's tracing
     client `POST`s to `api.smith.langchain.com/runs/multipart` with
     bodies containing per-build timestamps and UUIDs, so vcrpy's
     body matcher never matched a previous recording and recorded a
     fresh one each build. CLM now ships a default ignore-hosts list
     (see Added) that lets these requests pass through to the real
     network (telemetry preserved) without entering the cassette.
  2. **Dedup key was unstable for stream-body requests.** LangSmith's
     `_send_compressed_multipart_req` passes a `BytesIO` to
     `requests.Session.post`. vcrpy stores it as-is and YAML
     serializes it via `!!python/object/new:_io.BytesIO`; on reload,
     every entry becomes a fresh `BytesIO` instance — and
     `http_replay_cassette._dedup_key` was using
     `str(body).encode(...)` for non-bytes bodies, which for
     `BytesIO` is the object repr containing a memory address.
     Different across instances even for identical content → the
     merge thought every loaded LangSmith entry was new and folded it
     into canonical again. The cassette grew by N entries per build.
     A new `_body_to_dedup_bytes` helper reads + rewinds streams so
     equal payloads produce equal keys.

  Both fixes are needed for the "two-commits-of-just-cassettes"
  symptom to stop: (1) alone is insufficient because existing
  cassettes still contain stale LangSmith entries from pre-fix builds
  and (2) caused those entries to be re-added each merge cycle.
  Existing course repos should run `scripts/strip_cassette_hosts.py`
  once to clean accumulated LangSmith entries; subsequent builds will
  not re-add them.
- **`clm build` now actually invokes the orphan staging-cassette
  sweep (#145).** `Course._sweep_orphan_cassette_staging_files` was
  documented to run before every build but was only called from
  `Course.process_all` and `Course.process_file` — entry points
  `clm build` does not use. The actual build path
  (`process_course_with_backend` → `course.process_stage`) never
  called the sweep, so `.staging-*` files from previously-killed
  sessions accumulated next to canonical cassettes indefinitely. The
  sweep now runs in `process_course_with_backend` before the
  per-stage loop (wrapped in a defensive try/except so a sweep
  failure can't block the build); it remains a no-op when no topic
  uses `http-replay` or when no orphans exist. A regression test in
  `test_build_command.py` pins the call so a future refactor cannot
  silently re-break it.

## [1.6.1] - 2026-05-25

> **CHANGELOG correction.** The "`clm slides sync` direction
> auto-detection" entry below was originally documented under
> [1.6.0]. The 1.6.0 PyPI sdist was, however, built from a branch
> that did not include the `clm.slides.sync_direction` module — the
> direction-auto-detection feature first shipped to PyPI in this
> 1.6.1 release. The entry has been moved here to reflect what
> users actually got from `pip install`.

### Added

- **`clm slides sync` direction auto-detection (v2 follow-up, Phase 7 of the slide-format-redesign).**
  `--source-lang` is now optional. When omitted, the direction of edit
  is inferred from two signals in order of preference:

  1. **`SyncSnapshotCache` drift** — for each snapshot row covering the
     pair, the side whose current cell hash differs from the
     last-known-synced hash is treated as the drifted side. Snapshot
     evidence is content-addressed, so rebases that rewrite commit
     metadata do not destabilise it. If snapshot rows disagree on
     which side drifted, or any row shows BOTH sides drifted (the
     3-way merge case), the snapshot signal is reported as ambiguous.

  2. **Git commit timestamp** — when snapshots give no definite
     answer, the half whose most-recent commit (`git log -1 %ct`) is
     newer is the source. Requires both files tracked in a git repo.

  Inference falls back to requiring `--source-lang` when neither
  signal is conclusive (no snapshot rows, no git history, untracked
  files, equal timestamps, or the two signals disagree). `--source-lang`
  remains available as an explicit override; if it disagrees with the
  inferred direction, a warning is emitted on stderr and the explicit
  value is honored.

  Implementation lives in new module `clm.slides.sync_direction`
  (`infer_source_lang` + `DirectionInference`). The CLI command
  computes inference inside the cache-open scope so the snapshot table
  is consulted when available.

  **What's still deferred:** LLM-assisted 3-way merge prompt for
  "both sides drifted" cells (visible to direction inference as the
  ambiguous case).

- **`clm outline --sections-only` and H1 titles for disabled
  sections.** With `--include-disabled`, disabled-section bullets
  previously emitted the topic id (directory/file stem) while enabled
  sections showed the H1 header from the slide source. `clm outline`
  now resolves each disabled topic id against the course's
  filesystem-wide topic map and reads the title via
  `find_notebook_titles` — the same path `NotebookFile` uses — so
  disabled topics render with their real H1 headings (each slide as
  its own bullet) when the underlying file exists. Topics that cannot
  be resolved on disk keep the legacy
  `- <topic_id> (disabled)` fallback. A new `--sections-only` flag
  emits only the section headings: markdown output drops the topic
  bullet list, and JSON output omits the per-section `topics` key.
- **`RecordingsWatcher.on_rejected` callback.** Symmetric to
  the existing `on_submitted` / `on_error` hooks, the new optional
  callback fires on both early-return paths in `_on_file_event`
  (rejected by the backend, or never matched). Useful for
  observability ("why didn't my file get picked up?") and for tests
  that need a deterministic synchronization signal on the
  rejected-path branch.

### Changed

- **`clm slides normalize` `slide_ids` operation now uses the
  shared assign-ids engine.** Previously the normalizer carried its
  own naive slug logic (drop-non-ASCII slugify, file-stem-cell-N
  fallback, DE-source preferred). It now delegates to the same engine
  that powers `clm slides assign-ids`: EN-derived kebab slugs with
  German transliteration, narrative inheritance from the preceding
  slide, `!` preserve marker support, and soft refusals for
  headingless slides. The new internal API factors
  `assign_ids_for_cells(cells, file_path, options)` out of
  `assign_ids_for_text` so the normalizer can fold assign-ids into
  its multi-operation pass without re-parsing the file;
  `normalize_file`, `normalize_directory`, and `normalize_course`
  gain an `assign_options: AssignOptions | None` kwarg.
- **`execution_cache_hash` no longer folds cassette bytes into the
  cache key.** Folding cassette content into the hash was meant to
  invalidate the executed-notebook cache after a cassette refresh, but
  in practice it created an unfixable cache-miss loop:
  `compute_other_files` reads the cassette at payload construction
  (pre-execution), while record-capable modes
  (`once`/`new-episodes`/`refresh`) rewrite the cassette
  post-execution — so the next build's lookup hash uses the
  post-execution cassette and never matches the prior build's stored
  hash. The same disagreement fires the first time a cassette
  transitions from missing to populated, and whenever `.gitattributes`
  normalizes CRLF↔LF between builds. The cache key now uses only
  `prog_lang:language:data`; users who want re-execution after a
  manual cassette edit should use `--ignore-cache`. The
  build-scoped cassette-snapshot mechanism introduced in 1.6.0
  (`_build_cassette_snapshots` / `_snapshot_cassettes_for_build`) is
  removed since it only existed to keep the within-build hash stable.

### Fixed

- **`clm build --ignore-cache` now also bypasses the SQLite job
  cache.** `SqliteBackend.process()` consults two caches before
  submitting work: the DatabaseManager `processed_files` table and the
  JobQueue `results_cache`. Only the first was gated on `ignore_db`,
  so `--ignore-cache` could silently serve stale hits from the
  job-queue cache whenever a `(output_file, content_hash)` pair from
  a prior build was still present. The failure mode was invisible at
  build time (cached notebooks just made no HTTP calls) but caused
  downstream work to fail mysteriously: cassette re-records under
  `--http-replay=new-episodes` shipped variant-incomplete because
  skipped workers never went through the recorder, and subsequent
  strict-replay verifies failed on cells whose LLM call was never
  recorded. The job-queue lookup is now gated on `not self.ignore_db`
  too.
- **Strict HTTP-replay now matches JSON request bodies
  semantically.** Two latent vcrpy bugs together made strict-replay
  body matching unreliable for JSON POSTs through the
  LangChain/OpenAI stack: (1) `filter_post_data_parameters`
  re-serializes JSON bodies via `json.dumps()` whenever the filter
  is configured, even when no replacement key matches, so the
  cassette ends up pretty-printed while live `httpx` requests use
  compact separators; and (2) vcrpy's built-in `body` matcher gates
  its JSON transform on a case-sensitive `Content-Type` lookup, but
  real clients (and vcrpy itself) store the header lowercase so the
  transform never kicks in. CLM now registers a custom
  `clm_json_body` matcher that performs case-insensitive
  content-type detection and parses both sides as JSON before
  comparing; non-JSON bodies fall back to byte comparison.
- **Slide source files now write LF line endings on every platform
  (issue #132).** `Path.write_text` without `newline="\n"` applies
  `os.linesep`, so on Windows every `\n` in slide-file payloads
  became `\r\n` on disk. Course repos pin `* text=auto eol=lf` in
  `.gitattributes`, so the CRLF on disk produced spurious "modified"
  rows in `git status` and broke the slide-format-redesign Phase D
  pilot's byte-equivalence gate (jupytext hides the divergence on
  read but the byte-level difference remains). Fixed at every slide
  writer: `clm.slides.split` (the primary site, which writes
  `.de.py` / `.en.py` and the unified bilingual target),
  `clm.slides.assign_ids`, `clm.slides.normalizer`,
  `clm.slides.sync_writeback`, `clm.slides.voiceover_tools`, and
  `clm.notebooks.slide_writer`.
- **`clm slides assign-ids` is now a true no-op when the existing
  id already matches the proposed value (issue addressed in
  `_handle_slide`).** Previously, a cell whose author had already
  accepted a content-derived slug on an earlier run would trigger a
  spurious soft refusal under `--force` alone: the algorithm
  re-derived the same slug, but the EXTRACTABLE branch refused
  unless `--accept-content-derived` was also passed. The id never
  actually needed to change; the idempotency short-circuit is now
  evaluated ahead of the write/refuse decision.
- **`header_de` macro trailing whitespace now matches the bilingual
  `header` macro (issue #128).** The bilingual `header(de, en)`
  macro's DE half ended with `{% endif %}` + a literal blank line +
  the EN cell marker, all inside the macro. The split-form
  `header_de(de)` macro instead ended at `{% endif %}` and let the
  post-macro source supply the inter-cell whitespace, leaving one
  extra `\n` between the DE cell content and the next cell marker in
  split-form builds — which jupytext absorbed into the cell source
  (extra `"<br/>\n", "\n"` entries) and which shifted
  `lines_to_next_cell` off the title cell onto its successor.
  `header_de` now strips the trailing newline emitted by its outer
  `{% endif %}` so the macro ends with `# <br/>\n`, matching the DE
  half of `header`. The EN side already matched and is unchanged.
- **HTTP-replay cassettes now write LF line endings on every
  platform.** `http_replay_cassette._atomic_write_text` previously
  called `Path.write_text` without `newline=`, defaulting to
  `os.linesep` (i.e. `\r\n` on Windows). With a `.gitattributes`
  setting of `* text=auto eol=lf` (the recommended layout for course
  repositories), that produced a permanent flip-flop: each build
  wrote CRLF, each `git checkout`/`restore` rewrote LF, and the next
  build wrote CRLF again. Cassettes are now written LF-only.
- **Concurrent LLM cells no longer escape the HTTP-replay cassette
  (issue #129).** Under `--ignore-cache --http-replay=new-episodes`,
  the HTTP-replay bootstrap injected into every notebook now replaces
  `vcr.patch.reset_patchers` with a filtered generator that yields all
  patchers except the httpcore ones. Vcrpy's urllib3 stub calls
  `force_reset()` around every connection setup, which previously
  un-patched httpcore globally; if a background thread doing
  urllib3/requests traffic (e.g. LangSmith trace uploads) was in that
  window when a foreground httpcore call (e.g. an OpenRouter chat
  completion via httpx) dispatched, the foreground call resolved to
  the unpatched httpcore handler, hit the real upstream API, and never
  landed in the cassette. The scoped reset still un-patches urllib3
  (the recursion guard vcrpy needs), but leaves httpcore patched.
  Workaround for an upstream vcrpy issue; remove once vcrpy ships a
  scoped `force_reset` (search for `_clm_scoped_reset_patchers`).
  Investigation: `docs/claude/issue-129-vcrpy-force-reset-investigation.md`.

- **Cassette merge discards partial chains from aborted recording
  sessions (issue #115).** Previously, a kernel that died mid-cell —
  after recording the first call of a chained pair but before the
  chain-closing call landed on disk — could permanently poison the
  canonical cassette: the additive `first-seen-wins` dedup rule would
  promote the orphan chain-opener to canonical, and subsequent replay
  builds would fail on the missing chain-closer with
  `CannotOverwriteExistingCassetteException`. Refresh could not repair
  the poisoning either. The fix introduces a per-staging-file
  completion marker (`<staging>.completed`): the host writes it only
  on the success path of notebook execution. The merge now treats
  markered staging as "safe to fold" and markerless staging either as
  "concurrent worker, leave alone" (per-worker merge) or as "confirmed
  orphan from aborted previous build, discard" (pre-build sweep).
  Authors who hit issue #115 should delete the poisoned canonical
  cassette and rebuild; the partial chain will no longer re-poison.

## [1.6.0] - 2026-05-21

> **Note:** the "`clm slides sync` direction auto-detection" entry
> that was previously listed here has been moved to the [1.6.1]
> section above. It was implemented on master before the 1.6.0 tag
> date, but the 1.6.0 PyPI sdist was built from a branch that did
> not include the `clm.slides.sync_direction` module — so the
> feature first reached PyPI users in 1.6.1.

### Added

- **`clm slides sync --apply --trivial` (v2 follow-up, Phase 7 of the slide-format-redesign).**
  Auto-apply the safe subset of LLM sync proposals without prompting.
  A proposal qualifies as "trivial" iff its diff is one of:
  (1) EOL-only (CR/CRLF→LF or trailing-newline change after stripping)
  or (2) a single-line change where the differing line is equal once
  internal whitespace runs are collapsed and the line stripped.
  Everything else — including a single non-whitespace character flip —
  still falls through to the report (or to the `--interactive` walker
  when both flags are passed). Writes go through the same
  `clm.slides.raw_cells` machinery the `--interactive` walker uses, so
  cell headers and trailing-blank padding stay byte-identical.

  `--apply --trivial` records a snapshot row per write
  (`sync_snapshots` table from v2) so the new state becomes the
  last-known-synced anchor for future direction-auto-detection passes.
  Trivial-auto-applied proposals are subtracted from the proposal
  count for exit-code purposes — an all-trivial pass exits `0`
  instead of `1`. New report keys: `pairs_auto_applied` on
  `SyncResult` and `applied_trivially` per outcome (both also surfaced
  in `--json`).

  Flag validation: `--apply` alone is rejected (full `--apply` is not
  yet supported); `--trivial` alone is rejected (it is a modifier for
  `--apply`).

  **What's still deferred:** LLM-assisted 3-way merge for "both sides
  drifted" cells. (Direction auto-detection shipped alongside as a
  separate v2 follow-up — see the entry above.)

- **`clm slides sync --interactive` (v2, Phase 7 of the slide-format-redesign).**
  Walk proposed cross-language updates one at a time with an
  `[a]pply / [s]kip / [e]dit / [q]uit` prompt. Accepted and edited
  proposals are written to the target file in place; the cell header
  and the trailing blank-line padding are preserved verbatim via
  `clm.slides.raw_cells`, so applying a proposal only changes the
  cell body, not the surrounding bytes. `--interactive` is mutually
  exclusive with `--json`.

  **Edit flow** goes through `click.edit()` (honors `$EDITOR` /
  `$VISUAL`); exiting without saving falls back to skip.

  **Pilot accept-rate counters.** `SyncResult` now exposes
  `pairs_accepted`, `pairs_skipped`, `pairs_edited`, and
  `pairs_quit` (plus a `pairs_resolved` aggregate). The PythonCourses
  Phase D pilot's ship/cancel criterion — accepted as-is in >80% of
  cases — is now measurable: it is
  `(pairs_accepted + pairs_edited) / pairs_resolved`.

  **Snapshot writes.** A new `SyncSnapshotCache` table
  (`sync_snapshots`) records `(de_path, en_path, slide_id, role) →
  (de_hash, en_hash, direction, accepted_at)` per accepted / edited
  proposal. This captures the new last-known-synced state for each
  pair — it is location-addressed rather than content-addressed and
  therefore lives in its own table alongside the (content-addressed)
  `SyncCache` proposal cache. A future direction-auto-detection pass
  can compare current on-disk hashes against these rows.

  **What's deferred to follow-up PRs:** LLM-assisted 3-way merge for
  "both sides drifted" cells. (`--apply --trivial` and direction
  auto-detection both shipped as follow-up entries above.)

- **`clm slides sync` (v1, Phase 7 of the slide-format-redesign).**
  New CLI for cross-language sync of split-format decks
  (`<deck>.de.py` / `<deck>.en.py`). Walks the pair by `slide_id`,
  asks the local Ollama LLM to propose any needed updates to the
  target side, and emits a unified diff per cell. Memoizes the LLM
  call via a new `SyncCache` SQLite table keyed by
  `(de_hash, en_hash, prompt_version)` — re-runs against an unchanged
  pair hit the cache and avoid LLM spend.

  **Flags:** `--source-lang de|en` (required; tells the judge which
  side was edited), `--dry-run` (default and only mode in v1 — no
  files are written), `--llm-model`, `--ollama-url`, `--llm-timeout`,
  `--cache-dir`, `--no-cache`, `--json`. Exit codes: `0` clean,
  `1` proposed updates pending review, `2` structural error
  (mismatch or LLM unavailable).

  **Roles synced:** markdown `slide` / `subslide` and narrative
  `voiceover` / `notes` cells. Shared code cells are intentionally
  excluded — split companions must keep them byte-identical, and
  that consistency is checked by the Phase-6 validator.

  **What's deferred to v2:** `--interactive` apply/skip/edit walker,
  `--apply --trivial` write-without-prompting path, direction
  auto-detection via cache/git, 3-way merge UX for "both sides
  drifted" cells. The pilot instrumentation (per-session counters
  `pairs_visited` / `pairs_in_sync` / `pairs_proposed` /
  `pairs_error` / `cache_hits`) is wired in v1 so the eventual
  PythonCourses Phase D pilot can measure the >80% accept-rate
  ship/cancel criterion as soon as `--interactive` lands.

- **`clm slides assign-ids` extraction expansion ([#89](https://github.com/hoelzl/clm/issues/89)).**
  Four additive changes that clear the bulk of `assign-ids` hard refusals
  on slide corpora dominated by prose subslides and code-cell slide
  starts:
  - **First-prose-line extractor** — markdown cells with no heading,
    bullet, bold, or `<img alt>` now propose a slug from their first
    non-empty prose line (HTML tags and inline formatting stripped,
    trailing terminal punctuation dropped). Reported as
    `content:prose`. Only matches jupytext markdown lines (`# something`);
    leading comments inside code cells qualify too.
  - **Code-cell AST extractor** (`clm.slides.code_cell_extract`) —
    code cells tagged `slide`/`subslide` walk the top-level AST when
    the markdown path returns NON_EXTRACTABLE. Precedence:
    `class Foo` → `def foo` → `target = …` → `import x[, y, …]` /
    `from m import …` → `obj.method()`. Returns `None` on `SyntaxError`
    so unparsable cells (shell escapes, magics, half-finished stubs)
    fall through cleanly to the hard-refusal / LLM-fallback path
    instead of aborting the run. Reported as `content:code:<kind>`.
  - **Sibling-pair asymmetry fix** — when the EN slug source has
    nothing extractable but the DE sibling does, slug from the DE
    sibling (transliteration keeps the result ASCII; collision suffix
    enforces uniqueness). Reported as `content:sibling-<kind>` or
    `sibling-heading`. The LLM is intentionally NOT consulted on the
    sibling fallback — its prompts target English content and would
    propose German-derived titles otherwise.
  - **`--llm-suggest` fallback on hard refusals** — when classification
    returns NON_EXTRACTABLE on the slug source and the sibling fallback
    didn't help, `--llm-suggest` now gets a turn before the hard refusal
    is recorded. Previously the LLM was only consulted from the
    EXTRACTABLE branch, which silently no-op'd on the dominant
    refusal pattern in real corpora. Default behavior without
    `--llm-suggest` is unchanged.

- **`clm slides assign-ids`** — Phase 2 of the slide-format-redesign.
  Generates stable, EN-derived, kebab-case ASCII `slide_id`s for
  slide/subslide cells using a three-category policy:
  - *headed*: slug from the first markdown heading.
  - *extractable*: headingless with a first bullet, prominent bold
    line, or `<img alt="">`. Refused by default; auto-accept with
    `--accept-content-derived` or `--llm-suggest`.
  - *no content*: hard refuse; the author has to write `slide_id="…"`
    by hand.

  Paired DE/EN slide cells share the same EN-derived slug. Voiceover
  and notes cells inherit the id of the preceding slide. Title slides
  (j2 `header()` macro) anchor `slide_id="title"` automatically. An id
  prefixed with `!` (e.g. `slide_id="!intro"`) is a preserve marker —
  never regenerated, even under `--force`; the `!` is source-level
  only and stripped at validation / reference time.

  `--llm-suggest` calls a local Ollama model (default `qwen3:30b`) to
  propose a title for extractable cells; suggestions are cached in
  `clm-llm.sqlite` keyed by `(content_hash, prompt_version, lang)`.
  Cache location is resolved via `--cache-dir` →
  `$CLM_CACHE_DIR` → `tool.clm.cache_dir` in `pyproject.toml` →
  `<cwd>/.clm-cache/`. Falls back to refusal when Ollama is
  unreachable.

  Exit codes: `0` clean, `1` soft refusals, `2` hard refusals.

- **`clm slides coverage`** — Phase 4 of the slide-format-redesign.
  Asks a local LLM whether each slide's bullets are covered by the
  voiceover that follows it. Per-language: a paired DE/EN slide
  produces two independent checks, each cached separately. Verdicts
  are stored in `CoverageCache` (a new table in `clm-llm.sqlite`)
  keyed by `(slide_hash, voiceover_hash, prompt_version, lang)` so
  re-runs are free when neither the slide nor its voiceover has
  changed; editing one bullet only re-checks that one pair.

  Findings land at `warning` severity per the option-B rollout
  (matching Phase 3's missing-slide_id rule): once the false-positive
  rate against a real ML AZAV deck is known, the rollout can promote
  to `error`. Bullets with no voiceover at all are reported without
  consulting the LLM; non-bulleted slides (heading-only, image-only,
  code-only) are skipped silently. Workshop slides (cells inside a
  scope opened by either the `workshop` tag *or* a slide-start cell
  whose `slide_id` starts with `workshop-`, closed by `end-workshop`
  / the next opener / EOF, per
  `clm.slides.workshop_scope.find_workshop_ranges`) are also skipped
  silently — workshop exercise slides intentionally have no
  voiceover and flagging them drowns the report in known-OK
  findings. The run summary reports the count of excluded workshop
  slides so the skip is visible.

  Flags mirror `assign-ids`: `--llm-model`, `--ollama-url`,
  `--llm-timeout`, `--cache-dir`, plus `--json` (machine output),
  `--report-only` (skip cache writes; reads still happen), and
  `--dump` (text export of cached verdicts). Cache location resolves
  via `--cache-dir` → `$CLM_CACHE_DIR` → `tool.clm.cache_dir` in
  `pyproject.toml` → `<cwd>/.clm-cache/`. When Ollama is unreachable
  the command still works in cache-only mode (cached verdicts surface;
  fresh pairs are reported as skipped). The PostToolUse hook on
  PythonCourses should surface coverage findings as warnings only;
  blocking enforcement belongs in pre-commit / a manual sweep.

  Exit codes: `0` no findings, `1` at least one warning or error.

- **`clm build` routes split-source slide files directly** — Phase 6
  of the slide-format-redesign. The build pipeline now detects
  ``slides_NNN_*.de.py`` / ``slides_NNN_*.en.py`` split companions
  (produced by ``clm slides split``) per family and routes each
  file through the matching per-language pipeline only — no
  tempfile dance, no unify step. The worker's per-cell ``lang``
  filter already does the right thing: a ``.de.py`` file fed with
  ``lang=de`` produces byte-identical output to building the
  bilingual companion and filtering it.

  Detection is family-based: ``slides_foo.py``, ``slides_foo.de.py``,
  and ``slides_foo.en.py`` share a *slide family*. The build refuses
  with a clear error *before any worker runs* in two cases:
  - **dual-format conflict** — both a bare bilingual file and at
    least one of its split companions are present; the build
    surfaces the conflict so the author resolves it (run
    ``clm slides unify`` to merge, or delete the bilingual).
  - **half-pair** — only one of ``.de.py`` / ``.en.py`` is present;
    a split pair must be complete for routing to work.

  ``clm validate <topic_dir>`` (and ``validate <course-spec>``) now
  emits a ``pairing`` error finding when shared (no-``lang``) cells
  between a detected split pair diverge — the failure mode that
  silently produces different DE and EN output for what is supposed
  to be language-neutral material. The check reuses Phase 5's
  cell-classification machinery (``clm.slides.raw_cells.split_cells``
  + ``clm.slides.split._is_shared``) so the rule stays aligned with
  the splitter.

  Section-level notebook numbering treats split companions as one
  logical slot (keyed on the bilingual companion's filename), so a
  split pair lands at the same output index as the bilingual file
  would have — keeping output filenames byte-identical across both
  formats.

  Phase 6 is Python-only today, mirroring the Phase 5 scope: split
  detection works for any supported extension (cpp/csharp/java/
  typescript/etc.) but the matching sibling header macros only
  ship in the Python template. Phase 8 adds them to the other
  language templates.

- **`clm slides split` and `clm slides unify`** — Phase 5 of the
  slide-format-redesign. Bidirectional, byte-identical converters
  between the bilingual percent-format ``.py`` slide files and the
  split format introduced for per-language editing.

  `clm slides split deck.py` writes `deck.de.py` and `deck.en.py`
  next to the input: cells with `lang="de"` go to the DE file,
  `lang="en"` to the EN file, and shared cells (no `lang` — j2
  directives, language-neutral code) are copied verbatim to both.
  The bilingual `# {{ header("DE", "EN") }}` macro call is rewritten
  into the sibling-macro form `# {{ header_de("DE") }}` (DE) /
  `# {{ header_en("EN") }}` (EN), and its bare
  `# j2 from 'macros.j2' import header` directive is rewritten in
  parallel so each file only imports the macro it actually uses. New
  sibling macros `header_de(title_de)` and `header_en(title_en)` ship
  in `templates_python/macros.j2` alongside the existing two-arg
  `header(title_de, title_en)`. Decision (handover §3 Phase 5,
  2026-05-19): sibling macros rather than arg-count overloading — the
  latter is awkward to read in Jinja and surprising for template
  authors.

  `clm slides unify deck.de.py deck.en.py` is the inverse: pairs
  adjacent DE/EN cells by matching `slide_id` (Phase 3's hard
  prerequisite), validates that shared cells are byte-identical
  between the two inputs, and writes the bilingual companion. A
  divergent shared cell is a hard error — Phase 6's validator will
  surface the same check at build time. Both commands take
  `--report-only` / `--dry-run`, `--force`, and `--json` flags
  mirroring `assign-ids`.

  The round-trip property `unify(*split(deck.py)) == deck.py` is
  byte-identical and tested both as a Hypothesis property on
  procedurally generated decks and against two real ML AZAV fixtures
  (`slides_010_langchain_basics.py`, `slides_015_langsmith_tracing.py`).
  Phase 3's `clm.slides.pairing.HEADER_MACRO_RE` now recognises both
  the bilingual and split header-macro forms, so `assign-ids` and the
  validator handle both layouts unchanged.

  The lossless preamble + cell primitives that
  `assign-ids`/`normalize`/`split` all depend on are now shared in
  `clm.slides.raw_cells` (`RawCell`, `split_cells`, `reconstruct`,
  `is_cell_boundary`) — previously duplicated as private
  `_Cell`/`_RawCell` shapes in each module.

  Sibling macros currently ship only in the Python template; other
  prog_langs (cpp/csharp/java/typescript) keep just the bilingual
  `header()` macro until non-Python split support is scoped (Phase 8,
  deferred — `clm slides split` is Python-only today because the
  slide parser only recognises `# %%` cell boundaries).

- **`clm validate` enforces `slide_id` metadata** — Phase 3 of the
  slide-format-redesign. New checks land under the existing `pairing`
  category and run in both full and `--quick` modes (so the
  PostToolUse hook surfaces them at edit time):
  - **warning**: slide/subslide cell missing `slide_id`. The
    suggestion text directs authors to `clm slides assign-ids` and
    flags that the rule will become an *error* in CLM 1.7 (the same
    release that retires the Phase 0 deprecation aliases). This
    rollout shape gives PythonCourses two minor releases to migrate
    without a noisy hook in the meantime.
  - **error**: duplicate `slide_id` across different slide groups
    (group-aware — paired DE/EN cells sharing the EN-derived slug
    are *not* a duplicate). Bare-form comparison: `!intro` and
    `intro` collide.
  - **error**: voiceover/notes cell carries a `slide_id` that does
    not match the immediately preceding `slide`/`subslide` anchor.
    The walk-back skips j2, code, shared (lang-less), and
    cross-language narrative cells; the j2 `header()` macro line
    anchors `slide_id="title"` so following narrative cells validate
    clean even without a preceding `slide`-tagged cell.
  - **warning**: paired DE/EN slide cells (adjacent, different
    languages) carry mismatched bare `slide_id`s. Suggests
    `clm slides assign-ids --force` to resync.
  - **warning**: `slide_id` value is not a valid kebab-case ASCII
    slug. The leading `!` preserve marker is permitted and does not
    count toward the 30-char length cap.

  Internally, the DE/EN pair-detection and title-macro recognition
  used by both `assign-ids` and the validator now live in a shared
  `clm.slides.pairing` module (`build_slide_groups`,
  `build_slide_pairs`, `is_title_macro_cell`, `HEADER_MACRO_RE`,
  `TITLE_SLIDE_ID`). The Phase 6 split-source pipeline will reuse
  the same helpers when diffing shared cells between `.de.py` /
  `.en.py` companions.

- **`clm build --snapshot DIR` and `--verify-against DIR`** for
  byte-level migration verification. `--snapshot` captures build output
  to a baseline directory (mutually exclusive with `--output-dir` and
  `--verify-against`); `--verify-against` builds and compares the
  output tree against a previously-captured snapshot, exiting non-zero
  on any diff. Designed for the slide-format-redesign migration
  protocol (snapshot → apply change → verify byte-identical).
  - `.html` files are skipped by default because their content includes
    live-kernel execution output. Slides that use `random.choice`,
    `print(obj)` of a default-`__repr__` object, or have interleaved
    stdout/exception output produce different rendered HTML each run
    — this is a property of slide content, not of CLM.
  - `--include-html` re-enables HTML comparison with hex memory
    addresses normalized (`0xADDR` sentinel).
  - `--strict-verify` byte-compares every file with no normalization
    and no skipping; implies `--include-html`.

- **Per-cell heartbeat visibility for notebook workers (PR #84).**
  `clm monitor` and `clm status` now show a second indented line under
  each busy notebook worker:
  `cell N/M  in-cell <t>  idle <t>  last: <excerpt>`. `cell N/M`
  advances as cells execute; `in-cell` resets each new cell; `idle`
  keeps growing if a cell stops printing but is still alive — the
  signal that distinguishes a forgotten `input()` or `gradio.launch()`
  from a genuinely long ML training cell. `last:` shows the most recent
  stdout/stderr line (ANSI-stripped, ≤120 chars). Adds a new
  `worker_heartbeats` table in `clm_jobs.db` at schema **v8**;
  pre-v8 databases auto-migrate on next open and fall back gracefully
  while the migration is pending. `clm status --format json` exposes
  `current_cell`, `total_cells`, `cell_elapsed_seconds`,
  `since_last_output_seconds`, and `last_output_excerpt`. Note: `idle`
  only renders for cells that emit stdout/stderr — LangChain LLM cells
  return values silently and leave `idle` blank, which is correct.

### Changed

- **Deprecation removals slipped from CLM 1.6 to 1.7.** Two removals
  that 1.5 documented for the 1.6 release have been pushed out by one
  minor: the `--keep-directory` CLI flag (currently a no-op alias
  emitting `DeprecationWarning`) and the `<kind>speaker</kind>` output
  kind (currently accepted as a deprecated alias for `recording`).
  Both continue to behave as in 1.5 — the flag remains a no-op, the
  kind alias still normalizes to `recording` with a parse-time
  warning — and are now scheduled for removal in CLM 1.7. The slip
  aligns these two removals with the Phase 0 CLI-alias removal so
  consumers see a single deprecation cliff, and gives the
  PythonCourses slide-format-redesign migration room to land on 1.6
  without scrambling. Doc strings, `clm info commands`,
  `clm info migration`, the runtime `DeprecationWarning`, and the
  matching test (`tests/cli/test_build_command.py`) are updated; the
  historical 1.5.0 `### Deprecated` entry stays as written since it
  documents what was planned at that release's ship date.

- **`clm build --output-dir DIR` now produces the per-target layout
  that `--snapshot DIR` produces.** For a spec with `<output-targets>`
  (e.g. `shared` / `trainer` / `speaker`), `--output-dir DIR` re-roots
  each target under `<DIR>/<target.name>/` — matching what the regular
  spec-driven build writes — instead of collapsing every target into
  the legacy `<DIR>/public/`+`<DIR>/speaker/` shape that silently
  dropped non-default targets like `trainer/`. The two flags are now
  layout-equivalent; `--snapshot` still differs only in its safety
  guards (empty/non-existing DIR required, mutex with
  `--verify-against`, post-build confirmation line). `--verify-against`
  picks up the new layout automatically whether the verify build uses
  `--output-dir` or relies on the spec's target paths.

  **Migration**: callers that depended on the old collapsed
  `--output-dir DIR` layout (single `public/speaker` tree) for a
  multi-target spec should either (a) drop `<output-targets>` from
  the spec, (b) build without `--output-dir` (writing to the spec's
  declared target paths), or (c) update downstream tooling to read
  `<DIR>/<target.name>/`. The `Course.from_spec()` API change is
  source-compatible: the `snapshot_root` parameter is removed and
  `output_root` now performs the per-target re-root that
  `snapshot_root` did before.

- **CLI restructure: verb-grouped subcommands.** Several flat
  top-level commands moved under new groups for a smaller, more
  scannable surface. Old names still work and emit a deprecation
  notice naming the new invocation; aliases will be removed in CLM
  1.7. The MCP tool names are unchanged in this release — that
  rename will land in a coordinated commit with the PythonCourses
  skills that consume them.

  | Old (still works, deprecated)                  | New canonical                |
  |------------------------------------------------|------------------------------|
  | `clm normalize-slides`                         | `clm slides normalize`       |
  | `clm language-view`                            | `clm slides language-view`   |
  | `clm suggest-sync`                             | `clm slides suggest-sync`    |
  | `clm search-slides`                            | `clm slides search`          |
  | `clm resolve-topic`                            | `clm topic resolve`          |
  | `clm authoring-rules`                          | `clm authoring rules`        |
  | `clm extract-voiceover`                        | `clm voiceover extract`      |
  | `clm inline-voiceover`                         | `clm voiceover inline`       |
  | `clm validate-slides PATH`                     | `clm validate PATH`          |
  | `clm validate-spec SPEC`                       | `clm validate SPEC`          |

- **`clm validate <path>` consolidates `validate-slides` and
  `validate-spec`** with argument-type dispatch: `.xml` files →
  spec validation, `.py` files and directories → slide validation.
  Pass `--kind=slides` or `--kind=spec` to force a specific
  validator (useful for ambiguous cases like an empty directory).

### Fixed

- **`clm slides coverage` / `clm validate` no longer flag workshop task
  slides as missing voiceover** when the deck uses the announcement-by-
  slide_id convention (a slide whose `slide_id` starts with `workshop-`)
  rather than a literal `workshop` tag. `clm.slides.workshop_scope.find_workshop_ranges`
  now treats a slide/subslide markdown cell with a `workshop-…` slide_id
  as a workshop opener equivalent to the legacy `workshop` tag. Cells
  inheriting that slide_id (e.g. the announcement's voiceover) do not
  re-trigger a new range — only slide-start cells carry the boundary.
  Verified against `module_550_ml_azav/topic_055_prompt_templates/slides_010_prompt_templates.py`:
  12 previously-noisy workshop pairs (4 task slides × DE/EN, plus the
  announcement and setup slides) are now correctly excluded; the 34
  pre-workshop lecture pairs remain coverage-checked. The legacy
  `workshop` tag continues to work unchanged.

- **Strict HTTP-replay broke on identical repeated requests (issue
  #95 (A)).** The host-side cassette merger deduplicates by
  `(method, uri, body)`, so the canonical cassette stores exactly one
  entry per request fingerprint. The kernel-side bootstrap activated
  vcrpy without `allow_playback_repeats=True`, and vcrpy's
  `record_mode="none"` consumes each entry once — so a deck issuing
  the same request N times (e.g. `get_post(1)` repeated three times
  in a workshop cell, repeated LangChain prompt formatting) replayed
  the first call and raised
  `CannotOverwriteExistingCassetteException` on calls 2..N with every
  matcher reported as having succeeded. The bootstrap now sets
  `allow_playback_repeats=True`. Stale-cassette behavior for genuinely
  new requests is unchanged — those still fail loudly.

- **`clm build --snapshot DIR` ignored the spec's `<output-targets>`
  (issue #95 (B)).** `--snapshot` was implemented as an alias for
  `--output-dir`, which collapses every spec target into the single
  default target's `public/`/`speaker/` toplevel layout. A spec
  defining `shared`, `trainer`, and `speaker` targets dumped its
  `shared/` content under `<DIR>/public/` and silently dropped
  `trainer/` entirely. `--verify-against` then reported thousands of
  bogus "missing" entries because the snapshot tree and the regular
  build tree did not overlap. `--snapshot DIR` now re-roots each
  spec target to `<DIR>/<target.name>/` (matching what the regular
  build produces), and `--verify-against DIR` walks the spec targets
  per-target. Diffs are prefixed with the target name so an operator
  can tell which target diverged. Specs without `<output-targets>`
  retain the previous single-tree behavior.

- **HTTP-replay served wrong cassette response for chat-style APIs
  (PR #81).** vcrpy's default `match_on` is
  `[method, scheme, host, port, path, query]` — the request **body** is
  excluded. For chat-style endpoints where every call hits the same
  URL (e.g. `POST openrouter.ai/api/v1/chat/completions`), two distinct
  calls became indistinguishable to vcrpy, which then served recorded
  interactions in on-disk order. When the cassette's on-disk order
  diverged from the runtime call order (stale cassette vs current
  source), a non-streaming JSON response could be served to a
  streaming request, surfacing far downstream as
  `AttributeError: 'tuple' object has no attribute 'model_dump'`. The
  notebook bootstrap now adds `"body"` to `match_on`, so a mismatch
  fails loudly with `CannotOverwriteExistingCassetteException` instead
  of silently returning the wrong content. **Migration:** if you see
  the loud-failure exception after upgrading, regenerate the affected
  cassette with `clm build <spec> --http-replay=refresh`.

- **Unstable `execution_cache_hash` within a single build
  (PR #82).** `NotebookPayload.execution_cache_hash()` mixes cassette
  bytes into the hash. When vcrpy in `new-episodes` / `once` /
  `refresh` recorded a new interaction during Stage 3 (Recording HTML),
  the canonical cassette was rewritten in the worker's `finally` block,
  and Stage 4 (Completed / Trainer / Partial HTML) then computed the
  hash against the new bytes — missing the `executed_notebooks` cache
  and re-executing kernels unnecessarily. `Course` now snapshots
  cassette bytes once per build via `_snapshot_cassettes_for_build()`
  at the top of both `process_all` and `process_file`, and
  `compute_other_files` reads from that snapshot with a lazy fallback
  for ad-hoc test call sites. Complements the existing Stage 4 cache
  invariant fix in PR #71.

- **Orphan `*.http-cassette.yaml.staging-*` files crashed the next
  build (PR #83).** When a notebook worker was force-killed mid-record
  (timeout, kernel SIGKILL), its per-worker staging cassette was left
  behind. The next build's `compute_other_files` globbed the topic dir
  for supporting files and picked up the orphan, then another
  concurrent worker's `merge_staging_into_canonical` could delete it
  mid-read → `FileNotFoundError` on `b64encode(file.read_bytes())`.
  Two cooperating fixes: (1) an **eager sweep** at the top of
  `process_all` / `process_file`
  (`Course._sweep_orphan_cassette_staging_files()`) folds any orphan
  staging files into the canonical cassette before the snapshot —
  dedup is by `(method, uri, body)`, so no recordings are lost; and
  (2) a **filter** adds `*.http-cassette.yaml.staging-*` to
  `SKIP_OUTPUT_FILE_PATTERNS` / `SKIP_OUTPUT_FILE_GLOBS` so any
  orphans appearing mid-build never reach `compute_other_files`.

- **`clm build` exited 0 even when notebook cells crashed (issue
  #90).** The build summary listed every cell failure, but the process
  still returned exit 0 — so CI under `--http-replay=replay`,
  pre-commit hooks, and scripted pre-publish checks could not gate on
  cell errors programmatically. Surfaced during the #86 investigation,
  where 20+ cells crashed on `CannotOverwriteExistingCassetteException`
  and `clm build` still returned 0, masking the underlying race.
  `clm build` now exits non-zero when the build summary reports any
  cell or notebook error, **by default under `--http-replay=replay`**
  (the CI-strict mode). Other replay modes preserve exit 0 by default
  so local iteration over partial/transient failures is unchanged.
  Override via the new `--fail-on-error` / `--no-fail-on-error` flag,
  or via `CLM_FAIL_ON_ERROR={1,true,yes,0,false,no}` (CLI > env >
  replay-mode default). The check runs **before** `--verify-against`
  so CI logs show the cell error as the cause rather than a
  downstream verify diff. Watch mode is unaffected (`--watch` keeps
  looping regardless of per-iteration errors). **CI impact:** because
  `CI=true` already implies `--http-replay=replay`, CI builds will
  now exit non-zero on cell errors automatically; pass
  `--no-fail-on-error` or `CLM_FAIL_ON_ERROR=0` to opt out.

- **HTTP-replay race between concurrent worker seeds (issue #86).**
  PR #83 added a second orphan-sweep inside
  `seed_staging_from_canonical()` so each worker's first action was
  to merge every `*.http-cassette.yaml.staging-*` sibling into
  canonical and unlink them. But the sweep can't distinguish a dead
  orphan from a *live* staging file belonging to a concurrent worker
  that hasn't booted its kernel yet — so Worker B's seed deleted
  Worker A's still-active staging. When A's kernel then loaded the
  cassette, vcrpy silently treated the missing file as empty and the
  first replay request raised
  `CannotOverwriteExistingCassetteException` in `record_mode="none"`.
  The result was that `clm build --http-replay=replay` crashed on
  20+ cells across any course with `http-replay="yes"` topics when
  `--notebook-workers > 1`. The fix removes the seed-time sweep
  entirely; the pre-build sweep added by PR #83 in
  `Course._sweep_orphan_cassette_staging_files()` runs once, before
  any worker starts, and covers the orphan-recovery case the
  per-worker sweep was added for. The post-execution merge in
  `_persist_recorded_cassette` is unchanged and still handles
  this-worker recordings + any orphans that appear after seed.
  Regression coverage added at both the unit and end-to-end
  concurrent-workers level.

## [1.5.0] - 2026-05-17

### Changed
- **`clm build` no longer wipes the output tree by default.** The previous
  flow moved every nested `.git/` aside, ran `shutil.rmtree` over each
  output root, and regenerated from scratch — invalidating git's
  stat-cache on every build and turning sub-second `git status` calls
  into multi-minute re-hashes on large courses. The new default leaves
  the existing tree in place across builds and uses two cooperating
  mechanisms to keep the output correct:

  - **Hash-aware writes** at the two registry-aware write sites
    (`LocalOpsBackend.copy_file_to_output` and `SqliteBackend`
    cache-replay) check whether the destination already holds
    byte-identical content. If so, the write is skipped — mtime/inode
    are preserved so git's stat-cache stays valid.
  - **A post-build stray-file sweep** deletes anything under a build-owned
    root the build did not write (orphans from renamed/removed sections).
    Only nested `.git/` directories are spared; subtrees containing a
    `.git/` are treated as opaque. Auxiliary files (`.gitignore`,
    `README.md`, editor caches) hand-placed under an output root are
    removed — the governing principle is that the output tree is
    exclusively CLM's.

  Two new flags expose the moving parts:

  - `--clean` opts into the legacy wipe-and-restore flow (emergency
    recovery from a corrupted output tree, or scripts that depend on a
    clean rebuild).
  - `--no-sweep` keeps hash-aware writes but disables the post-build
    sweep (useful when iterating on a single section).

  Skip rules: the sweep is automatically skipped under `--clean`,
  `--only-sections`, `--watch`, `--incremental`, and after stage-fatal
  errors.

  `CLM_HASH_AWARE_WRITES` and `CLM_OUTPUT_SWEEP` env-var flags that
  gated the rollout in 1.4.x are gone — the behavior is now
  unconditional. Design doc: `docs/claude/design/git-friendly-output-writes.md`.

### Deprecated
- **`--keep-directory` is now a no-op alias.** Keeping the output tree
  is the default; passing `--keep-directory` emits a `DeprecationWarning`
  but has no effect. The flag is scheduled for removal in CLM 1.6.
  `--incremental` no longer implies `--keep-directory` (since not
  wiping is the default); it now implies `--no-sweep` instead.

## [1.4.2] - 2026-05-16

### Fixed
- **Stage 4 cache reuse works in Docker / API mode (PR #72).** When the
  notebook worker ran in API mode against `WorkerApiServer`, executed
  notebooks were stored only in the worker-local SQLite cache and never
  reached the controller's `executed_notebooks` table, so subsequent Stage 4
  consumers (HTML, code extraction) silently re-executed kernels instead of
  replaying from cache. The worker now writes through to the controller via
  new `/api/v1/executed-notebooks` endpoints, mirroring the asymmetric write
  paths already documented for direct mode. Adds `ApiExecutedNotebookCache`
  and the matching server-side routes.
- **Stage 4 cache stays warm when Recording short-circuits (`SqliteBackend`).**
  A Recording-style spec that skips Stage 1/2 could leave
  `processed_files`/`executed_notebooks` mismatched, causing the Stage 4
  cache-reuse check to fall through and re-execute kernels. `_can_replay_from_cache`
  now accepts the short-circuit path so cached executions are reused.
- **`shutil.move` no longer leaks duplicates on Windows file locks
  (recordings).** When the Auphonic upload held an open handle on a source
  file, `shutil.move` would fall back to copy-then-delete, leaving a
  duplicate behind. Recordings now use `safe_move` plus a
  `PendingRenameQueue` so a lock-blocked move is retried until the handle
  is released; the original is never copied and orphaned. Adds
  `clm.recordings.workflow.safe_move` and `rename_queue`.
- **Monitor TUI populates the header, surfaces job metadata, and shows
  sub-second durations.** The status header rendered with empty fields, job
  rows were missing key metadata columns, and durations rounded to whole
  seconds. The data provider and formatters now populate every header
  field, expose job metadata to the activity panel, and render
  sub-second precision.
- **`validate-slides --review` no longer reports missing voiceover for cells
  inside `workshop`/`end-workshop` ranges.** Workshops are narrated live by
  the trainer, so the authoring convention is to attach voiceover only to
  the workshop's opening heading (the `workshop`-tagged markdown cell) and
  leave subsequent exercise subslides and code cells silent. The validator
  now matches that convention: the workshop heading is still gap-checked,
  but every other cell inside the workshop range is suppressed. Files
  without any `workshop` heading are unaffected. Design:
  `docs/claude/design/validator-workshop-voiceover-suppression.md`.

## [1.4.1] - 2026-05-13

### Changed
- **`include_shadowed_by_local` / `include_shadowed` warnings now check the
  topic's `.clm-include` ledger before firing.** When a real file at
  `<topic-dir>/<as_path>` matches a ledger entry (same `as_path` *and*
  resolved `source`), the shadowing is `clm sync-includes`'s own
  materialization, not an ad-hoc local override — so the warning is
  suppressed. Without this, any course adopting `<include>` would see one
  HIGH warning per materialized file on every build (16 per build in the
  AZAV ML migration). Unauthorized shadowings — real local files with no
  matching ledger entry, or stale ledgers pointing at a different source —
  still warn as before. Both the build-time path (`Topic.apply_includes`)
  and `clm validate-spec` apply the same check. Ledger reader extracted to
  `clm.core.include_ledger` so build and sync-includes share one
  implementation.
- **Build summary always shows the output-write registry counts.** The
  `N duplicate output writes deduplicated; N output paths had conflicting
  writes` line now appears unconditionally alongside files-processed,
  errors, and warnings — previously it was suppressed when both counts
  were zero, which left users unable to confirm the registry had run.

### Changed
- **`clm sync-includes --gitignore` replaced with `--print-gitignore`.** The
  old flag wrote per-topic `.gitignore` files into every materialized topic
  directory, which would leak into student/trainer/speaker build output
  (same class of bug as the `.clm-include` ledger leak fixed earlier on
  master). The new flag prints suggested `.gitignore` patterns to stdout
  instead, so the author pastes them once into a course-root `.gitignore`
  and CLM never touches that file again. Output is paste-safe and idempotent:
  `clm sync-includes spec.xml --print-gitignore >> .gitignore`. Patterns are
  anchored under `slides/**/` so the canonical include source (typically
  under `examples/`) stays tracked. Breaking: scripts invoking `--gitignore`
  must switch to `--print-gitignore` and redirect. The flag is unreleased,
  so no migration path is provided.
- **`<include as="...">` rejects glob metacharacters.** `*`, `?`, `[`, `]`
  in the `as` attribute now produce a `CourseSpecError` at parse time. The
  `as` value flows into generated gitignore patterns and into a literal
  filesystem path; glob metacharacters in either context are confusing and
  almost always a typo. The `source` attribute is unchanged — it can still
  point at on-disk filenames containing these characters.

### Fixed
- **HTTP-replay cassettes survive forceful kernel termination.** Previously,
  if a build hit the wait-for-completion timeout while a notebook was
  recording HTTP interactions, the worker process was force-killed
  (`TerminateProcess` on Windows) before vcrpy could flush the cassette to
  disk. Every interaction recorded so far was discarded, and the next build
  re-ran the same long-running requests from scratch — for chained-request
  notebooks this caused the build to time out forever. The bootstrap cell
  now writes to a per-worker staging file at an absolute path under the
  source tree and patches `Cassette.append` to save eagerly after each
  recorded interaction, so the cassette on disk always reflects every
  interaction recorded up to the moment the kernel died. A post-execution
  merge step runs in a `finally` block to fold the worker's staging file
  (and any orphan staging files left by previously-killed workers) into the
  canonical cassette under a cross-process file lock, deduplicating by
  request fingerprint. This also makes concurrent builds of the same
  notebook in different languages safe — German and English workers each
  write to their own staging file and merge into the shared canonical
  cassette without races. Adds `filelock` to the `[replay]` extra.

### Removed
- **Validator: "start/completed inside workshop" warning.** The matching
  authoring guideline was retired, so the deterministic `tags`-category
  warning that flagged `start`/`completed` pairs nested in a `workshop`
  range no longer fires. The orphan `end-workshop` warning and the
  start/completed pairing checks are unchanged. Existing files that
  previously emitted this warning will validate cleanly without
  modification.

### Added
- **`<topic id="...">` attribute as an alternative to text-content topic IDs.**
  The legacy `<topic>foo</topic>` form continues to work for childless topics,
  but topics that carry `<include>` or any other child elements must now use
  the attribute form: `<topic id="foo"><include .../></topic>`. CLM hard-errors
  when a `<topic>` has children but no resolvable ID, and when the ID is
  specified via both attribute and text — closing the long-standing
  "text-after-child becomes the child's tail, so the topic ID is silently
  empty" footgun. See `clm info migration` for the migration guidance and
  `clm info spec-files` for the reference.
- **`<include>` element on `<topic>` and `<section>`.** Splice a shared
  source directory or file from elsewhere in the course root into a
  topic at build time, without keeping byte-identical physical copies
  in sync by hand. Attributes: `source` (required, course-root-relative,
  forward- or backward-slash, no `..`), `as` (optional, target path
  under the topic; defaults to source basename; per-topic dedup key),
  `optional` (default `false`). Section-level includes are inherited as
  defaults by every child topic; a topic overrides by declaring its own
  `<include>` with the same `as`. The build splice is virtual — your
  working tree is untouched, but workers see the source under
  `<topic>/<as>` and outputs land in the topic's output directory as if
  the files had been copied there. A real local file at `<topic>/<as>`
  shadows the include (warning `include_shadowed_by_local`).
- **`clm validate-spec` surfaces `<include>` problems.** New finding
  categories: `include_source_missing` (error), `include_shadowed`
  (warning), `include_source_is_topic_dir` (warning),
  `include_dependencies` (info; lists `pyproject.toml` `[project]
  dependencies` so authors can confirm the worker environment satisfies
  them), `include_section_inheritance` (info; lists topics inheriting
  each section-level include). Intra-parent target collisions are
  raised as `CourseSpecError` at parse time.
- **`clm sync-includes` command.** Materialize every `<include>`
  declared in a spec onto the filesystem so notebooks running directly
  in VS Code / `jupyter lab` find their sibling packages. Three modes:
  `copy` (default), `symlink` (falls back to `copy` per-include on
  `OSError`, so Windows-without-admin is not blocked), `hardlink`
  (falls back to per-file copy on cross-device errors). A per-topic
  `.clm-include` JSON ledger records every path the command created;
  `--remove` consults the ledger and deletes only those paths, leaving
  untracked files in place. Options: `--data-dir`, `--mode`, `--remove`,
  `--gitignore` (idempotent per-topic `.gitignore` rules for
  materialized includes), `--dry-run`. See `clm info commands` and
  `clm info spec-files` for the full reference.
- **Output-write deduplication and conflict warnings on `clm build`.**
  Builds that legitimately produce identical writes to the same output
  path (e.g. multiple topics that share an `<include>`-sourced file,
  or the C# course's repeated `NUnitTestRunner.cs`) now collapse to a
  single write, with the remaining writes reported as a dedup count
  in the build summary. Differing-content writes to the same output
  path still proceed with last-writer-wins (preserving previous
  behavior) but now surface a per-conflict `output_path_conflict`
  warning naming both source paths and a structured
  `output_conflicts` entry in the JSON summary, so authoring drift no
  longer hides silently. Image-path collisions continue to go through
  the existing `image_collision` channel — no double-warning. Tunable
  via `CLM_OUTPUT_DEDUP_HASH_LIMIT_MB` (default 50 MB; files above
  this size skip hashing and are reported as a single summary
  collision count).
- **`output_dedup_count`, `output_conflicts`, and
  `output_large_file_collision_count` keys on the `clm build` JSON
  summary.** Emitted unconditionally so machine consumers don't have
  to special-case the absence of registry events on clean builds.
  `output_conflicts` is a list of
  `{output_path, first_writer, last_writer, first_hash, last_hash,
  conflict_count}` records.
- **`--http-replay=new-episodes` build mode.** Replays every request
  that is in the existing cassette and records only the genuinely new
  ones into the same file. Fixes the case where an edited notebook now
  issues additional requests on top of an otherwise-valid cassette and a
  strict mode would fail with `CannotOverwriteExistingCassetteException`.
  Maps to vcrpy's `new_episodes` record mode. Also accepted via the
  `CLM_HTTP_REPLAY_MODE` environment variable.

### Changed
- **Local-build default for `--http-replay` is now `new-episodes`.**
  Previously the local default was `once`, which failed builds whenever
  an edited notebook issued a request not in its cassette. Local builds
  now replay recorded requests and append new ones to the same cassette,
  so authors can iterate on a notebook without manually choosing a flag.
  CI default is unchanged at strict `replay`. Pass `--http-replay=once`
  if you want a local build to fail loudly on unrecorded requests.
- **`evaluate="no"` topic attribute in course specs.** Renders the
  notebook to all configured output formats (HTML, `.ipynb`, code) without
  spawning a kernel — cells appear with empty outputs. Useful for topics
  that depend on live services, GPUs, long training runs, or interactive
  demos that should ship as static decks. Independent of `html=` (which
  skips HTML entirely) and `skip-errors` (which catches in-cell
  exceptions). Implemented at the `NotebookProcessor` layer by forcing
  `evaluate_for_html=False` on the active output spec and bypassing the
  executed-notebook cache, so neither Recording (cache producer) nor
  Completed/Trainer/Partial (cache consumers) execute when a topic opts
  out. See `clm info spec-files` for the attribute reference.

### Fixed
- **`.clm-include` ledger files no longer leak into build output.** The
  per-topic JSON ledger written by `clm sync-includes` is a
  build-internal artifact, but `DirectoryTopic.build_file_map` was
  picking it up as a regular topic file and copying it into every
  output variant alongside the materialized includes. It is now
  filtered at the course-scanning layer (`SKIP_FILE_NAMES` in
  `path_utils`), so it never enters the worker payload, source mount,
  or output tree. Builds that already published a ledger alongside
  outputs will stop emitting it on the next build.

## [1.3.3] - 2026-05-03

### Added
- **Recordings web UI: per-part chip strip in the lectures page.** Each
  deck row's Status column now renders one chip per existing part (color-
  coded: amber `recorded`, green `processed`, purple `processing`, red
  `failed`) plus a trailing `+ N` chip for "record the next part". The
  chip strip doubles as the part selector — clicking a chip targets the
  Record/Arm/Process/Advance buttons at that part, and selecting an
  existing chip swaps Record/Arm labels to Retake/Re-arm with a ⚠ icon
  warning that the current take will be moved to `takes/`. A
  `Process all` button appears only when ≥2 unprocessed parts exist on
  the deck. Right-click a chip to reveal an inline take-history panel
  below the deck row, lazy-fetched from a new
  `GET /decks/{course}/{section}/{deck}/takes` route and refreshed on
  job SSE events. Selection state lives in client-side `sessionStorage`
  keyed by `(course, section, deck)` so swaps no longer wipe the user's
  choice — incidentally fixing the long-standing `part_number`
  snap-back bug as a side-effect of removing the input.
- **Restore-take UI in the recordings dashboard.** The inline take-
  history panel exposed by Phase C gains a Restore action behind a
  two-step morph button on each history row, plus a new
  `POST /decks/{course}/{section}/{deck}/takes/{take}/restore` route
  that performs the filesystem swap with planned-rename rollback. The
  active take now appears in the panel alongside history rows so a
  single Open affordance covers every take, and the Recorded column
  renders as a local datetime instead of a raw epoch. Open in
  Explorer goes through a new `POST /open-explorer` endpoint
  (`explorer /select,…` on Windows, `open -R` on macOS, `xdg-open` on
  Linux) so the action works from `http://` origins where browsers
  block `file:///` links.
- **Validator: DE/EN cell-adjacency check.** `validate-slides` now
  flags paired DE/EN cells separated by another lang-tagged or
  narrative cell (the
  `[de slide] [de voiceover] [en slide] [en voiceover]` anti-pattern).
  Runs in both `pairing` mode and `validate_quick` (PostToolUse hook),
  so authoring tools surface ordering violations at edit time. The
  cohesion layout `[DE_start, DE_completed, EN_start, EN_completed]`
  is permitted: a same-language `start` + immediately-following
  `completed` pair is collapsed into one logical unit before the
  ordering check runs.

### Changed
- **Spec consumers consistently honor `module=` bindings.**
  `validate-slides`, `normalize-slides`, `search-slides`,
  `resolve-topic --course-spec`, and
  `authoring-rules --slide-path` previously ignored the `module=`
  attribute on `<section>`/`<topic>` and processed every filesystem
  match for a topic ID, leaking across modules in cohort-archive
  setups. They now route through new shared helpers
  (`SectionSpec.module_for`, `CourseSpec.iter_topic_bindings`,
  `topic_resolver.matches_for_binding`,
  `resolve_topic(course_topic_bindings=…)`) so every consumer applies
  the same effective-module logic as `Course._build_topics` and
  `spec_validator`.

### Fixed
- **`validate-slides` now recurses into subdirectories for module
  and root paths.** `validate_directory` previously called the
  topic-scoped `find_slide_files`, which only inspected direct
  children — so passing `slides/` or a module directory silently
  returned zero findings even when nested topics had real issues.
  Promotes the recursion logic to the public
  `topic_resolver.find_slide_files_recursive` helper and routes both
  `validator.validate_directory` and `normalizer.normalize_directory`
  through it. Topic-directory semantics are preserved (a path with
  direct slide files returns those without descending).
- **Recordings retake/restore correctness.** Several edge cases in
  the manual-process + restore path were silently corrupting take
  state: `record_retake` could clobber an existing history take
  after a restore (now uses `max(takes[].take) + 1` for stable
  identity); `_preserve_active_take` used a filesystem-derived take
  number that diverged from `state.active_take` after a restore;
  `_swap_active_with_take` derived the processed-state of the target
  from `state.json`'s `processed_file` (which the manual `/process`
  route doesn't update), so restoring a processed take dumped the
  raw back into `to-process/`; `_scan_active_take_files` only
  recognised video extensions, leaving Auphonic `.edl` cut lists
  (and future sidecars) behind on every retake; and the chip
  strip's `data-deck-key` referenced an undefined `section_name`
  variable, producing right-click 404s. `scan_take_files` /
  `scan_section_takes` now `sanitize_file_name` before joining so
  section names containing characters stripped by the sanitizer
  (e.g. colons) match the on-disk subtree.

### Build
- **Pinned micromamba in `docker/notebook/Dockerfile`** with SHA-256
  verification and `curl --fail/--retry`. The previous
  `latest` redirect intermittently served HTML error pages that got
  piped into `tar`, surfacing as `bzip2: (stdin) is not a bzip2 file`
  and failing two consecutive CI runs during the 1.3.2 release.
- **Bumped `[tool.uv] exclude-newer` floor** to 2026-04-18 (~14 days
  back) and re-locked. Pairs with a PowerShell-profile change that
  mirrors this date into `$env:UV_EXCLUDE_NEWER`, so the env var no
  longer drifts on a 4-day rolling window and `uv` stops silently
  regenerating `uv.lock` mid-pre-commit-hook.

## [1.3.2] - 2026-05-02

### Added
- **Module-bound section/topic references** in course specs. `<section>` and
  `<topic>` accept an optional `module="module_directory_name"` attribute; when
  set, topic resolution is restricted to that specific module directory. This
  removes the long-standing first-occurrence-wins ambiguity when two modules
  share topic IDs, and is the supported mechanism for cohort archives or
  course variants. Per-topic `module=` overrides the section default. The
  `clm resolve-topic` CLI and the MCP `resolve_topic` tool gain a matching
  `--module` / `module` argument. `clm validate-spec` reports unknown module
  names and module-bound topics that don't exist in the named module. See
  `clm info spec-files` for the full pattern, including the cohort-archive
  recipe.
- **`trainer` and `recording` output kinds** split the previous `speaker`
  kind into two named variants that match how the decks are actually used:
  - `trainer` keeps `notes` cells but strips `voiceover` cells — the
    variant most trainers want when teaching live without recording.
  - `recording` keeps both `notes` and `voiceover` cells — the deck used
    by the trainer recording the course on video, where voiceover cells
    contain the polished narration read on camera.
  Both kinds land under the existing private (`speaker/`) toplevel
  output directory; their kind subdirs (`Trainer/`, `Recording/`) keep
  their files distinct. `recording` is now the canonical HTML cache
  producer; `trainer`, `completed`, and `partial` HTML all reuse its
  executed notebook by filtering the appropriate cell subset.

### Deprecated
- **`speaker` output kind**: still accepted as an input alias for one
  release and treated as `recording`. Spec parsing logs a deprecation
  warning and rewrites `<kind>speaker</kind>` to `<kind>recording</kind>`
  internally so downstream consumers only see the canonical kinds.
  `--speaker-only` continues to work and now selects both `trainer` and
  `recording`. See `clm info migration` for the spec-rewrite recipe.

### Changed
- **Duplicate-topic-id warning is now emitted only when resolution
  actually depended on first-occurrence-wins.** Previously the warning
  fired for every duplicate topic ID found on disk, even when every
  reference in the spec was bound to a specific module via the new
  `module=` attribute. Specs that disambiguate every duplicate via
  `module=` now produce no duplicate-id noise. Unbound references that
  hit a duplicate still warn exactly as before — strict improvement, no
  behaviour change for existing specs.
- **Output paths for private kinds always include a kind subdir.**
  Previously a `speaker` build wrote to
  `output/speaker/<course>/Slides/Html/<topic>.html` (no kind subdir).
  `recording` and `trainer` builds now write to
  `output/speaker/<course>/Slides/Html/Recording/<topic>.html` and
  `output/speaker/<course>/Slides/Html/Trainer/<topic>.html`. The
  deprecated `speaker` kind alias produces the same layout as
  `recording`. Tooling that reads from the old kind-subdir-less path
  needs to switch to one of the new locations.

## [1.3.1] - 2026-05-02

### Added
- **`end-workshop` tag**: marks the end of a workshop section that does not
  run to end-of-notebook. Until now, a workshop section was implicitly the
  trailing suffix of the slide deck — `partial` output kept demonstrations
  worked out before the first `workshop` heading and treated everything
  from that heading onwards as code-along. With `end-workshop`, trainers
  can now put workshops in the middle of a deck. The tag attaches to the
  markdown heading that starts the next non-workshop section (the cell
  carrying it is *outside* the workshop), and a deck may contain multiple
  workshops separated by regular content. Backward compatible: a workshop
  without an explicit `end-workshop` continues to extend to EOF, exactly
  like before. The validator warns on a stray `end-workshop` that appears
  before any `workshop` heading. Surfaced through `partial` output, the
  notebook processor's cached-partial filter, and the slide validator.

## [1.3.0] - 2026-04-26

### Added
- **HTTP replay for notebook execution (opt-in, per topic)**: topics that
  call live HTTP services can now record a cassette once and replay it
  deterministically on subsequent builds. Opt in by setting
  `http-replay="yes"` on the `<topic>` element; CLM injects a hidden
  `vcrpy` bootstrap cell at execution time and strips it before HTML
  rendering. Cassettes live next to the source as
  `<stem>.http-cassette.yaml` (or in a per-topic `_cassettes/` directory
  if that exists) and travel with the notebook into worker payloads and
  Docker source mounts, but are excluded from public and speaker output.
  Record mode is selected per build via `--http-replay=<replay|once|
  refresh|disabled>` or `CLM_HTTP_REPLAY_MODE`; CI (`CI=true`) defaults
  to strict `replay`, local builds default to `once`. The executed-
  notebook cache key folds in the cassette bytes so a refresh invalidates
  only that topic's cache entry. Requires the new `[replay]` extra
  (`pip install -e .[replay]`), also included in `[all]`. See
  `docs/user-guide/http-replay.md` for the author workflow.
- **`skip-errors` topic attribute**: cheap, generic escape hatch for
  topics whose cells may raise. Set `skip-errors="yes"` on the `<topic>`
  to build HTML even when cells fail; error-output cells are cleared and
  a processing warning lists the affected indices. Not a substitute for
  HTTP replay — a topic with a recorded cassette should rely on replay
  so legitimate regressions still surface.
- **`partial` output kind**: fourth kind alongside `code-along`, `completed`,
  and `speaker`. A `partial` notebook is completed up to the first `workshop`
  markdown heading and code-along from there to end-of-notebook — intended as
  a student follow-along artifact so demonstrations remain worked out while
  workshop exercises stay blank. Partial HTML executes independently (pre-
  workshop cells produce outputs; post-workshop cells are blanked before
  execution so they produce none). Request via `<kind>partial</kind>` in an
  `<output-target>`.
- **MCP exposure for the voiceover pipeline**: six new MCP tools surface
  read-mostly stages of the voiceover workflow so authoring sessions can
  drive them without shelling out — `voiceover_transcribe`,
  `voiceover_identify_rev`, `voiceover_compare`, `voiceover_backfill_dry`,
  `voiceover_cache_list`, and `voiceover_trace_show`. Mutating operations
  (`sync`, `sync-at-rev`, `port-voiceover`, `backfill --apply`) stay
  CLI-only on purpose. All handlers honor the existing artifact cache.
- **Inventory-aware compare wrapper**: new
  `clm voiceover compare-from-inventory SLIDE_FILE --inventory PATH --lang`
  looks up the recording video(s) for a slide in a
  `video_to_slide_mapping.json`-style inventory and composes
  `identify-rev` → `sync-at-rev` → `compare` automatically. Supports
  multi-part recordings (inventory order is preserved). Accepts the same
  `--rev / --auto / --force-rev / --format / -o` knobs as the underlying
  commands.
- **Markdown output for compare reports**: `clm voiceover compare` now
  accepts `--format {table,json,markdown}`; the existing `--json` flag is
  a shorthand for `--format json`. New `clm voiceover report REPORT.json`
  re-renders a saved JSON report in any format without rerunning the LLM
  judge. Markdown output has a summary-per-slide table plus per-bucket
  sections grouped by `dropped` / `added` / `rewritten` / `manual_review`.
- **`latest.patch` pointer for backfill**: every non-dry-run
  `clm voiceover backfill` invocation now also writes
  `.clm/voiceover-backfill/<topic>/latest.patch` (one level shallower
  than the timestamped scratch directory) so "just show me the most
  recent diff for this topic" is a predictable read. The full
  timestamped history under `<topic>-<ts>/port.patch` is retained.
- **JupyterLite output (experimental, opt-in)**: new `jupyterlite` output
  format produces a deployable JupyterLite static site from the already-built
  `notebook`-format output for one `(target, language, kind)` tuple. Opt-in
  via a `<jupyterlite>` config block (at course or target level, with
  per-target overriding wholesale) plus explicit `<format>jupyterlite</format>`
  per target; no course without both gates produces JupyterLite artifacts.
  Supports the `xeus-python` and `pyodide` kernels, pre-staged offline
  wheels, and optional `environment.yml`. Installing the
  `[jupyterlite]` extra (also in `[all]`) brings in `jupyterlite-core`, the
  two kernel addons, and `jupyter-server`; the build coordinator spawns one
  `jupyterlite-builder` worker on-demand only when a target requests the
  format. Each build writes a deterministic `jupyterlite-manifest.json`
  (cache-keyed on notebook-tree hash + wheel hashes + kernel +
  `jupyterlite-core` version). See `clm info jupyterlite` for the spec
  reference.
- **JupyterLite student launchers**: `<launcher>python</launcher>` (default)
  emits a `launch.py` with `ThreadingHTTPServer`, `.wasm` MIME fix for
  Windows, free-port selection, and browser auto-open.
  `<launcher>miniserve</launcher>` bundles prebuilt miniserve binaries for
  Windows, macOS (x64 + ARM), and Linux (~20 MB) — zero runtime dependencies;
  each binary is SHA-256 verified and cached under
  `~/.cache/clm/miniserve/<version>/`. Per-OS launcher scripts (`launch.bat`,
  `launch.command`, `launch.sh`) are emitted alongside the binaries. A
  `README-offline.md` is always emitted with launcher-appropriate instructions
  and IndexedDB persistence guidance.
- **JupyterLite branding**: optional `<branding>` block inside
  `<jupyterlite>` with `<theme>` (light/dark), `<logo>`, and `<site-name>`
  fields, mapped to JupyterLab's `overrides.json`.
- **`clm jupyterlite preview`**: CLI command that serves a previously built
  JupyterLite site locally for quick testing.
- **JupyterLite user guide**: `docs/user-guide/jupyterlite.md` — installation,
  configuration reference, launcher options, branding, troubleshooting.

### Changed
- **Validator: workshop scope now runs to end-of-notebook.** The scope used
  to exit at the next non-workshop slide heading; it now extends from the
  first `workshop` heading to EOF, matching real-world notebooks where
  workshops span multiple slides. A future `end-workshop` tag may be
  introduced if content after the workshop section is needed.
- **`run_compare` is now a library entry point**. Extracted from the CLI
  into `clm.voiceover.compare` (sync `run_compare` + async
  `run_compare_async`) so the MCP handler and any future callers share
  the same code path. Behavior unchanged for CLI users.
- **Shared fingerprint/identify-rev helper**: `clm.voiceover.identify`
  houses the fingerprint-build + rev-score composition that was
  previously duplicated between `identify-rev` and the backfill CLI
  entry point.

### Fixed
- **Spurious "Unknown tag '_post_workshop'" warnings during partial HTML
  builds**: the synthetic `_post_workshop` sentinel that
  `PartialOutput.annotate_cells` injects into cell metadata was being
  flagged by the per-cell tag validator before the strip-pass at the end
  of `_process_notebook_node` removed it, producing one warning per
  post-workshop cell on every slide file with a `workshop` heading.
  `get_invalid_code_tags` / `get_invalid_markdown_tags` now skip any tag
  with a leading underscore so internal CLM sentinels never reach the
  warning channel.

## [1.2.1] - 2026-04-12

### Added
- **Training data extraction**: new `clm voiceover extract-training-data`
  command reads JSONL trace logs produced by `clm voiceover sync` and
  correlates each entry with the current slide file state to produce training
  triples (`input.baseline`, `input.transcript`, `llm_output`, `human_final`,
  `delta_vs_llm`). Entries where the human final matches the LLM output are
  emitted with an empty delta as positive training examples. Entries with
  unreachable `git_head` commits are skipped with a warning. Supports
  `--base-dir`, `--tag`, `--no-check-git`, and `--output` options.
- **Langfuse tracing for all LLM calls**: when `LANGFUSE_HOST` (or
  `LANGFUSE_BASE_URL`), `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY` are
  set, `_build_client` returns a Langfuse-observed `openai.AsyncOpenAI` that
  traces all LLM calls automatically. Benefits `clm voiceover sync` (merge),
  `clm polish`, and `clm summarize`. Env vars absent = no change. Langfuse
  unreachable = warning, pipeline continues. `langfuse>=3.0.0` added to the
  `[voiceover]` extra. Each voiceover merge invocation groups traces into a
  Langfuse session with per-batch trace IDs, tags, and metadata; the
  `langfuse_trace_id` is also written to the local JSONL trace log for
  correlation.

### Changed
- **Recordings dashboard: slide-deck-based lecture selection**: The
  `/lectures` page now lists individual slide decks (notebook files)
  instead of topics. This matches how recordings are actually made — one
  video per slide deck, not one per topic (a topic can contain multiple
  slide files).
  - The page builds a full `Course` object from the spec file at startup,
    reusing `Course.from_spec()` to resolve topics, find slide files,
    extract bilingual titles, and assign section numbers.
  - **Language toggle** (DE/EN): a cookie-based selector on the lectures
    page switches between German and English section names, slide deck
    titles, and course slugs. Default: German.
  - **Refresh button**: rebuilds the `Course` from disk without restarting
    the server, picking up title changes and new slides.
  - **Multi-part recording support**: the arm form now accepts a
    `part_number` field. When `part > 0`, filenames include a
    `(part N)` suffix (e.g. `03 Streaming (part 2)--RAW.mp4`).
  - `ArmedTopic` renamed to `ArmedDeck` with a `deck_name` field
    (replacing `topic_name`) and a `part_number` field.  Backward-compat
    aliases (`ArmedTopic`, `SessionSnapshot.armed_topic`,
    `RecordingSession.armed_topic`) are preserved.
  - Naming helpers (`raw_filename`, `final_filename`) accept `deck_name`
    (was `topic_name`) and a keyword-only `part` parameter.  New
    `parse_part()` function extracts the optional `(part N)` suffix from
    a base name.
  - New routes: `POST /set-lang`, `POST /lectures/refresh`.
  - JSON status API includes both `armed_deck` (new) and `armed_topic`
    (deprecated alias) for transition.
- **`clm voiceover sync` now accepts multiple video files** (breaking CLI
  change): argument order flipped from `sync VIDEO SLIDES` to
  `sync SLIDES VIDEO...`. Multiple video parts are processed independently
  (transcription + transition detection per part) and merged into a single
  logical timeline using running offsets — no on-disk concatenation. Each
  `TranscriptSegment` and `TransitionEvent` carries a `source_part_index`
  for downstream consumers. Single-video invocations work as before (just
  swap the argument order).
- **`clm voiceover sync` now merges into existing voiceover cells by
  default** instead of overwriting them. The merge uses a single-pass LLM
  call (Claude Sonnet 4.6 via OpenRouter by default) that preserves baseline
  content, integrates substantive transcript additions, and filters recording
  noise (greetings, self-corrections, code-typing dictation, operator
  asides). Use `--overwrite` to restore the old destructive behavior.
  - Factual contradictions in the transcript may rewrite baseline bullets;
    every rewrite is tracked in a structured `rewrites` field.
  - `--dry-run` now emits a colored unified diff with rewrite annotations.
  - `--mode verbatim` without `--overwrite` is now an error (verbatim has
    no noise filter, so merging raw transcript would be unsafe).
  - Every merge run writes a JSONL trace log to
    `.clm/voiceover-traces/` for future training data extraction.
  - LLM calls are batched across slides (20k char budget per batch) with
    automatic per-slide fallback on JSON parse failure.

### Fixed
- **`parse_dir_groups` now respects `<section enabled="false">`**: previously
  `CourseSpec.parse_dir_groups` used `root.iter("dir-group")` and walked the
  entire XML tree regardless of section enablement, so topic-scoped
  `<dir-group>` elements inside disabled sections silently leaked their
  directories into the build output. The traversal is now section-aware and
  mirrors `parse_sections`: topic-scoped dir-groups in disabled sections are
  dropped by default and retained when `keep_disabled=True`. Top-level
  `<dir-groups>` are unaffected. Document order of the returned dir-groups is
  preserved (topic-scoped before top-level). Fixes #29.
- `CourseSpec.from_file` now forwards its `keep_disabled` parameter to
  `parse_dir_groups` so full-roadmap enumeration (e.g.
  `clm outline --include-disabled`) sees the same dir-groups the sections do.

### Added
- **Section filtering**: Course spec `<section>` elements now accept
  `enabled` and `id` attributes, and `clm build` accepts an
  `--only-sections <selector>` flag for dev-time iteration on a subset
  of a course. Together these replace the common "`-build.xml` subset
  spec" pattern for courses with not-yet-implemented sections. See the
  proposal at `docs/proposals/SECTION_FILTERING.md` and the phased
  implementation plan at
  `docs/claude/design/section-filtering-plan.md`.
  - **`enabled="false"` on a `<section>`** drops it from the parsed spec
    entirely, so `clm build`, `clm outline`, `clm validate-spec`, MCP
    tools, and every other consumer of `CourseSpec.sections` ignores it
    without code changes. Default is `enabled="true"`.
  - Disabled sections may omit `<topics>` or reference topic IDs that do
    not yet exist on disk — they are never built or validated. This is
    the property that lets a full roadmap spec live as a single file
    (no more `-build.xml` companion specs).
  - `enabled` is case-insensitive (`true`/`True`/`TRUE`/`false`/`False`);
    any other value raises `CourseSpecError` with a clear message.
  - Optional `id` attribute on `<section>` (e.g. `id="w03"`) is stable
    under reordering and renaming; recommended for frequently filtered
    courses.
  - **`--include-disabled` flag** on `clm outline` and `clm validate-spec`
    (plus matching `include_disabled` parameters on the MCP
    `course_outline` and `validate_spec` tools) enumerates the full
    roadmap including disabled sections, with a `(disabled)` marker on
    each entry and a `(disabled)` suffix on each validation finding so
    users can tell which content is deferred.
  - `CourseSpec.parse_sections` and `CourseSpec.from_file` gain a
    keyword-only `keep_disabled: bool = False` parameter so tooling can
    enumerate the full roadmap.
  - **`clm build --only-sections <selector>`** rebuilds only the listed
    sections and leaves unselected section output directories untouched.
    Selector tokens are comma-separated; bare tokens try `id` → 1-based
    index → case-insensitive substring on the German or English name,
    stopping at the first hit. Prefixed tokens (`id:`, `idx:`, `name:`)
    force a single strategy. Section indices count disabled sections so
    toggling `enabled` does not renumber later sections.
  - Selector errors abort the build early: empty/whitespace tokens, zero
    matches (with a full section listing), ambiguous bare substring
    (with the matches listed), or an entirely-disabled selection. A
    mixed list containing disabled sections skips each disabled section
    with a warning and builds the rest.
  - `--only-sections` mode **skips `git_dir_mover`**, **skips dir-group
    processing**, and **rmtrees only the selected sections'
    subdirectories** per `(target, lang, kind)` tuple. Missing section
    dirs trigger a rename-hint warning rather than an error.
  - **`clm build --only-sections <selector> --watch`** reacts only to
    events under selected sections' source directories. Creation events
    outside the selected set are silently dropped; modification events
    rely on `course.find_course_file`, which naturally filters against
    the already-filtered `course.files` list. Restart the watcher if
    you change the section set in the spec.
  - New exports: `SectionSelection` and
    `CourseSpec.resolve_section_selectors` in `clm.core.course_spec`;
    `Course.from_spec` accepts a new `section_selection` parameter;
    `FileEventHandler` accepts a new `selected_section_source_dirs`
    constructor parameter.
  - Fully backward-compatible: existing spec files without the new
    attributes and existing `clm build` invocations without
    `--only-sections` behave exactly as before.
- **Environment-aware worker pool-size cap**: Spec-file worker counts are
  now clamped against the host machine's CPU, RAM, and an optional
  operator cap at pool start, so a spec tuned for a build farm (e.g.
  PythonCourses' 18 notebook workers) no longer saturates a developer
  laptop. See `docs/proposals/WORKER_CLEANUP_IMPLEMENTATION_PLAN.md`
  Fix 4 for the design rationale.
  - **`clm build --max-workers N`** — new CLI flag that caps the
    effective worker count for the invocation.
  - **`CLM_MAX_WORKERS`** — matching environment variable (empty,
    zero, negative, or non-integer values are tolerated and treated
    as "no cap").
  - **`WorkersManagementConfig.max_workers_cap: int | None`** — new
    config field (`ge=1, le=64`) surfaced through
    `config_loader.load_worker_config`.
  - Default caps are `cpu_cap = max(1, os.cpu_count() // 2)` and
    `mem_cap = max(1, floor(total_ram_gb / 2))`. `get_worker_config`
    logs a WARNING naming the worker type, requested count, and every
    individual cap value whenever clamping kicks in, so the diagnostic
    is visible in build logs.
  - New helper module `clm.infrastructure.workers.pool_size_cap`
    exposing `compute_pool_size_cap(requested, *, explicit_cap=None)`
    and a frozen `PoolSizeCapResult` dataclass with a
    `format_reason()` render for logs. The helper is pure so unit
    tests can pin CPU/RAM via `monkeypatch`.
- **`clm workers reap`**: New CLI subcommand that chains the full
  self-service recovery sequence for crashed or task-killed builds —
  orphan job-row reap, psutil-based scan for surviving
  `python -m clm.workers.*` processes, process-tree kill, and stale
  worker-row cleanup. Fix 5 of the worker cleanup reliability plan.
  - Options: `--jobs-db-path`, `--dry-run`, `--force`, `--all`.
  - Cross-worktree safety rail: by default only kills workers whose
    `DB_PATH` env var resolves to the same path as `--jobs-db-path`.
    Processes with unreadable env (common on Windows across sessions)
    or a different `DB_PATH` are listed but not killed. `--all` opts
    in to reaping them too, as an emergency escape hatch.
  - `--dry-run` prints what would be reaped without mutating the DB
    or touching any process. Without `--force`, the command prompts
    for confirmation before killing.
  - Uses `ctx.exit(1)` for the missing-DB error so CI scripts can
    reliably detect failures.
  - **Existing `clm workers cleanup` is unchanged** — it still only
    deletes DB rows and does not kill processes. The two commands
    now compose: `reap` does everything `cleanup` does plus the
    process-kill step.
  - New helper module `clm.infrastructure.workers.process_reaper`
    exposes `terminate_then_kill_procs`, `reap_process_tree`,
    `scan_worker_processes`, and the frozen `DiscoveredWorkerProcess`
    dataclass. Fix 2's `reap_kernel_descendants` is now a thin
    wrapper around the shared low-level helper.

### Fixed
- **Worker cleanup reliability on Windows** (resolves the incident
  documented in `docs/proposals/WORKER_CLEANUP_RELIABILITY.md`:
  `clm build` previously leaked Jupyter kernel subprocesses any time
  a worker was killed mid-job, eventually wedging WMI and Windows
  Terminal with hundreds of orphaned `python.exe` processes):
  - **Windows `JobObject` owns every direct-mode worker** (Fix 1).
    `DirectWorkerExecutor` now creates a
    `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` job on init and assigns
    every worker subprocess to it immediately after `Popen`. When
    the job handle closes (explicit `cleanup()` or process exit),
    Windows itself terminates every process in the tree — even
    through `atexit`, `taskkill /F`, or a hard CLM crash.
    No-op on non-Windows. New helper module
    `clm.infrastructure.workers.windows_job_object` with a full
    ctypes wrapper around `CreateJobObjectW` /
    `SetInformationJobObject` / `AssignProcessToJobObject`.
  - **Kernel grandchild reap via `_ReapingKernelManager`** (Fix 2).
    `jupyter_client`'s `LocalProvisioner.kill` is `TerminateProcess`
    on Windows, which kills only the kernel pid — any
    `subprocess.Popen` / `multiprocessing` children that a cell
    spawned survive as orphan processes. A new
    `AsyncKernelManager` subclass now snapshots descendants before
    the kernel shuts down and reaps survivors afterward via
    `psutil`. Wired into `TrackingExecutePreprocessor` via the
    `kernel_manager_class` traitlet so every nbclient-managed
    kernel uses it automatically. Emits WARNING logs when anything
    had to be force-killed — the diagnostic signal the team had
    been missing. psutil is now a hard dependency
    (`psutil>=5.9.0` in `pyproject.toml`), replacing the conditional
    import + `/proc` fallback in `worker_executor.is_worker_running`.
  - **Orphan job rows marked failed at `pool_stopped`** (Fix 3).
    When a worker died mid-job, its `jobs` row was left with
    `started_at` set and `completed_at` null forever, causing
    `clm status` to silently under-report failures. New atomic
    `JobQueue.mark_orphaned_jobs_failed()` runs a single
    `BEGIN IMMEDIATE` SELECT+UPDATE over rows matching
    `started_at IS NOT NULL AND completed_at IS NULL AND
    cancelled_at IS NULL AND status IN ('processing', 'pending')`
    and stamps each with `status='failed'`,
    `error=JobQueue.ORPHAN_ERROR_MESSAGE`, and a
    `completed_at` timestamp.
    `WorkerLifecycleManager.stop_managed_workers` invokes this
    between `stop_pools()` and `log_pool_stopped()`, emits a
    WARNING naming each orphan, and passes `orphan_count` +
    `orphan_job_ids` into the `pool_stopped` event metadata.
    Wrapped in `try/except Exception` so a DB hiccup can never
    break pool teardown.
  - **Mock-based cleanup test replaced with real-kernel regression
    tests** (Fix 2). The old
    `test_cleanup_called_on_kernel_death` used `km=None, kc=None`
    and only asserted the finally block ran — giving false
    confidence. Replaced with two real-kernel tests that spawn a
    subprocess grandchild from a cell, run `preprocess` on a live
    kernel (both success and `CellExecutionError` paths), and
    assert the grandchild is dead via `psutil.pid_exists` after
    preprocess returns.

## [1.2.0] - 2026-04-08

### Added
- **MCP server for AI-assisted slide authoring**: New `clm.mcp` package providing a
  Model Context Protocol server via stdio transport with 12 tools for course navigation,
  validation, normalization, bilingual editing, and voiceover management.
  - `clm mcp` — start the MCP server (requires `[mcp]` extra).
  - `--data-dir` option and `CLM_DATA_DIR` env var for data directory resolution.
  - Tools: `resolve_topic`, `search_slides`, `course_outline`, `validate_spec`,
    `validate_slides`, `normalize_slides`, `get_language_view`, `suggest_sync`,
    `extract_voiceover`, `inline_voiceover`, `course_authoring_rules`.
  - In-memory caching for course objects (keyed by spec file mtime).
  - New optional extras: `[slides]` (rapidfuzz) and `[mcp]` (mcp SDK + slides).
- **Slide authoring tools** (`clm.slides`): New package for AI-assisted slide authoring
  with CLI commands and MCP tools.
  - `clm resolve-topic` — resolve a topic ID to its filesystem path, with exact match,
    glob patterns (`what_is_ml*`), course-spec scoping, and JSON output.
  - `clm search-slides` — fuzzy search across topic names and slide file titles using
    `rapidfuzz` (with substring fallback when not installed).
  - `clm outline --format json` — structured JSON course outline alongside existing
    Markdown format.
  - `clm validate-spec` — course spec validation: unresolved/ambiguous topics, duplicates,
    missing dir-groups, near-match suggestions. `--json` flag.
  - `clm validate-slides` — slide file validation: format, tags, DE/EN pairing checks,
    review material extraction. `--quick` mode for syntax-only.
  - `clm normalize-slides` — slide normalization: tag migration (`alt`→`completed`),
    workshop tag insertion, DE/EN interleaving, slide ID auto-generation. `--dry-run`
    and `--operations` filter.
  - `clm language-view` — single-language view of bilingual slide files with
    `[original line N]` annotations. `--include-voiceover`/`--include-notes` flags.
  - `clm suggest-sync` — detect asymmetric bilingual edits vs git HEAD with
    `slide_id`-aware pairing. `--json` and `--source-language` flags.
  - `clm extract-voiceover` / `clm inline-voiceover` — move voiceover cells to/from
    companion `voiceover_*.py` files linked by `slide_id`/`for_slide`. `--dry-run`.
  - `clm authoring-rules` — look up merged authoring rules (common + course-specific)
    by course spec or slide path. `--json` flag.
  - `clm.core.topic_resolver` — standalone topic resolution: `build_topic_map()`,
    `resolve_topic()`, `find_slide_files()`, `get_course_topic_ids()`.
  - `clm.slides.tags` — canonical tag definitions, single source of truth. Adds
    `completed` and `workshop` tags.
  - `slide_id` and `for_slide` metadata parsing in `CellMetadata` and
    `parse_cell_header()` (backward-compatible).
- **Build pipeline integration for voiceover companion files**: Companion voiceover files
  are automatically merged during notebook processing, and internal metadata is stripped
  from all output.
  - When `voiceover_X.py` exists alongside `slides_X.py`, voiceover cells are merged
    in-memory for speaker output. Other output kinds filter them via tag-based deletion.
  - `slide_id` and `for_slide` metadata are stripped from all output cell metadata.
  - Companion files are excluded from the `other_files` payload to avoid duplication.
  - Unmatched `for_slide` references produce build warnings.
- **Recording management module** (`clm recordings`): New optional module for managing
  the video recording workflow for educational courses.
  - `clm recordings check` — verify recording dependencies (ffmpeg, onnxruntime)
  - `clm recordings process` — process a single recording through the 5-step audio
    pipeline (extract → DeepFilterNet3 ONNX noise reduction → FFmpeg filters → AAC → mux)
  - `clm recordings batch` — batch-process all recordings in a directory
  - `clm recordings status` — show per-lecture recording status for a course
  - `clm recordings compare` — generate A/B audio comparison HTML with blind test mode
  - `clm recordings assemble` — scan for paired raw video + processed audio, mux final
    output via FFmpeg, and archive originals
  - `clm recordings serve` — HTMX-based web dashboard with SSE, lecture selection,
    watcher controls, OBS connection indicator, and processing jobs panel
  - Recording workflow automation: naming conventions, three-tier directory structure
    (`to-process/`, `final/`, `archive/`), session state machine, OBS WebSocket integration
  - Per-course recording state stored as JSON with auto-assignment and status tracking
  - Git commit capture at recording assignment time
  - File watcher with stability detection and backend-aware behavior
- **Pluggable recording processing backends**: Architecture refactored from monolithic
  to Protocol-based with three implementations:
  - `OnnxAudioFirstBackend` — local DeepFilterNet3 ONNX inference (default)
  - `ExternalAudioFirstBackend` — iZotope RX 11 or similar external tool workflows
  - `AuphonicBackend` — cloud video-in/video-out with speech-aware denoising, leveling,
    loudness normalization, and optional cut lists
  - `make_backend()` factory for backend selection via config
  - `JobManager` with lazy async poller, `JsonFileJobStore` with atomic writes,
    `EventBus` for lifecycle events
  - 6 new CLI subcommands: `clm recordings backends`, `clm recordings submit`,
    `clm recordings jobs list/cancel`, `clm recordings auphonic preset list/sync`
  - Web dashboard "Processing Jobs" panel with progress bars and cancel buttons
- **Per-target remote-path for GitLab group support**: Each `<output-target>` can
  now override `<remote-path>` to push to a different GitLab group. When a target has
  its own `<remote-path>`, the target suffix is suppressed.
- **Voiceover backends and device control**: Pluggable transcription backends with
  Granite model support and configurable device selection.
- **`--remove-missing` flag for `clm db prune/clean`**: Remove jobs for files that
  no longer exist on disk.
- **Default to keeping completed/failed jobs indefinitely** in the job queue.
- 367 new tests for MCP/slide tooling, 355 tests for recordings module.

### Changed
- **`clm git init` is now idempotent**: Running on already-initialized repos adds the
  remote origin if the remote exists but wasn't configured locally.
- **Default processing backend changed to `onnx`**: Fresh installs work offline without
  cloud credentials; users opt into Auphonic or external backends explicitly.
- **Replaced DeepFilterNet CLI with ONNX inference**: Removes the dependency on the
  unmaintained `deepfilternet` package. Dependencies: `onnxruntime`, `soundfile`, `numpy`.
- **Renamed config field**: `deepfilter_atten_lim` → `denoise_atten_lim` in both
  `PipelineConfig` and `RecordingsProcessingConfig`.
- `jupyter_utils.py` tag constants now imported from `clm.slides.tags` instead of
  defined locally. Tag sets are `frozenset` (immutable).
- `Course._build_topic_map()` delegates to `clm.core.topic_resolver.build_topic_map()`.
- `completed` tag added to `CodeAlongOutput.tags_to_delete_cell` (processed identically
  to `alt`: deleted in code-along, kept in completed/speaker).
- Test suite runs in parallel by default via `pytest-xdist` (`-n auto`), reducing fast
  suite time to ~30 seconds.

### Removed
- **Legacy backend module**: Deleted `backends_legacy.py` and its companion test file.
  All legacy functionality superseded by the new backend package.

### Fixed
- **Voiceover: CUDA crash on Windows**: Transcription now runs in an isolated subprocess
  to prevent CUDA memory conflicts when the parent process also uses GPU resources.
- **Voiceover: slide 0 bug**: Fixed off-by-one error in slide matching that could assign
  content to a non-existent slide index.
- **Orphaned worker processes on Windows**: Worker subprocesses are now properly terminated
  when the parent process exits.
- **Tornado SelectorThread atexit race on Windows**: Fixed spurious exception during
  interpreter shutdown.
- **Git init misclassifying empty remote repos**: Empty remote repositories are no longer
  misidentified as nonexistent.
- **Flaky mock worker discovery tests**: Replaced timing-dependent assertions with
  event-based synchronization.
- **SSE bridge thread safety**: Cross-thread events now marshal via
  `loop.call_soon_threadsafe` instead of non-thread-safe `put_nowait`.

## [1.1.9] - 2026-03-25

### Changed
- **Replaced litellm with openai SDK**: The `[summarize]` extra now uses the `openai`
  package directly instead of `litellm`, reducing the dependency footprint. The LLM
  client, polish module, and summarize pipeline all use the OpenAI SDK natively.
- **Added langfuse dependency**: Added `langfuse` to the `[summarize]` optional
  dependency group for LLM observability and tracing.

### Fixed
- **mypy type annotation**: Fixed `cv2.cvtColor` return type annotation in
  `voiceover/keyframes.py`.

## [1.1.8] - 2026-03-17

### Added
- **bm25s dependency**: Added `bm25s[core]>=0.3.2.post1` as a core dependency for BM25
  sparse retrieval support in notebooks.
- **Docker notebook image**: Added `bm25s[core]` to both lite and full variants of the
  notebook-processor Docker image.

## [1.1.7] - 2026-03-17

### Added
- **`voiceover` cell tag**: New tag that behaves identically to `notes` (private,
  deleted from completed/code-along output, kept in speaker output) but renders with
  a light amber background (`#FFEEBA`) instead of yellow, to visually distinguish
  voiceover-originated content from hand-written speaker notes.

### Changed
- **Renamed `is_notes` → `is_narrative`** in `slide_parser` and `slide_writer`: The
  property now returns `True` for both `notes` and `voiceover` tags, reflecting that
  both are speaker-facing narrative content attached to slides.

## [1.1.6] - 2026-03-10

### Added
- **ipywidgets dependency**: Added `ipywidgets>=8.1.0` to the `[notebook]` optional
  dependency group to fix tqdm "IProgress not found" warning in Jupyter notebooks.

## [1.1.5] - 2026-03-09

### Added
- **`project_` file prefix**: Files named `project_*.py`, `project_*.md`, etc. are now
  recognized as notebook files and processed through the full notebook pipeline (jupytext →
  nbconvert → HTML/ipynb), alongside the existing `slides_` and `topic_` prefixes. This
  enables markdown-based project documents to be converted to notebooks and HTML slides.
- **`prog-lang` attribute on `<topic>`**: Individual topics can now override the course-level
  programming language with `<topic prog-lang="java">my_topic</topic>`. This is especially
  useful for `.md` notebook files where the language cannot be inferred from the file extension.

### Changed
- **`.md` default language changed from Rust to Python**: Markdown notebook files (`.md`) now
  default to Python instead of Rust when no course-level or topic-level `prog-lang` is set.
  The programming language for `.md` files follows a priority chain:
  topic `prog-lang` attribute → course `<prog-lang>` element → Python (default).

### Fixed
- **Markdown notebook parsing**: `.md` files are now correctly parsed using jupytext's `"md"`
  format, which auto-detects both standard markdown (fenced code blocks) and MyST
  (`{code-cell}`) variants. Previously, `.md` files were incorrectly parsed using the
  programming language's format (e.g., `"py:percent"`), causing the entire file content to be
  treated as a single code cell.
- Forbid Markdown headings in trainer summaries to preserve heading hierarchy in
  generated summary documents.

## [1.1.3] - 2026-03-05

### Added
- **Voiceover pipeline** (`clm voiceover`): Synchronize video recordings with slide files.
  Extracts audio, transcribes with Whisper, detects slide transitions via frame differencing,
  matches transitions to slides using OCR + fuzzy matching, and inserts speaker notes into
  percent-format `.py` slide files. Requires the `[voiceover]` extra.
  - `clm voiceover sync` — Full pipeline: video + slides → speaker notes
  - `clm voiceover transcribe` — Extract transcript from video
  - `clm voiceover detect` — Detect slide transitions in video
  - `clm voiceover identify` — Match video frames to slides via OCR
- **LLM polish** (`clm polish`): Clean up existing speaker notes using an LLM. Removes filler
  words, fixes grammar, and preserves technical terms. Works standalone or as part of the
  voiceover pipeline (`--mode polished`). Requires the `[summarize]` extra.
- **`clm.notebooks` module**: Shared slide file utilities for parsing, writing, and polishing
  percent-format `.py` slide files (`slide_parser`, `slide_writer`, `polish`).
- **`clm.voiceover` module**: Video processing pipeline with pluggable transcription backend,
  frame-based transition detection, OCR + fuzzy slide matching, and transcript-to-slide alignment.
- **`[voiceover]` optional dependency group**: `faster-whisper`, `opencv-python`, `pytesseract`,
  `rapidfuzz`, `Pillow`.
- 129 new tests across voiceover, notebooks, and CLI modules.

### Changed
- Voiceover optional dependencies use lazy imports so CI works without the `[voiceover]` extra.

## [1.1.2] - 2026-03-05

### Added
- **`clm summarize` command**: Generate LLM-powered markdown summaries of course content.
  Supports `--audience client|trainer`, `--style prose|bullets`, `--granularity notebook|section`,
  per-notebook caching, and configurable LLM models via the openai SDK. Requires the `[summarize]` extra.
- **`--amend` flag for `clm git commit` and `clm git sync`**: Amend the previous commit
  instead of creating a new one. When used without `-m`, reuses the previous commit message
  (`--no-edit`). When used with `-m`, replaces the commit message.
- **`--force-with-lease` flag for `clm git push` and `clm git sync`**: Safe force push
  for rewritten history. `--amend` on `sync` implies `--force-with-lease` automatically.
  When force-pushing, the "remote is ahead" safety check is skipped.

### Fixed
- Bullet-style client output formatting in summarize command.

## [1.1.1] - 2026-03-05

### Added
- Automatic `.env` file loading: The `build` command now walks up the directory tree to
  find a `.env` file and loads it before spawning workers.

### Changed
- Reorganized optional dependencies: moved data-science packages from `[notebook]` to
  `[ml]` extra, organized by category.

### Fixed
- CLI help text formatting for multi-line examples.
- Suppressed `RequestsDependencyWarning` from the requests library.

## [1.1.0] - 2026-02-27

### Added
- **Remote URL template for git operations**: Trainers can now override the git remote
  URL pattern via a configurable template with placeholders (`{repository_base}`, `{repo}`,
  `{slug}`, `{lang}`, `{suffix}`). Set via `CLM_GIT__REMOTE_TEMPLATE` environment variable,
  `[git] remote_template` in TOML config, or `<remote-template>` in the course spec XML.
  Enables SSH access with custom host aliases (e.g., `git@github.com-cam:Org/{repo}.git`).

### Changed
- **Flatten speaker kind subfolder**: Speaker output no longer creates a redundant `Speaker/`
  subfolder in the output path. Paths are now `.../Html/Section/` instead of
  `.../Html/Speaker/Section/`, since speaker output has only one variant.

## [1.0.9] - 2025-11-29

### Added
- `clm info <topic>` command for version-accurate documentation that downstream agents
  can query at runtime. Topics: `spec-files`, `commands`, `migration`.

### Changed
- `<project-slug>` promoted to top-level course spec element (previously inside `<github>`).
  The old location still works but is deprecated and logs a warning.

## [1.0.8] - 2025-11-28

### Added
- `docker.io/` registry prefix for Podman compatibility.
- `.python-version` file for Arch Linux compatibility.

### Fixed
- `sanitize_path` no longer strips leading dots from path components.

## [1.0.7] - 2025-11-27

### Added
- LangSmith and Ragas to ML optional dependencies.
