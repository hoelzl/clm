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
| 1 | `assign-ids` extraction expansion (#89) — prose + AST + sibling + LLM fallback | **CLM phases 1–4 implemented** (P5 = PC-side corpus rerun) | branch `claude/assign-ids-extraction-expansion` (PR pending) |
| 2 | Phase 7 `clm slides sync` (cross-language LLM sync, `SyncCache`) | Not started | — |
| 4 | Close hoelzl/clm#95 once PythonCourses confirms clean snapshot/verify | Awaiting PC confirmation | — |
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
| Priority 4 fixed PR | merged via `08726876` ("fix(http-replay,snapshot): allow_playback_repeats + spec-target snapshots (#95)") |
| Priority 5 background | PythonCourses repo: `docs/proposals/CLM_ISSUE_DRAFT_snapshot_replay_2026-05-19.md` §C |

## 2. Priority 1 — assign-ids extraction expansion (issue #89)

**Goal:** clear ~95% of the 407 missing-ID warnings on
`slides/module_550_ml_azav/` so PythonCourses Phase B can be declared
complete and the slide-format-redesign downstream phases (split-source
parity, coverage walker promotion to error) can run cleanly.

**Status:** Phases 1–4 implemented on branch
`claude/assign-ids-extraction-expansion`. PR not yet filed; the
recommended split is Phases 1+2 as PR #1, Phase 3 as PR #2, Phase 4 as
PR #3 (the only one with semantic changes to `--llm-suggest` behavior).
Currently bundled on a single branch — split before push if the PR
review prefers the staged sequence.

### Phase tracker

- [x] **Phase 1** — first-prose-line extractor in `clm.slides.headingless` (`content:prose`)
- [x] **Phase 2** — code-cell AST extractor in new `clm.slides.code_cell_extract` (`content:code:class|def|assign|import|call`)
- [x] **Phase 3** — sibling-pair asymmetry fix in `_handle_slide` (`content:sibling-…` / `sibling-heading`)
- [x] **Phase 4** — `--llm-suggest` fallback on NON_EXTRACTABLE (`source = "llm"`)
- [ ] **Phase 5** — corpus rerun + documentation updates; bump CLM pin in PythonCourses *(PC-side; pending CLM release that includes #89)*

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

**Status:** Designed in the PythonCourses-side
`handover-slide-format-redesign-clm.md` §3 Phase 7. No CLM-side design
doc yet; the PC handover is currently the canonical spec.

### Spec summary (from PC handover §3, repeated in the proposal)

**Modes:**
- `--dry-run` (default): show proposed diffs, no writes.
- `--interactive`: walk diffs one-by-one with apply / skip / edit.
- `--apply --trivial`: write proposed diffs without prompting; only with `--trivial` (mechanical typo fixes, identical-structure `keep` drift).

**Implementation notes:**
- New `src/clm/slides/sync.py`.
- New `src/clm/infrastructure/llm/sync_prompts.py`.
- Extend `src/clm/infrastructure/llm/cache.py` with a `SyncCache` class reusing the same SQLite file as `CoverageCache` (separate table).
- Register `sync` subcommand in `src/clm/cli/slides_cmd.py`.
- Tests: `tests/infrastructure/llm/test_sync_cache.py`, `tests/slides/test_sync.py`.

### Decisions to make at kickoff

- **3-way merge handling** (both DE and EN changed since last sync). PC handover's open UX question. **Recommendation from the PC proposal:** flag-and-defer-to-manual is acceptable for v1; LLM-suggested merge can follow as v2 once pilot data shows the rate.
- **Pilot instrumentation.** PC proposal requests per-session accept / skip / edit counts logged to the cache (or stderr) so the pilot's "human accepts proposed diff as-is in >80% of cases" decision criterion can be measured. Build this into v1 — without it the Phase E ship/cancel decision has no data.

### Phase tracker

- [ ] Scaffold `clm.slides.sync` module + `SyncCache` table (schema v9).
- [ ] LLM prompt scaffolding (`sync_prompts.py`) + `--dry-run` diff producer.
- [ ] `--interactive` apply/skip/edit walker.
- [ ] `--apply --trivial` path.
- [ ] Pilot instrumentation (per-session counters).
- [ ] CLI registration + `clm info commands` update.
- [ ] Tests (cache + sync logic + CLI smoke).

### Branch convention

`claude/slide-format-redesign-phase-7`.

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

**Status:** Fix shipped via commit `08726876`
("fix(http-replay,snapshot): allow_playback_repeats + spec-target
snapshots (#95)"). Both sub-issues are covered. GitHub issue is still
marked OPEN.

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

## 10. Decisions Recorded — Priority 3 fix (2026-05-20)

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
