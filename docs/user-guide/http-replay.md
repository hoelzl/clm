# HTTP Replay for Notebook Execution

Some teaching notebooks call live HTTP services (e.g. a slide that hits
`https://restcountries.com/` with `requests.get`). Left unguarded, those
cells fail whenever the upstream service is down and produce drifting
outputs even when it is up. CLM's HTTP replay feature lets authors record
a cassette of the HTTP traffic once and then replay it deterministically
on every subsequent build — offline-stable, CI-safe, and invisible to
students.

## Installation

Install CLM with the `[replay]` extra:

```bash
pip install "coding-academy-lecture-manager[replay]"
```

This pulls in `mitmproxy` (the default transport, see below) and `vcrpy`. The
extra is also included in `[all]`. Topics that do not opt in pay zero runtime
cost — no imports, no proxy, no cassette lookup. The extra requires **Python
3.12+** (mitmproxy's floor).

## Transports

The replay engine records and replays a topic's HTTP traffic through one of two
interchangeable **transports**:

- **`mitmproxy` (default).** An out-of-process proxy (`mitmdump`) that all
  notebook traffic is routed through. It matches **repeated and concurrent
  identical requests** correctly — a LangChain chain invoked many times with the
  same request body, or a `RunnableParallel` fan-out — which the in-process
  transport could not. The proxy is started only when a build actually contains
  an `http-replay` topic, so a replay-free build never spawns it.
- **`vcrpy`.** The original in-process transport. It patches the HTTP libraries
  inside the kernel and serves each recorded interaction at most once
  (consume-once), so a deck that replays the same request multiple times cannot
  be strict-replayed under it.

Select the transport with the `CLM_HTTP_REPLAY_TRANSPORT` environment variable
(`mitmproxy` or `vcrpy`); it defaults to `mitmproxy`.

```bash
CLM_HTTP_REPLAY_TRANSPORT=vcrpy clm build course.xml   # opt back into the old path
```

> **Cassettes are not byte-compatible between the two transports.** The on-disk
> format is the same vcrpy YAML layout (the mitmproxy addon serializes to it),
> but the recorded bytes differ enough that a cassette recorded under one
> transport will fail strict `replay` under the other. A course upgrading from a
> pre-1.10 CLM must **re-record its cassettes under mitmproxy** once
> (`--http-replay=refresh`, review the diff, commit) before CI's strict `replay`
> passes. During the transition you can keep building against existing cassettes
> by pinning `CLM_HTTP_REPLAY_TRANSPORT=vcrpy`.

### Client-library coverage (mitmproxy transport)

Under the mitmproxy transport, one shared proxy serves the whole build, so each
notebook kernel tags its outgoing requests with the destination cassette
(an `X-CLM-Cassette` header the proxy strips before recording or forwarding).
The tagging is done by patching the HTTP client libraries inside the kernel.
Covered:

- **httpx** (`Client`/`AsyncClient`) — the stack the OpenAI/Anthropic/LangChain
  SDKs use;
- **requests** (`Session.send`, which the module-level `requests.get`/`post`
  helpers funnel through);
- **aiohttp** (`ClientSession`).

Traffic from any *other* HTTP stack — `urllib.request`, raw
`urllib3`/`http.client`, or a subprocess that honors `HTTP(S)_PROXY` — still
flows through the proxy (the proxy env vars and CA bundle are set
process-wide), but arrives **untagged**: it is matched and recorded against a
per-build *catch-all* cassette in the build scratch directory, **not** the
topic's canonical cassette, so its recordings are never committed and strict
`replay` will miss. CLM logs a warning in the build log
(`CLM-HTTP-REPLAY-UNTAGGED: …`, once per build, naming the first offending
request) when this happens. If you hit it, either switch the deck to a covered
client library or pin the build to the `vcrpy` transport
(`CLM_HTTP_REPLAY_TRANSPORT=vcrpy`), which patches at the `http.client` level
and covers `urllib` too.

## Opting a topic in

Mark the topic in your course spec:

```xml
<topic file="slides_010v_requests_get.py" http-replay="yes"/>
```

Default is `"no"`. The attribute accepts the usual truthy/falsy values
(`yes`/`no`, `true`/`false`, `1`/`0`).

### Opting in a whole section

`http-replay` is also accepted on `<section>` and acts as the default
for every child `<topic>`. Topics may still override it with their own
`http-replay="yes"`/`"no"` — same precedence rule as `module`. Useful
when a whole week of material talks to an LLM API:

```xml
<section http-replay="yes">
    <name><de>Woche 03: LLM-APIs</de><en>Week 03: LLM APIs</en></name>
    <topics>
        <topic>llm_apis</topic>           <!-- inherits yes -->
        <topic>openai_library_azav</topic> <!-- inherits yes -->
        <topic http-replay="no">offline_intro</topic> <!-- explicit opt-out -->
    </topics>
</section>
```

## First run — recording a cassette

On a local build, the default mode is `new-episodes`:

```bash
clm build course.xml
```

- If no cassette exists for the topic, CLM records one during execution
  and writes it to disk.
- If a cassette already exists, CLM replays from it and appends any new
  (previously-unrecorded) requests to the same file.

After the build, commit the cassette alongside the slide source:

```bash
git add slides/module_550_ml_azav/topic_017_requests_azav/slides_010v_requests_get.http-cassette.yaml
```

The cassette is now part of the course and travels with the notebook on
future builds.

## Cassette location

Two layouts are supported per topic:

- **Default** — cassette sits next to the slide source:
  ```
  topic_017_requests_azav/
  ├── slides_010v_requests_get.py
  └── slides_010v_requests_get.http-cassette.yaml
  ```
- **Foldered** — cassettes collected in a `cassettes/` subdirectory:
  ```
  topic_017_requests_azav/
  ├── cassettes/
  │   └── slides_010v_requests_get.http-cassette.yaml
  └── slides_010v_requests_get.py
  ```

To switch layouts, `mkdir cassettes` at the topic level once — CLM prefers
`cassettes/` when that directory exists and falls back to the sibling location
otherwise. The historical name `_cassettes/` (leading underscore) is still
recognised as a read fallback after `cassettes/`; `clm slides tidy` consolidates
it into `cassettes/`. The filename is always `<stem>.http-cassette.yaml`.

> **Bulk reorg.** `clm slides tidy <topic|section|course>` moves cassettes into
> `cassettes/` (and voiceover companions into `voiceover/`) in one pass —
> `--layout sibling` flattens them back. See `clm info commands`.

Cassettes are never copied to public or speaker output. They travel with
the notebook into worker payloads and Docker source mounts so execution
can find them, but the student-facing build tree does not contain them.

### Per-worker staging files

While a build is running, each worker writes its recordings to a
*staging* file next to the canonical cassette
(`<stem>.http-cassette.yaml.staging-<unique>`). The kernel saves to
this file after every recorded interaction, so a worker that is killed
mid-execution (for example by the build-level wait-for-completion
timeout) leaves its partial recordings on disk. The next build merges
every staging file in the directory into the canonical cassette under a
file lock and then deletes them.

Staging files are normally invisible — the merge step runs in the
build's `finally` block on success and on failure. If you see lingering
`*.staging-*` files in a course repo after a build, the worker was
killed *before* the merge could acquire the lock; running any subsequent
build for that topic will pick them up. You can also delete them by
hand if you do not want their contents.

Concurrent builds of the same notebook in different languages (German
and English on the same topic) write to distinct staging files and are
merged together — neither overwrites the other.

## Record modes

The record mode is chosen per build. Set it via the `--http-replay` CLI
flag or the `CLM_HTTP_REPLAY_MODE` environment variable:

| Mode            | Cassette present   | Cassette missing   | Unknown request        |
|-----------------|--------------------|--------------------|------------------------|
| `replay`        | replay             | **hard error**     | **hard error**         |
| `once`          | replay             | record new         | **hard error**         |
| `new-episodes`  | replay             | record new         | record (append)        |
| `refresh`       | overwrite          | record new         | record                 |
| `disabled`      | ignored (bypass)   | ignored            | passthrough to network |

### Default selection

- **CI** (`CI=true` / `CI=1` / `CI=yes` in the environment): `replay`.
  Strict — a missing or incomplete cassette fails the build loudly.
- **Local / interactive**: `new-episodes`. Permissive — the first build
  records, subsequent builds replay, and any newly-added requests on an
  edited notebook are appended to the existing cassette instead of
  failing the build. Use `--http-replay=once` if you want a local build
  to fail loudly on unrecorded requests instead.

Precedence (high to low): explicit `--http-replay=` flag on the build
command → `CLM_HTTP_REPLAY_MODE` env var → CI-aware default.

## Extending a cassette with new requests

When a notebook has been edited and now issues additional requests that
the existing cassette does not cover, the strict `once` default fails
with `CannotOverwriteExistingCassetteException`. Run with
`--http-replay=new-episodes` to replay every request that *is* in the
cassette and record only the genuinely new ones into the same file:

```bash
clm build course.xml --http-replay=new-episodes
```

The merged cassette ends up containing the original interactions plus
the newly recorded ones. Review the diff before committing — this is the
mode that lets new traffic into the cassette without a full re-record.

## Refreshing a cassette

When the upstream service changes shape and you want to re-record from
scratch:

```bash
clm build course.xml --http-replay=refresh
```

This overwrites existing cassettes with fresh traffic. Review the diff
before committing — `refresh` and `new-episodes` are the modes that talk
to the network for previously-recorded interactions.

For a one-off debug run without any replay at all:

```bash
clm build course.xml --http-replay=disabled
```

## Cache behavior

HTTP-replay topics participate in the executed-notebook cache: the cache
key folds in the cassette bytes, so refreshing a cassette invalidates the
cache entry for that topic only. Topics without `http-replay="yes"`
share the same cache behavior as before.

## CI

In CI the default is strict `replay` mode. Two failure classes are
surfaced loudly:

- A topic marked `http-replay="yes"` with no committed cassette → build
  fails. Forces authors to commit the recording when they opt in.
- A cell issues a request that is not in the cassette → build fails.
  Keeps cassettes honest; drift is caught at the first CI run.

No override is needed for typical CI usage — a vanilla `clm build` picks
up the strict default from the `CI` environment variable.

## Redaction

`vcrpy` filters are applied at record time before the cassette is
written:

- Request headers: `authorization`, `cookie`, `x-api-key`, `set-cookie`
- POST body parameters: `password`, `token`, `api_key`
- Query parameters: `api_key`, `token`

These cover the typical teaching-material surface. Review the cassette
YAML by eye before committing if your topic hits an API with a novel
auth scheme.

## Telemetry passthrough

Some endpoints — notably LangSmith's `api.smith.langchain.com` upload
target — embed per-build timestamps and UUIDs in their request bodies.
That defeats the body matcher and causes the cassette to grow by an
entry per build even when the slide source is unchanged. CLM ships a
default ignore-hosts list that lets such requests pass through to the
real network instead of being recorded. Override the list via
`CLM_HTTP_REPLAY_IGNORE_HOSTS` (comma-separated hostnames); set to an
empty string to disable the default. See `clm info commands` for the
full env-var list.

## When to prefer `skip-errors` instead

`http-replay` is the structural fix for HTTP flakiness and should be
preferred for any topic that consistently makes network calls.
`skip-errors="yes"` is a cheaper escape hatch for topics that occasionally
fail for non-HTTP reasons (kernel hiccups, external tool timeouts) or for
short-lived gaps before a cassette is recorded. The two attributes are
not substitutes — a topic that has a cassette should rely on replay and
**not** also set `skip-errors`, so real regressions still surface in CI.

## Troubleshooting

- **`Cannot find cassette file ... and mode is replay`** — the topic opts
  in but no cassette has been recorded. Run locally to record the
  cassette (the default `new-episodes` mode replays existing recordings
  and appends new ones), commit it, and re-run CI.
- **`Can't overwrite existing cassette in 'none' mode`** — a cell issued
  a request that is not recorded. If the notebook now makes additional
  requests but the existing recordings are still valid, run
  `--http-replay=new-episodes` to append the new traffic. If the request
  shape drifted (same URL, different parameters), use
  `--http-replay=refresh` to re-record from scratch and review the diff.
- **Build hangs waiting for network** — the topic is not opted in. Add
  `http-replay="yes"` to the topic element.
- **Need to run offline without replay** — set
  `--http-replay=disabled`. Cells hit the network directly; this is for
  debugging only.

See also [Spec File Reference](spec-file-reference.md) and `clm info
spec-files` for the full list of topic attributes.
