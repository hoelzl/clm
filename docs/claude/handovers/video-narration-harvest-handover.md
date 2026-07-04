# Handover: Video Narration Harvest (agent-first rebuild)

**Status**: ALL FOUR PHASES implemented (v3 extraction; `clm harvest
report`; `task`/`accept`/`verify`; rename+cutover incl. multi-narrative
answers and multi-part caching). Phases 3–4 landed EARLY, before the v3
dogfood week, as a deliberate stress-test — back-out tags
`pre-harvest-phase3` / `harvest-phase3` / `harvest-phase4` on master.
Remaining: dogfood on real recordings.
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

- **Phase 1 [DONE] — v3 utility extraction** (no behavior change).
  Landed as two new sync-free modules:
  - `src/clm/slides/doc_identity.py`: `content_fingerprint`,
    `pair_signature`, `body_fingerprint`, `lines_fingerprint`,
    `MemberBaseline`/`DeckBaseline`, `iter_with_groups`,
    `member_group_token`, `baseline_from_deck`. `sync_diff` imports from it
    (aliasing the old private names) and keeps re-exporting the public four
    in `__all__`; `doc_ledger` and `sync_shadow` now import from
    `doc_identity` directly.
  - `src/clm/slides/doc_write.py`: `DeckEmitter` (stream view + `emit`/
    `emit_all` + `set_side`/`stream_remove`/`insert_mirrored`),
    `DeckWriteError`, `new_companion_path`, `write_changed_files`.
    `doc_apply._Executor` now subclasses `DeckEmitter` (its `_ItemError`
    subclasses `DeckWriteError`); the apply write tail calls
    `write_changed_files`.
  - Tests: `tests/slides/test_doc_write.py` (byte-identity emission,
    mutation round-trip, atomic multi-file + minted companion);
    `tests/cli/test_sync_import_cleanliness.py` extended — the new modules
    must import neither `sync_diff` nor `doc_ledger` nor the v2 core.
- **Phase 2 [DONE] — `clm harvest report`** (read-only). Landed as:
  - `src/clm/voiceover/harvest.py` — engine: `run_pipeline` (the cached
    deterministic tier, mirroring `voiceover sync`'s stage order + cache
    scoping, with `--transcript`/`--alignment` short-circuits),
    `build_report` (joins alignment×deck; the index→`slide_id`→
    `id:<slide_id>` identity seam), `classify_slide` (structural 2×2:
    speech assigned × VO present on the recorded side), `video_fingerprint`
    (single = `VideoKey.hash`; multi-part = sha1 over ordered part hashes —
    this is the future `harvest:<fp>` provenance), `report_exit_code`.
  - `src/clm/cli/commands/harvest.py` — lazily registered top-level group
    (`main.py` `lazy_subcommands` + `optional_subcommands` +
    `_OPTIONAL_COMPAT_EXPORTS`); `report` is the default verb (resolved in
    `resolve_command`, NOT sync's `parse_args` prepend — the group carries
    cache flags and a prepend would fire on `--no-cache`); group-level
    cache flags mirror `voiceover_group`; diagnostics re-registered as the
    SAME Click command objects imported from `voiceover.py` (old names stay
    until Phase 4).
  - Aligned notes pointing at slide indices absent from the parsed deck
    (stale injected alignment / slides deleted since recording) fold into
    `unmatched_speech` — never dropped silently.
  - Tests: `tests/cli/test_harvest_cli.py` (injected-alignment CLI
    round-trips pinning every class, exit codes, default verb incl. after
    group options, refusal → exit 2, re-homed diagnostics). Docs:
    `commands.md` §`clm harvest`.
- **Phase 3 [DONE — landed EARLY, before the v3 dogfood week, as a
  deliberate stress-test; back-out = revert the PR merge or reset to tag
  `pre-harvest-phase3`]** — `task` / `accept` / `verify`. Landed as:
  - `src/clm/voiceover/harvest_task.py` — `build_tasks(report, deck,
    kind, slide)`: instructions from the new
    `prompts/harvest_curate.md`/`harvest_translate.md` (merge/propagate
    rules restated for the caller); bullet-list `ANSWER_SCHEMA`
    (validator `harvest-bullets`); freshness tokens = per-side
    `baseline_fingerprint` (the report's per-cell `content_fingerprint`,
    added to report items) + `video_fingerprint`; >1 narrative cell per
    side ⇒ `TaskUnavailable` (P8). `revisited_segments` presented as
    structured groups with instruction 6 explaining their semantics.
  - `src/clm/voiceover/harvest_accept.py` — `parse_answer` (precise
    per-field rejections) + `accept_answer`: freshness re-check against
    the LIVE bundle; bullets rendered `#\n# - …`; existing member body
    replaced via the doc_apply `_replace_body` rule; missing side added
    via `swap_lang` + `insert_mirrored`; missing member created (minted
    `<owner>-vo` id, deck-majority role/layout, companion appends /
    inline inserts after the owner group) and written via the Phase-1
    `DeckEmitter`/`write_changed_files` after the re-parse gate.
  - **The §6 ledger semantics** (`_record_member`, provenance
    `harvest:<fp>`, gated on `structural_gate`): bilingual answer →
    fresh both-side entry (next sync report: `in_sync`); one-sided
    member → fresh one-sided entry (→ framed `translate_new`; without
    the record it would be unframed `verify_cold`); one-sided write over
    an existing twin → the written side's fingerprint is NOT advanced
    (existing entry kept / pre-write state synthesized → framed
    `translate_edit`). LedgerMember has no per-side trust field — the
    fingerprint mismatch IS the "twin owes translation" representation;
    advancing both fps is the forbidden silent-bless state.
  - `verify` is **v3-native**, NOT a `sync verify` delegate: the v2
    `verify_pair` projects companions and reads a one-sided narrative
    member as an `id-asymmetry` ERROR — exactly the corruption
    misreading §6 forbids. Harvest verify = lens gate + deck-half
    `structural_gate` + one-sided narrative members listed as
    `pending_twins` (exit 0).
  - Tests: `tests/cli/test_harvest_task_accept.py` (report→task→accept→
    verify loop; the §6 classification tests assert the v3 differ's
    verdicts against the recorded ledger: `translate_new`,
    `translate_edit` de→en, bilingual → no item).
- **Phase 4 [DONE] — rename + cutover (+ two extensions)**. Landed as two
  PRs (#551 extensions, then the cutover PR):
  - **Extensions**: multi-narrative answers (task lists every narrative
    cell with per-member `baseline_fingerprints`; answers are per-member
    `updates` with `member: null` + optional `after` for creates;
    set-level freshness; §6 record applied per written member) and
    multi-part caching (`cache.MultiVideoKey` = ordered composite of part
    hashes = the harvest fingerprint; `cached_timeline`/`cached_alignment`
    take the part list; `--alignment` override works multi-part).
  - **Cutover**: video verbs re-decorated as standalone Click commands in
    `voiceover.py` (implementations stay in that module) and registered
    ONLY on `harvest_group` (`_register_video_verbs`); `sync` →
    `@click.command("autopilot")`; `voiceover report` →
    `compare-report` (name clash with harvest report); the hidden
    `debug voiceover-commits` group moved too (it feeds identify-rev).
    `voiceover_group` keeps only extract/inline/inline-notes and lost its
    cache flags. MCP: six tools renamed `harvest_*`, `backfill_dry`'s
    subprocess now calls `harvest backfill`; NEW read-only `harvest_report`
    + `harvest_task` (accept stays CLI-only); `EXPECTED_TOOLS` updated;
    `tests/mcp/test_harvest_tools.py` replaces `test_voiceover_tools.py`.
    New `clm info harvest-agents` topic (registered in `info.py` TOPICS);
    `commands.md` video docs moved under `### clm harvest`; migration.md
    rename table; user guide split (`docs/user-guide/harvest.md` new,
    `voiceover.md` shrunk to the text layer). Tests re-pointed
    (`harvest_group` + `autopilot`/`compare-report` verbs).

## 4. Current Status

- Proposal written, decisions resolved, merged to master
  (`docs/proposals/video-narration-harvest.md`; PRs #541 merged, #542
  merged/auto-merge armed 2026-07-04).
- **Phases 1–3 implemented** (`doc_identity.py` + `doc_write.py`;
  `clm harvest report` + re-homed diagnostics; `task`/`accept`/`verify`,
  see §3). Epic issue: **#546**. Phase 3 landed BEFORE the v3 dogfood week
  finished (user decision, as a stress-test): master is tagged
  `pre-harvest-phase3` right before it, and the phase is one PR, so
  back-out = `git revert -m 1 <merge>` or comparison against the tag.
- Blocker for Phase 3 (and arguably 4): sync-engine-v3 must survive its
  **dogfood week on PythonCourses** first (see
  `docs/claude/sync-v3-handover.md`; v3 Phase 4 = flip default, delete v2).
  Harvest becomes v3's second consumer — extra motivation for v3 Phase 4,
  but do not build the write path on an engine still being shaken out.
- Open design detail (only one): how `**[Revisited]**` transcript segments
  (aligner backtracking markers, `src/clm/voiceover/aligner.py:40-60`) are
  presented inside a `curate` task's inputs.

## 5. Next Steps

All four phases are done (see §3; Phases 3–4 were pulled forward
deliberately as a v3 stress-test — the user's call, hedged by tags
`pre-harvest-phase3`/`harvest-phase3`/`harvest-phase4` on master and
per-phase PRs for easy revert). The one remaining step: **dogfood the
harvest loop on real recordings** (this doubles as the v3 dogfood) —
watch the §6 framing (`translate_new`/`translate_edit`, never `in_sync`,
never corruption) and the task/answer ergonomics. Gotchas that remain
live:
- `tests/cli/test_sync_import_cleanliness.py` pins the v3 import graph
  (model must not import v2; facade imports only v3 core; `doc_identity`/
  `doc_write` must stay differ/ledger-free) — extend, don't fight it.
- v2 and v3 coexist behind `CLM_SYNC_ENGINE`; touch only v3 modules.
- The v3 modules are actively changing during dogfooding; re-verify line
  numbers and shapes before building on them.

## 6. Key Files & Architecture

Created by Phase 1: `src/clm/slides/doc_identity.py` (identity/snapshot),
`src/clm/slides/doc_write.py` (emitter + atomic write surface),
`tests/slides/test_doc_write.py`. Created by Phase 2:
`src/clm/voiceover/harvest.py` (report engine),
`src/clm/cli/commands/harvest.py` (group + report + re-homed diagnostics),
`tests/cli/test_harvest_cli.py`. Created by Phase 3:
`src/clm/voiceover/harvest_task.py`, `harvest_accept.py`,
`prompts/harvest_curate.md`, `prompts/harvest_translate.md`,
`tests/cli/test_harvest_task_accept.py` (+ `task`/`accept`/`verify` verbs
in the CLI module). The rest of the map (from the 2026-07-04
investigation):

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
