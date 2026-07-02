Continue the sync v3 core replacement (#520) in hoelzl/clm — start **Phase 3**.

**Task & goal**: Phases 0–2 are merged (PR #532 model+lenses, PR #534 differ+shadow).
Phase 3 per `docs/claude/design/sync-total-identity-document-model.md` §11: per-item
`apply --decisions` + `record` verb, promote the ledger to the §5 member-keyed schema
(the ONLY trust store), and put the v3 engine behind `CLM_SYNC_ENGINE=v3` with one
verb-layer dispatch point (§12.5). Exit: mutation oracle green through the v3 write
path, then a real dogfood week on PythonCourses.

**Done so far** (all merged to master):
- `src/clm/slides/bilingual_doc.py` + `doc_lenses.py` — BilingualDeck model, parse/project lenses (byte-identity over the 644-pair corpus).
- `src/clm/slides/sync_diff.py` — generic 3-way differ; `DeckBaseline(complete=False)` is the ledger-mode hook Phase 3 plugs into; closed mechanical/framed action registry; schema-3 envelope via `DeckDiff.to_payload()`.
- `src/clm/slides/sync_shadow.py` + `clm slides sync shadow DECK|DIR --baseline REF` — v2-vs-v3 harness. W10 triage: `docs/claude/analysis/sync-v3-phase2-w10-replay.md` (per-deck-correct base → 2 genuine items, zero noise; the both-moved class is exactly what the ledger erases).

**Next step(s)**:
1. Read the memory topic `project_sync_assessment_2.md` (Phase 2 landmines) and design §5/§8/§12.5.
2. Promote `src/clm/slides/sync_ledger.py` (today: schema 1, `(slide_id, role)`-keyed + idless map) to the §5 member-keyed entry `{member: MemberKey, langness, layout, fingerprints, tags_fp, provenance, hash_version}`; build `DeckBaseline` from it with `complete=False`.
3. Per-item apply executor for the MECHANICAL_ACTIONS rows + `apply --decisions` (re-home the `sync_accept` guards as decision validators); writes go through `path_utils.atomic_write_all`, ≤4 files per deck.
4. `record` verb (bless/accept collapsed) incl. the §7.3 pos→id key migration at record time; ledger seed from a verified pass.
5. `CLM_SYNC_ENGINE` dispatch in `src/clm/cli/commands/slides/sync.py` (v2 default, v3 opt-in), envelope keeps `is_clean`/`needs_model`/`needs_agent` stable.

**Gotchas / constraints**:
- P8 is load-bearing: never emit/execute a mechanical action when the base carried a divergence, a pool has a deficit, or a twin is estranged — frame instead. `TestAdversarialReviewRegressions` in `tests/slides/test_sync_diff.py` pins these.
- v3 modules must not import `sync_plan`/`sync_apply`/`sync_code` (probe: `tests/cli/test_sync_import_cleanliness.py`); v2 `report.in_sync` is an int cell COUNT, the verdict is `is_clean`.
- Base orders are per-side and id-keyed-only (pos ordinals alias); `_pair_sig` strips slide_id+for_slide.
- Update `src/clm/cli/info_topics/commands.md` for any CLI change; changelog via `changelog.d/` fragment, never `CHANGELOG.md [Unreleased]`.
- Worktree rules: never switch to literal `master`; fresh branch off `origin/master` (e.g. `claude/sync-v3-phase3`); run `pre-commit`-gated commits, push triggers the fast suite; PR + `gh pr merge --merge --auto`.
- Corpus gates run with the maintainer's PythonCourses checkout; cache-dir is cwd-anchored — run clm against course repos from their own cwd.

**Verify with**: `pytest tests/slides/test_sync_diff.py tests/slides/test_sync_diff_matrix.py tests/slides/test_sync_diff_corpus.py tests/slides/test_sync_shadow.py -n 4` (fast), plus `pytest "tests/slides/test_sync_diff_corpus.py::TestRealCorpusSelfDiff" -m "" -n 0` and the `test_sync_corpus_noop/mutation` oracles once the write path exists.
