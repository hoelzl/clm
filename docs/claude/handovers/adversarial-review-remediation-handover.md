# Adversarial Review Remediation — Handover

**Created**: 2026-07-24 | **Status**: Phases 0–1 DONE; Phase 2 next | **Owner**: unassigned

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

### Phase 2 — Network-facing security  ▸ STATUS: not started
**Depends on**: Phase 1 (you are about to change Docker-mode networking and the
cache format; the worker tests must be alive first).

**Goal**: no CLM service is reachable, or actionable, by a party that has not
been explicitly granted access.

**Work**

1. **S2 + D2 + D3 — worker API.** `src/clm/infrastructure/api/server.py:22-23`,
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
2. **S3 + D4 — recordings dashboard.** `src/clm/recordings/web/routes.py:567-602`:
   apply the containment check its own sibling `/open-explorer` (`:794-804`)
   already implements — `resolve(strict=True)` + `relative_to(root)` — before
   `/process` submits anything. Then add origin checking to every mutating route
   and `TrustedHostMiddleware` to the app.
   - **Landmine**: `/arm` accepts `course_slug` and reaches `get_state_path`
     unfiltered (verified: `get_state_path('../../../evil')` escapes). Sanitize
     it here, not only in the generic path fix in Phase 4.
3. **S6 + D4 — `clm serve`.** `src/clm/web/app.py:127-136`: default
   `cors_origins` to the serve origin (never `["*"]` with
   `allow_credentials=True`). `src/clm/web/api/websocket.py:110-126`: require the
   bearer token **before** `accept()`, and validate channel names against a known
   set. Add `TrustedHostMiddleware`.
   - WebSockets bypass CORS entirely — this is why the token check must precede
     `accept()` rather than sitting on the HTTP routes.
4. **S7 — studio render.** `src/clm/web/studio/render.py:55-71` →
   `jinja2.SandboxedEnvironment`. Fix `esc()` in
   `src/clm/web/static/studio/app.js:45-47` to escape quotes (its output is
   interpolated into an `href`). Stop accepting `?token=` after the initial
   exchange (`auth.py:78-80`) — it currently lands in QR deep links, browser
   history and uvicorn access logs.
   - Update the module docstring: it currently advertises a "no-execution" tier
     that executes. Either the claim or the behavior has to change; this makes
     the claim true.

**Acceptance**: a cross-origin page cannot drive any mutating route on either
app; `/ws` rejects an unauthenticated connection before accept; the worker API
refuses a non-loopback bind without a token; no pickle remains on any API or
cache path; SSTI proof-of-concept from the review no longer executes.

---

### Phase 3 — Sync engine correctness  ▸ STATUS: not started
**Depends on**: Phase 1 (sync unit coverage is the best in the repo — keep it
that way and extend it here).

**Goal**: no path silently destroys authored content. Order within this phase
matters: **D8 first**, because the gate is what creates the divergent baselines
the other bugs consume.

**Work**

1. **Y2 + D8 — the ledger write gate.** `src/clm/cli/commands/slides/sync_v3.py:239-250`
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
2. **Y1 — `mirror_remove` divergence guard.** `src/clm/slides/sync_diff.py:1203-1215`
   and `:1911-1922`: a two-sided base with `entry.de_fp != entry.en_fp` must
   downgrade `mirror_remove` from MECHANICAL to a framed `remove_vs_edit` /
   `pending_divergence`. This mirrors the guard the edit paths already have at
   `:992/1025` and `:1979` — reuse that predicate rather than writing a second
   one.
3. **Y3 + D9 — decision keying.** `src/clm/slides/doc_apply.py:100-115`,
   `:199-200`, `:1296-1347`: keys become key+action so each framed question is
   answered independently.
   - **Breaking.** Update in lockstep: `src/clm/cli/info_topics/` (the sync
     topic), the downstream agent docs in the course repos, and any saved
     decision documents. Per `AGENTS.md`, a stale info topic makes downstream
     agents produce wrong output — treat it as part of the fix, not follow-up.
   - Add a clear rejection message for old-format keys pointing at the migration,
     rather than silently accepting them (which would preserve the bug).
4. **Y5 — trust-gate `stamp_twin_id`.** `sync_diff.py:417-435`: do not emit a
   mechanical id stamp for a member whose pairing came from
   `pair_positionally` (`doc_lenses.py:505-522`) and is not ledger-known at that
   pairing. Frame it instead. Since P2 makes the id *the* identity, a wrong stamp
   is the worst corruption in the system and it currently requires no judgment.
5. **Y4 — bind decisions to fingerprints.** A decision should carry the moved
   side's fingerprint from the report it answered; on mismatch, reject with
   "re-run report" rather than applying a stale translation and recording it as
   verified (`doc_apply.py:1399`).
6. **Y6 — preamble divergence guard.** `sync_diff.py:2560-2599`: extend the
   carried-divergence check from the neither-moved branch to the one-side-moved
   branch, matching the cell path at `:1025`.
7. **Y7 — rename+edit.** `sync_diff.py:1149-1227`, `:565-586`: `_pool_side_deficit`
   counts only `pos:` base entries, so an id-keyed base cell missing on one side
   never triggers `stamp_vs_new`. Widen it, and make rename+edit drop to cold
   rather than emitting `mirror_remove` + `copy_new_shared`.
8. **Y8** — require content affinity before `_align_pool`'s lone-candidate claim
   (`:1693-1697`).
9. **Y9** — hardening: warn when `record` runs on a deck with pending framed
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

---

### Phase 4 — Filesystem containment & secrets  ▸ STATUS: not started

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
4. **S5 — repo-supplied executables.** `config.py:936-947` discovers config from
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

### Phase 5 — Job-queue correctness  ▸ STATUS: not started
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

### Phase 6 — Cross-machine coordination via the worker API (D5)  ▸ STATUS: not started
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

### Phase 7 — Re-layering prerequisites (D11)  ▸ STATUS: not started

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

### Phase 8 — Full re-layering (D10)  ▸ STATUS: blocked on Phase 7

**Goal**: the architecture the docs describe. Work strictly in dependency order,
one PR per step, golden suite green after each.

1. **A2 — move `build_data_classes.py` and `error_categorizer.py` out of
   `clm.cli`** into infrastructure. Mechanical, and it alone kills the
   infrastructure→CLI cycle (`dummy_backend.py:7`, `local_ops_backend.py:36`,
   `sqlite_backend.py:874/998/1698/1804`, `worker_base.py:758`,
   `progress_tracker.py:356`, `db_operations.py:336`).
2. **A6 — extract the domain vocabulary from `path_utils`** (`Lang`, `Format`,
   `Kind`, `OutputSpec`, `output_specs()`, `output_path_for()`) into `clm.core`.
   43 files import this module; most of the core↔infrastructure cycle flows
   through it.
3. **A1/A3 — break the remaining cycles**: core's module-level infrastructure
   imports (`core/course.py:53-56`, `course_file.py:8-10`, `dir_group.py:9-10`,
   all of `core/operations/`), core's imports of `clm.workers` and of extension
   modules (including spec parsing depending on `clm.slides.sidecar_layout` at
   `course_spec.py:2599`).
4. **A11 — import-linter contract in CI.** Add it as soon as the first contract
   is true, not at the end — each subsequent step then cannot regress.
5. **A4 — extract build orchestration** from `cli/commands/build.py` (2694 lines;
   the Click command starts at :2071 — everything before it is the engine) into a
   callable core API. This is what unlocks programmatic builds for MCP, the web
   studio and tests. Highest risk step; do it last and lean on the golden suite.
6. **A5** — move voiceover merge/propagation logic out of
   `cli/commands/voiceover.py` into `clm.voiceover`, removing the private-symbol
   imports (`_langfuse_configured`, `_decode_alignment`).
7. **A7** — unify the three config mechanisms (`ClmConfig`, `sidecar_layout`'s
   `[tool.clm]` pyproject reader, and raw `os.environ` in 36 files including
   `build.py`'s `_resolve_*` family).
8. **A8** — one name and one default for the jobs-DB path (`CLM_JOBS_DB_PATH`
   host-side vs bare `DB_PATH` defaulting to the container path `/db/jobs.db`
   worker-side).
9. **A9** — remove the ~12 cross-module underscore-private imports, worst being
   `mcp/tools.py:391` → a CLI command's private helper.
10. **A10 — rewrite `architecture.md`** to describe what now exists.
    **Do this incrementally as each step lands**, not at the end. The doc is
    currently wrong in ways that actively mislead agents (a "service registry"
    directory holding one file, a layer diagram matching no import structure,
    undocumented env vars, orchestration attributed to `Course.process()`), and
    per `AGENTS.md` agents are told to trust it.
11. **A12** — move `docker`, `fastapi`, `uvicorn`, `watchdog` behind extras with
    lazy imports; `DummyBackend` (`dummy_backend.py:19`, never instantiated in
    `src/`) moves to `tests/`.

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
| S2 | 2 | C2 | 5 | Y2 | 3 |
| S3 | 2 | C3 | 5 | Y3 | 3 |
| S4 | 4 | C4 | 5 | Y4 | 3 |
| S5 | 4 | C5 | 5 | Y5 | 3 |
| S6 | 2 | C6 | 5 | Y6 | 3 |
| S7 | 2 | C7 | 5 | Y7 | 3 |
| S8 | 4 | C8 | 5 | Y8 | 3 |
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
3. **D8 override flag name** — something that reads as dangerous at a glance;
   `--allow-diverged-companion` rather than `--force`.
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
- `docs/developer-guide/architecture.md` — **currently inaccurate**; Phase 8/A10
  fixes it.
- `docs/developer-guide/caching.md`, `docs/developer-guide/testing.md` — context
  for Phases 5 and 1 respectively.

### Work spun out of this plan

- **#664** — separate generated diagram images from hand-authored assets in the
  source tree. Grew out of the Phase 7 nondeterminism investigation but is *not*
  a prerequisite: the golden suite only needs the derived ignore set (Phase 7
  item 1). #664 is deliberately sequenced **after** this phase, because a
  byte-comparable output tree is what makes the migration verifiable. Do not
  pull it forward.
