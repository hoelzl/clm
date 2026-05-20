# Handover: PythonCourses Revamp — Next Steps (CLM side)

CLM-resident tracking doc for the work requested in
`docs/proposals/PYTHON_COURSES_REVAMP_NEXT_STEPS_2026-05-19.md`. That
proposal was drafted from the PythonCourses side and enumerates five
priorities that block the slide-format-redesign migration's remaining
phases. This handover captures the CLM-side status of each, the
canonical source documents to read first, decisions made during
implementation, and the signal-back convention to PythonCourses.

The PythonCourses-side handover at
`<PythonCourses>/docs/handover-slide-format-redesign-clm.md` (referenced
throughout the proposal) is **read-only context** from this side — it
documents what PythonCourses consumes from each CLM phase. It is not
checked into the CLM repo.

## 0. Status at a Glance

| Priority | Scope | Status | PR / Branch |
|---|---|---|---|
| 3 | Phase 4 coverage walker: recognize `workshop-…` slide_id as opener | **Shipped** | [#98](https://github.com/hoelzl/clm/pull/98), branch `claude/coverage-workshop-slide-id-opener` |
| 1 | `assign-ids` extraction expansion (#89) — prose + AST + sibling + LLM fallback | **Shipped** (CLM phases 1–4); issue #89 closed; P5 = PC-side corpus rerun, pending CLM release | [PR #101](https://github.com/hoelzl/clm/pull/101) merged 2026-05-19, master commit `c820fb8` |
| 2 | Phase 7 `clm slides sync` (cross-language LLM sync, `SyncCache`) | **v1 + v2 + v2 `--apply --trivial` shipped**: v1 via [PR #105](https://github.com/hoelzl/clm/pull/105) (master `4d1c645`); v2 walker via [PR #110](https://github.com/hoelzl/clm/pull/110) (master `bdca1c9`); `--apply --trivial` follow-up on branch `claude/slide-format-redesign-phase-7-v2-trivial` ([PR #114](https://github.com/hoelzl/clm/pull/114)). Direction auto-detection and 3-way merge still deferred. | PRs #105, #110 merged 2026-05-20; [PR #114](https://github.com/hoelzl/clm/pull/114) opened 2026-05-21 |
| 4 | Close hoelzl/clm#95 once PythonCourses confirms clean snapshot/verify | Awaiting PC confirmation (CLM-side fully shipped via PRs #96 + #112) | — |
| 5 | `http-replay-skip` tag (deck-side chained-LLM-call escape hatch) | **Do not start** — gated on PythonCourses decision | — |

## 1. Source-of-Truth Pointers

Read these before touching any file. This handover deliberately does
**not** duplicate their content.

| Topic | Canonical doc |
|---|---|
| Priorities overview (PC perspective) | [`docs/proposals/PYTHON_COURSES_REVAMP_NEXT_STEPS_2026-05-19.md`](../proposals/PYTHON_COURSES_REVAMP_NEXT_STEPS_2026-05-19.md) |
| Priority 1 design (full) | [`docs/proposals/ASSIGN_IDS_EXTRACTION_EXPANSION.md`](../proposals/ASSIGN_IDS_EXTRACTION_EXPANSION.md) |
| Priority 1 tracking issue | [hoelzl/clm#89](https://github.com/hoelzl/clm/issues/89) |
| Priority 2 design (from PC handover §3) | PythonCourses repo: `docs/handover-slide-format-redesign-clm.md` §3 Phase 7 |
| Priority 4 fixed PRs | merged via `08726876` ("fix(http-replay,snapshot): allow_playback_repeats + spec-target snapshots (#95)") and `4dff779` ("fix(build): unify --output-dir layout with --snapshot (per-target re-root)", PR #112) |
| Priority 5 background | PythonCourses repo: `docs/proposals/CLM_ISSUE_DRAFT_snapshot_replay_2026-05-19.md` §C |

## 2. Priority 1 — assign-ids extraction expansion (issue #89)

**Goal:** clear ~95% of the 407 missing-ID warnings on
`slides/module_550_ml_azav/` so PythonCourses Phase B can be declared
complete and the slide-format-redesign downstream phases (split-source
parity, coverage walker promotion to error) can run cleanly.

**Status:** All four CLM-side phases **shipped** via
[PR #101](https://github.com/hoelzl/clm/pull/101), merged to master
2026-05-19 as commit `c820fb8` (single bundled PR — the proposal's
3-PR split was deemed unnecessary at review time). Issue #89
auto-closed by the merge. Phase 5 is PythonCourses-side and waits on
the next CLM release.

### Phase tracker

- [x] **Phase 1** — first-prose-line extractor in `clm.slides.headingless` (`content:prose`)
- [x] **Phase 2** — code-cell AST extractor in new `clm.slides.code_cell_extract` (`content:code:class|def|assign|import|call`)
- [x] **Phase 3** — sibling-pair asymmetry fix in `_handle_slide` (`content:sibling-…` / `sibling-heading`)
- [x] **Phase 4** — `--llm-suggest` fallback on NON_EXTRACTABLE (`source = "llm"`)
- [ ] **Phase 5** — corpus rerun + documentation updates; bump CLM pin in PythonCourses *(PC-side; pending the next CLM release that includes #89)*

P1+P2 alone clear ~95%; P3+P4 close the long tail. The proposal doc
recommends doing all four together since "they're naturally one design
discussion" — but they CAN ship in separate PRs if Phase 1 is wanted on
its own first.

### What the proposal doc already settles

- Extractor precedence (heading → bullet → numbered → bold → img_alt → prose; code-cell only when `cell_type == "code"`).
- Backward compatibility invariant: cells that succeed today produce the **same** slug.
- One stale claim in the issue body has been corrected (proposal §2.5): the "`--llm-suggest` is short-circuited by `--accept-content-derived`" gap is NOT real. Only the "LLM never fires on hard refusals" half is a real gap — implement only that half of issue-body Priority 4.

### Open decisions before starting

None. The proposal answers all four phases concretely.

### Branch convention

Branch off master per the project's PR convention:
`claude/assign-ids-extraction-expansion` (single branch covering all
phases), or one branch per phase if the user prefers granular PRs.

## 3. Priority 2 — `clm slides sync` (Phase 7)

**Goal:** cross-language sync helper that proposes edits from one
language file to its companion (e.g. after editing `<deck>.de.py`,
propose corresponding edits to `<deck>.en.py`), backed by an LLM call
gated by a `SyncCache` table on `(de_hash, en_hash, prompt_version)`.

**Status:** v1 **shipped** via [PR #105](https://github.com/hoelzl/clm/pull/105),
merged to master 2026-05-20 as commit `4d1c645`. The PythonCourses-side
`handover-slide-format-redesign-clm.md` §3 Phase 7 is still the
canonical spec for the full feature; this section tracks what v1
shipped versus what's deferred to v2.

### Spec summary (from PC handover §3, repeated in the proposal)

**Modes:**
- `--dry-run` (default): show proposed diffs, no writes.
- `--interactive`: walk diffs one-by-one with apply / skip / edit.
- `--apply --trivial`: write proposed diffs without prompting; only with `--trivial` (mechanical typo fixes, identical-structure `keep` drift).

**Implementation notes:**
- New `src/clm/slides/sync.py`.
- New `src/clm/infrastructure/llm/sync_prompts.py`.
- Extend `src/clm/infrastructure/llm/cache.py` with a `SyncCache` class reusing the same SQLite file as `CoverageCache` (separate table).
- Register `sync` subcommand under `slides_group` in `src/clm/cli/main.py` via a new `src/clm/cli/commands/slides_sync.py`.
- Tests: `tests/infrastructure/llm/test_sync_cache.py`, `tests/slides/test_sync.py`, `tests/cli/test_slides_sync.py`.

### Decisions made at kickoff

- **`--source-lang` is REQUIRED in v1.** No auto-detection from git
  history yet; the user explicitly tells us which side was edited.
  Auto-detection is on the v2 list.
- **3-way merge handling — flag-and-defer-to-manual.** When both DE
  and EN halves drift from the cached snapshot, v1 surfaces a
  structural-issue error rather than asking the LLM to merge. v2
  may add LLM-suggested merge once pilot data shows how often this
  case fires.
- **Pilot instrumentation present in v1.** `SyncResult` exposes
  per-session counters (`pairs_visited`, `pairs_in_sync`,
  `pairs_proposed`, `pairs_error`, `cache_hits`). Accept/skip/edit
  counters will be added in v2 alongside the `--interactive` walker.
- **Roles synced: markdown slide/subslide + voiceover/notes.** Code
  cells are intentionally excluded — they're shared by design across
  split companions, and the Phase-6 validator enforces byte-equality.
- **SyncCache row stores `(direction, proposal_json)`.** The
  direction is stored *in the value* so a single (de_hash, en_hash)
  pair can produce different proposals depending on which side was
  edited. When `--source-lang` flips, the cached entry is bypassed
  (cache only honors entries matching the requested direction).
- **Cell-pairing strategy: `(slide_id, role)`-keyed source-order
  zip.** Multiple cells per slide_id+role pair up positionally. Count
  mismatches surface as structural-issue errors; one-sided slide_ids
  surface as warnings.

### Phase tracker

- [x] Scaffold `clm.slides.sync` module + `SyncCache` table.
- [x] LLM prompt scaffolding (`sync_prompts.py`) + `--dry-run` diff producer.
- [x] `--interactive` apply/skip/edit walker. *(v2, [PR #110](https://github.com/hoelzl/clm/pull/110) merged 2026-05-20, master `bdca1c9`)*
- [x] Pilot accept-rate counters (`pairs_accepted` / `pairs_skipped` / `pairs_edited` / `pairs_quit`) + `SyncSnapshotCache`. *(v2, PR #110)*
- [ ] `--apply --trivial` path. *(follow-up PR)*
- [ ] Direction auto-detection from git history. *(follow-up PR)*
- [ ] LLM-assisted 3-way merge for "both sides drifted" cells. *(follow-up PR)*
- [x] Pilot instrumentation (per-session counters).
- [x] CLI registration + `clm info commands` update.
- [x] Tests (cache + sync logic + CLI smoke + walker + snapshot cache).

### Branch convention

`claude/slide-format-redesign-phase-7` (v1, merged via PR #105). `claude/slide-format-redesign-phase-7-v2` (v2, merged via PR #110).

## 4. Priority 3 — workshop-`workshop-…` slide_id opener (DONE)

Shipped 2026-05-20 via PR #98 (branch
`claude/coverage-workshop-slide-id-opener`, commit `2628a03`).

**Change:** `clm.slides.workshop_scope.find_workshop_ranges` now
recognizes a slide/subslide markdown cell whose `slide_id` starts with
`workshop-` as a workshop opener, equivalent to the legacy `workshop`
tag. Backward compatible.

**Impact verified:** on
`slides/module_550_ml_azav/topic_055_prompt_templates/slides_010_prompt_templates.py`,
12 workshop-internal slide-starts (announcement + setup + 4 tasks ×
DE/EN) now sit inside the workshop range; 34 pre-workshop lecture pairs
remain coverage-checked.

**Files touched:**
- `src/clm/notebooks/slide_parser.py` — `Cell.slide_id` passthrough property added.
- `src/clm/slides/workshop_scope.py` — `_CellLike` protocol extended; new `_is_workshop_opener` helper.
- `tests/slides/test_workshop_scope.py` — 6 new tests.
- `CHANGELOG.md` — Fixed/Changed entries.

**Note on scope:** the nbformat-side `find_workshop_ranges` in
`src/clm/workers/notebook/output_spec.py` was intentionally **not**
extended — task slides must still render fully in build output.

## 5. Priority 4 — close hoelzl/clm#95 (housekeeping)

**Status:** Both sub-issues fully shipped on the CLM side:
- Issue A (vcrpy `allow_playback_repeats=True`) — commit `08726876` (PR #96, merged 2026-05-19).
- Issue B (`--snapshot` re-roots `<output-targets>`) — same commit. Extended by commit `4dff779` (PR #112, merged 2026-05-20), which unified `--output-dir DIR` with the same per-target layout so users can pass `--output-dir` to verify builds without re-collapsing to a flat compare.

GitHub issue #95 is still marked OPEN.

**Action:** **Wait** for PythonCourses to post a confirmation comment
on the issue once the CLM bump produces a clean snapshot/verify cycle
on the AZAV ML spec. Then close the issue.

**Optional pickup if a session has spare time:** the proposal flags
adding an integration test in CLM for the `<output-targets>`
honoring behavior using a synthetic spec with the
shared/trainer/speaker triplet. The bug was a legacy public/speaker
shape regression; a triplet-shaped regression test is cheap insurance.

## 6. Priority 5 — `http-replay-skip` tag (DO NOT START)

Gated on PythonCourses decision between three options for the
chained-LLM-call workshop cells in
`slides_010_prompt_templates.py`:
1. stub the LLM in the chained cells (deck-side),
2. restructure the deck to break the dependency (deck-side),
3. accept the flakiness with a `http-replay-skip` tag (CLM-side).

PC has not picked. If they choose option 1 or 2, this work is unneeded.
Treat as **blocked** until PC confirms option 3.

## 7. Signaling Back to PythonCourses

Convention from the proposal doc, repeated here for the next agent:

After each priority lands on CLM master:
1. Post a comment on the relevant GitHub issue with the merge commit SHA and a one-line summary of what shipped.
2. (When possible) update the PythonCourses-side handover. From this side, that means leaving a note in the relevant CLM-side doc; PythonCourses will pick it up on their next sync.
3. The PythonCourses agent then bumps the CLM pin via `uv lock --upgrade-package coding-academy-lecture-manager`, runs the verification path documented in each priority of the proposal, and continues the migration.

If a design problem surfaces that needs a PythonCourses-side decision
before CLM can proceed, file it as a comment on the corresponding issue
**and** flag it in
`<PythonCourses>/docs/handover-slide-format-redesign-next-steps.md`
(the PC-side live action list). Don't block silently.

## 8. Environment Setup Notes (from the 2026-05-20 session)

- The worktree's `.venv` was missing `hypothesis` (declared as a `dev`
  extra; needed by `tests/slides/test_split.py`). `uv sync --extra dev
  --extra all` resolves it. Worth doing once at the start of any fresh
  CLM session — the pre-commit `pytest (fast)` hook will fail on the
  collection error otherwise.
- Pre-commit hook runs `ruff (lint)` + `ruff (format)` + `mypy` +
  `pytest (fast)`. The pre-Phase-1 baseline is currently
  `1ecfac1` (master) plus the Priority 3 fix on `claude/coverage-workshop-slide-id-opener`.
- The `.clm-cache/` directory may appear in the worktree after running
  `clm slides coverage` locally. It's gitignored — don't commit it.

## 9. Decisions Recorded — Priority 1 implementation (2026-05-20)

- **Prose extractor only matches `# ` (jupytext) markdown lines.** A
  raw `import requests` line in a code cell does NOT qualify as prose,
  so Phase 2's AST extractor still picks up such cells. A leading
  comment in a code cell (`# Initialize the client`) does qualify —
  human-written comments describe intent better than the first AST
  node typically would, so we let prose fire before the AST walker.
- **Code-cell AST extraction labels stay verbose** (`code:class`,
  `code:def`, `code:assign`, `code:import`, `code:call`). Wrapped by
  the existing `_handle_slide` source-naming step they surface as
  `content:code:<kind>` in the CLI report, which matches the proposal
  doc's recommended format.
- **Sibling fallback skips the LLM call.** When the EN slug source has
  nothing extractable and we fall back to the DE sibling, we do NOT
  consult the LLM. Its prompts target English content; firing it on
  DE-only content would produce German-derived titles and break the
  EN-derived contract. The DE content goes through `slugify`'s
  transliteration instead.
- **`extraction.source` is now plumbed through the HEADED branch.**
  Previously the HEADED path hard-coded `source = "heading"`. Phase 3
  needed to distinguish `heading` (direct) from `sibling-heading`
  (fallback), so the assignment now uses `extraction.source`
  verbatim. Direct headings still report as `"heading"` (extractor
  sets that label); sibling-headings report as `"sibling-heading"`.
- **Phase 4 promotes NON_EXTRACTABLE to EXTRACTABLE-equivalent when
  LLM fires.** Rather than duplicating the write path, the LLM branch
  rewrites `extraction` to a synthetic EXTRACTABLE with `source = "llm"`
  so the existing write-decision logic (which already treats
  `source == "llm"` as auto-write) handles it without a new code path.
- **`--force` + existing-id + NON_EXTRACTABLE + LLM-title behavior is
  unchanged** when `--llm-suggest` is off (baseline rule: don't
  destroy an id we can't replace). With `--llm-suggest`, if LLM
  produces a title, `--force` regenerates as expected — consistent
  with the EXTRACTABLE-with-force path.

## 10. Decisions Recorded — Priority 2 v1 implementation (2026-05-20)

- **SyncProposal has an explicit `verdict` field**
  (`"in_sync"` / `"update"`). Earlier draft used "empty proposed_text
  means in_sync"; that was fragile against whitespace and LLM
  formatting quirks. The prompt is explicit and the parser tolerates
  legacy responses by inferring the verdict from text emptiness.
- **`SyncCache` value stores direction.** Including direction in the
  *value* (not the key) means a single (de_hash, en_hash) pair has
  one cached proposal at a time, but the cache transparently
  bypasses a stale entry when `--source-lang` flips mid-session
  (cache returns the proposal only when the cached direction
  matches the requested one).
- **No "in_sync" shortcut without LLM.** Hash equality alone can't
  tell us if DE and EN content are semantically aligned (they have
  different surface forms by design). Every first-time pair fires
  the LLM. The cache makes steady-state re-runs free.
- **Code cells excluded from sync entirely.** Split format keeps
  code cells byte-identical across DE/EN companions (Phase-6
  validator enforces this). Re-running sync on them would be wasted
  LLM spend.
- **Roles walked: slide / subslide / voiceover / notes.** Other
  markdown roles (e.g. raw j2 cells, title macros) are excluded.
- **Pairing is `(slide_id, role)`-keyed source-order zip.** Multiple
  cells per role within one slide_id (e.g. two voiceover cells for a
  long slide) pair up positionally. Count mismatches surface as
  errors — no fuzzy/best-effort matching in v1.
- **CLI command file is named `slides_sync.py`** (not `sync.py`) to
  disambiguate from `clm.cli.commands.sync_includes` and from
  `clm.slides.sync` (the engine module).
- **Lazy import of `sync_prompts` inside `OllamaSyncJudge.propose`**
  to avoid a top-level circular reference (the prompt module
  imports nothing from `ollama_client` directly, but the
  `prompt_version` constant is referenced symbolically in both
  places via the protocol's `prompt_version` attribute).

## 10b. Decisions Recorded — Priority 2 v2 implementation (2026-05-20)

- **Separate `sync_snapshots` table** (not an `accepted_at` column on
  `sync_proposals`). The proposal cache is content-addressed
  (`(de_hash, en_hash, prompt_version) → proposal`); the snapshot
  store is location-addressed
  (`(de_path, en_path, slide_id, role) → (de_hash, en_hash,
  direction, accepted_at)`). Keeping them separate avoids mixing two
  unrelated questions (*"what would the LLM say?"* vs. *"what state
  did the author last confirm?"*) and leaves room for a future
  direction-auto-detection pass to read snapshots without consulting
  the proposal cache.
- **`click.edit()` is the edit-flow buffer.** It already shells out
  to `$EDITOR` / `$VISUAL` and returns `None` on no-save; we treat
  the `None` case as a skip. Tests inject `edit_fn` to stub a fixed
  editor response without touching the user's environment.
- **Snapshot rows written only on accept / edit.** Skips and quits
  do *not* write snapshots — the next sync run should re-ask the LLM
  about that pair, not silently treat the divergence as accepted.
  The skip telemetry counter is the audit trail for "user actively
  chose not to take the proposal".
- **Body write-back via `clm.slides.raw_cells`, not slide_parser.**
  `parse_cells` strips body whitespace; rewriting through it would
  shift trailing-blank padding and surrounding bytes. `raw_cells`
  preserves the cell header verbatim and only replaces `lines[1:]`,
  so the diff scope is the cell body only.
- **`--interactive` ↔ `--json` mutex.** The walker streams prompts
  on stdin; combining it with a structured JSON report would
  interleave prompts and the JSON object in the same output stream.
  CLI raises `UsageError` when both are passed.
- **`PairOutcome` carries `de_hash` / `en_hash`.** Earlier draft had
  the walker re-read the source side from disk to recompute the
  hash for snapshot writes. Stashing both hashes on the outcome
  directly removes that round-trip and the slide_parser-stripped
  hash recomputation guesswork — the walker is now a pure consumer
  of pre-computed hashes.

## 11. Decisions Recorded — Priority 3 fix (2026-05-20)

- **Priority 3 fix kept narrow.** Only extended the slide-parser-side
  `workshop_scope.find_workshop_ranges`, not the nbformat-side
  `output_spec.find_workshop_ranges`. The two paths' notion of
  "workshop" intentionally diverges: the coverage walker / validator
  treat `workshop-…` slide_id decks as workshops (skip task slides);
  the notebook output filter does not (task slides still render
  fully). This is the right asymmetry for the rollout convention.
- **Slide_id opener requires `is_slide_start`.** Voiceover and notes
  cells inheriting the announcement slide's id do NOT re-open a
  range. Implemented via tag check (`"slide" in tags or "subslide" in
  tags`) in `_is_workshop_opener` rather than threading
  `is_slide_start` through the protocol.
- **No separate Priority 3 design doc written.** The PC-side proposal
  doc plus the inline docstring on `find_workshop_ranges` were enough;
  a fresh design doc would have been duplicative.
- **One handover doc for Priorities 1+2+4+5.** Single tracking
  surface; each priority has a clear status row in §0. Priorities 1
  and 2 will get their own focused PRs when implementation starts;
  this handover does not become the design doc for either (the
  proposal doc and the PC handover §3 already cover that).
