- Sync v3 Phase 2 (#520): the generic 3-way member differ
  (`clm.slides.sync_diff`) — one diff over the `BilingualDeck` member stream
  against a per-member baseline (git-ref snapshot now, the committed ledger in
  Phase 3), with per-member direction, the closed §7.2/§7.3 class-transition
  table (fork/unify/id-stamp/relayout, complete or in-progress), and a
  registered mechanical/framed action vocabulary. Guarded by the §7.4
  transition-matrix walk, the §6.3 field-coverage test, Hypothesis noise-floor
  properties (any single one-sided mutation is propagated or alerted, never
  silent, never a cascade), and a full-corpus self-diff noise ceiling.
- Experimental `clm slides sync shadow DECK|DIR --baseline REF [--json]`
  (#520): read-only v2-vs-v3 comparison harness for the migration window —
  both engines' verdicts over the same pairs at the same git baseline. The W10
  dogfood replay triage is recorded in
  `docs/claude/analysis/sync-v3-phase2-w10-replay.md`.
