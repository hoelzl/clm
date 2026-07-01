# Proposal: unify CLI options, environment variables, and config files

**Status:** Proposal (for review) · **Date:** 2026-07-01 · **Author:** agent-assisted

## TL;DR

CLM resolves runtime settings through three channels — Click CLI options, the
pydantic `ClmConfig` system (env vars + TOML config files), and ad-hoc
`os.environ` reads — but **they are not connected**. The CLI never participates
in `ClmConfig`'s precedence chain, and roughly **half of `ClmConfig`'s surface
does nothing at all**: the config-file / `CLM_*__*` channels for `logging`,
`jupyter`, `external_tools`, and `workers` are dead because the real consumers
read raw environment variables directly. As a result a user who sets, say,
`[external_tools] plantuml_jar` or `[logging] log_level` in `clm.toml` gets no
effect — yet `clm config show` displays those values as if they worked.

This proposal establishes **one precedence order everywhere** —
`CLI flag > environment variable > config file > built-in default` — implemented
by a small shared resolver used at the host process, plus a rule for propagating
resolved values across the worker process boundary. The decision (confirmed) is
to **wire the inert sections up** so the config file becomes authoritative,
rather than delete them.

It is scoped as a sequence of small, independently-shippable PRs. The two DB-path
fixes that motivated it have already landed (#498, #499).

## 1. Background: how the three channels relate today

`ClmConfig` (`src/clm/infrastructure/config.py`) is a `pydantic-settings`
`BaseSettings` with `env_prefix="CLM_"`, `env_nested_delimiter="__"`, and TOML
config-file sources (project `.clm/config.toml` / `clm.toml` > user > system).
Its `settings_customise_sources` builds this precedence chain:

```
env vars  >  legacy env vars  >  TOML(project > user > system)  >  init/defaults
```

**Click is absent from that chain.** There is no `auto_envvar_prefix` and no
pydantic-settings `CliSettingsSource`. CLI option values never flow into
`ClmConfig`. The only genuine bridges are:

- `load_worker_config()` (`workers/config_loader.py`) — deep-copies
  `get_config().worker_management` and overlays a CLI dict on top. This is the
  one area that is already coherent, and it is the pattern to generalise.
- `clm export summary` / `context` — a hand-rolled `model or llm_config.model`.

Everything else that has both a CLI/env channel *and* a config field resolves the
two **independently**, so they can silently disagree.

## 2. The problem, concretely

### 2.1 ~Half of `ClmConfig` is inert (dead config-file channel)

For these sections the TOML / `CLM_*__*` channel does nothing — the real
consumer reads a raw env var directly, usually at **module import time** inside a
worker:

| Config field | What actually reads the value | Config-file effect |
|---|---|---|
| `external_tools.plantuml_jar` | `workers/plantuml/plantuml_converter.py:20` reads `PLANTUML_JAR` | **none** |
| `external_tools.drawio_executable` | `workers/drawio/drawio_converter.py` reads `DRAWIO_EXECUTABLE` | **none** |
| `logging.log_level` | worker mains read raw `LOG_LEVEL`; `--log-level` hardcodes `INFO` and never consults `get_config()` | **none** |
| `jupyter.jinja_line_statement_prefix` / `jinja_templates_path` / `log_cell_processing` | `workers/notebook/notebook_processor.py:104-107` read raw `JINJA_*` / `LOG_CELL_PROCESSING` | **none** |
| `workers.worker_type` / `worker_id` / `use_sqlite_queue` | `WORKER_ID` / `CLM_WORKER_ID` / `HOSTNAME`; `use_sqlite_queue` has no consumer | **none** |
| `paths.*` | *(removed in #499)* | — |

Because `clm config show` reads these same fields, it **advertises settings that
have no effect** — a trust bug in its own right.

### 2.2 Several settings have two live env spellings

| Setting | Spelling A | Spelling B | Risk |
|---|---|---|---|
| jobs DB path | `CLM_JOBS_DB_PATH` (global option) | `CLM_DB_PATH` (status/monitor) | *fixed in #499* (A wins, B fallback) |
| max workers | `CLM_MAX_WORKERS` (`workers/pool_size_cap.py`) | `CLM_WORKER_MANAGEMENT__MAX_WORKERS_CAP` (config field) | two spellings, precedence coherent but confusing |
| E2E progress knobs | `CLM_E2E_PROGRESS_INTERVAL` etc. (`progress_tracker.py`, default 5) | `CLM_LOGGING__TESTING__E2E_*` (config, default 10) | **different defaults**, config channel dead |
| LLM API key | `CLM_LLM__API_KEY` | `OPENROUTER_API_KEY` / `OPENAI_API_KEY` | reconciled by precedence; low impact |

### 2.3 Genuinely-live config (leave as-is)

`retention`, `worker_management`, `llm`, `git`, and `recordings` are actually
consumed via `get_config()` and work correctly. They are the model for where we
want every section to be.

## 3. The key architectural constraint: the worker process boundary

The reason so many settings are read from raw env vars is **not** an oversight —
it is because they are consumed inside **worker subprocesses (Direct mode) or
containers (Docker mode)**, and env vars are the reliable transport across that
boundary. The host already does this deliberately in
`infrastructure/workers/worker_executor.py`:

```python
env.update({
    "WORKER_TYPE": worker_type,
    "LOG_LEVEL": self.log_level,
    ...
})
...
for var in ["PLANTUML_JAR", "DRAWIO_EXECUTABLE"]:
    if var in os.environ:          # <-- forwards the HOST'S env var only
        env[var] = os.environ[var]
```

So the fix for worker-side settings is **not** to make the worker call
`get_config()` (a container has no access to the host's `clm.toml`). It is:

> **The host resolves the effective value (CLI > env > config file > default) and
> injects it into the worker environment as the single canonical env var. The
> worker keeps reading that one env var — unchanged.**

Two consequences that make this cheap:

- Workers read these at **import time** (`LOG_LEVEL = os.environ.get(...)`). That
  is fine: the host sets the worker's environment *before* spawning it, so the
  injected value is present when the worker module imports. **No worker refactor
  is required** — only a change to *what value the host injects*.
- The host-side injection point already exists (`worker_executor`); today it
  forwards the host's env var instead of the resolved config value. Changing it
  to inject `get_config().external_tools.plantuml_jar` (via the resolver) is a
  localized edit.

Settings consumed **only on the host** (e.g. `logging.log_level` for the main
process, retention, git) skip the injection step and just use the resolver
directly.

## 4. Target design

### 4.1 One precedence order, one resolver

Every multi-channel setting resolves as:

```
CLI flag  >  environment variable  >  config file  >  built-in default
```

Implemented with a single tiny helper (host-side), generalising what
`export summary` and `load_worker_config` already do by hand:

```python
def resolve_setting(cli_value, *, config_value, default):
    """First non-None of CLI > (env-or-file via ClmConfig) > default."""
    if cli_value is not None:
        return cli_value
    if config_value is not None and config_value != "":
        return config_value      # ClmConfig already folded env > file
    return default
```

Note `ClmConfig` **already** implements `env > file > default` internally, so
`config_value = get_config().<section>.<field>` collapses the middle two tiers
correctly. The resolver only has to layer the CLI flag on top. Click options for
these settings change their `default` to `None` so "unset" is distinguishable
from "explicitly set to the default".

### 4.2 Rule for worker-side settings

For any setting a worker consumes: the host computes `effective =
resolve_setting(...)` and injects it into the worker env at the existing
`worker_executor` injection site (both Direct and Docker paths). The worker's
import-time `os.environ.get(...)` read is unchanged. One canonical env var per
setting; legacy spellings become deprecated fallbacks (below).

### 4.3 Canonical env spellings — hard cut, no deprecation window

**Decision:** the user base is small enough that we do a **hard cut** — the old
spelling is removed outright (no deprecation-warning grace release), and the
removal is announced by email + documented in `clm info migration` (a
removed → replacement table). Pick one canonical spelling per setting:

| Setting | Canonical | Removed (hard cut) |
|---|---|---|
| jobs DB path | `CLM_JOBS_DB_PATH` | `CLM_DB_PATH` |
| max workers | `CLM_WORKER_MANAGEMENT__MAX_WORKERS_CAP` | `CLM_MAX_WORKERS` |
| E2E knobs | `CLM_LOGGING__TESTING__E2E_*` (align defaults) | `CLM_E2E_*` |
| plantuml / drawio | `PLANTUML_JAR` / `DRAWIO_EXECUTABLE` (no `CLM_` prefix — established) | — |

Every hard cut adds a row to the `clm info migration` table so downstream repos
have a single reference.

### 4.4 `clm config show` becomes honest

Display the **effective resolved value and its source** for every setting
(`flag` / `env:VAR` / `file:<path>` / `default`), the way the DB-path section now
does after #499. A value that came from the config file must actually be in
effect — no more advertising dead settings.

## 5. Per-setting plan

| Setting | Host or worker | Change |
|---|---|---|
| cache/jobs/telemetry DB paths | host | **done** (#498, #499) |
| `external_tools.plantuml_jar` / `drawio_executable` | worker | host resolves via `get_config().external_tools.*` and injects (replace the `if var in os.environ` forward) |
| `logging.log_level` | host + worker | `--log-level` default → `None`; resolve `flag > CLM_LOGGING__LOG_LEVEL/file > "INFO"`; inject as `LOG_LEVEL` |
| `jupyter.jinja_*` / `log_cell_processing` | worker | host resolves via `get_config().jupyter.*`, injects `JINJA_*` / `LOG_CELL_PROCESSING` |
| `workers.worker_type` / `worker_id` | worker | fold into the existing worker-config path; drop `use_sqlite_queue` (no consumer) or wire it |
| `max_workers` cap | host | collapse to one env spelling (§4.3) |
| E2E test knobs | host | align defaults + one spelling (§4.3) |
| LLM api key | host | keep precedence; document both spellings |

## 6. Backwards compatibility

- **No breaking change for env-var users.** Every currently-working env var keeps
  working; deprecated spellings warn but still function for one release.
- **Config files gain power, never lose it.** A `[paths]` block left over from
  before #499 still loads (ignored). Sections that were inert become effective —
  this can *change behaviour* for anyone who had set a value in `clm.toml`
  expecting it to work (it previously did nothing). Call this out in the
  changelog under `changed`.
- **`clm config show` output changes** (now source-annotated). Update the
  matching tests + `commands` info topic.

## 7. Phasing (independently-shippable PRs)

0. **DB paths** — *done* (#498 add `CLM_*_DB_PATH`; #499 fix cleanup + status/monitor + remove `[paths]`).
1. **Resolver + `config show --json` + first hard cut** — *done* (#502): add `resolve_setting` (the shared seam), add `clm config show --json` (machine-readable effective config), remove `USE_SQLITE_QUEUE` / `workers.use_sqlite_queue` outright, and seed the `clm info migration` removed → replacement table.
2. **external_tools** — host-resolve + inject `PLANTUML_JAR` / `DRAWIO_EXECUTABLE`; config file now works. Add a test that a `clm.toml` value reaches the converter.
3. **logging.log_level** — resolver + inject; `--log-level` default `None`.
4. **jupyter.*** — host-resolve + inject.
5. **env-spelling unification** — deprecate `CLM_DB_PATH` (already fallback), `CLM_MAX_WORKERS`, `CLM_E2E_*`; align E2E defaults.
6. **cleanup + docs** — remove any now-unused `LegacyEnvSettingsSource` mirrors, refresh `configuration.md` + the `commands` info topic, changelog `changed` note.

Each phase carries its own test proving the full chain (`flag > env > file >
default`) reaches the actual behaviour, not just the `ClmConfig` object.

## 8. Alternatives considered

- **pydantic-settings `CliSettingsSource` / `auto_envvar_prefix`** — make Click
  values the top source *inside* `ClmConfig`. Rejected: Click's per-command
  option model doesn't map onto one global settings object; every command would
  have to be rewritten to read `ClmConfig`, and positional/optional args don't
  fit. High churn, high regression risk.
- **Delete the inert sections instead of wiring them** — smaller, but loses the
  config-file channel for real settings and keeps raw-env-only ergonomics.
  Rejected in favour of wire-up (the goal is a config file that actually works).

## 9. Decisions (resolved)

1. **`use_sqlite_queue`** — **removed** (Phase 1 / #502). It was a leftover from
   the multi-queue era; SQLite is the only job queue and nothing read the flag.
2. **`clm config show --json`** — **added** (Phase 1 / #502).
3. **Deprecation window** — **none; hard cuts** (§4.3). Small user base; removals
   are announced by email and captured in the `clm info migration` table.
