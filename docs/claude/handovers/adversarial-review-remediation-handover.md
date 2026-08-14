# Adversarial Review Remediation — Handover

**Created**: 2026-07-24 | **Status**: Phases 0–2 DONE; Phase 3 in progress (items 1, 3, 5 DONE) | **Owner**: unassigned

**2026-08-14 update**: Phase 3 status audit against git history and issues — two
items shipped under the #656 ceremony arc without being recorded here: **item 3
(Y3 + D9, decision keying)** and **item 5 (Y4, decision freshness)** — see the
item bodies for evidence. The remaining items (2/Y1, 4/Y5, 6/Y6, 7/Y7, 8/Y8,
9/Y9) were re-verified against current code and are genuinely **not done**:
their code sites are unchanged since the 2026-07-24 review. Note Y2's strict
gate (item 1) closed the main *arming* path for Y1/Y6 (new diverged baselines
can no longer be banked via `record` without `--allow-diverged-companion`), but
the missing guards themselves are still latent for pre-existing or
escape-hatch baselines. Phase 3a's S4/S8 ride-alongs are now tracked as part of
issue #798, so Phase 3a leaves nothing untracked.

**2026-07-26 update (3)**: **release 1.23.0 shipped** (Phase 2's whole body of
work is now on PyPI), and **Phase 3 item 1 (Y2 + D8) is DONE** — the strict
`record` gate. Notes at the end of Phase 3. Two maintainer decisions were fixed
in the same session and are recorded in §2 as **D13** (#697 — sanitize
macro-emitted HTML *on the server*) and **D14** (#698 — render the preview in a
**subprocess**); both are now unblocked issues for the normal backlog, not phase
work. **S5 has been pulled forward** out of Phase 4 into its own **Phase 3a**
(see §4) — repo-supplied executable paths are the same finding class as the
Phase 0 cassette RCE, which is information the original risk×independence
ordering predates. Next: Phase 3a (S5, plus the adjacent S4/S8 if they ride
cheaply), then the rest of Phase 3 starting at Y1.

**2026-07-26 update**: **Phase 2 is DONE** — items 3 (`clm serve`, PR #695)
and 4 (Studio render, PR #696) landed, closing S6 and S7. Two follow-ups were
spun out and are *not* prerequisites for Phase 3: **#697** (wire up the
Studio's in-page tier-2 preview — the consumer was dead code around a raw
`innerHTML` sink and was removed) and **#698** (bound the preview's CPU; its
memory is bounded in-process now). Read the "Notes from items 3–4" at the end
of Phase 2 before the next phase — two of the four sub-findings turned out to
be about code that had never executed, which is a pattern worth expecting.
**Next up: Phase 3 (sync engine correctness), starting with D8.**

**2026-07-25 update**: one of Phase 7's prerequisites — "fix the known build
nondeterminism" — was investigated ahead of schedule and is now resolved; see
the correction in Phase 7 item 1. It was partly a false alarm (the named causes
were fixed back in PR #76) and partly a real, *different* bug that only showed
up on re-measurement (PR #661). That investigation also spun out **#664**
(separate generated images from hand-authored assets), which is explicitly
scheduled *after* Phase 7 — see §7.

Remediation plan for the adversarial review of 2026-07-24. Findings, evidence
and reproduction details live in **`docs/claude/adversarial-review-2026-07-24.md`** —
this document does not repeat them; it says what to *do*, in what order, under
which constraints.

Every design decision in §2 was made explicitly by the maintainer on 2026-07-24
after being walked through the alternatives. **They are settled. Do not
relitigate them** — if you believe one is wrong, stop and raise it rather than
quietly implementing something else.

---

## 1. How to use this document

1. Read §2 (decisions) — they constrain everything.
2. Find the lowest-numbered phase in §4 that is not `DONE`.
3. Read that phase in full, including its **Landmines**, before touching code.
4. Work the phase, update its status line here, and record anything surprising
   in the phase's **Notes** subsection.
5. Ship per `AGENTS.md` (branch `claude/…`, changelog fragment in
   `changelog.d/` — **never** the `[Unreleased]` section, update the matching
   `clm info` topic for any CLI/spec/behavior change). The `ship-a-pr` skill
   encodes the repo-specific landmines.

Phases are ordered by risk × independence. Later phases assume earlier ones
landed; §4 states dependencies explicitly where they are hard.

---

## 2. Decisions register (settled 2026-07-24)

| # | Decision | Chosen | Rationale / constraint it imposes |
|---|----------|--------|------------------------------------|
| D1 | Cassette RCE release posture | **Patch release now, changelog only** | Ship 1.21.2 with the fix noted as a security fix. No GHSA/CVE — this is effectively single-maintainer tooling; formal disclosure would advertise the vector faster than it would protect anyone. |
| D2 | Worker API network exposure | **Loopback + Docker gateway by default; binding wider is an explicit opt-in that requires a token** | The safe posture must be what you get by accident. See D5 — this API becomes the cross-machine DB owner, so the opt-in path is a first-class supported mode, not an escape hatch. |
| D3 | Executed-notebook cache payload | **Replace pickle with JSON** | Removes the deserialization-RCE class outright rather than defending it with a secret. Accepts larger/slower payloads and a cache-format migration. |
| D4 | Recordings + `clm serve` auth | **Origin/Sec-Fetch-Site checks + loopback bind + TrustedHostMiddleware + CORS default off `*`** | Kills CSRF and DNS rebinding with zero added UX friction. Explicitly *not* protecting against same-machine actors. |
| D5 | Shared jobs DB across machines | **Route cross-machine access through the worker API** (one DB owner) | Long-term target. Makes the worker API load-bearing for correctness — its auth story (D2) and the queue-correctness work (Phase 5) are prerequisites. |
| D6 | Interim shared-DB posture | **Force `journal_mode=DELETE` on network paths now** | Stopgap shipped in Phase 0, deliberately throwaway. Removes the `-shm` incoherence corruption vector while D5 is built. Accepts a performance hit on Z:. |
| D7 | Docker containment | **Per-worker mount mode + non-root `USER`** | `/source` read-only for the notebook worker (which runs untrusted code); read-write retained for PlantUML/DrawIO, which genuinely generate images into the source tree (`worker_executor.py:340` says so explicitly). |
| D8 | Sync `record` gate | **Strict, with an explicit escape hatch** | Gate must project companions exactly as `verify` does. Provide a documented override flag that **logs loudly** when used, so it cannot quietly become the normal path. |
| D9 | Decision-document format | **Add an action discriminator to the key** (breaking) | Restores the documented per-item contract. Requires the sync info topic, downstream course-repo agent docs, and any saved decision documents to be updated in lockstep. |
| D10 | Architecture remediation scope | **Full re-layering, including extracting build orchestration — gated behind test coverage** | The test prerequisite (D11) is a *hard gate*: no re-layering commit lands before Phase 7 is complete and green. |
| D11 | Re-layering test prerequisite | **All four**: golden e2e characterization suite, layer-boundary contract tests, real unmocked build-pipeline tests in the fast suite, and a coverage threshold on the modules being moved | Phase 7 exists solely to satisfy this. It is the largest single investment in the plan and it is deliberate. |
| D12 | Test policy | **Fix the dead tests + add a nightly CI job for the `slow` tier** | Recovers lost coverage without lengthening PR CI. Nightly failures need a watcher — see Phase 1 landmines. |
| D13 | #697 — the Studio's tier-2 preview and macro-emitted HTML | **Sanitize on the server** (option 2) | The preview is rewired, and the macro-emitted HTML is sanitized **server-side** before it reaches the page — not by trusting the macro output, and not by sanitizing in the browser where the sink already exists. Add the sanitizer dependency to the **correct dependency group**: the `[web]` extra in `pyproject.toml` (what the Studio already requires) — not core, not `[dev]`, and `[all]` picks it up through `web`. `nh3` over `bleach` (bleach is deprecated upstream). An install without `[web]` must fail closed rather than fall back to unsanitized HTML. |
| D14 | #698 — bounding the preview's CPU | **Render in a subprocess** | The in-process memory bounds shipped with S7; CPU needs a boundary a Jinja hook cannot give. A subprocess is killable on a wall-clock timeout, which is the only mechanism that survives a template that loops without allocating. Accepts the per-render process cost. |

**Session scope note**: the maintainer chose *handover document only* for the
session that produced this plan. No code was changed on 2026-07-24. Phase 0 is
genuinely un-started.

---

## 3. Current risk posture (as of 2026-07-24)

Live and unmitigated until Phase 0 ships:

- **A working RCE** reachable by a one-line edit to any committed cassette in
  any course repo, triggered by a normal `clm build` on the maintainer's
  machine. Verified by execution, not inference.
- **An unauthenticated pickle-accepting HTTP service on all interfaces**
  whenever a Docker-mode build runs, reachable both from the LAN and from any
  web page open in the maintainer's browser.
- **Silent cross-build job corruption** on the shared Z: jobs DB, including a
  path where a torn output file is persisted into the content-keyed cache and
  replayed as a valid hit indefinitely.
- **Two confirmed sync data-loss paths** (`mirror_remove` without the
  divergence guard; one decision answer landing on an unaddressed item).

---

## 4. Phase plan

### Phase 0 — Emergency patch → release 1.22.1  ▸ STATUS: DONE

> **Completed 2026-07-25.** Both fixes implemented with 17 regression tests.
> PR #658 merged (all CI green including Docker). Release PR #659 merged;
> `pytest -m "not docker"` was 8704 passed / 0 failed before the bump.
>
> **Note on the version**: master released **1.22.0** while this plan was being
> written, so the patch release is **1.22.1**, not 1.21.2 as originally stated.
> Master also moved to schema v11 (`jobs.session_id`); the branch was merged
> with master and re-verified against it.
>
> Docs shipped beyond the plan's list, because the change is user-visible and
> the release rules require docs first: a "Databases on network shares" section
> in `docs/user-guide/configuration.md` (including the non-obvious point that a
> RAM disk on a local drive letter is *local* and keeps WAL, so the documented
> `Z:\clm_jobs.db` RAM-disk setup is unaffected), and a troubleshooting entry
> for the new "still in WAL mode" error.

**Goal**: close the confirmed RCE and the shared-DB corruption vector, ship
today. Nothing else rides along — this release must be reviewable in one sitting.

**Work**

1. **S1 — cassette YAML loader.** `src/clm/infrastructure/http_replay_mitm/vcr_format.py:46`
   → `from yaml import CSafeLoader as _Loader` (and the pure-Python fallback at
   `:49` → `SafeLoader`). Remove the now-false `# noqa: S506 — trusted repo files`
   comment at `:319`; the premise of that comment is exactly what the review
   disproved.
   - **Regression test** (required): a cassette containing
     `!!python/object/apply:os.getenv [...]` must raise a YAML constructor error,
     not execute. Assert on the exception, and assert the sentinel side effect
     did **not** occur — a test that only checks "raises" would still pass if a
     future loader executed *and then* raised.
   - **Verify the format still round-trips**: the cassette format uses scalars,
     maps, lists and `!!binary`. `CSafeLoader` supports all four, but run the
     existing cassette golden-fixture tests to confirm, since those goldens are
     the canary for emitter/parser drift.
   - Check the other two load sites named in the review (`addon.py:546`,
     `cassette_doctor.py:355`) route through the same `_Loader`; fix any that
     don't.
2. **C1-interim (D6) — network-path journaling.** `src/clm/infrastructure/database/schema.py:193`.
   Detect UNC/network paths (`\\server\share`, and mapped drives — on Windows use
   `GetDriveType`; do not rely on the path string alone, since `Z:` looks local)
   and use `journal_mode=DELETE` with a generous `busy_timeout` instead of WAL.
   Log once, at INFO, which mode was chosen and why.
   - **Landmine**: `journal_mode` is *persistent* in the DB file. An existing Z:
     DB is already in WAL mode, and a fresh `PRAGMA journal_mode=DELETE` on it
     will fail while any other connection holds it. Handle the failure path
     explicitly — refuse to proceed with a clear message rather than silently
     continuing in WAL.
3. Changelog fragments in `changelog.d/` (type `security` for S1, `fixed` for
   the journaling change).
4. Release 1.21.2 per `docs/developer-guide/releasing.md` — docs first, full
   `pytest -m "not docker"` green locally, then land the `Bump version …` commit
   on master **as a merge commit**. Publishing is automated; never run
   `uv publish` or `gh release create` by hand.

**Acceptance**: the exploit test fails before the fix and passes after; a build
against a network-path jobs DB reports DELETE journaling; 1.22.1 is on PyPI with
CI green for the tagged commit.

**Explicitly out of scope here**: everything else. Resist bundling.

**Notes from the implementation (2026-07-24)**

- The five `journal_mode=WAL` call sites had to be unified, not just
  `schema.py`. `journal_mode` is a persistent property of the database *file*,
  so leaving the other four setting WAL would have silently undone the safe
  mode. They now all route through `database/journal_mode.py`.
- Verifying "the test fails before the fix" by running the suite with the
  vulnerable loader restored is itself an exploit execution, and was blocked by
  a tool-permission classifier — correctly. The teeth of the test were confirmed
  structurally instead: `CLoader` and `Loader` both fail the safe-loader
  allowlist and both *are* subclasses of `UnsafeConstructor`, and the payload's
  execution under `CLoader` had already been demonstrated during the review. If
  you need to re-verify behaviourally, do it in a throwaway subprocess, not by
  reverting the source fix.
- The YAML **dumper** is still `CDumper`. Not part of this vulnerability (the
  RCE is on load), and a safe dumper refuses types the current one accepts
  (notably tuples), so it needs its own change with its own testing.

---

### Phase 1 — Test resurrection  ▸ STATUS: DONE

> **Completed 2026-07-25.** Seven PRs, one per item, all merged:
> #667 (T1), #668 (T1 fallout), #669 (T4), #670 (T5/T6/T10), #671 (T7),
> #672 (T8), #673 (T2/D12), plus this one for T9 and the close-out.
> Notes and surprises below — read them before Phase 5, which touches the
> code T1 resurrected.

**Goal**: stop flying blind. This is cheap, high-yield, and every later phase
depends on it — especially Phase 5, which modifies the exact code the dead tests
covered.

**Work**

1. **T1 — 8 permanently-skipped tests.** `tests/infrastructure/workers/test_direct_integration.py:69-70`:
   `check_worker_module_available("drawio_converter")` → `"clm.workers.drawio"`,
   `"plantuml_converter"` → `"clm.workers.plantuml"`. Same stale flags at
   `tests/infrastructure/workers/test_lifecycle_integration.py:76-77`.
   - **Expect failures.** These tests have not run since the module rename;
     assume some are stale or broken rather than assuming the code is fine.
     Triage each: a genuine product bug gets its own issue and a fix in Phase 5;
     a stale test gets updated. **Do not delete a failing test to get green** —
     that is how they died the first time.
2. **T2/D12 — nightly `slow` job.** Add a scheduled workflow running
   `pytest -m "slow and not docker"`. Route failures somewhere you will actually
   see them (see landmine below).
3. **T4 — tests that cannot fail.** `tests/cli/test_cli_integration.py`: remove
   the `if result.exit_code == 0:` wrappers (`:55-68`), fix the tautology at
   `:400-402`, fix `assert x or result.exit_code != 0` at `:196-198`, and make
   `test_build_with_clear_cache` actually assert the cache was cleared. If a test
   genuinely cannot assert success in CI (missing workers), mark it skipped for a
   stated reason — a skip is honest, a swallowed assertion is not.
4. **T5** — add the missing `assert len(de_images) > 0` in the PlantUML e2e test
   (`tests/e2e/test_e2e_course_conversion.py` ~`:1105-1143`).
5. **T6** — drop `AssertionError` from the `only_rerun` list in
   `tests/infrastructure/workers/test_worker_base.py:26-35` and
   `test_lifecycle_mock.py:36-49`. Keep the OSError/PermissionError/OperationalError
   entries; those are genuine environment flakes. An intermittent race in the
   production claim loop *is* an intermittent `AssertionError` — that is signal,
   not noise.
6. **T7** — bring `tests/fixtures/mock_workers.py:212-226` to v10 parity
   (`execution_mode` filter, session-ownership semantics), or better, have
   MockWorker call the real `JobQueue.get_next_job` so it cannot drift again.
7. **T10** — remove the dead `PLANTUML_JAR` discovery path in
   `tests/conftest.py:437` (points at the pre-PR-#239 vendored location) and
   replace the import-time `os.environ` mutation in
   `tests/workers/plantuml/test_plantuml_converter.py:31-47` with a proper
   fixture. Under xdist the current arrangement is ordering-dependent.
8. **T8** — leave the autouse neutralizers in place (they are justified), but
   document each with a comment naming the single test that covers the real
   production value, and add one interaction test where the gap is widest (the
   pool-size clamp firing during managed-worker startup).

**Landmines**
- A nightly job nobody reads is worse than no job: it manufactures the feeling
  of coverage. Decide *now* where failures surface (issue auto-filed, or a
  notification you actually receive) and write that into the workflow.
- `-n auto` OOMs locally on the e2e tests; use `-n 4`. Known, recorded in
  project memory.

**Acceptance**: 8 previously-dead tests run and pass (or have issues filed for
genuine bugs they exposed); no test in `tests/cli/test_cli_integration.py`
passes when the build is broken; nightly workflow green and its failure route
verified by deliberately breaking a test once. — **All met.**

**Notes from the implementation (2026-07-25)**

- **T1's two-string fix was two strings; the fallout was not.** Un-skipping the
  module surfaced *three* further stale things the permanent skip had hidden:
  1. `test_direct_worker_processes_job` and `test_high_concurrency_notebook_workers`
     submitted `payload={"kernel": "python3", "timeout": 60}`, which stopped
     being a valid notebook payload when `NotebookPayload` gained its required
     `kind`/`prog_lang`/`language`/`format` descriptors. Every such job failed
     validation. Now built from a real `NotebookPayload`.
  2. `TestMixedModeIntegration` is `docker`-marked, so it did not run in the PR
     matrix — it broke **master's Docker job** after #667 merged, and needed the
     follow-up #668. Its image tag was `drawio-converter:latest`, dead since the
     `clm-` prefix; and its "stale" worker rows were inserted without
     `last_heartbeat`, so they defaulted to *fresh* and cleanup correctly kept
     them. **If you un-skip anything else, check what it does under `-m docker`
     before merging** — that job is not a required check, so a green PR proves
     nothing about it.
  3. High-concurrency jobs all shared one content hash; identical sources are
     served from the executed-notebook cache after the first job, which would
     have turned a concurrency test into a cache-lookup test.
- **Guard against recurrence**: `tests/infrastructure/workers/test_worker_module_probes.py`
  asserts every module name used by a `find_spec` skip guard actually resolves.
  A stale probe is invisible in a report — "skipped" reads as "not applicable
  here", not "permanently dead". That is the whole reason T1 survived so long.
- **T4 found repo pollution, not just weak assertions.** The CLI integration
  tests left `--cache-db-path` at its default, which `main.py:_anchor_default`
  anchors to the *project root* — so every run wrote a ~4.6 MB `clm_cache.db`
  into the working tree (gitignored, hence invisible in `git status`) and shared
  it across xdist workers.
- **T8 exposed a bug in its own neutralizer.** `_neutralise_pool_size_cap`
  exempted itself with `"test_pool_size_cap" in nodeid` — a bare substring, so
  the new `test_pool_size_cap_interaction.py` was also exempted, saw the *real*
  host caps, and passed on a 32-core dev box while failing on the 2-core runner.
  Now matched on `test_pool_size_cap.py`. **Watch for this shape elsewhere**:
  nodeid substring matching in a conftest fixture is a silent-scope-creep
  hazard.
- **T2's failure route is verified, not assumed.** `.github/workflows/nightly.yml`
  files/updates a `nightly-failure` issue and has `workflow_dispatch`, so the
  alert path was exercised by dispatching it against a throwaway branch carrying
  one deliberately failing `slow` test: the run failed, the `Report failure` job
  succeeded, and it filed the issue with the run URL, commit and trigger. A
  second dispatch confirmed the dedup path — it comments on the existing open
  issue rather than filing a second one, so an outage produces one issue, not
  one per night. (Both the issue and the throwaway branch were cleaned up
  afterwards.) Chosen over an emailed Actions failure because the maintainer
  works from the issue tracker; say so if you would rather have it the other
  way.
- **The slow tier is cheap**: 37 tests, 78s locally at `-n 4`. It was never
  excluded for cost.
- **T9 partially deferred, deliberately.** Its "constructor-echo tests" half is
  fixed (`TestBuildDataClasses` now asserts the rendered `__str__` a user
  actually reads, instead of reading back its own constructor arguments). Its
  other half — the sync full-corpus gates being triple-gated to manual-only — is
  **not fixable in CI**: `TestRealCorpusSelfDiff` needs the maintainer's private
  PythonCourses checkout (`CLM_SYNC_CORPUS_DIR`, else a hard-coded local path).
  The nightly job runs it, and it will report `skipped` there until that corpus
  is reachable from CI. The bundled-fixture gate in the same file does run in
  the fast suite. Nothing to fix; recorded so the next reader does not re-derive
  it.
- **Phase 5 inherits live tests now.** `test_direct_worker_health_monitoring` is
  the one place `start_monitoring()` runs outside a `__main__` demo. It passes on
  a CEST host *despite* C8 (UTC `CURRENT_TIMESTAMP` vs local `datetime.now()`)
  only because a stale heartbeat merely warns for direct workers — hung-marking
  is Docker-only. Do not read its green as evidence against C8.

---

### Phase 2 — Network-facing security  ▸ STATUS: DONE
**Depends on**: Phase 1 (you are about to change Docker-mode networking and the
cache format; the worker tests must be alive first). **Phase 1 is DONE**, so
this is unblocked.

> **Item 1 (S2 + D2 + D3) completed 2026-07-25.** Notes at the end of this
> phase — read them before item 2, and before anything that touches a worker
> image.

**Goal**: no CLM service is reachable, or actionable, by a party that has not
been explicitly granted access.

**Read this before you start — it is specific to this phase.**

- **The Docker CI job is not a required status check.** You are about to change
  Docker-mode networking and the worker-API contract, which is precisely the
  code `-m docker` covers and the PR matrix does not. A green PR proves nothing
  about it. Run `pytest <module> -m ""` locally, and check the merge commit's
  Docker job — not just the PR's green tick. Phase 1 landed three mitigations:
  a `report-master-failure` job that files a `master-red` issue, an image-tag
  guard in the fast suite, and a nightly that runs the docker tier. Issue #679
  tracks whether the job is stable enough to become required.
- **S2 is confirmed live, not inferred.** A Docker-mode test run during Phase 1
  logged, verbatim:
  `Worker API server started on http://0.0.0.0:8765 (Docker: http://host.docker.internal:8765)`.
  So the bind is genuinely all-interfaces on a normal build, exactly as the
  review said.
- **CI shape changed during Phase 1.** There are now four PR suites
  (`unit`/`integration`/`e2e`/`slow`) × two Python versions = eight required
  contexts plus `Lint and type check`. If you add or rename a suite you must
  update the "Require CI green" ruleset in the same breath, *after* the workflow
  merges. See `docs/developer-guide/testing.md`.
- **`test_direct_worker_health_monitoring` is the only place `start_monitoring()`
  runs outside a `__main__` demo.** It passes on a CEST host despite finding C8
  only because a stale heartbeat merely warns for direct workers. Do not read
  its green as evidence about the timezone bug.

**Work**

1. **S2 + D2 + D3 — worker API.** ▸ **DONE** — `src/clm/infrastructure/api/server.py:22-23`,
   `worker_routes.py`.
   - Default bind: `127.0.0.1` **plus** the Docker bridge gateway address so
     containers keep working. Binding to anything else requires an explicit
     config/flag **and** a configured token; refuse to start otherwise with a
     clear error. This opt-in mode is the supported path for D5 — name and
     document it as such (e.g. "coordinator mode"), not as a debug escape hatch.
   - Auth: per-build bearer token on **every** route. Reuse the constant-time
     comparison in `src/clm/web/studio/auth.py`; do **not** reimplement it, and
     do **not** accept the token as a query parameter (that is S7's leak).
     Inject it into workers via the environment the same way `DB_PATH` is.
   - **D3**: replace the pickle payload on `/cache/executed_notebook` (both
     directions) with JSON. `NotebookNode` is a dict subclass, so
     `nbformat.writes`/`reads` is the natural carrier. Update the three consumers
     (`sqlite_backend.py:575`, `notebook_worker.py:107`,
     `api_executed_notebook_cache.py:68`) and bump the cache schema/version so
     old pickled entries are invalidated rather than misread.
   - **Landmine**: `executed_notebook_cache.py` stores raw bytes; make sure the
     *stored* format changes too, not just the wire format, or you leave a
     pickle-loading path alive behind an auth wall.
2. **S3 + D4 — recordings dashboard.** ▸ **DONE** (PR #691) —
   `src/clm/recordings/web/routes.py`, new
   `src/clm/infrastructure/web_security.py`.
   - `/process` now applies the containment check its own sibling
     `/open-explorer` already implements, over the **whole batch before
     submitting any of it** (a mid-loop refusal had already started an
     Auphonic upload).
   - Two ASGI guards, installed together by `install_web_security()`: a `Host`
     allowlist (loopback by default; `--allowed-host` opts in) and an origin
     check on mutating requests only. The host half is what closes DNS
     rebinding — an origin check alone cannot, because a rebound page *is*
     same-origin. Hand-rolled rather than Starlette's, whose host splitter
     turns `[::1]:8000` into `"["`.
   - `course_slug` validated strictly (it reaches `get_state_path` unsanitized
     and becomes `<slug>.json`); `section_name`/`deck_name` validated for
     containment only. **See the notes below — this distinction is the whole
     lesson of the item.**
3. **S6 + D4 — `clm serve`.** ▸ **DONE** (PR #695) — `src/clm/web/app.py`,
   `api/websocket.py`, `cli/commands/serve.py`.
   - CORS installs *no* middleware by default. An explicit `*` keeps working
     but drops credentials; `install_web_security()` is reused unchanged, with
     `--allowed-host` / `--allowed-origin` mirroring `clm recordings serve`.
   - `/ws` requires the Studio token before `accept()` **when `--spec` is in
     play**, presented as the `clm-token.<token>` subprotocol (a browser
     cannot set an `Authorization` header on a WebSocket, and a query
     parameter would land in the access log on every 3-second reconnect).
     Channels are restricted to a known set.
   - **Decision, flagged in the PR**: without `--spec` there is no token
     concept and `/ws` stays open behind the host/origin guards — every
     `/api` route is an unauthenticated GET, so gating only the WebSocket
     would be friction without a gain. Pinned by a test so it reads as a
     decision. The flip side is that *with* `--spec` the token covers the
     whole endpoint, including `status`/`workers`/`jobs`.
4. **S7 — studio render.** ▸ **DONE** (PR #696) — `web/studio/render.py`,
   `static/studio/app.js`, `studio/auth.py`, `static/studio/sw.js`.
   - `ImmutableSandboxedEnvironment` for the cell preview, plus the size
     bounds a sandbox does not give you (see the notes below).
   - `esc()` escapes quotes; link targets restricted to
     http/https/mailto/relative, with control characters stripped before the
     scheme is judged and all four `//`-style authority forms refused.
   - `?token=` is no longer a credential; the QR pairing URL moved to the URL
     fragment, which browsers never transmit.
   - Spun out: **#697** (the in-page tier-2 consumer was dead code around a
     raw `innerHTML` sink — removed; rewiring it needs a decision on
     sanitizing macro-emitted HTML) and **#698** (bound the preview's CPU).
4. **S7 — studio render.** `src/clm/web/studio/render.py:55-71` →
**Acceptance**: a cross-origin page cannot drive any mutating route on either
app; `/ws` rejects an unauthenticated connection before accept; the worker API
refuses a non-loopback bind without a token; no pickle remains on any API or
cache path; SSTI proof-of-concept from the review no longer executes.

**Notes from item 1 (2026-07-25)**

- **Worker images do not install FastAPI, and nothing warned about it.** The
  token's env-var name started life in `infrastructure/api/auth.py`, which
  imports FastAPI; `client.py` imports that name; every worker container then
  died with `ModuleNotFoundError: No module named 'fastapi'` before claiming a
  job. On the host — and in CI's non-docker matrix — the import is fine, so it
  was invisible. The constant now lives in `infrastructure/api/token.py`, which
  imports nothing, and
  `tests/infrastructure/workers/test_worker_import_surface.py` pins the
  property in the **fast** suite: it re-imports each worker-side module in a
  subprocess with an import hook that refuses fastapi/uvicorn/starlette, and
  includes a case proving the hook actually bites. **Anything you add to a
  worker's import path has to survive that.**
- **`TYPE_CHECKING`-only annotations break FastAPI dependencies.** The first
  attempt at keeping `auth.py` FastAPI-free put `Request` behind
  `if TYPE_CHECKING`. FastAPI resolves a dependency's annotations at runtime,
  so `require_api_token` stopped being recognized and *every* route went back to
  answering unauthenticated — 52 of the new tests caught it. Splitting the
  module was the fix; lazy imports inside the function are not enough when
  FastAPI needs the signature.
- **Docker Desktop forwards `host.docker.internal` to the host's loopback.**
  Verified directly (a container `curl`ed a loopback-bound host server), so on
  Windows/macOS the loopback bind alone is sufficient and the Docker bridge
  gateway is not even a bindable host address. Linux is the opposite case and
  gets the gateway binds — via `uvicorn.Server.run(sockets=…)`, since one
  uvicorn config binds one address. If Linux discovery finds no gateway the
  server now logs a loud WARNING, because the symptom otherwise is jobs sitting
  `pending` with nothing in the host log.
- **`start()` binds before spawning the thread.** It used to signal "started",
  sleep 0.1s and hope. Binding on the caller's thread makes "port is taken" a
  synchronous error, and makes `port=0` resolve to the real port before
  `start()` returns — which let `test_server.py` drop both `_DUMMY_PORT` and
  the `_free_port()` TOCTOU race it documented.
- **Docker tests in this tier are order-dependent; re-run before believing a
  failure.** Three notebook tests failed inside a full serial `-m docker` tier
  and passed in 26s when run alone immediately afterwards, on the same images
  and the same code. **The cause is not established.** "Cold image start-up"
  was the first guess and it is *wrong* — measured: container start is ~1.0s
  with no cold/warm difference (a months-unused image: 1060ms first run,
  1044ms second), and a warm container registers ~3s after `docker run`,
  against a 5s + 60s test budget.
  The leading hypothesis was port 8765: every test in the tier started and
  stopped its own `WorkerApiServer` on that fixed port, and `SO_REUSEADDR` on
  Windows lets a second bind succeed against a socket that is still listening,
  leaving which socket receives connections undefined. That matches the symptom
  exactly — the failing assertion is `status 'pending'` with `Error: None`,
  i.e. the job was *never claimed*, not "claimed and too slow".

  **Acted on (item 1 follow-up).** The hijack is now measured, not inferred: on
  Windows 11, two sockets bound `127.0.0.1:18765` successfully with
  `SO_REUSEADDR`, and the second bind failed `WSAEADDRINUSE` without it. Three
  things changed as a result — `bind_socket` no longer sets the option on
  Windows; the Docker test tier runs with `CLM_WORKER_API_PORT=0`, so every test
  gets a private OS-assigned port; and `DockerWorkerExecutor` now takes
  `CLM_API_URL` from the running server instead of rebuilding it from
  `DEFAULT_PORT` — without which per-test ports could not work at all, since
  containers were being told 8765 no matter where the server was. **The original
  three failures were never reproduced**, so treat the hypothesis as plausible
  and now un-testable rather than confirmed; what is certain is that the
  mechanism it named can no longer occur.
- **`/health` no longer reports the database path.** It is the one
  unauthenticated route, and the path names a host filesystem — and, on a
  share, a server name.
- Item 1 is a **breaking change for Docker mode**: an image older than the host
  presents no token. The 401 path is given its own error message naming
  `clm docker build`, plus entries in the migration info topic and
  troubleshooting guide. Direct mode is untouched.
- **A stale *local* image now fails the docker tier, and the symptom hides the
  cause.** The token requirement means any image predating it registers 401 and
  its jobs stay `pending` — which surfaces as "job never claimed" or
  `Expected 1 active workers within 30s`, saying nothing about images. CI does
  not see this (it builds fresh `:test` images and skips what it has not built);
  a dev machine with published images pulled weeks ago does. Two casualties
  found while verifying the port work, both pre-existing:
  - `test_docker_job_execution.py`'s DrawIO tests **hardcoded**
    `mhoelzl/clm-drawio-converter:latest`, so they ran against Docker Hub rather
    than the working tree. **Fixed**: they now discover the image like the
    notebook tests do, preferring the CI-built `clm-drawio-converter:test`.
  - `test_notebook_error_context.py::TestCppErrorWithDocker` asked for the
    `:full` image, and failed on a machine whose `:full` predates the token
    change — verified failing identically on `master`. **Fixed, and the fix was
    not "rebuild `:full`": the `full` variant is not where the C++ kernel comes
    from.** xeus-cpp is installed in the Dockerfile's shared `common` stage, so
    **every** notebook image ships `xcpp17/20/23`; `full` differs only by an
    nvidia/cuda base plus torch/fastai. The test now prefers the CI-built
    `clm-notebook-processor:lite-test` and passes against it in 30s, with the
    real compilation error correctly attributed to cell #2 — so **C++ execution
    is covered by the existing CI docker job at zero extra build time**, where
    before it was skipped in CI and ran only on a machine that had pulled 23 GB.
  Diagnosing a stale image is one command:
  `docker run --rm --entrypoint python <image> -c "import inspect; from clm.infrastructure.api import client; print('CLM_API_TOKEN' in inspect.getsource(client))"`.

- **Do not add a `full`-variant build to CI.** The decisive number is size:
  `full` is **23 GB** against `lite`'s **6.3 GB** (`docker images`, freshly built
  from this tree). Time says the same thing — a cold `full` build on the dev
  machine took **~80 min** (fresh buildx builder, so layer cache and the uv
  cache-mount both started empty, the CI cache-miss analogue; it overlapped with
  other work, so treat it as an order of magnitude, not a stopwatch) and **6.5
  min** to rebuild warm. `load: true` on a 23 GB image does not fit a standard
  GitHub runner's disk without clearing it first, and its layers alone would
  exceed the repository's 10 GB Actions-cache budget — evicting the uv caches the
  other jobs depend on, which the workflow's own comment already worries about at
  lite's size. And there is nothing to buy: the ML stack is the *only* thing
  `full` adds, so no language kernel depends on it.

**Notes from item 2 (2026-07-25/26) — read before items 3 and 4**

- **The dangerous mistake in this item was not a missed attack; it was an
  over-strict validator.** One rule was applied to `course_slug`,
  `section_name` and `deck_name`, and it rejected `:`. Real section names are
  *titles the user wrote* — "Woche 01: Einführung, LLMs und Python in
  JupyterLite" — and **33 of the 39 in `machine-learning-azav.xml`, the
  default recordings spec, contain a colon**. That silently 400'd `/arm`,
  `/record`, `/advance` and both `/decks/…/takes` routes: the entire Lectures
  page, dead, for every real course. An adversarial review caught it; the
  test suite did not, because its only positive case was `"Section 1"`.
- **Then a second false positive of the same shape survived the fix**:
  "Woche 12: Datenanalyse mit pandas (+ ML-/Metrik-Kostprobe)" contains a
  **slash**. It was found only by driving the app with *all 22* section names
  from the real spec instead of three hand-picked ones.
  **Generalisable rule: when you add validation to a value that originates as
  human-written content, test it against the real corpus, not against examples
  you invented.** Both regressions were invisible to hand-picked cases and
  obvious within seconds of using real data.
- **The correct distinction**, now encoded in two validators: only
  `course_slug` is used as a filename *verbatim* (`get_state_path()` joins it
  into the config dir and appends `.json`), and it is machine-generated
  (`project_slug + "-de"`, else a sanitized course name), so it can afford a
  strict rule. `section_name`/`deck_name` reach disk only through
  `sanitize_file_name()`, which deletes `; ! ? " ' :` and replaces
  `/ \ $ # % & < > * = ^ € |` — so containment must be judged on the
  **sanitized** form. Judging the raw form is a false positive. What the
  sanitizer does *not* neutralize is **dots**: `":..:"` sanitizes to `..` and
  escapes, which is why the sanitized form is checked for `.`/`..`.
- **`clm serve` (items 3–4) can reuse `install_web_security()` as-is**,
  including for its `/ws` route — the origin guard already covers WebSocket
  handshakes, which are CORS-exempt. Note `clm.web.create_app` will need an
  `allowed_hosts` parameter for the same reason the recordings app did:
  `TestClient` sends `Host: testserver`, so the production default 400s every
  existing test.
- **Two Windows-first blind spots worth remembering**: `Path.resolve()` raises
  `OSError` for an embedded null byte on Windows but `ValueError` on POSIX
  (so catch both — CI is Linux and the local fast suite is not); and a
  `git checkout HEAD -- <file>` used to check "does this test fail without the
  fix" **discards uncommitted work in that file** — commit first, then revert
  to test.

**Notes from items 3–4 (2026-07-26) — Phase 2 close-out**

- **Both items uncovered a feature that had never worked**, in code the review
  had assessed as a live hole. `clm serve`'s `/ws` route took an *unannotated*
  `websocket` parameter, so FastAPI analysed it as a required **query
  parameter** and closed every handshake with `Field required` — the Studio's
  disk-change banner and sync line had never fired. And the Studio's in-page
  tier-2 preview was gated on `cell_type === "markdown"` while the API types a
  Jinja cell as `"j2"`, so it was unreachable from the day it was written.
  **Generalisable**: when hardening a path, check it *runs* first. Two of the
  four S6/S7 sub-findings were about code that could not execute, which
  changes what the fix is for — S6's WebSocket hole was real in code and
  unreachable through the mounted route, and fixing the route is what made the
  token check load-bearing rather than theoretical.
- **The review rounds were worth more than the first pass, twice over.** On
  #695 the reviewer found `--allowed-origin` was a **no-op for every current
  browser** — the guard consulted the operator's allowlist only in its
  `Origin` fallback, unreachable whenever `Sec-Fetch-Site` is present, so the
  flag bought a working CORS preflight and a 403 behind it. That bug shipped
  in PR #691 (item 2) and had been live since. On #696 a focused third pass
  caught me *documenting a limitation that did not exist*: I had written that
  Jinja's `~` could only be bounded by monkeypatching `jinja2.runtime`
  process-wide, and accepted a 1.2 GB one-request memory bomb on that basis.
  `Environment.code_generator_class` is a documented per-environment hook;
  fifteen lines closed it. **If you find yourself writing "this cannot be
  fixed without X", check that X is really the only option before shipping the
  claim** — a wrong limitation is worse than a missing fix, because it stops
  the next person from looking.
- **Three growth vectors, three different hooks.** Bounding Jinja is not one
  switch: `*`/`+` go through `intercepted_binops`; the rendered output goes
  through `environment.concat`; `~` goes through neither (its `Concat` node is
  not a `BinExpr`, and it calls `str_join` from the compiled template's own
  namespace) and needs the code-generator redirect. Also: a size check *after*
  `render()` bounds only what is returned — a 500-iteration loop of
  individually-legal emits peaked at 100 MB before such a check could run.
  Bound during accumulation.
- **A security fix behind a service-worker cache is not shipped.** `sw.js`
  re-installs only when its own bytes change, and `activate` drops only caches
  whose *name* differs — so with a pure cache-first shell handler, `app.js`
  was served from `clm-studio-shell-v1` forever, and it had changed three
  times under that name. Any installed PWA would have kept running the
  pre-fix frontend *and*, because the pairing URL moved in the same PR, would
  have been unable to re-pair. **If you touch anything under
  `static/studio/`, bump `SHELL_CACHE`.** The handler is now
  stale-while-revalidate so a missed bump costs one stale load rather than
  permanent staleness.
- **Windows-first landmines hit again, both in tooling rather than product
  code**: writing a JS regex containing a literal control-character range through the editing
  tools twice produced *literal control bytes* in the file, and once collapsed
  `\` to `\` (silently breaking a character class). Verify byte content
  after writing regex-heavy JS — `node --check` catches the syntax errors but
  not a class that parses and means something else.
- **`TestClient` sends `Host: testserver`.** The production allowlist is
  loopback-only, so every existing `create_app` test 400s until it passes
  `allowed_hosts=["testserver"]`. For the Studio suites that knowledge lives
  in one place now (`tests/web/studio/conftest.py::make_app`).
- **There is no JS test harness in this repo**, and adding one (package.json,
  runner, CI step) is out of proportion to a few pure functions. The
  frontend's `esc`/`safeUrl`/`inline`/`resolveToken` are lifted out of
  `app.js` by name and executed under `node`, skipping with a stated reason if
  node is absent. Worth reusing rather than re-deciding.

#### Language coverage: what a broken C++ (or C#, or Java) course would hit

Everything about C++ *except execution* was already unit-tested — the
`cpp:percent` jupytext round-trip, the `// j2` jinja prefix, comment-token
parsing, code extraction, the CMake export. Execution was the hole, and the one
Docker test that ran a C++ notebook ran a **deliberately broken** one to check
error attribution, so "valid C++ compiles and its output reaches the deck" was
asserted nowhere. Now in the docker tier (`test_cpp_docker_execution.py`, ~13s
for both, no new image):

- a valid C++ deck through the real Docker worker, asserting the rendered HTML
  contains a value the C++ code *computes* (`6 * 7`), so a kernel that stops
  running code cannot pass it. **Note it must be an HTML target**: execution is
  gated by `evaluate_for_html` (`output_spec.py`), so a `format: notebook` job
  writes a deck with no outputs and asserts nothing about the kernel — the first
  version of this test passed that way before the assertion was tightened.
- the kernel names CLM asks for (`kernelspec_for`) against the kernels the image
  installs. A rename in an image bump — which the xeus-cling → xeus-cpp move
  actually did — breaks every deck in a language at once, and this fails in
  seconds with the name in the message, for every language rather than only C++.

**The matrix is now all five shipped languages** (`test_language_docker_execution.py`,
renamed from the C++-only module): Python, C++, C#, Java and TypeScript each get a
percent deck written with that language's own comment token, executed through a
real container, asserted on a value the code *computes*. **All five pass**, in
**88s** for the whole module — the .NET and Java kernels are slower to start than
xeus-cpp but well inside budget. A `test_every_shipped_language_has_an_execution_case`
guard fails if the case list and the `prog_lang` config drift, so deleting a case
to make a failure go away is not quiet.

**Rust: measured, and the recommendation is no.** A probe image on top of
`lite-test` (2026-07-25) installed a Rust toolchain and `evcxr_jupyter`:

| | cost |
|---|---|
| Layers added | **+856 MB** — toolchain 642 MB, cmake+pkg-config (for evcxr's zmq) 171 MB, evcxr itself 36 MB |
| Build time added | **+4 min** — rustup 80s, `cargo install --locked evcxr_jupyter` 97s (compiles rust-analyzer's crates), apt 22s, export 41s |
| New external hosts at image-build time | rustup.rs and crates.io — and `ci.yml`'s own comment names external fetches as *the* flake source |

conda-forge has **no** `evcxr_jupyter` package (checked), so compiling from source
is the only route; there is no cheap version of this. And the image is only half
the work: the Rust config was never finished — `line_comment_for("rust")` returns
`#` and its jupytext format is `md`, neither of which round-trips a `.rs` percent
deck. With no Rust courses in existence, the choice is between leaving the config
as a dead entry (pinned by `KNOWN_MISSING_KERNELS`, which is where a future
decision will be visible) or deleting `_rust_config` outright. Deleting is
tempting but is a user-visible removal, so it wants a deliberate call rather than
a drive-by.

**Still uncovered, in rough value order:**
1. **No full `clm build` of a non-Python course spec.** Output targets, code
   extraction, per-language directories and the CMake export are unit-tested but
   never exercised together for C++. An e2e fixture course with one C++ topic
   would cover the shape a course repo actually uses. This is now the top gap.
2. **`clm kernel-triage` is the real-world signal** (it re-runs `evaluate="no"`
   decks and telemetry-flagged flakes against the current kernel) but needs the
   maintainer's CppCourses checkout, so it is the same "private corpus" case as
   the sync full-corpus gates in Phase 1 — a nightly with a course checkout, not
   a PR check.

---

### Phase 3 — Sync engine correctness  ▸ STATUS: items 1 (Y2 + D8), 2 (Y1, PR #824), 3 (Y3 + D9), 5 (Y4) DONE; next is item 4 (Y5)
**Depends on**: Phase 1 (sync unit coverage is the best in the repo — keep it
that way and extend it here).

> **Item 1's blast-radius sweep is done, and the answer is zero (2026-07-26).**
> The plan says to "sweep the course repos to size the blast radius, and
> prepare the fix-forward instructions" before shipping the strict gate. That
> was measured rather than estimated, and **no deck regresses**, so there is no
> migration to prepare and the item is safe to ship on its own.
>
> Method — for every split pair, compare what `record` checks today against
> what D8 will make it check:
> `structural_gate(raw_de, raw_en)` vs
> `structural_gate(*project_pair(...))`. A deck regresses iff the first is
> empty and the second is not.
>
> | repo | split pairs | clean both ways | newly failing |
> |---|---|---|---|
> | PythonCourses | 956 | 956 | **0** |
> | CppCourses | 340 | 340 | **0** |
> | CSharpCourses | 0 | — | — |
>
> **The result is not vacuous**: projection genuinely differs from raw text on
> **230 of the 956** PythonCourses pairs (those with separated voiceover
> companions), and all 230 still gate clean. **CSharpCourses is out of scope by
> format**, not by accident — it is entirely *unsplit* (one `slides_x.cs` per
> topic carrying both `lang="de"` and `lang="en"` cells; no `.de.*` file exists
> anywhere in the repo), so the split-pair engine never runs on it.
>
> Re-measure if this sits unimplemented for long — the sweep reflects the
> committed state of three working trees on 2026-07-26. The script is trivial
> to rebuild from the method line above.

> **Two corrections to item 1 as written, found while sizing it:**
>
> 1. **There are four gate call sites, not two.** The plan names
>    `sync_v3.py:239-250` and `:375-385`, but `cli/commands/harvest.py:544`
>    and `voiceover/harvest_accept.py:442` call `structural_gate` on raw text
>    too. Fixing only the named two would leave the gate projecting on some
>    paths and not others — drift *by call site* instead of by function, which
>    is harder to notice than what is there now.
> 2. **The "can never drift" claim is at `sync_verify.py:300`**, inside
>    `structural_gate`'s own docstring, and it is true about the *function*
>    (the gate is literally the error subset of `structural_violations`) while
>    being false about its *input* — `verify_pair` projects companions at
>    `:476` before calling it, and all four gate callers do not. That is
>    exactly the bug. Whatever shape the fix takes, the shared thing has to be
>    the **projection**, not just the violation computation.

**Goal**: no path silently destroys authored content. Order within this phase
matters: **D8 first**, because the gate is what creates the divergent baselines
the other bugs consume.

**Work**

1. **Y2 + D8 — the ledger write gate.** ▸ **DONE** (2026-07-26) —
   `src/clm/slides/sync_verify.py` grew the two named gate entry points and the
   shared projection; see "Notes from item 1" at the end of this phase.
   `src/clm/cli/commands/slides/sync_v3.py:239-250`
   and `:375-385`: `structural_gate` must run on the **projected** pair (companions
   inlined via `project_pair`), exactly as `verify_pair` does
   (`sync_verify.py:329-333`). Add the documented override flag; make it log at
   WARNING with the specific divergence it is overriding.
   - Fix the docstring claiming gate and CLI "can never drift" — make it true, or
     delete the claim. Better: have both call one function so the claim is
     structurally guaranteed.
   - **Migration**: decks with existing diverged companions will start failing
     `record`. Before shipping, sweep the course repos (PythonCourses, CppCourses,
     CSharpCourses) to size the blast radius, and prepare the fix-forward
     instructions. The `cold_sweep_hint` in `doc_report.py:85` actively steers
     agents toward wholesale `record` — re-word it once the gate is strict.
2. **Y1 — `mirror_remove` divergence guard.** ▸ **DONE** (2026-08-14, PR #824).
   Both removal paths now consult the extracted `_base_carried_divergence()`
   predicate (the edit paths' inline checks call it too) and frame
   `remove_vs_edit` on a diverged base: id-keyed at `sync_diff.py` in
   `_classify_one_sided`, positional in `_classify_pool_slot`. Regression
   tests `TestMirrorRemoveCarriedDivergence` (both paths, RED before the fix)
   plus the preserved-mechanical pin; `test_diverged_base_has_no_similarity_proxy`
   flipped from pinning `mirror_remove` to the framed row (its #630 subject
   unchanged). `remove_vs_edit` chosen over `pending_divergence` because its
   `remove`/`keep` answers fit a one-sided member. Original plan text:
   `src/clm/slides/sync_diff.py:1203-1215`
   and `:1911-1922`: a two-sided base with `entry.de_fp != entry.en_fp` must
   downgrade `mirror_remove` from MECHANICAL to a framed `remove_vs_edit` /
   `pending_divergence`. This mirrors the guard the edit paths already have at
   `:992/1025` and `:1979` — reuse that predicate rather than writing a second
   one.
3. **Y3 + D9 — decision keying.** ▸ **DONE** (shipped 2026-07-31/08-04 under
   the #656 schema-4 arc; recorded here 2026-08-14). `Decision` carries an
   `action` field and answers match per `(key, action)` — exact first, key-only
   fallback binds at most one item (`doc_apply.py:285-293`, `:1826-1841`), so a
   valid answer can no longer land on a co-keyed item the judge never
   addressed. Schema 3 retired and `report_id` made mandatory by `a9153d3d`
   (breaking); stale-shape rows are rejected with the accepted shapes named.
   Info topic `sync-agents.md` documents the discriminator (§"Naming the row
   you are answering"); downstream course-repo docs migration was part of the
   schema-4 rollout. Original plan text: `src/clm/slides/doc_apply.py:100-115`,
   `:199-200`, `:1296-1347`: keys become key+action so each framed question is
   answered independently.
   - **Breaking.** Update in lockstep: `src/clm/cli/info_topics/` (the sync
     topic), the downstream agent docs in the course repos, and any saved
     decision documents. Per `AGENTS.md`, a stale info topic makes downstream
     agents produce wrong output — treat it as part of the fix, not follow-up.
   - Add a clear rejection message for old-format keys pointing at the migration,
     rather than silently accepting them (which would preserve the bug).
4. **Y5 — trust-gate `stamp_twin_id`.** ▸ Re-verified OPEN 2026-08-14:
   `_emit_pending_id_stamps` (`sync_diff.py:652-670`) still emits the stamp
   mechanically with no ledger-trust check; #716's cardinality guard
   (`3c92b8da`) fixed the residue-*surplus* adoption shape (C1/C2), not the
   equal-cardinality swapped-twin Y5 repro. Original plan: `sync_diff.py:417-435`: do not emit a
   mechanical id stamp for a member whose pairing came from
   `pair_positionally` (`doc_lenses.py:505-522`) and is not ledger-known at that
   pairing. Frame it instead. Since P2 makes the id *the* identity, a wrong stamp
   is the worst corruption in the system and it currently requires no judgment.
5. **Y4 — bind decisions to fingerprints.** ▸ **DONE — via a different
   mechanism than specified** (shipped 2026-07-31/08-04 as #649's `report_id`;
   recorded here 2026-08-14). Instead of a per-decision fingerprint, every
   report envelope carries a `report_id` freshness token over bundle bytes +
   ledger section, and `apply` refuses a document whose token no longer matches
   the deck: "Nothing was written: re-run `clm slides sync report`"
   (`sync_v3.py:415-441`, `REQUIRE_REPORT_ID` since `a9153d3d`). This covers the
   Y4 repro (v2 translation landing after a v3 edit, banked as verified) at the
   document level — the v3 edit changes the token, so the stale document is
   rejected before any write. Per-decision fingerprints were deliberately not
   added; if item-level staleness ever slips past the token (it is per-deck,
   not per-member), revisit the original design:
   A decision should carry the moved
   side's fingerprint from the report it answered; on mismatch, reject with
   "re-run report" rather than applying a stale translation and recording it as
   verified (`doc_apply.py:1399`).
6. **Y6 — preamble divergence guard.** ▸ Re-verified OPEN 2026-08-14: the
   one-side-moved branch (`sync_diff.py:3589-3598`) still emits mechanical
   `propagate_preamble` unconditionally; only the neither-moved branch
   (`:3560-3570`) has the carried-divergence check. Original plan: `sync_diff.py:2560-2599`: extend the
   carried-divergence check from the neither-moved branch to the one-side-moved
   branch, matching the cell path at `:1025`.
7. **Y7 — rename+edit.** ▸ Re-verified OPEN 2026-08-14: `_pool_side_deficit`
   (`sync_diff.py:1050-1071`) still skips non-`pos:` base entries (`:1058`), and
   no rename+edit drop-to-cold path exists. Original plan: `sync_diff.py:1149-1227`, `:565-586`: `_pool_side_deficit`
   counts only `pos:` base entries, so an id-keyed base cell missing on one side
   never triggers `stamp_vs_new`. Widen it, and make rename+edit drop to cold
   rather than emitting `mirror_remove` + `copy_new_shared`.
8. **Y8** ▸ Re-verified OPEN 2026-08-14: the lone-candidate claim
   (`sync_diff.py:2422-2423`) still requires no content affinity (it remains
   framed downstream, so misleading rather than silent). Original plan:
   require content affinity before `_align_pool`'s lone-candidate claim
   (`:1693-1697`).
9. **Y9** ▸ Re-verified OPEN 2026-08-14: `record` (`sync_v3.py:460-592`) still
   never consults the diff — no pending-framed warning; the `confirm` path
   (`doc_apply.py:1584-1601`) still has no byte-divergence rejection for a
   shared companion member. Original plan: — hardening: warn when `record` runs on a deck with pending framed
   items; reject `confirm` on a byte-diverged shared companion member.

**Landmines**
- Reproduction scripts for Y1–Y5 were written during the review but not kept.
  Rebuild them as **regression tests** — each finding's scenario is stated
  precisely enough in the review doc to reconstruct.
- Inline∘extract is **not** byte-identity (recorded in project memory). Do not
  "fix" a spurious diff by assuming it is.
- `apply` writes files *before* the structural verify, so a gate failure leaves
  files mutated and the ledger unsaved. Y7's strip-id variant can *create* that
  state. Consider whether the write should move after the verify — but treat that
  as its own change with its own testing, not a drive-by.

**Acceptance**: each of Y1–Y8 has a regression test that fails before the fix;
`record` and `verify` provably agree on the same projected pair; course repos
migrated to the new decision format.

**Notes from item 1 — Y2 + D8 (2026-07-26)**

- **There are two correct gates, not one, and the plan's "fix all four call
  sites" would have broken `clm harvest`.** The two sync sites (`record`,
  `apply`) must project; the two harvest sites must **not**. A harvest write
  lands narration on one language side, and proposal §6 *requires* that
  one-sided member to be recorded — it is what makes the next sync report frame
  the twin as `translate_new`. Measured: a one-sided **id'd** companion member
  projects to an `id-asymmetry` error, so projecting at
  `harvest_accept._record_members` would have withheld exactly that ledger
  entry, breaking `test_one_sided_create_with_record_frames_translate_new`.
  `harvest verify`'s docstring had said so all along ("the corruption misreading
  harvest must avoid — §6"); the sizing note in this plan missed it.
  **The drift-by-call-site worry was real, though**, so the fix is two *named*
  functions living next to each other in `sync_verify.py` —
  `gate_projected_pair` and `gate_deck_halves` — each documenting when it is
  correct, with the projection itself behind one shared `projected_pair`. A
  boolean parameter was rejected: the call site should read as a choice.
- **Which shapes projection actually changes** (measured, not reasoned — the
  probe is trivial to rebuild): a **byte-diverged shared** companion cell →
  `unify` error (this is Y2's exact shape: raw gate clean, projected not); a
  **one-sided id'd** narrative member → `id-asymmetry`; a duplicated companion
  `(slide_id, role)` → `duplicate-id`. An **id-less** one-sided companion cell
  is clean both ways (`unify_texts` degrades gracefully), which is why the
  existing `test_one_sided_companion_is_not_a_structural_error` was never
  evidence that the gate was fine.
- **A projection *refusal* had to become a gate error, and that is a second
  hole the finding did not name.** For a mixed / cross-language layout, or a
  companion whose `for_slide` matches no slide, `project_pair` refuses and
  returns the *raw* texts — so a gate that just used them reached a clean
  verdict by not looking. `gate_projected_pair` emits an error-severity
  `companion-refusal` instead. **Do not assume the v3 lens already stops these**:
  measured with id'd cells, `load_bundle` *parses* all three (the layout checks
  at `doc_lenses.py:937-957` are `observe`, not `refuse`), so before this change
  `record` banked them.
- **`verify` reports the same refusal as a *warning*, deliberately.** Its
  contract is "did an edit corrupt this pair?" and an unprojectable *layout* is
  not that; the gate's question is "may I record this as verified?", which it may
  not, having been unable to read the narration. This is the one place gate and
  verify differ, and it is stated in both docstrings.
- **The escape hatch is scoped by construction, not by discipline.**
  `--allow-diverged-companion` drops only violations the *raw* halves do not
  also show, keyed on `(kind, slide_id, role)` rather than the message — a
  `unify` message carries line numbers that inlining shifts, and matching on it
  would have let the flag override a pre-existing deck-half corruption. It is
  therefore provably not a `--force`, and the cross-language fixture pins that
  (its refusal is overridable, its id-asymmetry is not). The WARNING log lives
  inside the gate, so no call site can forget it.
- **Re-measured the blast radius against the final semantics** (the earlier
  sweep predated the refusal-as-error decision): 723 PythonCourses + 340
  CppCourses + 0 CSharpCourses split pairs, **0 newly failing**, 229 projecting
  differently, **0 projection refusals anywhere**. Note the earlier sweep's
  "956" counted `.claude/worktrees/` copies inside the course repo — 5801 of the
  6524 `*.de.*` files under PythonCourses are nested worktrees. **Exclude
  `.claude` when sweeping a course repo**, or you measure the same deck a dozen
  times.
- **The "fails before the fix" evidence is in the tests, permanently.** Each
  blindness test asserts `structural_gate` over the *raw* halves is empty in the
  same breath as asserting the strict gate is not — i.e. the old behaviour is
  pinned beside the new one, rather than demonstrated once by reverting the
  source and then lost.

---

### Phase 3a — Repo-supplied executables (S5, pulled forward)  ▸ STATUS: S5 DONE 2026-07-26; S4/S8 ride-alongs not started

**Why it is here.** Pulled out of Phase 4 by the maintainer on 2026-07-26. S5 is
the same finding class as Phase 0's cassette RCE — content that arrives with a
course repo causing code execution on a normal `clm` invocation — and Phase 0
established that class as real and exploitable in this codebase. The original
phase order was set by risk × independence *before* that was known.

**Work** — item 4 of Phase 4, verbatim; see it for the file references. The
open sub-decision (§6 item 1) still stands: recommendation is that **repo-local
config may not set executable paths at all** (user/system config only), falling
back to an allowlist if that proves too restrictive. Same treatment for
`notebook_kernel_python`, plus the scheme allowlist for spec-supplied
`repository_base` before it reaches `git` (`ext::` executes its argument).

**Ride-alongs, if and only if they stay cheap**: S4 (the `.clm-include`
`rmtree`) and S8 (MCP containment) are adjacent and both amount to reusing an
existing correct normalizer. If either grows, split it back out to Phase 4
rather than letting this phase sprawl. **Not** done with S5 — they stayed out to
keep the security change reviewable in one sitting, and neither is blocked.

**Notes from S5 (2026-07-26)**

- **The sub-decision splits, and the corpus is what splits it.** "Repo-local
  config may not set executable paths" is right for
  `external_tools.{plantuml_jar,drawio_executable}` — nothing uses them from a
  repo — but **PythonCourses commits `clm.toml` with `[jupyter] kernel_python =
  ".venv"`**, and it is load-bearing: it is what makes a *globally* installed clm
  run the notebook kernel in the repo's own venv (where the ML stack lives).
  Banning that would have broken the maintainer's primary course repo on the
  first build. Read a course repo's `clm.toml` before restricting a config tier.
- **And `kernel_python` closes nothing anyway.** It only takes effect when a
  Direct-mode kernel executes *that same repo's* notebook code on the host — i.e.
  in the one situation where arbitrary repo code already runs with the same
  privileges. An attacker gains nothing by redirecting the interpreter that was
  about to run their notebook. The tool paths are different: they fire on diagram
  conversion, on the host, regardless of output target or worker mode, and
  regardless of whether a single notebook is executed. **That asymmetry is the
  argument for the split, and it is worth keeping in mind for the rest of Phase 4
  — "content chooses a program" is only interesting where the content was not
  already going to be executed.**
- **`ext::` is two layers, deliberately.** `clm.core.remote_url.validate_remote_url`
  rejects spec-derived URLs (error names the element), and every git invocation
  now carries `-c protocol.ext.allow=never` — which covers URLs that never pass
  through a spec, e.g. one hand-edited into an output repo's `.git/config`.
  Neither layer alone is enough. Watch the ordering: the `-c` options must precede
  the git subcommand, which is why they are prepended in `run_git`/`run_git_global`
  rather than appended.
- **A `::` check has to come before any scp-like fallback.** `ext::sh -c '…'`
  parses as "host `ext`, path `:sh -c …`" under a naive `host:path` rule, so the
  helper syntax must be refused first. Also: a **single-letter** scheme is a
  Windows drive (`C:\repos\x`), so the bare-scheme pattern requires ≥2 characters.
- **Two existing tests asserted the vulnerable behaviour** (`test_toml_file_loading`
  and `test_all_config_options` both set `plantuml_jar` from a project-tier
  `clm.toml`). They were rewritten to cover a non-restricted key, with the
  restriction and its operator-tier escape hatches pinned in
  `tests/infrastructure/test_repo_supplied_executables.py`. Expect this shape when
  removing a capability: the test suite documents what *was* true.

---

### Phase 4 — Filesystem containment & secrets  ▸ STATUS: not started (S5 pulled forward to Phase 3a) ▸ TRACKED: #798

**Goal**: content and config from a course repo cannot reach outside the paths
CLM owns, and secrets stay out of logs and commits.

**Work**

1. **S4 — `.clm-include` `rmtree`.** `src/clm/cli/commands/course/sync_includes.py:429-436`:
   validate `as_path` before use. `Path.__truediv__` discards the left operand
   when the right is absolute — that is the whole bug. The correct normalizer
   already exists for spec `<include>` paths (`course_spec.py:88-140`): normalize
   separators, reject absolute, reject any `..` part, then `resolve()` and confirm
   containment under `topic_dir`. **Reuse it; do not write a second one.**
2. **S8 — MCP containment.** `src/clm/mcp/tools.py:939-942`: `_resolve_under` is
   a bare join. Lift the correct implementation from
   `src/clm/web/studio/service.py:131-141` (resolve-then-parents-membership —
   symlink-correct and not spoofable by a sibling prefix) and apply it to every
   mutating handler (`:536`, `:612-614`, `:668`, `:734`, `:777`, `:864-866`,
   `:924-926`, `:1554`). Apply to read-only handlers too: they hand file contents
   to a model, which is a prompt-injection exfiltration path.
3. **S11 — spec-driven writes.** Sanitize `<dir-group><name>` and `<subdir>`
   (`src/clm/core/dir_group.py:75-84,94`) the way section names already are —
   the asymmetry is an oversight, not a design. Validate `<output-target><path>`
   beyond emptiness. Make `sanitize_file_name` reject `.` and `..`
   (`text_utils.py:56-70`). Consider requiring a marker file (e.g.
   `.clm-manifest.json`) before the sweep will empty a directory — a
   one-character typo (`<path>.</path>`) currently reaches the sweep.
4. **S5 — repo-supplied executables.** ▸ **moved to Phase 3a** (pulled forward
   2026-07-26 — same class as the Phase 0 RCE). Kept here as the reference text.
   `config.py:936-947` discovers config from
   inside the course repo, and `drawio_converter.py:14,47` runs the resulting
   path with no validation. **Open sub-decision** (recommendation:
   repo-local config may not set executable paths at all — those come from user
   or system config only; if that proves too restrictive, require an explicit
   allowlist or an interactive confirmation). Same treatment for
   `notebook_kernel_python` (`kernel_env.py:165`). Separately, add a scheme
   allowlist for spec-supplied `repository_base` before it reaches `git` —
   Git's `ext::` transport executes its argument (`git.py:215,796`).
5. **S10 + D7 — Docker.** `worker_executor.py:314-341`: mount `/source`
   read-only for the notebook worker; keep read-write for PlantUML/DrawIO (the
   comment at `:340` explains why they need it). Add a non-root `USER` to the
   images and verify generated-image ownership still works. Fix the three
   verified gaps in the `course.py:340` whole-volume guard: the `len(resolved)==1`
   early return skips it, `data_dir` has no guard at all, and
   `_build_has_docker_notebook_worker` returns `False` on any exception.
6. **S9 — cassette scrubbing.** `cassette_format.py:72-74`: add `api-key`
   (Azure OpenAI), `x-goog-api-key` (Gemini), `proxy-authorization`,
   `x-amz-security-token`, `x-auth-token`; add `key`, `access_token`, `apikey`,
   `subscription-key`, `X-Amz-Signature` to query filtering; match content-type
   by prefix so `application/json; charset=utf-8` is filtered; **add a
   response-side filter** (`addon.py:459-465` passes no filter hook, so
   `Set-Cookie` and OAuth token bodies are committed verbatim).
   - **Landmine**: this changes recorded bytes. The golden pin test and existing
     cassettes need re-recording — plan it as part of the change.
   - Also move the mitmproxy CA **private key** out of the course-repo working
     tree (`build.py:313-316`), or ignore it explicitly. `umask_secret()` is a
     no-op on Windows, and with `CLM_JOBS_DB_PATH=Z:\…` the key lands on a share.
7. **S12 — secrets in config output.** `config.py:504,600,692`: make the LLM key,
   Auphonic key and OBS password `SecretStr`. `clm config show --json` is exactly
   what gets pasted into bug reports and agent transcripts. Add `--reveal` if you
   need the values. Also `chmod 0600` the Google refresh token
   (`cohort_calendar/google_sync.py:236-238`).

**Acceptance**: a spec or ledger containing `..` or an absolute path cannot write
or delete outside the output root; MCP tools refuse paths outside `data_dir`; a
recorded cassette contains no Azure/Gemini key and no `Set-Cookie`;
`clm config show --json` shows no cleartext secret.

---

### Phase 5 — Job-queue correctness  ▸ STATUS: not started ▸ TRACKED: #799
**Depends on**: Phase 1 (T1's resurrected tests cover exactly this code).

**Goal**: no job runs twice, no build interferes with another, no corrupted
output enters the cache. Treat C5+C3+C4+C2 as **one coherent change** — fixing
any one alone leaves the others able to produce the same outcome.

**Work**

1. **C5 — fencing.** `job_queue.py:340-386` and `worker_routes.py:149`: add
   `AND worker_id = ? AND status = 'processing'` to status updates. This is the
   guard that turns C3/C4 from corruption into merely wasted work.
2. **C3 — liveness before reset.** `job_queue.py:650-670`: do not reset a
   `processing` job to `pending` without checking the owning worker is actually
   dead. Runs on every build start *and* end, both default-on, so this fires
   routinely.
3. **C4 — scope orphan marking.** `job_queue.py:428-467`: restrict to the
   session's own worker ids. The docstring's safety argument holds only
   single-session; the shared DB makes it false.
4. **C2 — heartbeat while processing.** `worker_base.py:645-790`: heartbeat from
   within `process_job` or a side thread. Then scope `cleanup_stale_workers`
   deletions by session (`pool_manager.py:279-294` currently hard-DELETEs with no
   ownership filter — the #594/#595 fix covered reuse counting, not deletion).
5. **C6 — content-verify cache hits.** `sqlite_backend.py:484-493`: existence is
   not equality. Store the output's own digest in `result_metadata` and compare,
   or re-hash on hit.
6. **C7 — dead-worker recovery.** `start_monitoring` is only called from a
   `__main__` demo (`pool_manager.py:1228`), so `_cleanup_dead_worker_jobs` can
   never fire during a build and the 5-second cleanup poll is dead code. Wire
   monitoring into the build path — **but fix C8 first or it will mass-kill
   workers on day one.**
7. **C8 — timezone.** `pool_manager.py:941-943` compares a UTC `CURRENT_TIMESTAMP`
   against local `datetime.now()`; on a CEST host every heartbeat reads ~7200s
   stale. `discovery.py:183` does it correctly — converge on that.
8. **C9** — treat `cancelled` and attempts-exhausted as terminal in
   `wait_for_completion`; scope `cancel_jobs_for_file` to the cancelling build.
9. **C10** — offload blocking SQLite off the uvicorn loop in `worker_routes.py:88-192`;
   make `_can_replay_from_cache` probe existence without unpickling a multi-MB
   BLOB on the build's event loop.
10. **C11** — use `atomic_write_bytes` (temp + `os.replace` + retry) for worker
    output writes (`notebook_worker.py:264`), matching the host path. This is the
    amplifier that makes C3 durable.
11. **C12** — close every thread's connection in `JobQueue.close()`, not just the
    caller's; otherwise `wal_checkpoint(TRUNCATE)` silently no-ops on Windows
    (issue #144's symptom via a different door).

**Acceptance**: a job cannot be claimed twice under a forced-interleaving test;
one build's start/teardown provably cannot alter another session's jobs; a
deliberately torn output file is rejected rather than cached.

---

### Phase 6 — Cross-machine coordination via the worker API (D5)  ▸ STATUS: not started ▸ TRACKED: #800
**Depends on**: Phase 2 (auth + coordinator-mode binding) and Phase 5 (the queue
semantics being replicated must be correct first).

**Goal**: replace the D6 stopgap. One machine owns the jobs DB file; every other
machine goes through the API. No SQLite file is ever opened over a network share.

**Sketch** (design work is part of this phase, not settled here):
- Coordinator mode: the owning machine binds per D2 with a token; participants
  are configured with its URL.
- All queue operations used cross-machine must exist as API routes with the same
  atomicity guarantees the local `BEGIN IMMEDIATE` path provides.
- Failure modes need explicit answers: coordinator unreachable mid-build,
  coordinator restart with in-flight jobs, participant crash.
- Remove the Phase 0 journaling stopgap once this lands, and re-enable WAL for
  the (now always local) DB file.

**Landmine**: this makes the worker API load-bearing for build correctness, not
just convenience. Its test coverage must be real before it carries this weight —
which is another reason Phase 1 precedes everything.

---

### Phase 7 — Re-layering prerequisites (D11)  ▸ STATUS: DONE 2026-08-06 (wiring proof = the delivering PR's own CI run) ▸ TRACKED: #801

**Landed as** (all four items; acceptance met — the golden suite passed
twice in a row on unchanged code): (1) `tests/e2e/test_e2e_golden_build.py`
— double-build byte-identity over test-spec-1 (rich) and test-spec-3
(minimal), on the `--snapshot`/`--verify-against` harness; the #681 replay
round trip covers the http-replay course. (2)
`tests/test_architecture_contracts.py` — the 50-edge layer-violation
RATCHET over the FULL documented stack (file-level, both-direction,
lazy-import-aware, string-import-guarded; the round-2 review added the
infrastructure→workers and workers→extensions edges its round-1 inventory
missed; Phase 8's shrinking checklist), the Backend-surface pin (incl. the honest A11
ladder shape: LocalOpsBackend is itself partially abstract), and the
worker payload schema pins. (3) `tests/build/test_pipeline_unmocked.py`
— T3: real Course + real SqliteBackend + temp DB, data-only stage flow
and a real PlantUML worker round-tripping a job through the queue, in
the fast suite (~15 s). (4) `scripts/check_coverage_floor.py` + a CI
unit-job step flooring `build.py`, `course.py`, `path_utils.py`, the
backends.

**Hard gate**: no Phase 8 commit lands until all four items below are complete
and green. This is the maintainer's explicit condition on D10.

1. **Golden end-to-end build characterization suite.** Reference courses built
   end-to-end, full output trees snapshotted, asserted byte-identical across a
   refactor.
   - **Prerequisite within the prerequisite — RESOLVED 2026-07-25.** This
     originally read "the known build nondeterminism must be fixed first". That
     was written from a stale memory index line; the two sources it named
     (jupytext `cell_metadata_filter` set-ordering, and the output-path race
     between a static image and a PlantUML render) were already fixed in PR #76
     back in 2026-05. **Correcting the record**: re-measuring did surface a
     *third*, still-live source, now fixed in PR #661 — generated images that
     are committed to the course repo were misclassified as static and copied
     in stage 1, racing the DrawIO/PlantUML conversion that overwrites the same
     source path. Two `--ignore-cache` builds of `test-spec-1` differed in 24 of
     288 files before that fix; they are byte-identical after it.
   - **Do not re-derive this from memory — measure.** The harness already
     exists and is what found the third source: `clm build SPEC --snapshot DIR
     --ignore-cache`, then `clm build SPEC -o OTHER --verify-against DIR
     --ignore-cache`. It skips `.html` by default (live-kernel execution output
     is inherently nondeterministic — `random.*`, ASLR object reprs) and
     normalizes hex addresses under `--include-html`. Run it before building
     goldens; goldens over nondeterministic output produce flapping tests,
     which get muted, which returns you to Phase 1's problem.
   - **Sub-task, do this first: derive the generated-image ignore set.** The
     build renders diagrams *into the source tree*
     (`ImageFile.img_path` → `<topic>/img/<stem>.png`), so a build leaves the
     course repo dirty and any golden that also snapshots the *source* tree sees
     churn. Output is deterministic regardless — this is only about which files
     a golden should look at.
     Do **not** solve it with a hand-maintained skip list, and do **not** wait
     for the directory split in #664. The generated set is already computable
     exactly from the course model: the union of `CourseFile.source_outputs`
     over the course's files. That is the same authoritative signal PR #661 used
     to fix the stage misclassification, so reusing it keeps one definition of
     "generated" rather than two that can drift apart. Small change, no repo
     migration, unblocks the goldens immediately.
   - **Accepted design, not a defect**: the build writes into the source tree at
     all — diagram renders, HTTP cassettes, sync ledgers. That is deliberate; it
     is what lets another machine rebuild from the same network traces and
     ledger state. Do not "fix" it by making builds read-only w.r.t. source
     without a decision from the maintainer; it would break the cassette and
     ledger workflows. The narrower, agreed improvement is #664 — stop the build
     and the human sharing one directory — which is **scheduled after this phase
     precisely because a golden suite makes it verifiable** (a correct move
     produces a byte-identical output tree).
2. **Layer-boundary contract tests.** Pin what core exposes, what a backend must
   implement, and what the worker Pydantic boundary accepts — *before* moving
   anything beneath them. These stay valuable afterward as the executable
   statement of the new design.
3. **Real unmocked build-pipeline tests in the fast suite (T3).** Replace the
   fully-mocked `main_build` coverage (`tests/cli/test_build_command.py:1159-1278`
   currently stubs `Course`, both backends, `BuildReporter`, `init_database`, and
   makes `execution_stages()` return `[]`). Exercise real stage sequencing,
   backend wiring and job submission against a temp DB.
   - Keep the fast suite fast. If it grows past roughly two minutes, people
     start bypassing the pre-push hook, and the gate stops existing.
4. **Coverage threshold on the modules being moved.** `build.py`, `course.py`,
   `path_utils.py`, the backends. Set the bar in CI.
   - **Landmine**: this codebase already contains tautological tests
     (Phase 1/T4), so a coverage number can be met without safety existing.
     Pair the threshold with a mutation-testing spot-check, or at minimum review
     the new tests for assertions that can actually fail.

**Acceptance**: all four in place; the golden suite passes twice in a row on
unchanged code (proving determinism) before it is trusted as a refactor gate.

---

### Phase 8 — Full re-layering (D10)  ▸ STATUS: **COMPLETE** — A1–A12 all DONE (A12 2026-08-07), ratchet 50 → 0, import-linter + private-import + optional-dependency guards enforced ▸ TRACKED: #802

**Goal**: the architecture the docs describe. Work strictly in dependency order,
one PR per step, golden suite green after each.

1. ✔ **A2 — move `build_data_classes.py` and `error_categorizer.py` out of
   `clm.cli`** into infrastructure — **DONE 2026-08-06, PR #804**. Both modules
   now live in `clm.infrastructure`; `strip_ansi` moved to
   `clm.infrastructure.utils.text_utils`; backends type their reporter against
   the structural `BuildReporterProtocol` (beside the data classes) instead of
   the CLI's `BuildReporter`. Killed every infrastructure→cli edge AND the only
   core→cli edge (ratchet 50 → 42).
2. ✔ **A6 — extract the domain vocabulary from `path_utils`** into `clm.core` —
   **DONE 2026-08-06, PR #805**. The whole course-domain vocabulary (not just
   the six named symbols — also the skip/ignore tables+predicates, slide
   family detection, image dir constants, prog-lang mapping) moved to a new
   `clm.core.utils.path_utils`; `clm.infrastructure.utils.path_utils` keeps
   only `find_project_root` + `atomic_write_all`/`atomic_write_bytes`. No
   re-export shims — all ~54 importers retargeted. Cleared the 11 core files
   whose only infrastructure import was path_utils (ratchet 42 → 31).
3. **A1/A3 — break the remaining cycles**: core's module-level infrastructure
   imports (`core/course.py:53-56`, `course_file.py:8-10`, `dir_group.py:9-10`,
   all of `core/operations/`), core's imports of `clm.workers` and of extension
   modules (including spec parsing depending on `clm.slides.sidecar_layout` at
   `course_spec.py:2599`).
   **Design pass DONE 2026-08-06** —
   `docs/claude/design/phase8-a1-a3-core-decoupling.md` decomposes the 31
   remaining edges into six PR-sized steps (S1 vocabulary descents → S2
   contract descent → S3 cassette relocation → S4 identity inversion → S5
   slide-text model descent → S6 = A11 import-linter), with edge accounting,
   risks, and four maintainer questions. **Maintainer approved S5 as proposed
   2026-08-06** (slide-text model into core — wanted for future
   generalization anyway). **S1 DONE 2026-08-06** (PR #808) — leaf vocabulary
   descents, ratchet 31 → 19 edges over 17 files; the lazy-import canary now
   pins process_notebook's core→slides entry (survives until S5).
   **S2 DONE 2026-08-06** — contract seam (Operation, Backend ABC,
   core.messaging payload package, File, copy-data, build_data_classes,
   build_profiling) descended into core; ratchet 19 → 5 edges over 4 files
   (exactly the S3/S4/S5 residue). `clm.infrastructure`'s lazy
   Backend/Operation exports stay as a compatibility surface, now pointing
   at core. **S3 DONE 2026-08-06** — cassette staging maintenance moved off
   `Course` into `infrastructure.http_replay_mitm.cassette_staging`
   (functions take `Course.http_replay_canonical_paths()`; sweeping is the
   entry points' job — build pre-stage hook + watch-mode FileEventHandler);
   `http_replay_cassette` moved workers → infrastructure. Ratchet 5 → 4
   edges over 3 files. **S4 DONE 2026-08-06** — worker-image identity
   inverted through the `clm.core.worker_identity` registry;
   infrastructure records identities and registers the singleton fallback
   provider (eager at image_identity import + lazy via
   `clm.infrastructure.__init__`). Ratchet 4 → 1 edge. **S5 DONE
   2026-08-06** — the slide-text model (slide_parser, raw_cells,
   anchor_primitives, pairing, voiceover_merge) descended into
   `clm.core.slide_text`; ~72 importers retargeted. **Ratchet EMPTY —
   the documented architecture exists in the import graph.**
   **S6 (= A11) DONE 2026-08-06** — import-linter contracts in
   pyproject `[tool.importlinter]` (layers core < infrastructure <
   workers; constrained layers never import cli or extensions), wired
   into CI's lint job and pre-commit; the inventory ratchet is replaced,
   `tests/test_architecture_contracts.py` keeps the string-import guard
   + Backend/payload pins. **A1/A2/A3/A4/A5/A6/A10/A11 are complete** (A10:
   architecture.md rewritten 2026-08-06 — see item 10 below; A4/A5: see
   items 5 and 6). **A7 DONE 2026-08-07** — see item 7 below.
   A8 DONE 2026-08-07 — see item 8 below. **A9 DONE 2026-08-07** — see
   item 9 below. **A12 DONE 2026-08-07** — see item 11 below; Phase 8 is
   complete.
   LANDMINE learned on #809: the Phase 7
   coverage-floor list (`scripts/check_coverage_floor.py`) keys by path
   suffix and runs only in CI's unit job — move the floor entry in the
   same commit as any floored file, and check floors locally via
   `pytest -m "not slow and not integration and not e2e and not docker"
   --cov=src/clm --cov-report=xml` + the script.
4. **A11 — import-linter contract in CI.** Add it as soon as the first contract
   is true, not at the end — each subsequent step then cannot regress.
5. ✔ **A4 — extract build orchestration** from `cli/commands/build.py` into a
   callable API — **DONE 2026-08-06**. New constrained layer `clm.build`
   (above `clm.workers` in the layers contract; forbidden from cli/extensions
   like the other constrained layers): `config` (BuildConfig + the
   `resolve_*` option family, raising typed `BuildOptionError`), `engine`
   (`run_build(config) -> BuildSummary | None` — the ex-`main_build`
   orchestration), plus `reporter`, `output_formatter`, `output_sweep`,
   `git_dir_mover`, `text_utils` moved from `clm.cli`. The Click command is
   now a thin adapter (~980 lines from 2,808): flag parsing/resolution,
   `.env` loading, signal handlers, `setup_logging` (the engine never
   configures logging), typed-error → Click-error conversion, exit-code
   policy, snapshot/verify, and the injected watchdog `watch_runner`
   (watch mode is CLI-only). `clm.cli.main`'s compat exports retarget to
   `clm.build`. Coverage floors added for `build/engine.py` (75) and
   `build/config.py` (88); `cli/commands/build.py` re-floored at 74
   (measured 78.6 post-split). Golden suite byte-identical; fast suite
   9,517 green.
6. ✔ **A5** — move voiceover merge/propagation logic out of
   `cli/commands/voiceover.py` into `clm.voiceover` — **DONE 2026-08-07**.
   New `clm.voiceover.autopilot` (the `merge_notes`/propagation apply flow,
   `polish_notes`, `require_slide_ids` raising `MissingSlideIdError`, display
   helpers — rich-console printing kept in the extracted layer per the A4
   precedent) and `clm.voiceover.overrides`
   (`load_transcript_override`/`load_alignment_override` raising
   `OverrideError`; callers convert to their own error surface). Private
   seams replaced with public ones: `langfuse_configured`
   (`infrastructure.llm.client`) and `decode_alignment` (`voiceover.cache`).
   `mcp/tools.py` now imports `clm.voiceover.overrides` instead of a CLI
   command's private helpers (shrinks A9), and `_expand_video_args` moved to
   the shared `cli/commands/_video_args.py` (`expand_video_args`) so
   `harvest.py` no longer privately imports from `voiceover.py`.
   `cli/commands/voiceover.py` is a thin adapter for these paths
   (~2,780 lines from 3,606); no re-export shims, all importers retargeted.
7. ✔ **A7 — unify the three config mechanisms** — **DONE 2026-08-07**. Most
   of the finding had already been retired by the config/CLI/env-unification
   proposal (`docs/proposals/config-cli-precedence-unification.md`, PRs
   #498–#509: `resolve_setting`, the inert `ClmConfig` sections wired to
   real consumers via host-resolve-and-inject, duplicate spellings hard-cut)
   and by A4 (the `_resolve_*` family became the public, documented
   flag > env > default resolvers in `clm.build.config`). The close-out PR:
   ONE shared `[tool.clm]` reader (`core/utils/pyproject_settings.py`, used
   by both `sidecar_layout` and the LLM cache-dir resolver — `[tool.clm]`
   stays core-side because layering forbids core → infrastructure);
   sidecar-layout surfaced in `clm config show`/`locate` with provenance
   (`describe_layout`); a full audit of every raw `os.environ` read with the
   residual bypasses wired through `ClmConfig` (`[external_tools] mitmdump`
   — also added to `PROJECT_FORBIDDEN_KEYS`; `[jupyter]
   cell_timeout_seconds`/`replay_cell_timeout_seconds` — injected into BOTH
   worker modes, fixing the Direct/Docker asymmetry; `[git] token_auth`;
   `CLM_MAX_WORKER_STARTUP_CONCURRENCY` hard-cut in favour of
   `startup_parallel`); and the three-channel config model documented in
   `configuration.md` ("How the pieces fit together") + `architecture.md`.
   Deliberately NOT changed: worker-side reads of host-injected vars,
   per-invocation build options (no config-file tier by design), secrets
   (env-only), core-layer escape hatches (`CLM_MAX_CONCURRENCY`,
   `CLM_OUTPUT_DEDUP_HASH_LIMIT_MB`), worker-API bind vars (A8 territory),
   and the settled `CLM_MAX_WORKERS` short spelling.
8. ✔ **A8** — one name and one default for the jobs-DB path — **DONE
   2026-08-07**. The env-name contract lives in `worker_base`
   (`JOBS_DB_PATH_ENV_VAR` = `CLM_JOBS_DB_PATH`, `resolve_jobs_db_path()`,
   `missing_jobs_db_error()`): the executor injects the host-resolved
   absolute path under the host's own name, and the four worker modules
   resolve it with **no default** — the container default `/db/jobs.db` is
   gone, and a SQLite-mode worker without the variable exits with a clear
   error instead of silently polling a freshly created empty queue (Docker
   workers get `CLM_API_URL` and never open the DB; the Dockerfiles never
   set `DB_PATH`, so the old default was dead weight in production).
   Ride-along: `CACHE_DB_PATH` → `CLM_CACHE_DB_PATH` (same two-name
   pattern, notebook worker only; default `clm_cache.db` kept — a wrong
   cache path degrades to cache misses, not silent no-work).
   `process_reaper._read_worker_env` reads the new name with a legacy
   `DB_PATH` fallback so `clm workers reap` still matches workers that
   survived an upgrade; bare `DB_PATH` added to the conftest env-isolation
   fixture.
9. ✔ **A9 — remove the cross-module underscore-private imports** — **DONE
   2026-08-07**. An AST scan (not grep — multiline parenthesized imports)
   found 30 offender import-sites across 18 files, vs the review's ~12.
   Every shared seam got a public name in its defining module, no
   underscore aliases kept (A5 precedent): `build_client`
   (infrastructure.llm.client — 6 importing modules + 7 patching test
   files), `summaries_by_hash` (export/context — the review's worst item,
   `mcp/tools.py` → CLI private; made a documented public seam rather
   than relocated, since `mcp/tools.py` already imports the module's
   public renderers), `atomic_write_text` (http_replay_cassette — the
   `cassette_format.atomic_write_lf` twin stays self-contained because
   the mitmdump interpreter cannot import clm), `git_toplevel`,
   `group_paths_into_units`, `twin_ids_for`, `lines_sans_id`,
   `is_shared_cell`, `apply_slide_ids`, `format_exit_failure`,
   `parse_checks`/`print_human_readable`/`raise_on_findings`/
   `result_to_dict` (validate_slides + validate_spec), `build_course`
   (recordings web). Two imports dissolved into dedups instead:
   `raw_cells.is_cell_boundary`'s delegating wrapper became a re-export
   of the now-public canonical predicate in `slide_parser` (kills the
   two-copies drift risk the multi-language investigation flagged), and
   `output_spec._is_in_workshop` (a verbatim copy of
   `core.workshop_scope.is_in_workshop`, unused in its own module) was
   deleted with `notebook_processor` retargeted to core. Normalizer's
   `_RawCell`/`_reconstruct`/`_split_raw_cells` "privates" were private
   *aliases* of public `raw_cells` names — `voiceover_tools` now imports
   the real home. Enforcement:
   `tests/test_architecture_contracts.py::TestPrivateImportGuard` AST-scans
   src/clm and fails on any `from clm.x import _name` (dunders exempt;
   a private *module* stays importable within its own package subtree,
   e.g. `cli/commands/_export_shared.py`).
10. ✔ **A10 — rewrite `architecture.md`** to describe what now exists —
    **DONE 2026-08-06**. Full rewrite: the enforced three-constrained-layer
    stack with cli/extensions unconstrained on top, the contract seam in
    core, the import-linter + contract-test enforcement story, honest
    orchestration attribution (`main_build` in `build.py`, A4 pending),
    the WAL/network journal-mode policy, config pointed at
    `configuration.md` with the A7/A8 non-uniformity named, and a "Known
    Deviations and Pending Work" section listing A4/A5/A7/A8/A9/A12 so the
    doc cannot silently describe an end state that hasn't arrived. Every
    specific wrongness the review listed (fictional "service registry",
    layer diagram matching no import structure, undocumented env vars,
    orchestration attributed to `Course.process()`, "DELETE journal mode",
    core↔infrastructure claims) is corrected or removed.
11. ✔ **A12** — move `docker`, `fastapi`, `uvicorn`, `watchdog` behind extras
    with lazy imports; `DummyBackend` moves to `tests/` — **DONE 2026-08-07**.
    New extras `[docker]` (SDK + fastapi/uvicorn for the host-side Worker API
    server — one extra because Docker mode needs all three) and `[watch]`
    (watchdog for `clm build --watch`); `[web]` and `[recordings]` carry their
    own server stacks; `[all]` includes the new extras. Lazy seams:
    `pool_manager` imports `api.server` only in `_start_worker_api_server`
    (with `ensure_docker_worker_deps()` gates in `start_pools` /
    `_get_or_create_executor` raising the pip hint), the build command
    pre-flights watchdog on `--watch` as a UsageError, and `clm serve` /
    `clm recordings serve` name their extras on ImportError.
    `DummyBackend` → `tests/dummy_backend.py` (`from tests.dummy_backend
    import DummyBackend`). Teeth:
    `TestOptionalServerDependencyContract` in
    `tests/test_architecture_contracts.py` imports the core surface in a
    subprocess with docker/fastapi/uvicorn/watchdog **and starlette**
    blocked (starlette because `infrastructure/web_security.py` would
    otherwise leak a fastapi transitive into a core chain). CI/nightly
    `uv sync` lines gained `--extra docker --extra watch`.

**Landmine**: if Phase 8 stalls partway — a real risk for a project this size —
the *docs must not* describe the end state as if it arrived. Update A10
incrementally so the doc is always true of the current code.

---

## 5. Finding coverage map

Every finding from the review, and where it is handled. IDs match
`docs/claude/adversarial-review-2026-07-24.md`.

| Finding | Phase | Finding | Phase | Finding | Phase |
|---|---|---|---|---|---|
| S1 | 0 | C1 | 0 (interim) → 6 (final) | Y1 | 3 |
| S2 | 2 | C2 | 5 | Y2 | 3 ✔ DONE |
| S3 | 2 | C3 | 5 | Y3 | 3 |
| S4 | 4 (3a if cheap) | C4 | 5 | Y4 | 3 |
| S5 | **3a** (pulled fwd) | C5 | 5 | Y5 | 3 |
| S6 | 2 | C6 | 5 | Y6 | 3 |
| S7 | 2 | C7 | 5 | Y7 | 3 |
| S8 | 4 (3a if cheap) | C8 | 5 | Y8 | 3 |
| S9 | 4 | C9 | 5 | Y9 | 3 |
| S10 | 4 | C10 | 5 | A1 | 8 |
| S11 | 4 | C11 | 5 | A2 | 8 |
| S12 | 4 | C12 | 5 | A3 | 8 |
| T1 | 1 | T5 | 1 | A4 | 8 |
| T2 | 1 | T6 | 1 | A5 | 8 |
| T3 | 7 | T7 | 1 | A6 | 8 |
| T4 | 1 | T8 | 1 | A7 | 8 |
| T9 | 1 | T10 | 1 | A8 | 8 |
| | | | | A9 | 8 |
| | | | | A10 | 8 |
| | | | | A11 | 8 |
| | | | | A12 | 8 |

**Deliberately accepted, not fixed**: billion-laughs XML expansion in spec
parsing (local hang/OOM only, LOW for a local tool); cassette response-header
CRLF replay into the kernel's HTTP client (noted under S9 — fix opportunistically
if touching that code).

---

## 6. Open sub-decisions (for the implementing session)

Small enough to decide in flight; recommendations given, but flag them in the PR
so the maintainer can object:

1. **S5 scope** — recommendation: repo-local config may not set executable paths
   at all (user/system config only). Fall back to an allowlist if too restrictive.
2. **S11 sweep marker** — recommendation: require `.clm-manifest.json` (or
   equivalent) before the sweep will empty a directory.
3. ~~**D8 override flag name**~~ — **settled and shipped**: the flag is
   `--allow-diverged-companion` on both `record` and `apply`, and it is not only
   *named* narrowly, it *is* narrow — it drops only the violations the companion
   projection introduced, so it cannot act as a `--force`.
4. **D3 cache migration** — recommendation: bump the cache version so old pickled
   entries are ignored, rather than writing a converter for a cache that
   regenerates itself.

---

## 7. Related documents

- `docs/claude/adversarial-review-2026-07-24.md` — findings, evidence,
  reproduction details. The authority for *why* each item is here.
- `AGENTS.md` — branch/commit/PR conventions, changelog fragments, the info-topic
  maintenance rule.
- `docs/developer-guide/releasing.md` — Phase 0's release procedure.
- `docs/developer-guide/architecture.md` — rewritten 2026-08-06 (Phase 8/A10)
  to describe the enforced post-#802 layering; its "Known Deviations" list is
  fully resolved (A12 landed 2026-08-07) and stays as the home for future
  deviations.
- `docs/developer-guide/caching.md`, `docs/developer-guide/testing.md` — context
  for Phases 5 and 1 respectively.

### Work spun out of this plan

- **#664** — separate generated diagram images from hand-authored assets in the
  source tree. Grew out of the Phase 7 nondeterminism investigation but is *not*
  a prerequisite: the golden suite only needs the derived ignore set (Phase 7
  item 1). #664 is deliberately sequenced **after** this phase, because a
  byte-comparable output tree is what makes the migration verifiable. Do not
  pull it forward.
