# HTTP Replay and `skip_errors` for Notebook Execution

## Status: DRAFT

**Last Updated**: 2026-04-23
**Related problem**: Notebooks that call live HTTP services (e.g.
`slides_010v_requests_get.py` hitting `https://restcountries.com/`) break
HTML builds whenever the upstream service is down, and produce drifting
outputs even when it is up. We want deterministic, offline-stable builds
without cluttering the slide source with error handling or mock scaffolding.

## Feature overview

Two mechanisms ship together:

- **Phase 1 — `skip_errors`**: a topic-level escape hatch that renders
  HTML even when cells raise. Cheap, generic, useful before a cassette
  exists or for non-HTTP flakiness (kernel lib hiccups, external tools).
- **Phase 2 — HTTP replay**: the main feature. Cassette-backed,
  offline-by-default, `vcrpy`-powered. Eliminates the root cause for
  HTTP-flaky notebooks instead of papering over it.

`skip_errors` is not a substitute for replay — a topic that successfully
records a cassette should have `http_replay="yes"` and NOT
`skip_errors="yes"`, so that legitimate regressions still surface.

## Goals

1. HTML builds are deterministic and succeed offline by default.
2. Notebooks that do not use HTTP are unaffected: no injected code, no new
   runtime dependency at notebook-evaluation time, no wall-clock cost.
3. Slide source files (`.py` in percent format) remain clean teaching
   material — no imports, decorators, or patches added for replay's sake.
4. Cassettes are versioned for course authors but invisible to students
   (not copied into public output directories).
5. Refreshing a cassette is a single explicit command. The default build
   path **never** hits the network for cells covered by a cassette.
6. Sensitive headers and request bodies are stripped from cassettes
   before they are written.

## Non-goals

- Freezing non-HTTP non-determinism (timestamps, `id()`, UUIDs, hash
  ordering). That was considered as Phase 3 in the brainstorm and dropped
  — the mechanisms are different and the authoring cost is not worth it.
- A generic network mock for libraries other than `requests`, `httpx`,
  `urllib3`, `aiohttp`. `vcrpy` covers all four out of the box.
- Cassette editing UX. Cassettes are plain YAML; authors hand-edit or
  refresh-and-review via git diff.
- Sandboxing — cassettes are trusted inputs produced by the author. If
  `refresh` mode is run against a compromised service, that is out of
  scope for this feature.

## Design

### Opt-in, topic-level

A new boolean attribute on the topic element in the course spec XML:

```xml
<topic file="slides_010v_requests_get.py" http_replay="yes"/>
```

Default is `no`. When `yes`, the notebook processor:

1. Ensures the topic's cassette file is available to the kernel's working
   directory at execution time.
2. Injects a hidden bootstrap cell at position 0 that activates `vcrpy`
   against that cassette.
3. Strips the bootstrap cell from the executed notebook before HTML
   rendering.

Notebooks without this attribute go through the existing code path
unchanged — no import of `vcrpy`, no cell injection, zero runtime cost.

### Cassette storage and naming

Two layouts are supported; per-topic opt-in.

**Default layout** — cassette next to source file:

```
slides/module_550_ml_azav/topic_017_requests_azav/
├── slides_010v_requests_get.py
└── slides_010v_requests_get.http-cassette.yaml
```

**Opt-out layout** — cassettes collected in a sibling `_cassettes/`
directory at the topic level:

```
slides/module_550_ml_azav/topic_017_requests_azav/
├── _cassettes/
│   └── slides_010v_requests_get.http-cassette.yaml
└── slides_010v_requests_get.py
```

Resolution rule at build time: if `<topic_dir>/_cassettes/<stem>.http-cassette.yaml`
exists, use it; otherwise fall back to `<topic_dir>/<stem>.http-cassette.yaml`.
When recording (`once` with no existing cassette, or `refresh`), prefer
`_cassettes/` if that directory already exists on disk at the topic level,
otherwise write next to the source. This lets authors `mkdir _cassettes`
once per topic to switch layouts without touching spec XML.

**Naming**: `<stem>.http-cassette.yaml`. Both layouts use the same
filename convention.

**Course-level scanning**: cassettes remain visible to course scanning
(they travel with the notebook into worker payloads and Docker source
mounts). The `_cassettes/` directory name is added to `SKIP_DIRS_FOR_OUTPUT`
(not `SKIP_DIRS_FOR_COURSE`) so it is seen during scanning but not copied
to student output.

**Output-level filtering**: cassette files never land in public or speaker
output. Two predicates, both needed:

- Files named `*.http-cassette.yaml` — excluded via a new
  `SKIP_OUTPUT_FILE_PATTERNS` list checked in a new
  `is_ignored_file_for_output` function (sibling to
  `is_ignored_file_for_course` at `path_utils.py:157`).
- The entire `_cassettes/` directory — excluded via
  `SKIP_DIRS_FOR_OUTPUT` (extend the frozenset at `path_utils.py:67`).

**Course-level scanning**: cassettes remain visible to course scanning
so they travel with the notebook into worker payloads (packaged via
`other_files`) and Docker source mounts.

**Output-level filtering**: the `CopyFileOperation` path needs to skip
cassette files so they never land in public or speaker output. This is a
small addition: either extend `SKIP_FILE_SUFFIXES` with
`.http-cassette.yaml` (path_utils.py:71) or add a dedicated
`IGNORE_OUTPUT_NAME_REGEX` matched in `is_ignored_dir_for_output`'s
sibling file-level check. Concrete choice: add a new
`SKIP_OUTPUT_FILE_PATTERNS` regex list alongside `SKIP_FILE_SUFFIXES`,
so the public/output filter gains one extra predicate without enlarging
the global skip suffix list (which applies to course scanning too).

### Record modes

Environment variable `CLM_HTTP_REPLAY_MODE`, set by the notebook
processor per-build from CLI flags:

| Mode       | Cassette present | Cassette missing | Unknown request    |
|------------|------------------|------------------|--------------------|
| `replay`   | replay           | hard error       | hard error         |
| `once`     | replay           | record new       | hard error         |
| `refresh`  | overwrite        | record new       | record             |
| `disabled` | ignored          | ignored          | passthrough        |

Mapping to `vcrpy.record_mode`:

- `replay` → `none`
- `once` → `once`
- `refresh` → `all`
- `disabled` → bypass (do not inject bootstrap cell at all)

Default selection:

- In CI (`CI=true` in env): `replay`. Strict — a missing or incomplete
  cassette fails the build loudly, which is what we want.
- Local/interactive: `once`. Permissive — the first build against a new
  topic records a cassette. Subsequent builds replay.
- Override: `clm build --http-replay=refresh` (or
  `--http-replay=disabled` for debugging). Overrides both CI and local
  defaults.

### Bootstrap cell injection

The bootstrap cell is added to the notebook in memory **after** jupytext
parsing but **before** execution. It runs in the same kernel as user
code and activates `vcrpy` for the remainder of the notebook's lifetime.

Cell source (approximate):

```python
# CLM HTTP REPLAY BOOTSTRAP - DO NOT EDIT
import os as _clm_os
if _clm_os.environ.get("CLM_HTTP_REPLAY_ACTIVE") == "1":
    import vcr as _clm_vcr
    _clm_cassette = _clm_os.environ["CLM_HTTP_REPLAY_CASSETTE"]
    _clm_mode = _clm_os.environ["CLM_HTTP_REPLAY_MODE"]
    _clm_vcr_instance = _clm_vcr.VCR(
        record_mode=_clm_mode,
        filter_headers=["authorization", "cookie", "x-api-key", "set-cookie"],
        filter_post_data_parameters=["password", "token", "api_key"],
        filter_query_parameters=["api_key", "token"],
        decode_compressed_response=True,
    )
    _clm_ctx = _clm_vcr_instance.use_cassette(_clm_cassette)
    _clm_ctx.__enter__()
```

The cell is marked with:

- Jupyter tag `del` so any existing output-filtering code paths that
  honor `del` also strip it from outputs other than HTML.
- Jupyter metadata `clm_injected: http_replay` so we can locate and
  remove it in the post-execution pass with a precise check (not a
  string match on source).

Post-execution, a new pass walks the notebook and removes every cell
carrying `metadata.clm_injected == "http_replay"` before HTML
conversion and before any cache storage.

**Why a cell and not `PYTHONSTARTUP` or an IPython startup file?** Two
reasons: (1) the kernel's working directory already contains
`other_files` resolved against the payload, so the cassette path
resolves cleanly via relative path; (2) we only want the patch when the
topic opts in — per-topic kernel-level configuration is more fragile
than per-notebook cell injection.

### Cassette transport

**Direct mode** (`source_dir is None` at
`notebook_processor.py:954–961`): the cassette file must be added to
`NotebookPayload.other_files` alongside images and data fixtures. This
is the same plumbing used for `other_files` today — extending the
course-file collection to include cassette files when they exist is a
one-line change in whichever operation populates `other_files`.

**Docker mode with source mount** (`source_dir is not None` at
`notebook_processor.py:947–953`): the cassette is already on disk next
to the `.py` — no extra work.

In both cases the bootstrap cell reads
`CLM_HTTP_REPLAY_CASSETTE` as the absolute path inside the kernel's
working directory.

### Redaction

`vcrpy` filters applied at record time (see bootstrap cell source):

- Request headers: `authorization`, `cookie`, `x-api-key`, `set-cookie`
- POST body parameters: `password`, `token`, `api_key`
- Query parameters: `api_key`, `token`

These are sufficient for teaching materials against public APIs. If a
future cell uses something more exotic, we either add filters here or
expose a per-topic override. Deferred until a concrete case appears.

### Interaction with the execution cache

The existing `ExecutedNotebookCache` stores executed notebooks keyed by
source hash. Interaction rules:

- The cache key must factor in the cassette contents hash when
  `http_replay="yes"`. Otherwise: topic's `.py` unchanged but cassette
  refreshed → cache returns stale outputs.
- Simplest implementation: if the topic has a cassette, hash the
  cassette bytes alongside the source hash when computing the cache
  key.
- Refreshing a cassette invalidates the cache entry for that topic —
  desired behavior.

### CI enforcement

In CI the default mode is `replay` (strict). Effects:

- A topic with `http_replay="yes"` and no cassette → build fails.
  Forces authors to commit a cassette when they opt in.
- A cell that issues a request not in the cassette → build fails. Keeps
  cassettes honest over time; drift is caught at the first CI run.

## Scope and phasing

Phase 1 ships first as a cheap, independent escape hatch. Phase 2a
(replay proper) is split into three small commits, each independently
testable.

### Phase 1 — `skip_errors` topic attribute (~100 LoC)

- Add `skip_errors` attribute to topic in `course_spec.py` (mirrors
  `skip_html` at `course_spec.py:43,649`).
- Thread through `OutputSpec` / `NotebookPayload` as
  `skip_errors: bool = False`.
- In `_execute_notebook_with_files` at `notebook_processor.py:876`:
  when the flag is set, construct `TrackingExecutePreprocessor` with
  `allow_errors=True` and catch all remaining exceptions without
  raising.
- Post-execution cleanup: walk cells; for any cell whose outputs
  contain an `error`-type output or whose `execution_count is None`
  after the point of first failure, clear outputs. Emit a
  `ProcessingWarning` listing the affected cell indices so the author
  sees impact without reading tracebacks.
- Update `clm info spec-files` with the new attribute.
- Tests: spec parsing; notebook with a cell that raises builds HTML
  successfully when `skip_errors="yes"` and fails as before when not.

Phase 1 is independent of Phase 2 and can merge on its own. It remains
useful after Phase 2 lands as a generic escape hatch for non-HTTP
flakiness.

### 2a.1 — Plumbing and spec attribute (~120 LoC)

- Add `http_replay` attribute to topic in `course_spec.py` (mirroring
  `skip_html`).
- Thread through `OutputSpec` / `NotebookPayload` as
  `http_replay_mode: str | None`.
- Add `CLM_HTTP_REPLAY_MODE` handling in `build.py`: CLI flag
  `--http-replay=<replay|once|refresh|disabled>`, default selection
  logic (CI → `replay`, local → `once`).
- Add `clm.http_replay` optional dep as a new `[replay]` extra wrapping
  `vcrpy`.
- Update `clm info spec-files` with the new attribute.
- **No behavior change yet** — nothing injects the bootstrap cell.
  Tests: spec parsing, CLI flag parsing, extra install.

### 2a.2 — Bootstrap injection and execution (~150 LoC)

- New `_inject_http_replay_bootstrap(nb, cassette_path, mode)` in
  `notebook_processor.py`.
- New `_strip_injected_cells(nb)` post-execution pass.
- Hook both into `_create_using_nbconvert` around
  `notebook_processor.py:931`.
- Ensure cassette is available:
  - Direct mode: extend `other_files` population to include the topic's
    cassette.
  - Docker mode: no change, already present.
- Tests:
  - Unit test: bootstrap cell is injected iff `http_replay_mode` is
    truthy.
  - Unit test: injected cell is stripped from output regardless of
    execution success.
  - Integration test (marked `integration`): notebook with `requests`
    call, cassette recorded once, replay mode succeeds offline (network
    monkeypatched to fail).
  - Regression test: notebook without `http_replay` is byte-identical
    before/after the change.

### 2a.3 — Cache keying and output filtering (~60 LoC)

- `ExecutedNotebookCache` key includes cassette hash when present.
- `SKIP_OUTPUT_FILE_PATTERNS` (or equivalent) excludes
  `*.http-cassette.yaml` from output copying. Add to
  `is_ignored_dir_for_output`'s file-level sibling check (new function
  `is_ignored_file_for_output`).
- Tests:
  - Cassette refresh invalidates cache for that topic only.
  - Cassette file does not appear in any `public/` or `speaker/`
    output directory.
  - Cassette file *does* appear in worker payload `other_files` (direct
    mode) and is readable via `source_dir` (Docker mode).

### 2b — CI strict mode + docs (~30 LoC, after 2a lands)

- `CI=true` selects `replay` mode by default. One env read + test.
- `docs/user-guide/voiceover.md` sibling: new
  `docs/user-guide/http-replay.md` with author workflow (opt in, first
  run, refresh, committing cassettes).
- `CHANGELOG.md` entry.

## Out-of-scope / deferred

- Per-topic filter overrides for unusual auth schemes.
- Cassette editing or inspection command (`clm cassette ls`,
  `clm cassette show`).
- Replay for non-HTTP side effects (filesystem, subprocess, database).
- Automatic detection of `import requests` to opt-in without the
  `http_replay` attribute — explicitly rejected: authors should mark
  their network-using topics, and CI should flag network use in
  un-marked topics separately.

## Resolved decisions

1. **Cassette location.** Support both "next to source file" (default)
   and a per-topic `_cassettes/` subdirectory opt-out. Resolution prefers
   `_cassettes/` if that directory exists.
2. **Cache-key versioning.** Including the cassette hash in the cache
   key invalidates existing cached executions for HTTP-replay topics on
   first run. Accepted — cache rebuilds cheaply.
3. **Phase 1 inclusion.** `skip_errors` ships as a first, independent
   phase. It is cheap and useful before a cassette is recorded and for
   non-HTTP failure modes.

## Open questions

1. **vcrpy async support.** `vcrpy` supports `aiohttp` and `httpx`'s
   async client out of the box. Our `requests`-using teaching slides
   are all sync, so this is not blocking; noted for future slides that
   use async clients.

## Success criteria

- `slides_010v_requests_get.py` builds HTML successfully with the
  `restcountries.com` service down, byte-identical to the last
  successful build (after Phase 2a).
- A topic with `skip_errors="yes"` builds HTML successfully when one
  of its cells raises an exception, with error-output cells cleared
  (after Phase 1).
- All existing notebook tests pass unchanged (no regression in the
  opt-out path).
- A new integration test locks in the replay behavior end-to-end.
- `clm info spec-files` documents both `skip_errors` and `http_replay`.
- A new user-guide page documents the author workflow.
