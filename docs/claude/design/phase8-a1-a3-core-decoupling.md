# Phase 8 A1/A3 — Breaking core's remaining cycles (design)

**Status**: PROPOSED (design pass for #802 steps A1/A3; no code moved yet)
**Date**: 2026-08-06
**Prereqs landed**: A2 (PR #804), A6 (PR #805) — ratchet at 31 edges over 23 files
**Authority for findings**: `docs/claude/adversarial-review-2026-07-24.md` §4;
inventory pinned in `tests/test_architecture_contracts.py::KNOWN_LAYER_VIOLATIONS`

## 1. What remains, and why it is not one problem

After A2/A6 the 31 remaining forbidden edges decompose into four mechanisms,
not one. Each edge below was classified from the actual import statements
(file:line, enclosing scope, symbols — not from the review's summaries):

1. **The contract seam lives one layer too high.** Core's `Course`, every
   `CourseFile` subclass, `DirGroup` and all of `core/operations/` import
   `Operation`/`NoOperation`/`Concurrently`, the `Backend` ABC, `File`,
   `CopyFileData`/`CopyDirGroupData`, the Pydantic payload family
   (`infrastructure.messaging.*`), `correlation_ids`, `build_profiling`, and
   (since A2) `build_data_classes`. These are **contracts and data** — the
   `Backend` ABC's only fields are two *core* registries; the messaging
   package has zero clm imports outside itself; `build_profiling` is
   stdlib-only. This accounts for 17 of the 21 `core -> infrastructure`
   edge-files.
2. **Shared vocabulary parked in upper layers.** Small, pure modules that
   core/workers/infrastructure all need but that live in `clm.slides`,
   `clm.notebooks`, or `clm.workers`: prog-lang tables, slide tags, the
   workshop detector, the sidecar-layout resolver, comment-token resolution,
   the `no-compile` header marker, diagram-tool locators, file hashing for
   the JupyterLite manifest, the HTTP-replay trace leaf, and the voiceover
   companion path family.
3. **Code sitting in core that is not core's job.** `Course`'s two
   mitmproxy-cassette staging methods: every live entry point is CLI-layer
   (`build.py:2093/1424`, watch mode via `file_event_handler`), and
   `build.py:1424` already reaches into the private `_sweep…` cross-module
   (an A9 offender). The machinery they call (`http_replay_cassette`) is
   itself downward-clean (deps: `infrastructure.http_replay_mitm.vcr_format`
   + a dependency-free trace module).
4. **One genuine inversion.** Worker-image identity
   (`infrastructure/workers/image_identity.py`) is real infrastructure —
   Docker/binary fingerprinting over `infrastructure.config` and the diagram
   tool locators — but core operations must stamp the identity into payloads
   at build time (`process_notebook.py:132`, `convert_{drawio,plantuml}:27`).
   Core needs the *value*, not the machinery.

## 2. Target end-state

The documented stack, made real: `core` imports **nothing** clm-internal
above it; `infrastructure` imports only `core`; `workers` import
`core`/`infrastructure`; extensions and the CLI are unconstrained. Concretely:

- **core** = domain model (Course/Section/Topic/CourseFile/DirGroup), the
  **contract seam** (Operation hierarchy, `Backend` ABC, payload schemas,
  copy-data, `File`, build reporting data classes + `BuildReporterProtocol`),
  shared vocabulary (path/prog-lang/tags/markers/sidecar layout), and
  dependency-free diagnostic leaves (`build_profiling`, `http_replay_trace`).
- **infrastructure** = engines: SqliteBackend/LocalOpsBackend, job queue and
  DB, worker management, mitm/cassette machinery, error categorization,
  image-identity fingerprinting.
- **workers** = execution: notebook/plantuml/drawio/jupyterlite processors,
  plus the C++ code analysis/emission they run.

## 3. The six steps (one PR each, strict order, golden suite green after each)

Edge accounting below is against the current 31-entry ratchet; each step
deletes its entries from `KNOWN_LAYER_VIOLATIONS` in the same commit, and
updates the affected `architecture.md` claims (A10 is incremental, per the
handover's landmine note).

### S1 — Leaf vocabulary descents (mechanical; clears 12 edges → 19)

All stdlib-only (or core-only) modules/closures, moved to their honest layer,
importers retargeted, **no re-export shims** (A2/A6 precedent):

| What | From → To | Unblocks / clears |
|---|---|---|
| `prog_lang_utils` (183 ln, typing-only) | `workers/notebook/utils/` → `core/utils/` | prerequisite for `comment_token_for_path`; sits beside A6's `EXTENSION_TO_PROG_LANG` family |
| `comment_token_for_path` (14 ln) | `notebooks/slide_parser.py` → `core/utils/prog_lang_utils.py` | `core -> notebooks`: `cmake_export`, `process_notebook`; ~25 importer files retarget |
| `tags.py` (84 ln, zero imports) | `slides/` → `core/` | `workers -> slides`: `jupyter_utils` |
| `workshop_scope.py` (108 ln) | `slides/` → `core/` | `workers -> slides`: `output_spec` (keeps the "one workshop detector" single-sourced) |
| `cpp_code_analysis` (542 ln) + `cpp_code_emitter` (194 ln), both stdlib-only | `slides/` → `workers/notebook/` | `workers -> slides`: `notebook_processor`; `slides.validator` imports back downward (legal) |
| `diagram_tools.py` (55 ln) | `workers/` → `infrastructure/utils/` | `infrastructure -> workers`: `image_identity` — **this is the ratchet's lazy-import canary; repoint the pin in `test_the_scanner_sees_lazy_imports` at a surviving lazy-only resident in the same commit** |
| no-compile marker trio (`has_no_compile_marker`, `_has_header_marker`, `_NO_COMPILE_MARKER_RE`) | `slides/validator.py` → core (small `core/deck_markers.py`) | `core -> slides`: `cmake_export` (which then has **zero** forbidden imports and stays in core) |
| `sidecar_layout.py` (125 ln, os+pathlib+tomllib) | `slides/` → `core/` | `core -> slides`: `course.py:293`, `course_spec.py:2606`. A7 note: this is one of the three config mechanisms; descending it now neither helps nor hurts A7 |
| voiceover companion **path family** (`COMPANION_SUBDIR`, `companion_name`, `companion_locations`, `resolve_companion`, plus `companion_path`/`expected_companion` to keep the family whole; ~90 ln, pathlib-only) | `slides/voiceover_tools.py` → `core/voiceover_companions.py` | `core -> slides`: `notebook_file.py:128`; ~15 importer sites |
| `http_replay_trace.py` (324 ln, **zero clm imports**) | `workers/notebook/` → `core/` | `core -> workers`: `process_notebook.py:146`, plus `course.py:638` (fully cleared by S3) |
| `sha256_of_file` + `collect_notebook_tree(s)` (39 ln closure) | `workers/jupyterlite/lite_dir.py` → `core/utils/` | `core -> workers`: `build_jupyterlite_site` (with the version pin below) |
| `JUPYTERLITE_CORE_VERSION` | `workers/jupyterlite/builder.py:32` → beside the JupyterLite payload schema (core, once S2 lands — in S1 park it in the same core utils module as the hashing closure) | the pin feeds a payload field and the cache key; the builder's three *private* uvx sibling pins stay put |

Rationale for `http_replay_trace` and `build_profiling` (S2) living in core:
they are dependency-free diagnostic leaves consumed on both sides of the
core/infrastructure seam; core is the only layer everyone may import. The
alternative (thread trace config through payload-construction parameters from
the CLI) buys purity at the cost of a five-hop parameter relay.

### S2 — Contract descent (mechanical but wide; clears 14 edges → 5)

Move, with `git mv`, importers retargeted, no shims:

- `infrastructure/operation.py` → `core/operation.py`
- `infrastructure/backend.py` → `core/backend.py` (ABC + `JobsPendingTimeoutError`)
- `infrastructure/messaging/` → `core/messaging/` (whole package incl.
  `correlation_ids`; verified: no clm imports outside the package)
- `infrastructure/utils/file.py`, `copy_file_data.py`, `copy_dir_group_data.py` → `core/utils/`
- `infrastructure/build_data_classes.py` → `core/build_data_classes.py`
- `infrastructure/build_profiling.py` → `core/build_profiling.py`

Notes:
- **`build_data_classes` moves a second time** (cli → infrastructure in A2,
  now → core). A2 followed the handover's letter and killed the
  infrastructure→cli cycle ratchet-first; the contract descent reveals the
  final home. Two mechanical hops beat one speculative one — but this is
  called out so the churn is a decision, not an accident.
- The three contract pins in `tests/test_architecture_contracts.py`
  (`TestBackendContract`, `TestWorkerPayloadContract`) retarget their imports;
  **surfaces and schemas are unchanged** — payloads cross the worker boundary
  as JSON, so a module path change never touches the wire.
- `error_categorizer` stays in infrastructure (it imports `JobQueue`).
- Biggest retarget surface: `infrastructure.messaging.*` importers across
  workers/infrastructure/cli (comparable to A6's 54 files; same playbook).

### S3 — Cassette staging leaves `Course` (clears 1 edge → 4)

- `workers/notebook/http_replay_cassette.py` → `infrastructure/http_replay_mitm/`
  (deps are already `vcr_format` + the trace leaf; worker-side importers
  `notebook_processor`/`cassette_doctor` retarget downward, legal).
- `Course.merge_mitmproxy_cassette_staging` and
  `Course._sweep_orphan_cassette_staging_files` become functions in the same
  infrastructure module, taking the course as an argument. Call sites move to
  the callers that already own the lifecycle: `build.py` (pre-build sweep +
  post-build merge — removing an A9 private-symbol cross-call) and
  `file_event_handler` (watch mode, replacing the `process_file` self-call).
  `Course.process_all`'s self-call is dead weight — no caller in `src/`.
- Clears `core -> workers: core/course.py`; `course.py` is then fully clean.

### S4 — Worker-identity inversion (the one real hook; clears 3 edges → 1)

New `core/worker_identity.py`: the effective-identity registry —
`set_effective_worker_identities(dict)`, `register_fallback_provider(fn)`,
`effective_worker_image_identity(worker_type) -> str`.

- `infrastructure/workers/image_identity.py` keeps ALL fingerprinting
  (`worker_image_identity_for`, Docker/binary probing) and registers itself
  as the fallback provider at import; its `set_effective_worker_identities`
  forwards to the core registry. `clm build` is unchanged.
- Core call sites (`process_notebook.py:132`, `convert_{drawio,plantuml}:27`)
  read the core registry.
- **Import-order risk** (the step's main one): the fallback provider only
  exists once `infrastructure...image_identity` is imported. The build path
  imports it transitively; `clm cache explain` (`cache.py:388` calls bare
  `op.payload()`) must import it explicitly — pin that with a test.
  Unregistered fallback = the pre-#744 "direct" degradation, never an
  exception.
- `process_notebook`'s `worker_image_identity_for` re-export
  (`# noqa: F401`, no external importers found) is dropped.
- Rejected alternative: a `worker_image_identity()` method on the `Backend`
  contract. It fails exactly where the registry succeeds — payload
  construction without a backend (cache explain) — and widens the pinned
  Backend surface for one string lookup.

### S5 — Slide-text model descends to core (the D-level decision; clears the last edge → 0)

`ProcessNotebookOperation.payload` merges voiceover companions into the deck
at payload time (`merge_voiceover_text`). Its closure is ~421 lines and pulls
a cone of four modules — `slides/raw_cells` (111), `notebooks/slide_parser`
(449, stdlib-only leaf), `slides/anchor_primitives` (265), `slides/pairing`
(374) — **all pure text/logic, zero infrastructure deps, and `pairing`
already imports core**. Recommendation: descend the cone as a core subpackage
(working name `clm/core/slide_text/`: `slide_parser`, `raw_cells`,
`anchor_primitives`, `pairing`, `voiceover_merge`), and let
`slides.voiceover_tools` (~24 back-call sites), the sync engine, and the
authoring tools import it from core.

Why not a merge hook (core defines a callback, `clm.slides` registers the
merger)? Because the merge is **mandatory** for a correct build — a hook the
build breaks without is a hard dependency wearing a costume, plus
registration-order fragility for zero conceptual gain. The slide text model
(cells, anchors, families, narration placement) *is* course-domain
vocabulary; core owning it is the honest reading, and it is what makes
`NotebookFile`/`ProcessNotebookOperation` legitimately core.

Risk: `pairing`/`anchor_primitives` are load-bearing for the sync engine
(see the sync-arc memory landmines). This step changes **import paths only,
zero logic**, but must gate on the full sync test surface + golden suite, and
lands as its own PR so a revert is one commit.

### S6 — A11: import-linter contract in CI (the ratchet collapses)

With the inventory at zero: add `import-linter` (dev dep) with layer
contracts matching `_forbidden_targets()`, wire it into the lint CI job and
pre-commit, and shrink the ratchet test to the scanner-based *guards*
(string-import dodge, lazy-import visibility) plus an empty inventory — or
retire it in favor of the linter if the maintainer prefers one enforcement
point. The handover already frames the ratchet as collapsing into A11.

## 4. Sequencing rationale

S1 before S2 keeps each PR reviewable and lets S2's moved modules land in a
core that already owns their vocabulary deps. S3–S5 each depend on S1's
leaves (trace, companions) or S2's seam (payload family). S4 before S5 only
because it is smaller; they are independent. A4 (build-orchestration
extraction) is explicitly out of scope here but S2 is its enabler: once the
contract seam is core, a callable core build API stops being a layering lie.
`cmake_export` stays core (post-S1 it is clean) and remains a CLI post-build
step — A4 must not fold it into the core API.

## 5. Risks and mitigations (cross-step)

- **Wide mechanical retargets** (messaging in S2, `slide_parser`/
  `comment_token_for_path` in S1/S5): same playbook as A6 — AST-partitioned
  rewrite, ruff/mypy, fast suite, golden suite byte-identical, ratchet delta
  exact. The golden suite has now caught two refactors' worth of nothing —
  that is the point.
- **Docker/worker version skew**: payloads are JSON on the wire; module moves
  cannot break a lagging worker. The payload-schema pin stays the guard.
- **Monkeypatch string targets**: each step greps for `"clm.` string
  references (A6 found one in `test_build_command`); patch targets follow the
  moved modules.
- **The ratchet's own canary**: S1 clears the pinned lazy-import resident
  (`image_identity`); the pin must be repointed in the same commit (surviving
  candidates: `core -> slides: core/course_spec.py` until S1 lands it, then
  pick from the post-S1 inventory — recompute, don't guess).
- **Stall risk**: if the sequence stops partway, every completed step left
  the tree consistent, the ratchet smaller, and `architecture.md` truthful
  for what landed (A10 incremental rule).

## 6. Open questions for the maintainer (recommendations inline)

1. **S5 shape**: descend the slide-text cone into core (recommended), or
   keep it in extensions behind a mandatory-hook inversion (rejected above)?
   This is the only step that moves >1000 lines and touches sync-adjacent
   modules; a veto here reshapes S5 only — S1–S4 and S6 stand alone.
2. **C++ analysis home** (S1): `workers/notebook/` (recommended: it is
   execution-path transformation; keeps core lean) vs `core` (stdlib-only,
   so also legal).
3. **`core/messaging` naming** (S2): keep module names under a new package
   root (recommended) vs renaming (`core/payloads`?). Pure bikeshed; the pin
   tests don't care.
4. **S6 enforcement**: import-linter *replaces* the ratchet inventory test
   (recommended: one source of truth) vs both.

## 7. What this deliberately does not do

- No behavior changes anywhere; every step is import-topology only (S3 moves
  two methods verbatim; S4 preserves the identity fallback semantics).
- No `DummyBackend` relocation (A12), no config unification (A7), no
  `build.py` orchestration split (A4), no doc rewrite beyond the per-step
  A10 increments.
