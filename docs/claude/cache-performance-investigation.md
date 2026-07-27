# Cache Performance Investigation (Issue #711)

Cross-session record of the 2026-07-27 investigation into slow builds of large,
fully-cached courses (reported for the AZAV ML 2026-04 course in
PythonCourses, clm 1.21 vs 1.23). Issue: https://github.com/hoelzl/clm/issues/711
(interim measured findings + root cause + py-spy profile posted as comments).

## Reported symptom

Fully-cached builds of the huge AZAV ML course (~820 source files, ~5800
build ops, 3.3 GB cache DB, 6.8 GB output tree) felt much slower on clm 1.23
than 1.21. Expectation: unchanged files should be neither re-sent to workers
nor rewritten to disk. In the user's slow build, most ops DID replay from
cache — yet the build was still slow. The user's other (AI-dev) course showed
the same spurious "Rebuilding" lines, so it is not course-specific.

## Measured timeline (all on the real course, cache/output never deleted)

| Build | clm install | Workers | Result |
|---|---|---|---|
| User build ending 00:17 | global uv-tool (editable, CRLF) | 1 (default) | mostly cache hits (9012 hits in 13-min log tail), still slow (>45 min) |
| Assistant run 1, 01:02 | course-venv copy (LF) | 1 (default) | **EXIT=1 after 29m24s**; 2958/5814 ops (51%) missed; ~3000 jobs to workers; 3660 output files rewritten (1111 HTML); 275+ worker errors, notebooks hung 1200 s |
| User build, 02:26 | global uv-tool (editable, CRLF) | **8** | **~12.5 min, complete**, despite re-executing ~3700 notebook jobs |

## Root causes found

### 1. CRLF vs LF template bytes → template-fingerprint thrash (primary)

`compute_template_fingerprint` (`src/clm/core/operations/process_notebook.py`)
hashes `entry.read_bytes()` — raw bytes, no newline normalization.

- The user's **global `clm`** (uv tool) is an **editable install of the live
  clm working tree** (`_editable_impl_coding_academy_lecture_manager.pth` →
  `Projects/clm/src`). The clm repo has `core.autocrlf=true`, so
  `workers/notebook/templates_python/macros.j2` etc. have **CRLF** on disk.
- The **course-venv clm** (site-packages copy) has the same templates as
  **LF** (wheel/copy normalization).
- Same clm 1.23.0, same committed template content → different fingerprints
  (`53d7a3eb819f…` CRLF vs `d22ef1e3e429…` LF) → different `content_hash`
  for every notebook payload → **total cache invalidation on install switch**;
  each build re-stores its own lineage and thrashes the other.

Proven by hash archaeology: one deck's stored hash alternated between two
stable values across builds (`4cdbb58dabf4` ↔ `f8007149e296`); `clm cache
explain` component diff showed data/other_files/skip-flags identical and only
the template fingerprint differing. `file(1)` + `tr -d '\r'` diff confirmed
CRLF↔LF as the only byte difference.

**Fix**: newline-normalize in `compute_template_fingerprint` (e.g.
`read_bytes().replace(b"\r\n", b"\n")`, mirroring git text normalization).
One-time key change — consider noting alongside `CACHE_HASH_SCHEMA_VERSION`.
Also decide policy for `_dependency_digest`'s `other_files` hashing (CRLF
drift in course files arguably SHOULD invalidate; clm's own templates should
not disagree with themselves).

**Mitigation (user-side, done)**: PythonCourses `clm.toml` now sets
`[retention] cache_versions_to_keep = 5` so both install lineages coexist
instead of thrashing (default 1 → keep 2 rows).

### 2. One-time v1.23 invalidation: pickle → nbformat-json wipe (b70d2080)

v1.23.0's `_init_table` in `executed_notebook_cache.py` deletes all
pickle-era `executed_notebooks` rows (510 on this course) on first run →
forced re-execution of every deck, and the #579 warmup gate
(`_can_replay_from_cache`, sqlite_backend.py:616) forces worker runs whenever
the executed-notebook cache is cold. One-time cost per course repo, but it
landed exactly in the 1.21→1.23 window the user compared.

### 3. Per-op schema init + full-scan DELETE on every cache open (new in 1.23)

`_can_replay_from_cache` opens a **new** `ExecutedNotebookCache` connection
**per recording/speaker HTML op** (sqlite_backend.py:616). Every `__enter__`
runs `_init_table`: CREATE TABLE + CREATE INDEX + PRAGMA + **`DELETE FROM
executed_notebooks WHERE payload_format != 'nbformat-json'` (full scan) +
COMMIT** on a 3.3 GB WAL DB. py-spy: **15.1% of MainThread** during the
cached phase. Fix: open once per build, or gate the migration behind a
schema/user-version pragma so steady-state opens are cheap.

### 4. Single-worker serialization (default `--notebook-workers=1`)

Recording/Speaker HTML renders are worker jobs (~10–15 s each for the big ML
decks). With the default single notebook worker, Stage 3/4 of this course has
>1 h ETA when a miss storm hits. With `--notebook-workers=8` the same miss
profile finished in 12.5 min. Not a regression (defaults unchanged
1.21→1.23), but the biggest wall-clock lever. Consider raising the default or
documenting it prominently for large courses.

### 5. Health-monitor psutil burn (pre-existing, Windows-expensive)

`_monitor_health` → `is_worker_running` (worker_executor.py:909) falls back
to `psutil.process_iter(["pid", "environ"])` — a **system-wide scan reading
every process's PEB environment block** — for any worker not in the
executor's local process dict, every 10 s cycle. py-spy: **~57% of ALL
process samples** during the build, ~18% of one core continuously. Present
since before 1.21, but scales with worker count and fights the build for
CPU/GIL. The pool_manager.py:902 comment "the check is cheap" is wrong for
this fallback. Fix: store PID in the workers table (check
`psutil.pid_exists`/`Process(pid)`), or cache/throttle the scan; investigate
why the local-dict fast path misses at all.

### 6. Notebook job hangs blocking completion (run 1)

Multiple notebook jobs "did not complete within 1200s" (embeddings workshop,
vector-DB topics) and the build died on an orphaned worker job (#617 path).
If hangs recur, no build completes → cache never fully warms → perpetual
partial rebuilds. Needs a standalone repro; possibly mitigated by 8 workers
(the user's 02:26 build completed with 0 failed).

## Hit-path cost profile (py-spy, cached phase, MainThread ~20 s busy / 180 s)

| Cost | Share | New in 1.23? |
|---|---|---|
| `ExecutedNotebookCache._init_table` | 15.1% | **yes (b70d2080)** |
| output-write hashing (`_hash_bytes`/`_hash_file`, `is_destination_identical`) | ~22% | no |
| `stat`/`open` churn | ~16% | no |
| `get_result` (pickle.loads per hit) | ~12% | no |
| `get_job_statuses_batch` polling | ~12% of wait time | no |
| `diagnose_cache_miss` (only with `--explain-rebuilds`) | 36% during miss storm | instrumentation, not normal builds |

Cached-replay throughput observed: ~11.5 DB-cache-hits/s (~87 ms/hit).

## Ruled out

- Replay logic: `sqlite_backend.py` cached fast path byte-identical 1.21→1.23.
- `CACHE_HASH_SCHEMA_VERSION`: unchanged.
- Worker counts: default 1/type in both versions.
- Template *content* changes v1.23.0..master: none (byte-diff is CRLF only).
- Course source changes: git tree clean during measurements; the one mid-build
  pull (01:05) postdates run 1's start and doesn't explain the misses.
- pickle→JSON parse cost itself: ~0.2 s per 45 MB notebook — negligible; its
  cost is the one-time wipe (#2), not steady-state parsing.

## Miss amplification factor (design note)

A deck's content hash covers ALL sibling slide files in its topic
(`compute_other_files`): topic_880 has 20 files → one edit rebuilds ~90 ops.
Intentional (shared dependencies), but it multiplies every real edit and
every invalidation event.

## How to reproduce / measure

```bash
# PythonCourses root, Git Bash:
export CLM_RETENTION__CACHE_VERSIONS_TO_KEEP=5   # now permanent in clm.toml
time .venv/Scripts/clm.exe build --explain-rebuilds --log-level info \
  course-specs/machine-learning-azav-2026-04.xml 2>&1 | tee /tmp/build.log

# Host profile during the build (find PID via Get-CimInstance Win32_Process):
py-spy record --pid <PID> -d 180 -r 99 -f speedscope -o /tmp/profile.speedscope

# Cache forensics (read-only DB access only!):
.venv/Scripts/clm.exe cache explain <path-to-deck> --spec course-specs/machine-learning-azav-2026-04.xml
```

Decisive A/B now possible: rerun the same build — with both hash lineages
cached (retention=5) it should be ~100% hits; its wall time is the true
steady-state hit-path cost. Compare per-hit cost against 1.21 by installing
clm 1.21 into a scratch venv (beware: spec features may require newer clm).

## Ranked optimization candidates

1. Newline-normalize `compute_template_fingerprint` (correctness-of-cache;
   kills cross-install thrash).
2. Hoist `ExecutedNotebookCache` open out of the per-op path / gate
   `_init_table`'s migration on a schema marker (15% of cached-phase
   MainThread; new in 1.23).
3. PID-based worker liveness instead of system-wide psutil environ scans
   (~18% of a core every build on Windows).
4. Raise/document `--notebook-workers` default for large courses
   (12.5 min vs 29+ min on this course).
5. Investigate 1200 s notebook hangs (embeddings/vector-DB topics) — blocks
   cache warm-up entirely when it strikes.
6. Consider per-file (not whole-topic) dependency hashing to reduce miss
   amplification — needs care, sibling files are genuine dependencies.
