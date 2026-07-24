# Adversarial Review — CLM 1.21.1 (2026-07-24)

Five independent adversarial reviewers attacked the codebase along separate
axes: architecture/design, concurrency/correctness, the slides sync engine,
security/robustness, and testing strategy. Each was instructed to assume the
design is flawed until proven otherwise and to discard any finding it could not
confirm in code. Sync-engine findings were reproduced end-to-end with scripts;
the top security findings were re-verified by hand in the project venv.

This document records findings as they stood on 2026-07-24.

**Fixed since**: S1 (cassette YAML RCE) and C1 (WAL on network-shared
databases) shipped in **1.22.1** via PR #658. Everything else below is still
open — see
`docs/claude/handovers/adversarial-review-remediation-handover.md` for the
phased plan and the maintainer's settled decisions.

---

## Top 10 by expected damage

| # | Sev | Finding | Location |
|---|-----|---------|----------|
| 1 | Critical | Cassettes parsed with PyYAML's **unsafe** `CLoader` → RCE from a committed course-repo file | `infrastructure/http_replay_mitm/vcr_format.py:46,319` |
| 2 | Critical | Worker API binds `0.0.0.0:8765`, no auth, stores/serves **pickles** → LAN- and browser-reachable RCE | `infrastructure/api/server.py:22-23`, `worker_routes.py:363-394` |
| 3 | Critical | `clm.core` ↔ `clm.infrastructure` is a module-level import **cycle**; the documented "core has ZERO infrastructure dependencies" is false | `core/course.py:53-56`, `architecture.md:88` |
| 4 | High | WAL mode unconditionally enabled on a jobs DB explicitly shared across machines over SMB → claim atomicity broken, DB corruption | `infrastructure/database/schema.py:193` |
| 5 | High | Workers never heartbeat **while processing**; any job >30s looks dead → spurious build failures, cross-build worker deletion | `infrastructure/workers/worker_base.py:645-790` |
| 6 | High | `reset_hung_jobs` resets at 600s with **no liveness check** → double execution, torn output, poisoned result cache | `infrastructure/database/job_queue.py:650-670` |
| 7 | High | `mirror_remove` lacks the carried-divergence guard the edit paths have → mechanically deletes unique surviving content | `slides/sync_diff.py:1203-1215`, `:1911-1922` |
| 8 | High | Ledger `record` gate never inlines voiceover companions the way `verify` does → diverged companions blessed as verified | `cli/commands/slides/sync_v3.py:239-250` |
| 9 | High | `test_direct_integration.py` — 8 Direct-mode worker tests — permanently skipped everywhere via renamed module names | `tests/infrastructure/workers/test_direct_integration.py:69-70` |
| 10 | High | `.clm-include` ledger drives an unvalidated `shutil.rmtree`; absolute/`..` `as_path` escapes the topic dir | `cli/commands/course/sync_includes.py:429-436` |

---

## 1. Security

Threat model: malicious course content (notebook *execution* is expected and
excluded), the local web servers, the MCP surface, secrets leakage, unsafe file
ops.

### S1 — CRITICAL: unsafe YAML loader on committed cassettes

`vcr_format.py:46` imports `CLoader`, whose MRO reaches `UnsafeConstructor`
*before* `SafeConstructor`. Verified in the project venv: a cassette line
`a: !!python/object/apply:os.getenv ["COMPUTERNAME"]` executes during
`yaml.load`, before any schema check. The `except Exception → "treating as
empty"` handler at `http_replay_cassette.py:212` then swallows any error, so a
build continues silently.

Reachability is what makes this critical rather than theoretical:
`.gitignore:280` explicitly **un-ignores** `**/.clm/cassettes/`, so cassettes
are tracked, committed, rarely-reviewed files (~400 MiB per project notes);
replay defaults to `new-episodes` for local builds (`build.py:52-76`); and the
host `clm build` process itself parses them (`build.py:2001`). A one-line PR to
a course repo executes code as the maintainer on the next build — no notebook
runs.

**Fix:** `from yaml import CSafeLoader as _Loader`. The format is scalars, maps,
lists and `!!binary`, all SafeLoader-supported. Add a safe-load round-trip test.

### S2 — CRITICAL: unauthenticated worker API serving pickles on all interfaces

`server.py:22-23` binds `0.0.0.0:8765` "for Docker access"; `worker_routes.py`
contains **zero** `Depends` (verified by grep). `POST /cache/executed_notebook`
gzip-decompresses the body and stores it verbatim; those bytes are later
`pickle.loads`-ed on the host (`sqlite_backend.py:575`,
`executed_notebook_cache.py:130`). The cache key is not secret — the equally
unauthenticated `POST /jobs/claim` hands out `input_file` and `content_hash`.

Two attack positions: anyone on the LAN, and **any web page the developer
visits** — a `fetch(..., {mode:'no-cors'})` with `Content-Type: text/plain` is a
simple request needing no preflight, and the handler ignores content type.

**Fix:** bind `127.0.0.1` and publish explicitly to containers; add the
per-build token `studio/auth.py` already implements; replace pickle with JSON
(`NotebookNode` is a dict subclass).

### S3 — HIGH: `/process` uploads any local file to a third party via plain CSRF

`recordings/web/routes.py:567-602` takes `raw_path` from a form, checks only
`exists()`, and submits it; with the Auphonic backend that streams the file to
auphonic.com. The **sibling** endpoint `/open-explorer` (`:794-804`) does
`resolve(strict=True)` + `relative_to(root)` — the omission is inconsistent, not
deliberate. The recordings app has no auth and no CSRF protection on any
mutating route, all `Form(...)`-based, i.e. reachable by a cross-origin
auto-submitting form.

### S4 — HIGH: `.clm-include` ledger → unvalidated `rmtree`

`sync_includes.py:429-436` computes `topic_dir / entry.as_path` with `as_path`
unvalidated. `Path.__truediv__` **discards** the left operand when the right is
absolute, so `{"as_path": "C:/Users/tc/Programming"}` targets that literally;
`../../../..` works too. The module docstring promises the opposite. The
correct normalizer already exists for spec `<include>` paths
(`course_spec.py:88-140`).

### S5 — HIGH: repo-local `clm.toml` chooses which executable `clm build` runs

`config.py:936-947` discovers config by walking up from cwd — including a file
shipped inside the course repo. `drawio_converter.py:14,47` runs
`external_tools.drawio_executable` verbatim, with no validation at all. Clone a
course repo, run `clm build`, and a repo-supplied binary executes on the first
`.drawio` file. Same shape: `notebook_kernel_python` (`kernel_env.py:165`), and
spec-supplied `repository_base` reaching `git clone` — Git's `ext::` transport
executes its argument (`git.py:215,796`).

### S6 — MEDIUM-HIGH: `clm serve` — CORS `*` with credentials, unauthenticated WebSocket, no Host validation

`web/app.py:127-136` defaults `cors_origins=["*"]` with
`allow_credentials=True`, so Starlette echoes any requesting Origin.
`api/websocket.py:110-126` accepts **any** `/ws` connection with no token and no
origin check, and `subscribe` accepts arbitrary channel names — WebSockets are
CORS-exempt, so any page can subscribe to `"studio"` and receive sync progress
and deck-change events, bypassing the bearer token `studio/auth.py` calls "the
real access gate". Neither app installs `TrustedHostMiddleware`.

### S7 — MEDIUM: Studio `render-cell` is unsandboxed Jinja on a request body

`web/studio/render.py:55-71` uses a plain `jinja2.Environment` and
`from_string()` on client text; SSTI reproduced against the real function. The
module docstring advertises this as the "no-execution" render tier. Token-gated,
but the token is accepted as a `?token=` query param (`auth.py:78-80`), so it
lands in QR deep links, browser history and uvicorn access logs. Chained XSS:
`static/studio/app.js:45-47` — `esc()` does not escape quotes and its output is
interpolated into an `href`, reaching `innerHTML` at `:315`.

### S8 — MEDIUM: MCP mutating tools have no containment check

`mcp/tools.py:939-942` — `_resolve_under` is a bare join: no `resolve()`, no
`relative_to`, no `commonpath`. Inlined in every mutating handler.
`voiceover_inline(file=<absolute path>)` rewrites that deck and `unlink()`s its
companion. The correct check already exists at `web/studio/service.py:131-141`.

### S9 — MEDIUM: cassette scrubbing is a request-only denylist

`cassette_format.py:72-74` misses `api-key` (Azure OpenAI), `x-goog-api-key`
(Gemini), `proxy-authorization`, `x-amz-security-token`; misses Gemini's
`?key=`, `?access_token=`, `?subscription-key=`, `?X-Amz-Signature=`; filters
body params only for an exact `application/json` compare, so
`application/json; charset=utf-8` gets none; and **never filters responses** —
`Set-Cookie` and OAuth token bodies are committed verbatim. Related: the
mitmproxy CA **private key** lands inside the course repo working tree by
default (`build.py:313-316`), unignored, and mitmproxy's `umask_secret()` is a
no-op on Windows.

### S10 — MEDIUM: Docker mode is not a containment boundary

`worker_executor.py:314-341` mounts the **entire course repo including `.git/`**
read-write as `/source`; no `user` is set and no Dockerfile has a `USER`, so
containers run as uid 0; `extra_hosts` maps `host.docker.internal`. A malicious
cell can rewrite `.git/hooks/post-checkout` (host execution at next git op) or
reach `host.docker.internal:8765` (finding S2). The `course.py:340`
"refuse to bind-mount an entire volume" guard has three verified gaps: the
`len(resolved)==1` early return skips it, `data_dir` has no guard at all, and it
is only consulted when `_build_has_docker_notebook_worker` is true — a detector
that returns `False` on any exception.

### S11 — MEDIUM: spec-driven writes escape the output root

`dir_group.py:75-84,94` appends raw `<name>`/`<subdir>` text to output paths
with no sanitization (section names *are* sanitized — the asymmetry looks like
an oversight), so `<name><de>../../..</de></name>` copies into the source tree.
`<output-target><path>` is validated only for emptiness, and the sweep keeps
only `.git/**` with no marker-file precondition — a one-character typo
(`<path>.</path>`) empties whatever directory resolves there. `sanitize_file_name`
does not reject `..` (verified: `sanitize_file_name('..') == '..'`).

### S12 — MEDIUM: `clm config show --json` prints every secret in cleartext

`config.py:504,600,692` — LLM api_key, Auphonic api_key, OBS password are plain
`str`, no `SecretStr`, no custom `__repr__`. This is exactly the output pasted
into bug reports and agent transcripts.

### Verified clean (the named suspects)

No `shell=True`, `os.system`, or `os.popen` anywhere. `clm run` confines a
malicious spec to `[sys.executable, "-m", "clm", ...]`. Cassette replay **is**
host-validated (scheme/host/port/path/query in the match key) — a cassette for
`evil.example` can never be served for `api.openai.com`. XXE not exploitable
(stdlib ElementTree); billion-laughs works but is a local hang only. No pickle
outside the three cache sites; no `eval`/`exec` on content-derived strings. No
`verify=False`. GitLab/GitHub tokens pass only the *variable name* in argv. No
docker socket mount, no `privileged`.

---

## 2. Concurrency & correctness

**Verified sound first:** the local claim path is atomic — `get_next_job`
(`job_queue.py:254-322`) wraps SELECT+UPDATE in `BEGIN IMMEDIATE`, so two
workers on the same machine cannot double-claim. Everything below is a real gap,
and all of it concerns the *shared jobs DB* scenario the schema explicitly
supports (v10 `execution_mode` tags, `session_id` ownership columns).

- **C1 CRITICAL — WAL over SMB.** `schema.py:193` sets `journal_mode=WAL`
  persistently. WAL coordination lives in the memory-mapped `-shm` file; over
  SMB each machine maps its own incoherent copy, which SQLite documents as
  unsupported. No UNC/network guard exists anywhere. Machine B's
  `BEGIN IMMEDIATE` can claim a row A already claimed, against a stale snapshot;
  interleaved checkpoints can corrupt the file. This amplifies every finding
  below.
- **C2 HIGH — no heartbeat while processing.** `worker_base.py:645-790` updates
  the heartbeat only in the idle-poll branch and the post-job `finally`. During a
  multi-minute notebook the worker looks dead. Three confirmed consequences:
  (a) `_get_available_workers` (`sqlite_backend.py:1241-1254`) counts only
  workers seen in the last 30s, so submitting deck N+1 while all workers are
  busy raises `RuntimeError("No workers available")`; (b)
  `cleanup_stale_workers` (`pool_manager.py:279-294`) hard-DELETEs any direct
  worker not in `healthy_ids` **with no session filter**, so a second build
  deletes the first build's busy worker rows; (c) persistent workers get
  duplicated.
- **C3 HIGH — `reset_hung_jobs` is liveness-blind.** `job_queue.py:650-670`
  resets anything `processing` >600s with no worker check, and it runs on every
  build start *and* end (both default-on). A job 11 minutes into a heavy deck —
  well inside the 20-minute `max_wait_for_completion_duration` — flips to
  `pending`, a second worker claims it, and both write `job.output_file` via the
  non-atomic `open(path,"w")` in `notebook_worker.py:264`. Then
  `_persist_result_to_cache` reads the torn file back and stores it keyed by
  content hash: **the corruption is replayed as a cache hit on every future
  build**.
- **C4 HIGH — `mark_orphaned_jobs_failed` is unscoped.**
  `job_queue.py:428-467` fails *every* non-terminal started job at pool
  shutdown. Its docstring's safety argument holds only single-session. Build A's
  teardown marks build B's live job failed; B reports a spurious error, then B's
  worker finishes and flips the row back to completed.
- **C5 HIGH — `update_job_status` has no fencing.** `job_queue.py:340-386` and
  `worker_routes.py:149` do `UPDATE jobs SET status=? WHERE id=?` with no
  `AND worker_id=?` / `AND status='processing'`. This is the missing guard that
  lets C3/C4 corrupt state rather than merely waste work.
- **C6 MEDIUM-HIGH — job-cache hit validates existence, never content.**
  `sqlite_backend.py:484-493` short-circuits on a `results_cache` row plus
  `output_path.exists()`. Nothing checks the bytes match the hash, so a stale or
  torn output ships as a hit.
- **C7 MEDIUM — dead-worker recovery can never fire during a build.**
  `_cleanup_dead_worker_jobs` requires `workers.status='dead'`, but
  `start_monitoring` is only called from a `__main__` demo (`pool_manager.py:1228`),
  direct workers DELETE their row, and the query is an INNER JOIN on a row that
  may be gone. An OOM-killed worker hangs the build for the full 1200s timeout;
  the 5-second cleanup poll is dead code in production.
- **C8 MEDIUM — latent timezone bug.** `_is_heartbeat_stale`
  (`pool_manager.py:941-943`) compares a UTC `CURRENT_TIMESTAMP` against local
  `datetime.now()` — on a CEST host every heartbeat reads ~7200s stale.
  `discovery.py:183` does it correctly; the two have drifted. Harmless only
  because monitoring is never started (C7).
- **C9 MEDIUM — no terminal handling for `cancelled` or attempts-exhausted
  jobs** in `wait_for_completion` → 20-minute stalls.
- **C10 MEDIUM — blocking SQLite on async loops.** `worker_routes.py:88-192` are
  `async def` but run blocking SQLite with a 30s busy timeout on the uvicorn
  loop, so one held write lock freezes all Docker-worker traffic *including
  heartbeats* — feeding C2. `_can_replay_from_cache` unpickles a multi-MB BLOB
  on the build's event loop merely to test existence.
- **C11 LOW-MEDIUM — worker output write is truncate-in-place**, unlike the
  host's `atomic_write_bytes` (temp + `os.replace` + retry). This is the
  amplifier that turns C3 into durable cache poisoning.
- **C12 LOW — `JobQueue.close()` closes only the calling thread's connection**,
  leaving submit-executor and cache-writer connections to GC; on Windows that
  pins `-wal`/`-shm` so the subsequent `wal_checkpoint(TRUNCATE)` silently
  no-ops (issue #144's symptom, via a different door).

---

## 3. Slides sync engine

All findings reproduced end-to-end against the real code, not read from it.

- **Y1 HIGH — `mirror_remove` has no carried-divergence guard.** Every shared
  *edit* path refuses verbatim propagation when the baseline itself carried a
  divergence (`sync_diff.py:992/1025`, `:1979` — "no side is a safe verbatim
  source"). The *removal* paths (`:1203-1215`, `:1911-1922`) check only that the
  surviving side sits on its own fingerprint. Reproduced: a shared companion
  recorded divergent (DE real content, EN placeholder); EN deletes the
  placeholder; plain `apply` emits `mirror_remove` as MECHANICAL and **empties
  the DE cell containing content that never existed on EN**. Exit 0, report
  clean afterward.
- **Y2 HIGH — the ledger write gate never reads the companions.**
  `structural_gate` (`sync_v3.py:239-250`, `:375-385`) is called with only the
  two deck-half texts, while the `verify` verb first runs `project_pair` to
  inline companions (the #501 fix). Its docstring claims gate and CLI "can never
  drift" — they have: **verify FAILS on a byte-diverged shared companion while
  record blesses it**. This is what arms Y1 and Y6, and `cold_sweep_hint`
  actively steers agents to run `record` wholesale on cold decks.
- **Y3 HIGH — decision keying is not item keying.** `Decision`
  (`doc_apply.py:100-115`) has no action field and `parse_decisions` rejects
  duplicate keys, but the differ legitimately emits several framed items per
  member handle. Reproduced: answering `{"key":"id:n1","choice":"de"}` to settle
  an *owner* conflict also lands on the co-keyed *content* conflict and
  overwrites the EN author's rewritten note body. Violates the §8 per-item
  contract — a *valid* answer lands on an item the judge never addressed.
- **Y4 MEDIUM-HIGH — decisions carry no fingerprint binding.** `apply
  --decisions` re-diffs from current files but validates only shape. Reproduced:
  DE edited to v2 → report frames `translate_edit` → agent translates v2 → DE
  edited to v3 before apply → the v2 translation lands and the ledger records it
  as **verified**; next report is CLEAN and the v3 edit is never surfaced again.
  Defeats the ledger's fail-safe staleness property in exactly the window agents
  operate in.
- **Y5 MEDIUM-HIGH — `stamp_twin_id` stamps positionally-guessed, never-verified
  twins.** `pair_positionally` (`doc_lenses.py:505-522`) adopts an id-less
  localized twin purely by pool order, and `_emit_pending_id_stamps`
  (`sync_diff.py:417-435`) emits the stamp mechanically **regardless of ledger
  trust**. Reproduced: two localized cells added per side, EN twins id-less and
  swapped → `slide_id="apples"` is stamped onto the oranges text. Since P2 makes
  the id *the* identity, this is permanent identity corruption, later bankable
  as verified.
- **Y6 MEDIUM — preamble propagation lacks the divergence guard** cells have;
  the check exists only in the neither-moved branch (`sync_diff.py:2561-2570`).
  A trivial DE kernel-metadata edit replaces the entire EN preamble.
  CLI-reachable for companions via Y2.
- **Y7 MEDIUM — one-sided rename+edit executes destructive mechanics.** The
  design claims a hand rename drops to cold; true only when content is untouched.
  Rename+edit defeats both rival checks and yields `mirror_remove` +
  `copy_new_shared`. The `#600` deficit guard (`:565-586`) counts only `pos:`
  base entries, so an id-keyed base cell missing on one side never triggers
  `stamp_vs_new`. The strip-id variant leaves the pair structurally corrupt and
  fails the post-apply gate — files mutated, ledger unsaved, manual cleanup.
- **Y8 MEDIUM-LOW — `_align_pool`'s lone-candidate claim** (`:1693-1697`) binds
  any single unmatched new cell as "the landed twin" with no content affinity;
  the offered resolution overwrites a genuinely-new unrelated cell. Framed, so
  misleading rather than silent.
- **Y9 LOW — hardening notes.** `record` never consults the diff (a warm deck
  with pending conflicts can be wholesale-blessed); `verify_cold` `confirm`
  accepts a byte-diverged shared member for companions; `apply` writes files
  *before* the structural verify, and Y7 shows apply can itself create the
  gate-failing state.

**Checked and sound:** per-side pool alignment (phantom-slot exclusion,
unique-fp move detection); the #566 one-sided add routing; #600 pool freezing
(over-drops to cold, fail-safe); hash-version cold-drop; ledger load
fail-safety; `rename-id`'s fingerprint-carrying migration; re-parse gate +
atomic writes; `--since` staying read-only; duplicate-id refusals per side.

---

## 4. Architecture

The documented four-layer architecture does not exist in the code. The
individual modules are often well-written; the *boundaries* are fiction.

- **A1 CRITICAL — core↔infrastructure is a package-level cycle.**
  `architecture.md:88` claims "The core layer has ZERO dependencies on
  infrastructure. It can be tested in complete isolation." In fact
  `core/course.py:53-56`, `core/course_file.py:8-10`, `core/dir_group.py:9-10`
  and every file in `core/operations/` import infrastructure at module level,
  while infrastructure imports core back (`backend.py:10-11`,
  `local_ops_backend.py:37`). The doc explicitly targets AI agents, so a wrong
  architecture doc is worse than none.
- **A2 CRITICAL — infrastructure imports the CLI layer at runtime.**
  Module-level: `dummy_backend.py:7`, `local_ops_backend.py:36` import
  `clm.cli.build_data_classes.BuildWarning`. Lazy: `sqlite_backend.py:874,998,1698,1804`,
  `worker_base.py:758` (inside the worker loop), `progress_tracker.py:356`,
  `db_operations.py:336`. The abstract `Backend` interface itself references a
  `clm.cli` type. `build_data_classes.py` (329 lines of dataclasses) and
  `error_categorizer.py` (702 lines of business logic) are misfiled — every
  worker process executes CLI-layer code. The fix is mechanical.
- **A3 HIGH — core imports workers and optional extensions.**
  `core/course.py:548,637,641`, `core/operations/build_jupyterlite_site.py:26,39`,
  `core/cmake_export.py:34-36`, `core/course_files/notebook_file.py:128`, and
  spec parsing itself (`course_spec.py:2599` → `clm.slides.sidecar_layout`). No
  ImportError today only because those extension modules happen to be
  stdlib-only — luck, not design, and nothing enforces it.
- **A4 HIGH — the build engine lives in the CLI layer.**
  `cli/commands/build.py` is 2694 lines of which the Click command starts at
  :2071; everything before it is orchestration (`process_course_with_backend`,
  `watch_and_rebuild`, worker startup, mitmproxy bring-up, cleanup sweeps).
  Nothing else — MCP, web studio, tests, a future API — can run a build without
  going through a Click module.
- **A5 HIGH — voiceover business logic in the CLI**, via private imports:
  `cli/commands/voiceover.py:557-847` imports `_langfuse_configured` from
  `infrastructure.llm.client` and `_decode_alignment` from `voiceover.cache`,
  while `clm.voiceover.merge` exists precisely to hold this.
- **A6 HIGH — `path_utils` is a 785-line god module in the wrong layer**,
  imported by 43 files. It holds the purest domain vocabulary in the system —
  `Lang`, `Format`, `Kind`, `OutputSpec`, `output_specs()`, `output_path_for()` —
  inside `infrastructure.utils`, and core imports those types back up. Most of
  the A1 cycle flows through this one file.
- **A7 MEDIUM — three parallel config mechanisms:** Pydantic `ClmConfig` with a
  4-level hierarchy; `sidecar_layout.py:26-89`'s own resolver reading
  `[tool.clm]` in **pyproject.toml** (a file `ClmConfig` knows nothing about);
  and raw `os.environ` reads in 36 files including `build.py`'s private
  `_resolve_*` family that bypasses `get_config()` entirely.
- **A8 MEDIUM — the jobs-DB path has two env names and two defaults.** Host uses
  `CLM_JOBS_DB_PATH` (default `clm_jobs.db`); workers use bare `DB_PATH` with the
  *container* default `/db/jobs.db` even in direct mode. It works only because
  `worker_executor` always injects it; any other spawn path silently polls an
  empty queue — a known incident class in this repo's history.
- **A9 MEDIUM — ~12 cross-module underscore-private imports**, worst being
  `mcp/tools.py:391` → a CLI command's private helper.
- **A10 MEDIUM — `architecture.md` is materially wrong** beyond A1: a claimed
  "service registry" directory that holds one file, a layer diagram matching no
  import structure, undocumented env vars, and orchestration attributed to
  `Course.process()`.
- **A11/A12 LOW —** three-class `Backend` ladder with one production impl and a
  test-only `DummyBackend` shipped in the package; `docker`, `fastapi`,
  `uvicorn`, `watchdog` unconditional in the "core" install.

---

## 5. Testing

~451 test files, ~155k LOC. The suite's problem is not volume — it is that
several of the most critical paths are guarded by tests that run nowhere or
cannot fail.

- **T1 HIGH — 8 Direct-mode integration tests permanently skipped everywhere.**
  `test_direct_integration.py:69-70` gates on `find_spec("drawio_converter")`
  and `find_spec("plantuml_converter")` — top-level modules folded into
  `clm.workers.*` long ago. Verified: both return `None` in this venv, and in CI
  too. Dead: worker startup/registration, concurrent claiming, health
  monitoring, graceful shutdown, mixed worker modes, stale cleanup — exactly the
  PR #564/#595 territory. Same stale flags in `test_lifecycle_integration.py:76-77`.
  **Fixing two module names resurrects all 8.**
- **T2 HIGH — `slow` is a "runs nowhere" tier.** CI excludes `slow` from all
  three non-docker steps and the local default excludes it too, yet ~32 tests
  carry it without `docker`: all 7 **cache-equivalence** tests
  (`test_cache_equivalence.py:67-341` — the only proof that cached-notebook reuse
  is byte-identical to direct execution, i.e. the guard against C6-class silent
  wrong output), the two **worker-reuse-across-builds** e2e tests covering the
  PR #595 bug, and all 18 real-subprocess CLI tests.
- **T3 HIGH — pre-push build-pipeline coverage is 100% mock.**
  `test_build_command.py:1159-1278` replaces `Course`, both backends,
  `BuildReporter`, `init_database`, and makes `execution_stages()` return `[]`.
  It delegates real coverage to `test_cli_subprocess.py` — which runs nowhere
  (T2). The 72-second gate developers trust cannot catch broken stage
  sequencing, backend wiring, or job submission in a 2694-line file.
- **T4 MEDIUM-HIGH — CI "integration" tests for the build CLI cannot fail.**
  `test_cli_integration.py:55-68` wraps every functional assertion in
  `if result.exit_code == 0:`; `:400-402` is a literal tautology; `:196-198`
  passes on any failure; `test_build_with_clear_cache` never verifies the cache
  was cleared. Green checkmarks for "CLI → Backend → Workers → Output" that
  detect only argument-parsing typos.
- **T5 MEDIUM — the PlantUML e2e test logs but never asserts** that images were
  produced (`test_e2e_course_conversion.py` ~:1105-1143 — `logger.info` on the
  PNG count, no assertion).
- **T6 MEDIUM — `flaky` retries swallow `AssertionError` on tests of the real
  worker base** (`test_worker_base.py:26-35`), which is precisely how an
  intermittent race regression in production claim/heartbeat code manifests. The
  surrounding discipline (no global reruns, scoped exceptions) is otherwise good.
- **T7 MEDIUM — `MockWorker` reimplements claiming with pre-v10 SQL**
  (`tests/fixtures/mock_workers.py:212-226`): no `execution_mode` filter, no
  session ownership. The tier meant to test real claiming with real workers
  together is T1's dead file, so the drift is uncompensated.
- **T8 MEDIUM — autouse env-neutralizers make prod defaults a monoculture:**
  heartbeat slow-write threshold 50ms→30s suite-wide, pool-size caps pinned to
  128, `CLM_*_DB_PATH` cleared. Each is documented and reasonable in isolation;
  each leaves the *interaction* (e.g. clamp firing during managed-worker startup
  mid-build) invisible.
- **T9/T10 LOW —** sync full-corpus gates are triple-gated to manual-only;
  constructor-echo tests inflate apparent build-reporting coverage; the conftest
  `PLANTUML_JAR` path still points at the de-vendored (PR #239) location, so
  local availability now depends on an import-time `os.environ` mutation in
  another test module — ordering-dependent under xdist.

**Genuinely strong:** sync diff/apply unit coverage (76 + 51 fast tests — the
best-guarded critical path), job-claiming mode-tag tests against real SQLite,
`_can_replay_from_cache` gating, and golden-fixture handling (committed goldens,
no auto-bless flag, drift fails loudly — no circularity found).

---

## Suggested order of work

1. **`CSafeLoader`** (S1) — one line, removes an RCE reachable by PR.
2. **Worker API** (S2) — bind loopback, add the existing token pattern, drop
   pickle for JSON.
3. **Fencing + scoping in the job queue** (C5, C3, C4, C2) — add
   `AND worker_id=? AND status='processing'` to status updates, a liveness check
   before reset, session scoping on orphan-marking and stale-worker deletion, and
   a heartbeat during `process_job`. These are one coherent piece of work.
4. **WAL guard** (C1) — refuse or downgrade `journal_mode` on network paths, and
   document the constraint.
5. **Sync gate + removal guard** (Y2 then Y1) — projecting the pair in
   `structural_gate` closes the door that makes Y1 and Y6 reachable.
6. **Containment checks** (S4, S8, S3) — one shared normalizer; correct
   implementations already exist in `course_spec.py` and `studio/service.py`.
7. **Test resurrection** (T1, T2) — fix two module names, re-triage every `slow`
   marker; cheapest confidence recovered per hour of the whole list.
8. **Layering** (A2 then A6) — move `build_data_classes` + `error_categorizer`
   out of `clm.cli`, extract the domain types from `path_utils`, then add an
   `import-linter` contract to CI so the boundaries cannot silently regress. Fix
   `architecture.md` to describe reality until then.
