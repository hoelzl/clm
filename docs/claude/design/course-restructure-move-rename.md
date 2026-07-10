# Course Restructure: Safe Move / Rename of Modules, Topics, and Slides

**Status:** Proposal / investigation (2026-07-10). Phase-1 core delivered — see §9.
**Decisions taken:** command surface = flat verbs under `clm course` (§5); build
the reusable cache-path migrator first, before wiring any command (§9).
**Motivating case:** `module_550_ml_azav` (AZAV-ML) has accumulated topics whose
`topic_NNN_` ordinal prefixes no longer reflect the course-spec order, making the
directory hard for humans to navigate. We want to **renumber the topic
directories to match the spec order** without cold-starting every cached build
and every sync-ledger baseline. More generally we want first-class commands to
move/rename modules, topics, and slides that update all the derived
metadata/caches/ledgers.

---

## 1. The one insight that makes this tractable

CLM already separates **identity** (what a thing *is*) from **ordering** (where it
sorts). The `NNN` in `module_NNN_`, `topic_NNN_`, `slides_NNN_` is a **sort key
only** — it is stripped out of every identity CLM persists:

- `simplify_ordered_name("topic_310_what_is_ml")` → `"what_is_ml"`
  (`src/clm/infrastructure/utils/path_utils.py:463-468`: drops `parts[0]` prefix
  and `parts[1]` number, keeps `parts[2:]`).
- **Topic id** = that suffix (`src/clm/core/topic_resolver.py:102`).
- **Spec `<topic>` references** bind to the suffix + optional `module=`
  (`src/clm/core/course_spec.py:188-209`; `spec-files.md:94-96`).
- **Cross-references** (`clm:<topic-id>`) are keyed by the same suffix
  (`src/clm/core/cross_references.py:29-34`).
- **Build/output order** follows **spec order**, not directory order
  (`Course._build_topics`) — so the on-disk `NNN` has **zero** effect on what CLM
  produces.
- **Cache payloads** are keyed by a **content hash that contains no path**
  (`content_hash()` / `execution_cache_hash()`,
  `src/clm/infrastructure/messaging/notebook_classes.py:174-201`); the path
  appears only as a **separate lookup column**.

**Corollary:** renaming the *ordinal prefix* of a directory changes almost no
identity in the system. What it changes is a handful of **path-keyed lookup
columns** whose *values* are still valid — so the fix is to **rewrite keys**, not
to recompute anything. Renaming the *semantic suffix* (the id) is the harder
operation, because that suffix genuinely is the identity.

---

## 2. Impact inventory — every store keyed on a name or path

Verdicts: **AUTO** = follows a whole-directory `git mv` with no action; **REBUILD**
= safe to lose, regenerated next build; **REWRITE** = must be migrated by the
tool; **SPEC/TEXT** = author-facing edit.

| Store | Location | Keyed by | On a topic *renumber* (suffix kept) | On a topic *rename* (suffix changed) / slide moved to new topic |
|---|---|---|---|---|
| `processed_files` | `clm_cache.db` | `file_path` (abs **input**) + content_hash + output_metadata | **REWRITE** input path | REWRITE input path |
| `processing_issues` | `clm_cache.db` | `file_path` (abs input) | **REWRITE** input path | REWRITE input path |
| `executed_notebooks` (kernel cache) | `clm_cache.db` | `input_file` (abs) + content_hash + lang + prog_lang | **REWRITE** input path | REWRITE input path |
| `results_cache` (jobcache) | `clm_jobs.db` | `output_file` (abs) + content_hash | **AUTO** — output path unchanged (derives from spec section name + `number_in_section` + title, not the dir number) | REWRITE *iff* output path changes |
| `jobs` rows | `clm_jobs.db` | ephemeral (content_hash) | **REBUILD** (7d/30d retention, auto-pruned) | REBUILD |
| HTTP-replay cassettes | `<topic>/.clm/cassettes/<stem>.http-cassette.yaml` | on-disk, named by slide **stem** | **AUTO** (inside the dir) | AUTO if stem kept; **REWRITE** (rename file) if stem changes |
| v3 sync ledger | `<topic>/.clm/sync-ledger.json` | file location = dir; deck section = stem; members = slide_id / anchor | **AUTO** (moves with dir; stem + member keys unchanged) | REWRITE only when the **stem** or **topic dir** for a deck changes (move to new topic → cut/paste deck section) |
| Voiceover companions | `<topic>/voiceover/…` (or sibling) | resolved relative to deck path; cell pairing by `slide_id`/`for_slide` | **AUTO** | AUTO (move with file); pairing survives while `slide_id` survives |
| `.clm-manifest.json` (provenance) | output-target root | rebuilt each build; entries keyed by `topic_id` | **AUTO** (self-heals) | AUTO (self-heals to new id) |
| `.clm-released.<stream>.json` (frozen release) | **cohort's dest repo** | `topic_id` | **AUTO** (id unchanged) | **REWRITE** old id → new id (else the topic looks new → re-propagated / freeze orphaned) |
| Release ledger (`.clm` text) | source repo | cumulative `topic_id` list | **AUTO** | **REWRITE** (old id silently dropped from "released") |
| Recording drift stamps | central `<course_id>.json` (user config dir) | stamped `topic_id` | **AUTO** | **REWRITE** (stale id → silent `UNKNOWN` verdict) |
| Cohort calendar | `release/<channel>.calendar.toml` | `ref` = `topic_id`/deck-stem; `.ics` UID = module/topic/stem | **AUTO** | **TEXT** (hand-edit refs; UID churn) |
| Cross-reference links | slide markdown | `clm:<topic-id>[/stem]` | **AUTO** (id + stem suffix unchanged) | **TEXT** (rewrite every `clm:<old>` link) |
| Spec `<topic>` id | `course-specs/*.xml` | topic suffix | **AUTO** | **SPEC** edit |
| Spec `module=` binding | `course-specs/*.xml` | **literal** module dir name | **AUTO** for topic ops; **SPEC** edit for **any module dir rename (even renumber)** | SPEC edit |
| Legacy watermark | `.clm-cache/clm-llm.sqlite` | abs `(de_path, en_path)` | REBUILD (v3 does not use it) | REBUILD |
| `execution_telemetry` | telemetry db | `input_file` | REBUILD (diagnostic) | REBUILD |

### The headline result for the AZAV-ML case

Renumbering topic directories (suffix kept, module kept) touches **exactly one
thing that isn't AUTO or REBUILD**: the three **input-path** columns in
`clm_cache.db`. Everything else — spec, cross-refs, outputs, `results_cache`,
sync ledgers, cassettes, companions, manifests, release freeze, calendar — is
either unaffected or moves inside the directory. So:

> **Renumber = `git mv` the topic dirs + rewrite 3 columns in `clm_cache.db`.**
> No re-execution, no spec edit, no ledger cold-start.

---

## 3. Why the cache rewrite is sound (not a hack)

The two cache hashes are **content + build-config only** — they exclude the input
path, output path, module/topic/slide name, and `number_in_section`
(`notebook_classes.py:174-201`). The sibling-file digest keys siblings by
**topic-relative** path (`CourseFile.relative_path =
path.relative_to(topic.path)`, `course_file.py:94-100`), so even those are stable
when the *parent* directory is renamed. Therefore, after a rename the stored
`Result` / executed-`NotebookNode` bytes are **still exactly what the build would
produce** — only the lookup key (`file_path`/`input_file`) points at the old
path. Rewriting the key is observationally identical to a fresh cache hit.

Concrete migration for each `old_input → new_input` (and `old_output →
new_output` when output moves):

```sql
-- clm_cache.db  (--cache-db-path / CLM_CACHE_DB_PATH)
UPDATE processed_files    SET file_path  = :new_input  WHERE file_path  = :old_input;
UPDATE processing_issues  SET file_path  = :new_input  WHERE file_path  = :old_input;
UPDATE executed_notebooks SET input_file = :new_input  WHERE input_file = :old_input;

-- clm_jobs.db  (--jobs-db-path / CLM_JOBS_DB_PATH)  — only if output path changes
UPDATE results_cache      SET output_file = :new_output WHERE output_file = :old_output;
```

**Collision caveat (main correctness risk):** both `executed_notebooks
UNIQUE(input_file, content_hash, language, prog_lang)` and `results_cache
UNIQUE(output_file, content_hash)` can clash if the destination path already has
rows from a prior build. Resolve inside the transaction by keeping the newest row
per `content_hash` and dropping the superseded one (mirrors the existing
newest-N trim policy). Wrap each DB in a single `BEGIN…COMMIT`.

**Recompute, don't guess, the output path.** `output_file` tracks `section.name`
+ `number_in_section` + notebook title (`course_file.py:102-105`,
`notebook_file.py:401-403`), **not** the source path. A move must recompute the
new output path the *same way the build does* (reuse `output_specs` +
`CourseFile.output_dir` + `file_name`, exactly as `provenance_manifest.py:120-143`
already does) rather than string-substituting the source delta. For a pure topic
renumber the output path does not move, so no `results_cache` rewrite is needed
at all.

**Cassettes** move with the directory; only a **slide-stem** change needs
`<old_stem>.http-cassette.yaml` (+ any `*.staging-*`, `*.completed`, `*.lock`)
renamed alongside the deck.

---

## 4. The precedent to generalize: `clm slides rename-id`

`clm slides rename-id OLD NEW` (`src/clm/cli/commands/slides/rename_id.py`,
`src/clm/slides/rename_id.py`) already implements the exact discipline we need,
for the `slide_id` axis:

- Rewrites the id on **both** split halves **and** every `for_slide` owner ref.
- **Migrates** the ledger baseline key (`migrate_ledger_key`,
  `rename_group_scopes`) — re-keys `id:old → id:new`, owner refs, `member_order`
  handles, and the `pos:old/... → pos:new/...` group cascade — **carrying the
  recorded fingerprints instead of re-fingerprinting**, so a simultaneous edit
  shows up as `translate_edit`, never a silent cold-`confirm`.
- Atomic across all coupled stores; `--report-only` / `--dry-run` and `--json`.
- Usage-error exit codes; collision + existence guards.

**The design principle to lift from it:** *migrate keys, carry fingerprints/bytes,
never recompute; be atomic across every coupled store; always offer a dry run.*
Directory/file moves are the same shape, one level up (deck-section keys, cache
path columns, sidecar locations) instead of member keys.

---

## 5. Proposed command surface

Three **flat verbs** under the existing `clm course` group (home of
`resolve-topic`, `decks`, `targets`, `orphans`) — matching CLM's flat-verb
convention (decision taken; the nested-group alternative was rejected).

### 5.1 `clm course renumber` — the convenience (covers AZAV-ML directly)

```
clm course renumber [MODULE] --spec course-specs/<course>.xml [options]
```

Renumbers **topic directories** so their `topic_NNN_` prefixes ascend in the
course-spec's topic order. `MODULE` restricts to one module dir (else every
module the spec references). Suffixes are untouched → ids, spec, cross-refs,
outputs, ledgers all unchanged; only cache input-path columns are rewritten.

Options:
- `--start N` / `--step K` — numbering scheme (default `--start 10 --step 10`,
  leaving gaps for future inserts — the very problem that motivated this).
- `--width W` — zero-pad width (default: preserve current, e.g. 3 digits).
- `--slides` — also renumber `slides_NNN_` files *within* each topic to the
  order the build assigns them (opt-in; this *does* touch deck stems → ledger
  deck-section + cassette rename, so it is off by default).
- `--report-only` / `--json` — dry-run, same contract as `rename-id`.
- `--no-cache-migrate` — skip the DB rewrite (accept a one-time re-execution).

### 5.2 `clm course mv` — general single move/rename

```
clm course mv SRC DST [--spec ...] [--report-only] [--json]
```

Auto-detects the level of `SRC` (module dir / topic dir / slide file) and does the
full migration for that level:
- **Topic renumber/rename** (`topic_100_intro` → `topic_040_intro` or
  `topic_100_introduction`): git mv + cache rewrite; on a **suffix change** also
  the spec `<topic>` edit, `clm:` link rewrites, and the topic_id-keyed downstream
  stores (§2).
- **Slide move to another topic** (`…/topic_a/slides_x.de.py` →
  `…/topic_b/`): git mv both halves + companions; **cut/paste the deck section**
  between the two `.clm/sync-ledger.json` files; cache rewrite; recompute output
  paths (both topics' `number_in_section` shift).
- **Module rename** (`module_545_x` → `module_540_x`): git mv + cache rewrite +
  **mandatory** spec `module=` binding edits.

### 5.3 `clm course restructure` — transactional batch

```
clm course restructure PLAN.json [--spec ...] [--report-only] [--json]
```

A single JSON plan of ordered operations applied as one unit (see §6). This is
the right primitive for "renumber a whole module" when the moves collide
pairwise (many dirs swapping numbers) and for scripted, reviewable
reorganizations. Example:

```json
{
  "version": 1,
  "spec": "course-specs/machine-learning-azav.xml",
  "operations": [
    { "op": "renumber-topic", "from": "module_550_ml_azav/topic_310_what_is_ml",
      "to_number": 40 },
    { "op": "rename-topic-id", "from": "module_550_ml_azav/topic_120_dl_intro",
      "to_id": "deep_learning_intro" },
    { "op": "move-slide",
      "from": "module_550_ml_azav/topic_040_what_is_ml/slides_020_history.de.py",
      "to_topic": "module_550_ml_azav/topic_050_ml_history" }
  ]
}
```

(`clm course renumber` is sugar that *generates* such a plan from spec order and
feeds it to the same executor.)

---

## 6. Transaction model: plan → validate → apply → verify

No cross-resource ACID exists across FS + 2 SQLite DBs + text files, so we lean on
**git for filesystem reversibility** and **per-DB transactions**, gated by a full
up-front validation and a dry-run.

1. **Plan.** Resolve every op to concrete mappings: dir/file `old→new` paths,
   sidecars that ride along, recomputed output paths (build's own path code),
   DB row rewrites, spec edits, `clm:` link edits, downstream id migrations.

2. **Validate (fail closed).** Reject before touching anything if: a destination
   path exists (outside the batch's own moves), a `topic_id` becomes ambiguous
   within a module, a `UNIQUE` cache-key collision can't be reconciled, a
   `clm:` reference or spec `<topic>`/`module=` would dangle, or a slide move
   would orphan a `for_slide` companion. Reuse `resolve_topic` /
   `validate_cross_references` / `validate_spec`.

3. **Dry-run report** (`--report-only` / `--json`): print the whole change set —
   files moved, DB rows rewritten, spec/link edits, and a **checklist of
   out-of-repo follow-ups** (cohort `.clm-released.*.json`, calendar TOML,
   recording stamps) the tool cannot safely reach into other repos to change.

4. **Apply, in reversible order:**
   1. **Filesystem** via `git mv` (preserves history; carries `.clm/` ledgers,
      cassettes, `voiceover/` sidecars). Collisions during a renumber are avoided
      with a **two-phase move** (everything to unique temp names, then to final
      numbers). Record a journal of every move.
   2. **`clm_cache.db`** then **`clm_jobs.db`** — path-column rewrites, one
      `BEGIN…COMMIT` per DB, with collision reconciliation.
   3. **Ledger content edits** — only for stem/topic-dir changes and slide moves;
      reuse `doc_ledger` primitives (`migrate_ledger_key`, `rename_group_scopes`,
      cut/paste deck section).
   4. **Slide text + spec** — `slide_id`/`for_slide` and `clm:` link rewrites
      (reuse `rename_id` primitives), spec `<topic>`/`module=` edits.
   5. **Downstream id-keyed stores in *this* repo** (release ledger); *other*
      repos (cohort freeze, calendar) are reported, not auto-edited.

5. **Verify.** Run `clm validate` (spec resolves, no dangling `clm:`), and
   `clm cache explain` on a sample deck to confirm the migrated caches **HIT**
   (not silently cold).

**Rollback / undo.** Any failure after step 4.1 is recoverable: reverse the git
moves from the journal and roll back the (not-yet-committed, or explicitly
reverted) DB transactions. A `clm course restructure --undo <journal.json>`
inverts a completed batch (git mv back, inverse cache rewrite). Because git mv is
reversible and DB writes are transactional, we never reach a state that can't be
walked back — the one exception is edits already pushed to *other* repos, which
is why those stay on the reported-checklist side of the line.

---

## 7. Reusable building blocks (already in the tree)

| Need | Existing code |
|---|---|
| Topic id ↔ path resolution, ambiguity, module binding | `core/topic_resolver.py`, `cli/commands/course/resolve_topic.py` |
| Recompute output paths the build's way | `output_specs` + `CourseFile.output_dir` + `file_name`; see `core/provenance_manifest.py:120-143` |
| Ledger key migration (carry fingerprints) | `slides/doc_ledger.py` (`migrate_ledger_key`, `rename_group_scopes`, `deck_key_for`, `ledger_path_for`) |
| slide_id / for_slide rewrite across a pair | `slides/rename_id.py`, `cli/commands/slides/rename_id.py` |
| Cross-ref discovery + validation | `core/cross_references.py` (`validate_cross_references`, `build_href_map`) |
| Spec parse/edit | `core/course_spec.py` (`topic_bindings`, `SectionSpec.module_for`) |
| Cache DBs + path columns + prune/collision policy | `database/db_operations.py`, `database/executed_notebook_cache.py`, `database/job_queue.py`, `database/schema.py` |
| Manifest topic↔output join | `core/provenance_manifest.py` |
| Missing-path cache cleanup (post-move sweep) | `db.py` `--remove-missing`, `remove_entries_for_missing_files` |

---

## 8. Risks & edge cases

- **Cache UNIQUE collisions** on rewrite (§3) — the one real correctness hazard;
  reconcile by newest-per-content_hash inside the txn.
- **Two-phase renumber** to avoid transient `git mv` path collisions when many
  topics swap numbers.
- **`--slides` reorder cascades:** renumbering a slide file changes its
  `number_in_section`, shifting **later siblings'** output filenames too — the
  plan must include those siblings' `results_cache`/output moves, not just the
  moved file.
- **Cross-repo state is out of reach:** cohort `.clm-released.*.json` and
  `*.calendar.toml` live in *other* repos; a suffix/id rename that isn't mirrored
  there causes silent re-propagation or calendar-`check` errors. The tool must
  *report* these loudly (dry-run checklist), and we may add
  `clm release remap-topic-id` / a calendar-ref updater as companions.
- **Recording drift stamps** in the central `<course_id>.json` go `UNKNOWN`
  (silent) on an id change — include a re-stamp step or a warning.
- **`clear_orphaned_cache_entries` is defined but not wired** into `db cleanup`
  (`caching.md:54` overstates it); post-move, stale-output `results_cache` rows
  may linger — either wire it up or have the tool delete the old-output rows it
  supersedes.
- **Info-topic + changelog obligations:** new CLI ⇒ update
  `src/clm/cli/info_topics/commands.md`; behavior affecting course layout ⇒
  `spec-files.md` / `migration.md`; add a `changelog.d/*.added.md` fragment.

---

## 9. Phasing & recommendation

- **Phase 1 — `clm course renumber` (topics only) + cache-key migrator.**
  Solves the AZAV-ML need end to end with the smallest, safest surface: `git mv`
  + `clm_cache.db` input-path rewrite + two-phase collision handling +
  `--report-only`. No spec/ledger/downstream edits are in play (all AUTO). Ship
  the reusable **path-column migrator** here as its own tested unit.
- **Phase 2 — `clm course mv` for topic-id rename & module rename.** Adds the
  SPEC/TEXT edits (spec `<topic>`/`module=`, `clm:` links) and the downstream
  id-keyed checklist. Generalize `rename-id`'s discipline to the id axis.
- **Phase 3 — slide moves across topics + `clm course restructure PLAN.json`.**
  Adds ledger cut/paste, output-path recompute with sibling cascades, and the
  transactional batch/undo.
- **Phase 4 — cross-repo companions** (`clm release remap-topic-id`, calendar-ref
  updater, recording re-stamp) so id renames propagate cleanly to cohort repos.

Recommendation: build Phase 1 first — it is high value, low risk, and delivers the
motivating use case, while forcing us to get the cache-migrator (the reusable
core of every later phase) right in isolation.

### Delivered so far (Phase 1 core)

`src/clm/infrastructure/database/cache_path_migration.py` — the reusable
`clm_cache.db` path-column migrator, tested in isolation
(`tests/infrastructure/database/test_cache_path_migration.py`, 14 tests):

- `plan_dir_rename(cache_db, old_dir, new_dir) → [PathMapping]` — scans the DB's
  own distinct input paths and computes the `old → new` rewrites for a directory
  rename (separator- and, on Windows, case-insensitive; never touches paths
  outside `old_dir`).
- `migrate_cache_paths(cache_db, mappings, *, dry_run)` — rewrites
  `processed_files.file_path`, `processing_issues.file_path`, and
  `executed_notebooks.input_file` in one transaction; drops the migrated
  duplicate on an `executed_notebooks` UNIQUE collision (same `content_hash` ⇒
  interchangeable payload); `dry_run` does the identical work then rolls back so
  its counts equal a real run's.
- `migrate_dir_rename(...)` — the `plan` + `migrate` convenience.

The tests seed rows through the *real* cache managers and assert a live cache
**hit at the new path / miss at the old** after migration — verifying the
observable outcome, not just row counts. `results_cache.output_file`
(`clm_jobs.db`) is the deliberately-separate analogue for output-moving ops and is
**not** part of this unit (a topic renumber does not move outputs).

**Still to wire (Phase 1 remainder):** the `clm course renumber` command that
computes spec-order numbering, does the two-phase `git mv`, and calls this
migrator; plus its `--report-only`/`--json` surface.

---

## 10. Open questions

1. ~~Command naming~~ — **decided**: flat verbs `clm course renumber` / `mv` /
   `restructure`.
2. Default numbering scheme for `renumber` — gap-10 (`10,20,30…`) seems right
   given the "ran out of numbers" origin; confirm width handling (keep 3 digits?).
3. Should `renumber` ever touch `slides_NNN_` files, or is topic-only the
   contract (slides opt-in via `--slides`)?
4. How aggressively to chase cross-repo state — auto-edit sibling cohort repos if
   co-located, or always report-only?
5. Is `git mv` mandatory (history preservation) or should a `--no-git` plain-move
   fallback exist for non-git course trees?
