# Handover: Video Narration Harvest (agent-first rebuild)

**Status**: planning complete, implementation NOT started
**Last updated**: 2026-07-04
**Canonical design source**: `docs/proposals/video-narration-harvest.md`
(merged via PR #541, decisions folded in via PR #542). Read that proposal
first; this handover adds only execution state and pointers.

## 1. Feature Overview

Rebuild CLM's video→voiceover feature (extract spoken narration from
recorded videos, curate it, integrate it into slide decks) as an
**agent-first toolkit** named `clm harvest`, mirroring the proven
`clm slides sync` verb pattern, with write-back through the sync-v3
internal deck model.

Problems solved:
- The current `clm voiceover sync` is a one-shot autonomous solver whose
  curation judgment lives in an embedded OpenRouter/Sonnet call; in practice
  a driving agent (Claude Code) cannot reach the intermediate state and
  resorts to throw-away scripts.
- Write-back predates the v3 identity model (inline writes keyed by slide
  *index* per language; twin pair left divergent with no shared trust
  store).
- "Voiceover" is overloaded: text-layer tooling (`extract`/`inline`/
  companions) vs. the video pipeline collide on `extract`, `sync`, `report`.

References: **epic #546**; proposal (above); #366 (agent-first sync
pivot), #520 (sync-engine-v3), #501 (separated companions);
`docs/claude/voiceover-design.md` (original pipeline design);
`docs/claude/sync-v3-handover.md` (v3 state).

## 2. Design Decisions (all settled — see proposal §3–§6, §8)

1. **Promote the v3 model** (`bilingual_doc` + `doc_lenses`) to the general
   internal deck representation for any tool that *mutates* deck
   narration/structure. Why: it is the only representation unifying DE/EN +
   inline/companion voiceover under one identity (`MemberKey`), already
   sync-agnostic (clean DAG, model imports only attrs), byte-lossless
   round-trip. Rejected alternative: extending `slide_parser` (lossy,
   single-language) or the index-keyed `slide_writer` path.
   Non-goal: replacing `slide_parser`/`raw_cells` for read-only consumers.
2. **Deterministic/judgment split**: engine owns ASR, transition detection,
   OCR matching, alignment, caching, validators; the driving agent owns
   curation and translation judgment (replacing `merge.py`'s embedded LLM
   call). Verbs: `report / task / accept / verify / autopilot`
   (autopilot = today's one-shot, key-gated, never CI).
3. **Naming**: video feature → `clm harvest`; "voiceover" keeps only the
   text-layer meaning. `port`/`compare`/`compare-from-inventory` move into
   harvest. Rejected names: `transcript`, `backport`, anything under
   `recordings` (OBS/Auphonic workflow).
4. **Single-language recordings**: harvest is recorded-language-
   authoritative. `task --kind curate` vs `--kind translate`; `accept`
   takes a single-language or bilingual answer; a one-sided write is a
   *representable* divergence that the existing `clm slides sync` loop
   resolves as tier-3 translation work. One reconciliation machine, not two.
5. **Answer schema = bullet lists**: per-language ordered bullet strings +
   `dropped` audit list; engine renders bullets into the cell body at
   accept time (voiceovers are bullet lists so the speaker can track
   position while recording).
6. **Novelty classification is purely structural** (VO present/absent +
   transcript matched/unmatched) — no textual-similarity heuristic
   (unbounded failure-mode chasing). "Adds material?" is agent judgment.
7. **Ledger provenance**: harvest writes record as
   `harvest:<video-fingerprint>` (same fingerprint keying the artifact
   cache); one-sided `--record` marks only the written side's trust, never
   blesses the stale twin.
8. **No backward compatibility**: single user; direct cutover, no
   deprecated aliases, old video-side `voiceover` verbs deleted.

## 3. Phase Breakdown

- **Phase 1 [TODO] — v3 utility extraction** (no behavior change).
  Extract from `src/clm/slides/sync_diff.py` into a sync-free module
  (working name `src/clm/slides/doc_identity.py`): `content_fingerprint`,
  `baseline_from_deck` (general structural snapshot), `_iter_with_groups`/
  `_member_group_token` (the privates `doc_apply.py:61-69` and
  `doc_ledger.py` import today). Extract from `doc_apply.py` a sync-free
  write surface: "given a `BilingualDeck` + member edits, emit and
  atomically write the ≤4 files" without ledger/differ imports. Update
  `tests/cli/test_sync_import_cleanliness.py` expectations. Acceptance:
  differ/ledger/apply keep passing; new module importable without pulling
  in `sync_diff`.
- **Phase 2 [TODO] — `clm harvest report`** (read-only; can land during v3
  dogfooding). New group + verb wrapping the deterministic pipeline
  (`transcribe`→`detect`→`match`→`align`, cached via
  `src/clm/voiceover/cache.py`), emitting per-slide JSON: `MemberKey`,
  video language, transcript segment(s), both-language VO baseline,
  structural novelty class (`no_existing_vo` | `transcript_adds_material`
  → *structural only, see decision 6* | `covered` | `unmatched_speech` |
  `unmatched_slide`). Reads the deck through `load_bundle`
  (`doc_lenses.py`). Existing diagnostics (`transcribe`/`detect`/
  `identify`/`identify-rev`/`cache`/`trace`) re-home under the group.
- **Phase 3 [TODO] — `task` / `accept` / `verify`** (gated on v3 confidence
  — see Blockers). `task --kind curate|translate` framing (instructions =
  today's `src/clm/voiceover/prompts/merge_*.md` content restated as
  caller instructions; bullet-list `answer_schema`; named validator).
  `accept`: validate (schema; baseline-hash freshness vs. what `task`
  framed; `de_id==en_id`; shared-cell byte-identity — reuse sync
  validators), then id-keyed member write through the Phase-1 write
  surface; `--record` with `harvest:<fp>` provenance, one-sided-trust
  semantics. `verify` delegates to `clm slides sync verify`.
- **Phase 4 [TODO] — rename + cutover**. `clm harvest` absorbs
  `port`/`compare`/`compare-from-inventory`; `voiceover sync` →
  `harvest autopilot`; delete old video-side `voiceover` verbs (no
  aliases); MCP renames (`harvest_report`, `harvest_task` read-only;
  accept stays CLI-only); new `clm info harvest-agents` topic modeled on
  `sync-agents.md`; update `commands.md`, `migration.md`,
  `docs/user-guide/voiceover.md` (rename/split into harvest doc).

## 4. Current Status

- Proposal written, decisions resolved, merged to master
  (`docs/proposals/video-narration-harvest.md`; PRs #541 merged, #542
  merged/auto-merge armed 2026-07-04).
- **No implementation code exists.** Epic issue: **#546** (phases, settled
  decisions, and the Phase-3 gate mirrored there).
- Blocker for Phase 3 (and arguably 4): sync-engine-v3 must survive its
  **dogfood week on PythonCourses** first (see
  `docs/claude/sync-v3-handover.md`; v3 Phase 4 = flip default, delete v2).
  Harvest becomes v3's second consumer — extra motivation for v3 Phase 4,
  but do not build the write path on an engine still being shaken out.
- Open design detail (only one): how `**[Revisited]**` transcript segments
  (aligner backtracking markers, `src/clm/voiceover/aligner.py:40-60`) are
  presented inside a `curate` task's inputs.

## 5. Next Steps

Start **Phase 1**. Prerequisites: none (independent of v3 dogfooding; it
only moves code). Steps: read `sync_diff.py` fingerprint/snapshot helpers
(`content_fingerprint`/`_pair_sig`/`_body_fp` ~:153-192,
`baseline_from_deck` ~:294-347) and the imports at `doc_apply.py:47-70`,
`doc_ledger.py:47-48`; carve out `doc_identity.py`; then the sync-free
write surface from `doc_apply` (`emit_all` ~:395, `atomic_write_all`
~:1312, `_new_companion_path` ~:1360). Gotchas:
- `tests/cli/test_sync_import_cleanliness.py` pins the v3 import graph
  (model must not import v2; facade imports only v3 core) — extend, don't
  fight it.
- v2 and v3 coexist behind `CLM_SYNC_ENGINE`; touch only v3 modules.
- Line numbers here are from the 2026-07-04 investigation; re-verify, the
  v3 modules are actively changing during dogfooding.

## 6. Key Files & Architecture

Nothing created yet. The map (from the 2026-07-04 investigation):

- **v3 model (reuse)**: `src/clm/slides/bilingual_doc.py` (model: `Member`,
  `MemberKey`, `SideCell`, `BilingualDeck`, `Observation`,
  `NormalizeRefusal`), `doc_lenses.py` (`parse_bundle`/`project`/
  `load_bundle`, byte-identity laws), `sync_diff.py` (differ + the helpers
  Phase 1 extracts), `doc_ledger.py` (schema-2 trust store,
  `.clm/sync-ledger.json`), `doc_apply.py` (write executor), CLI facade
  `src/clm/cli/commands/slides/sync_v3.py`, dispatch in
  `.../slides/sync.py` (`_v3_engine`, `:117-122`).
- **Video pipeline (repackage)**: `src/clm/voiceover/` — `transcribe.py`
  (ASR backends, subprocess isolation), `keyframes.py`, `matcher.py` (OCR +
  monotonic alignment), `aligner.py`, `timeline.py` (multi-part),
  `cache.py` (`.clm/voiceover-cache/`, video-fingerprint keys), `merge.py`
  (embedded-LLM merge — its prompt content migrates into `task`
  instructions; module itself survives only for `autopilot`), `backfill.py`
  / `identify.py` / `rev_scorer.py` / `port.py` / `compare.py` /
  `trace_log.py`.
- **Current CLI to replace**: `src/clm/cli/commands/voiceover.py`
  (group `:85`, `sync` `:123`, companion-route selection `:308-319`,
  merge path `_merge_notes` `:577`, propagation `_run_propagation` `:869`).
- **Old write paths (retired for harvest, kept for autopilot until P4)**:
  `src/clm/notebooks/slide_writer.py` (index-keyed inline),
  `src/clm/slides/voiceover_tools.py` (companion helpers — also used by the
  text-layer feature, which stays).
- **Patterns to copy**: verb group + JSON envelopes + task/accept in
  `src/clm/cli/commands/slides/sync.py` (`_DefaultVerbGroup` `:1750`,
  `sync_task_cmd` `:2741`, `sync_accept_cmd` `:2920`); MCP thin-wrapper
  style in `src/clm/mcp/server.py`/`tools.py`; agent-loop doc style in
  `src/clm/cli/info_topics/sync-agents.md`.

Conventions to continue: read-only by default, every write an explicit
verb; the engine emits, it never invokes a model (except `autopilot`);
`--json` on every verb; exit codes 0/1/2; changelog via `changelog.d/`
fragments; update info topics with any CLI change (CRITICAL rule).

## 7. Testing Approach

- Phase 1: existing suites are the safety net (`tests/slides/` v3 tests +
  `tests/cli/test_sync_import_cleanliness.py`); add import-graph
  expectations for `doc_identity` and unit tests for the extracted write
  surface (byte-identity round-trip, atomic multi-file).
- Phase 2: `report` golden-JSON tests with cached/faked pipeline artifacts
  (pattern: `tests/voiceover/test_sync_companion.py` fixtures build tiny
  decks inline; `--transcript`/`--alignment` injection already exists to
  skip ASR). No GPU/ffmpeg in the fast suite — keep heavy stages behind
  existing markers.
- Phase 3: validator unit tests (schema, stale-baseline rejection, twin
  invariants), accept round-trip on split pair + companion + inline
  layouts, one-sided-write → `slides sync report` classification test
  (the §6 hard requirement), ledger provenance assertions.
- Run: `pytest` (fast suite; pre-push hook), `pytest -m "not docker"`
  pre-release. Existing video-pipeline suites: `tests/voiceover/`
  (26 files), `tests/cli/test_voiceover_*`.

## 8. Session Notes

- Investigation (3 parallel explorations, 2026-07-04) corrected two
  premises: the video pipeline IS already split/companion-aware in its
  write-back (auto-detects companions, preserves `.de`/`.en` naming) — the
  real gap is identity/twin-reconciliation, not format support; and MCP
  already exposes read-only video tools (`voiceover_transcribe`,
  `voiceover_identify_rev`, `voiceover_compare`, `voiceover_backfill_dry`).
- User priorities: make the agent's work simple (drove bullet-list schema);
  avoid heuristics that accrete complexity; no legacy burden whatsoever.
- The proposal's §6 constraint is the subtlest part of Phase 3: a
  one-sided harvest write + `--record` must leave a state that
  `slides sync report` reads as "recorded side edited, twin needs
  translation", never as corruption, and must not bless the stale twin.
  Design the ledger write for that case before coding `accept`.
