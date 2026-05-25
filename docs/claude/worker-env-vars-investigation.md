# Worker Environment Variables — Investigation & Deferred Design

**Status:** Deferred (2026-05-25). No code changes pending.
**Trigger:** Issue #129 (vcrpy / LangSmith `force_reset` race) — workaround was
to set `LANGSMITH_TRACING=false` in worker environment. Question raised: do we
have a clean way to set env vars in workers, ideally per-course?
**Resolution for now:** Course-side source files all call `load_dotenv()`, so
the slides side can manage the env without any CLM-side feature. Picking this
up again is unblocked but not urgent.

## Why this is filed instead of built

The integration is less clean than it first appeared (see "Constraints" below),
and the immediate user-visible problem is solvable in the course repo via
`load_dotenv()` in slide sources. The durable fix for the underlying race
(scoped `force_reset` monkey-patch — see
`project_issue_129_vcr_force_reset.md` in agent memory) is independent and
arguably more important for cassette correctness than a generic env-var
feature.

If a future need arises (different env values across courses, env values that
must be set before Python imports, env values that should be version-controlled
with the course spec), this document is the starting point.

---

## Current state

### Direct workers (`src/clm/infrastructure/worker_executor.py:498-525`)

Workers inherit the parent process environment via `os.environ.copy()`, then
CLM adds its own keys:

- `WORKER_TYPE`, `WORKER_ID`, `DB_PATH`, `WORKSPACE_PATH`, `LOG_LEVEL`,
  `USE_SQLITE_QUEUE`, `CLM_WORKER_ID` (if pre-registered),
  `CACHE_DB_PATH` (if set)
- Converter-specific: `PLANTUML_JAR`, `DRAWIO_EXECUTABLE`

**Consequence:** Setting `LANGSMITH_TRACING=false` in the shell that runs
`clm build` already propagates to Direct workers today. No code change needed
for that path.

### Docker workers (`src/clm/infrastructure/worker_executor.py:209-223`)

Containers receive an **explicit closed allowlist**:

- `WORKER_TYPE`, `CLM_API_URL`, `CLM_HOST_WORKSPACE`, `LOG_LEVEL`,
  `PYTHONUNBUFFERED`, `CLM_WORKER_ID` (if pre-registered),
  `CLM_HOST_DATA_DIR` (if set)

The host environment is **not** inherited. Anything outside this list cannot
reach a Docker worker without a code change.

### Course spec (`src/clm/core/course_spec.py`, info topic
`src/clm/cli/info_topics/spec-files.md`)

XML with structured attributes on course/section/topic. No `<environment>`-like
element exists. Course attributes today: `<name>`, `<prog-lang>`,
`<project-slug>`, `<github>`, `<sections>`, `<dir-groups>`,
`<output-targets>`, etc. Topic attributes: `id`, `html`, `evaluate`,
`skip-errors`, `http-replay`, `author`, `prog-lang`, `module`.

### Job payload (`src/clm/core/notebook_classes.py:15-58`)

`NotebookPayload` is an extensible JSON dict carrying per-job parameters
(`input_file`, `kind`, `prog_lang`, `language`, `format`, `skip_evaluation`,
`skip_errors`, `http_replay_mode`, `http_replay_cassette_name`,
`template_dir`, `source_topic_dir`, `img_path_prefix`, `author`,
`organization`).

### LangSmith / LangChain integration today

Searched: **no `LANGSMITH_*` or `LANGCHAIN_*` references in the CLM codebase.**
The only observability env vars touched are `LANGFUSE_HOST`,
`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` in `src/clm/llm/client.py:1-90`,
where Langfuse optionally wraps the OpenAI async client. The LangSmith vars
come from the **course side** (slide sources that import `langsmith` or
`langchain`), not from CLM itself.

---

## Key constraint that shaped the design space

`langsmith` (and many other env-driven libraries) reads its configuration **at
import time**. Setting `LANGSMITH_TRACING=false` after a worker process has
already started and imported langsmith is too late.

**Implication:** the env var must be set **before the worker process starts**.
Per-job in-process injection inside an already-running worker won't help for
this class of consumer. The feature has to operate at *worker spawn* time, not
at *job dispatch* time.

This also means: if env values vary per course but a worker pool is shared
across courses in one build, either workers must be restarted between courses
or pooled-by-env-signature. CLM today builds one course per `clm build`
invocation, so this is mostly moot — but it's the reason "just put it in the
job payload" doesn't work.

---

## Design alternatives considered

### A. CLI pass-through (smallest)

```
clm build --env LANGSMITH_TRACING=false --env FOO=bar
```

Merged into both executors at spawn time. Roughly ~30 LOC.

- **Pro:** zero spec changes, trivial to ship.
- **Con:** not version-controlled with the course; users have to remember and
  document the right flags. Doesn't survive CI without per-pipeline plumbing.

### B. Per-course `<environment>` in spec (cleanest)

```xml
<course>
  <name>...</name>
  <environment>
    <var name="LANGSMITH_TRACING" value="false"/>
  </environment>
</course>
```

Parsed in `course_spec.py`, carried on `Course`, merged at worker spawn.

- **Pro:** version-controlled, travels with the course, declarative.
- **Con:** worker-pool lifecycle question (above); spec schema change requires
  parser, dataclass, info-topic, and tests updates (the CLAUDE.md "Info Topics
  Maintenance Rule" applies to `src/clm/cli/info_topics/spec-files.md`).
- **Subtle:** the precedence rules vs. CLI flags and inherited env need to be
  pinned down.

### C. Sidecar `.env` file

`course.env` (or `.env`) next to the spec XML, dotenv-format, auto-loaded at
build time and merged into worker env.

- **Pro:** familiar 12-factor pattern, no XML schema work.
- **Con:** splits course config across two files; mismatches CLM's
  "spec XML is the source of truth" idiom.
- **Note:** this is essentially what the course side does today via
  `load_dotenv()` in slide sources — except the CLM-side variant would set env
  **before worker startup**, which is what's needed for import-time consumers
  like langsmith.

### D. Global worker-env config

`~/.clm/worker.env`, or a `[tool.clm.worker-env]` block in `pyproject.toml`,
applied to every worker on the machine.

- **Pro:** good for personal defaults (developer's LangSmith account vars).
- **Con:** not per-course; opaque to other contributors; bad fit when intent
  is "this course needs this env."

### E. Hybrid A + B (the recommended starting point if revived)

Ship CLI flag and spec block together. Spec defines durable course-bound
defaults; CLI is the ad-hoc override. Estimated ~150 LOC total.

Precedence (proposed): **CLI > spec > inherited from parent shell**.

Docker executor would switch from a closed allowlist to "base allowlist ∪
explicit additions from spec/CLI."

---

## Open questions to resolve before building

1. **Key allowlist vs. wide-open passthrough.** Likely refuse CLM-internal
   names (`WORKER_*`, `CLM_*`, `DB_PATH`, `WORKSPACE_PATH`, `LOG_LEVEL`,
   `PYTHONUNBUFFERED`) to prevent spec authors accidentally breaking worker
   wiring; otherwise open. Course specs are user-authored and trusted, so a
   blanket restriction isn't warranted.

2. **Worker pool lifecycle across courses.** Today `clm build` is typically
   one course per invocation, so workers spawn fresh with that course's env.
   Verify this empirically before assuming it (see `worker_executor.py` —
   Direct path spawns per build run; Docker may reuse pre-warmed containers,
   need to check).

3. **Multi-target builds.** If a single build covers multiple output targets
   that nominally share env, no issue. If a future feature ever builds across
   *courses* in one invocation, env merging becomes ambiguous.

4. **Logging / observability.** Env vars containing secrets must not be
   echoed to logs. Currently CLM logs the worker spawn command; that would
   need redaction.

5. **Docs touch-list.**
   - `src/clm/cli/info_topics/spec-files.md` (if option B/E)
   - `src/clm/cli/info_topics/commands.md` (if option A/E)
   - `docs/user-guide/configuration.md` (env-var section)
   - CHANGELOG entry

---

## Extension points (file:line)

If/when this is built, these are the touchpoints identified during the
investigation:

- **Docker spawn merge point:** `src/clm/infrastructure/worker_executor.py:209`
  (`DockerWorkerExecutor.start_worker`, `environment = {...}` dict — extend
  here)
- **Direct spawn merge point:** `src/clm/infrastructure/worker_executor.py:498`
  (`DirectWorkerExecutor.start_worker`, `env = os.environ.copy(); env.update(...)`
  — extend here)
- **Course dataclass:** add `environment: dict[str, str]` field in
  `src/clm/core/course.py`
- **Spec parser:** extend course-element parsing in
  `src/clm/core/course_spec.py` to populate the new field
- **Info topic:** update `src/clm/cli/info_topics/spec-files.md` (required by
  the CRITICAL maintenance rule in CLAUDE.md)
- **CLI flag:** add `--env KEY=VAL` (repeatable) to the `clm build` command
  group; validate format and merge precedence

---

## Cross-references

- Memory: `project_issue_129_vcr_force_reset.md` — the bug that triggered
  this investigation; durable fix (scoped `force_reset` monkey-patch) is
  independent of this feature.
- Reproducer:
  `~/Programming/Python/Tests/clm-bug-repros/issue-129-vcrpy-force-reset-race/`
- Related session-IDE artifact: `docs/claude/issue-129-vcrpy-force-reset-investigation.md`
  (in the `wondrous-snacking-teacup` worktree at the time of writing).

---

**Last updated:** 2026-05-25
