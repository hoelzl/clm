# Caching Layers

CLM caches build work in **three independent caches** spread across **two SQLite
database files**. They have different keys, different readers/writers, different
trim policies, and — crucially — they can *disagree*. A hit in one does not imply
a hit in another. Most "why did this unchanged deck rebuild?" and "why did a
cached file vanish?" bugs come from missing one of these interactions.

This document is the canonical reference for how the caches are keyed, when each
is consulted during a build, how they are trimmed, and the subtleties that have
produced real bugs (issues #321, #577/#578, #579/#582, #580/#581).

> **Diagnose first, theorize second.** `clm cache explain <deck> --spec <xml>`
> prints, per output artifact, a HIT/MISS for every layer and a plain-English
> verdict ("replays stored result" / "will execute …"). Reach for it before
> guessing. See [Diagnosing cache behavior](#diagnosing-cache-behavior).

## The two database files

| File | Default location | Holds | Overridden by |
|---|---|---|---|
| **`clm_jobs.db`** | project root | `jobs`, `results_cache`, `workers`, `worker_events` | `--jobs-db-path` / `CLM_JOBS_DB_PATH` |
| **`clm_cache.db`** | project root | `processed_files`, `executed_notebooks`, `processing_issues` | `--cache-db-path` / `CLM_CACHE_DB_PATH` |

The two files have **independent lifecycles**. A build can update `clm_cache.db`
while leaving `clm_jobs.db` untouched (or vice versa), so one file can be days
staler than the other. When lookups in the two disagree, check the file mtimes
first — a stale `clm_jobs.db` alone will make every `results_cache` probe miss
while `processed_files` in a fresh `clm_cache.db` still hits.

Both DB paths walk up to the project root; `clm cache explain` (and any manual
probe) **must** be given the *same* `--cache-db-path` / `--jobs-db-path` /
`--output-dir` the build used, or the lookups miss spuriously.

## The three caches

### 1. `results_cache` — the job-level cache ("jobcache")

- **File / table:** `clm_jobs.db` → `results_cache`
- **Key:** `UNIQUE(output_file, content_hash)`
- **Read by:** `JobQueue.check_cache(output_file, content_hash)`
  (`infrastructure/database/job_queue.py`)
- **Written by:** `JobQueue.add_to_cache(...)` (worker-side, or via the
  worker HTTP API in Docker mode)
- **Purpose:** the *scheduling* short-circuit. If a stored result for this
  `(output_file, content_hash)` exists **and the output file is already on
  disk**, no worker job is enqueued at all. Consulted in `_submit_job_blocking`
  (`infrastructure/backends/sqlite_backend.py`); a hit yields
  `("jobcache_hit", None)`.
- **Trim policy:** newest `cache_versions_to_keep` rows per `output_file`,
  indefinitely (`JobQueue.prune_old_cache_versions`, wired into
  `cleanup_all` + build-end cleanup + `clm db cleanup`). Also
  `clear_orphaned_cache_entries()` drops rows whose output file is gone from
  disk. *(Before #581 this table had no trim at all and grew without bound —
  a changed hash left the old row behind forever.)*

### 2. `processed_files` — the operation result cache

- **File / table:** `clm_cache.db` → `processed_files`
- **Key:** `(file_path, content_hash, output_metadata)`
- **Read by:** `DatabaseManager.get_result(...)`
  (`infrastructure/database/db_operations.py`)
- **Written by:** `store_latest_result(...)` (trims to `retain_count + 1` on
  write)
- **Purpose:** the *recomputation* short-circuit. Stores the full pickled
  `Result` object; on a hit the backend writes the cached bytes straight to the
  output file (or, in incremental mode, skips the write) — no worker runs.
  Consulted at the top of `execute_operation`.
- **Trim policy:** newest `cache_versions_to_keep` per
  `(file_path, output_metadata)`, indefinitely
  (`DatabaseManager.prune_old_versions`, build-end + `clm db cleanup`).

### 3. `executed_notebooks` — the execution cache

- **File / table:** `clm_cache.db` → `executed_notebooks`
- **Key:** `(input_file, language, prog_lang, content_hash)` where the hash is
  `payload.execution_cache_hash()` — **kind-agnostic** (see below)
- **Read by:** `ExecutedNotebookCache.get(...)`
  (`infrastructure/database/executed_notebook_cache.py`)
- **Written by:** the notebook worker after kernel execution
- **Purpose:** caches the *executed* notebook (with kernel outputs) so that
  Stage-4 consumers replay execution instead of re-running the kernel. This is
  the producer/consumer relationship described below.
- **Trim policy:** newest per `(input_file, language, prog_lang)`
  (`prune_stale_hashes()`, build-end). `prune_old_entries(days)` exists but is
  **not** wired into the build path — there is no age-based expiry.

## Two hashes, and why the caches disagree

Both hashes live in `NotebookPayload` (`infrastructure/messaging/notebook_classes.py`).

```
content_hash()          = SCHEMA_VERSION : output_metadata      : _dependency_digest : data
execution_cache_hash()  = SCHEMA_VERSION : prog_lang : language : _dependency_digest : data
```

- `content_hash()` folds in **`output_metadata`** = `(kind, prog_lang, language,
  format)`. It is therefore **kind- and format-specific** — Recording HTML,
  Completed HTML, and the Code-Along notebook each get a *different*
  `content_hash`. This keys `results_cache` and `processed_files`.
- `execution_cache_hash()` **excludes `kind` and `format`**. Speaker/Recording
  and Completed HTML share one executed notebook (Completed is Speaker with
  `notes` cells filtered out), so they must share one execution-cache key. This
  keys `executed_notebooks`.

This asymmetry is *why* `executed_notebooks` can be cold while `processed_files`
hits for the same deck: they are keyed by different hashes over different tables.
Do not assume they move together.

### `_dependency_digest` — what invalidates a cache

`_dependency_digest()` folds in, besides `data` (the notebook text):

- **`template_fingerprint`** — a digest of the bundled Jinja template directory
  **plus the CLM version**. ⚠️ **Any `clm` upgrade changes this and invalidates
  the entire notebook cache, wholesale.** This is intentional (#321): a template
  shipped with a new clm must not replay stale output.
- **`worker_image_identity`** — `"direct"` or `"docker:<image reference>"`. A
  cache populated under one execution environment must not be replayed under
  another. ⚠️ Switching `--workers direct` ↔ `docker`, or a moved/retagged
  image, cold-misses everything.
- **`skip_evaluation` / `skip_errors`** execution flags.
- **All sibling `other_files` bytes** — the C++ headers a deck `#include`s,
  Jinja `{% include %}` targets, data files. ⚠️ On Windows, CRLF↔LF
  normalization of a sibling between builds changes these bytes and invalidates
  the deck.

The HTTP-replay cassette entry is **deliberately excluded** from the digest;
folding it in produced an unfixable cache-miss loop (the cassette is read at
payload construction but written after kernel execution). Do **not** "fix" that
exclusion — see the `_dependency_digest` docstring.

**Takeaway:** a cold cache on an *unchanged* deck is almost always *key
invalidation* (clm upgrade, worker-image/mode switch, CRLF drift, a
`CACHE_HASH_SCHEMA_VERSION` bump), **not** eviction by retention. Confirm with
`clm cache explain`.

## Where each cache is consulted during a build

`execute_operation` (`infrastructure/backends/sqlite_backend.py`) is the choke
point. Order of consultation for one output artifact:

```
execute_operation(operation, payload)
│
├─ processed_files: db_manager.get_result(input_file, content_hash, output_metadata)
│     │
│     ├─ HIT and _can_replay_from_cache(payload) ──▶ write cached bytes (or skip in
│     │                                              incremental mode); register the
│     │                                              output; report hit; RETURN. No worker.
│     │
│     └─ HIT but _can_replay_from_cache == False ──▶ fall through with force_execution=True
│           (Recording/Speaker HTML producer with a cold executed_notebooks — see below)
│
└─ _submit_job_blocking(payload, job_type, force_execution)   [offloaded to a submit thread]
      │
      ├─ results_cache: check_cache(output_file, content_hash)   (skipped when force_execution)
      │     └─ HIT and output on disk ──▶ ("jobcache_hit", None): register the output,
      │           replay stored issues, report hit; RETURN. No worker.
      │
      └─ MISS ──▶ enqueue a worker job ("submitted", job_id). Worker runs, writes output,
                  populates results_cache and (for notebook execution) executed_notebooks.
```

Key invariants encoded here:

1. **A cache replay must be observationally equivalent to execution (#321).**
   Whenever a cache short-circuits a worker run, the replay path must do
   *everything* the worker's completion would have done that downstream steps
   observe: write (or dedup) the output, **register it in
   `output_write_registry`**, and replay any stored `processing_issues`. Missing
   the registration is what caused #577 (see below).

2. **`processed_files` and `results_cache` are gated by the same
   `--ignore-cache` flag** (`self.ignore_db`). `executed_notebooks` is peeked
   read-only by the Stage-4 guard.

## Stage-4 producer/consumer: the execution-cache warmup guard

Some output kinds **produce** the execution cache; others **consume** it:

- **Producer:** Recording/Speaker HTML — running it executes the notebook and
  stores the result in `executed_notebooks`.
- **Consumers:** Completed / Trainer / Partial HTML (Stage 4) — they read
  `executed_notebooks` to skip re-executing the kernel.

If a producer is served from a cache without actually running, the execution
cache stays cold and every consumer falls back to direct kernel execution. To
prevent that, `_can_replay_from_cache(payload)` returns **`False`** for
Recording/Speaker HTML when `executed_notebooks` is cold, steering the payload
off the `processed_files` replay and into a worker run that repopulates the
execution cache.

⚠️ **The subtlety (#579):** forcing the payload past `processed_files` is not
enough — the *next* layer, `results_cache`, would independently short-circuit
the very run the guard just forced (`jobcache_hit`), leaving the execution cache
cold anyway. The fix threads the guard's decision through as `force_execution`,
which suppresses the `results_cache` probe for exactly those payloads. When you
touch either the guard or the job-cache probe, keep them in sync: **a decision to
warm the execution cache must suppress *both* replay layers, not just one.**

## Retention: newest-N, indefinitely

The retention policy for **every cache that gates a rebuild** is *keep the newest
`cache_versions_to_keep` versions per file, indefinitely — no age-based expiry*.
The superseded versions (old content hashes) are the only thing trimmed.

`RetentionConfig` (`infrastructure/config.py`) also exposes
`completed_jobs_retention_days`, `failed_jobs_retention_days`,
`cancelled_jobs_retention_days`, and `worker_events_retention_days`. ⚠️ **These
day-based knobs prune only the diagnostic `jobs` and `worker_events` tables —
they never touch `results_cache`, `processed_files`, or `executed_notebooks`.**
Pruning a finished job row does not cause re-execution; the caches live in
separate tables. Unbounded growth of `jobs` was purely a `clm status` / `clm
monitor` startup-time problem, not a correctness one.

Build-end cleanup (`_perform_build_end_cleanup`) runs by default
(`auto_cleanup_on_build_end = true`) and trims all three caches to
`cache_versions_to_keep`. `clm db cleanup` applies the same policy on demand.

## Diagnosing cache behavior

```bash
# Same DB/output paths the build used — otherwise lookups miss spuriously.
clm cache explain path/to/slides_deck.cpp --spec course-specs/your-course.xml

# Narrow to a language/kind/format, or emit JSON:
clm cache explain deck.cpp --spec course.xml -L de --format html --json
```

For each output artifact it prints the key components (data hash,
`template_fingerprint`, `worker_image_identity`, sibling files) and, per layer,
`processed_files` / `executed_notebooks` / `results_cache` HIT or MISS plus a
verdict. Read the verdict:

- **"replays stored result"** — `processed_files` hit; no worker runs. Healthy.
- **"will execute (processed_files hit, but the execution cache is cold and this
  kind is its producer)"** — a Recording/Speaker HTML deck that must run to warm
  `executed_notebooks`. Expected on the first build after the execution cache was
  cleared; a *persistent* one across builds means the warm-up isn't sticking.

⚠️ **Path fidelity:** the caches key on absolute input/output paths. Running
`clm cache explain` from a *git worktree* against a cache the build populated
from the *main checkout* produces spurious misses because the absolute deck path
differs. Point it at the same checkout that ran the build.

## Worked history — the four coupled bugs

All four lived in the interaction between these layers; together they are a
useful map of the failure modes.

| Issue / PR | Failure mode | Fix |
|---|---|---|
| **#577 / #578** | `jobcache_hit` left the on-disk output unregistered, so the end-of-build stray-file sweep deleted valid Recording/Speaker HTML for unchanged topics. | Register the output on the `jobcache_hit` path (uphold "replay == execution"). |
| **#580 / #581** | `results_cache` grew without bound: `UNIQUE(output_file, content_hash)` + `INSERT OR REPLACE` meant a changed hash inserted a new row and never removed the old one; nothing trimmed the table. | `prune_old_cache_versions` — newest-N per `output_file`, wired into build-end cleanup. |
| **#579 / #582** | The execution-cache warmup guard forced a producer run past `processed_files`, but `results_cache` then short-circuited it, so `executed_notebooks` stayed cold and Stage-4 consumers re-executed. | Thread the guard's decision as `force_execution`, suppressing the job-cache probe for those payloads. |
| **#321** | Template/worker-image/sibling changes replayed stale teaching material because the cache key ignored them. | Fold `template_fingerprint`, `worker_image_identity`, and sibling bytes into both hashes; add `CACHE_HASH_SCHEMA_VERSION`. |

## Rules of thumb for changing cache code

- **When you touch one cache's gate or trim, check the other two.** They are
  keyed differently and can disagree; a change that looks local often shifts
  which layer serves a payload.
- **Any new cache short-circuit must register its output** in
  `output_write_registry`, or the sweep will delete the file.
- **A decision to warm the execution cache must suppress every replay layer**
  (`processed_files` *and* `results_cache`), not just the first one.
- **Retention is count-based and indefinite.** Do not add age-based expiry to a
  rebuild-gating cache without a very good reason; document it loudly if you do.
- **Validate regression tests against the unfixed source.** A cache test that
  passes on both old and new code is not testing the fix.
