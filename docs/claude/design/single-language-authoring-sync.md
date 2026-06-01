# Single-Language Authoring Sync — Design Note

**Status**: Design agreed (pre-implementation)
**Author**: Claude (Opus 4.8)
**Date**: 2026-05-31
**Scope**: `src/clm/slides/sync.py`, `sync_direction.py`, `sync_writeback.py`,
`assign_ids.py`, the `clm slides sync` CLI, and the LLM sync/translation path
**Issues**: [#166](https://github.com/hoelzl/clm/issues/166) (this workflow) ·
[#162](https://github.com/hoelzl/clm/issues/162) (the cross-language
`slide_id` invariant it serves)

This note records the design decisions reached for #166 so a fresh session can
implement against them. It resolves the four "Open questions" from the issue.
The companion invariant work (#162's validator + guard-rail) remains
independently valuable for hand-edited / legacy split files the sync-driven
path never touches and is **not** in scope here.

---

## 1. The workflow we want

Most future authoring happens on **split single-language decks**
(`<deck>.de.py` / `<deck>.en.py`). The editing loop:

1. A trainer edits **one** language deck — adds / edits / removes / reorders
   slides. Which language depends on the course (DE or EN).
2. They run **one command**. The other deck is brought into sync via an LLM:
   edits propagate, new slides are translated and inserted, removed slides are
   dropped, reorders are mirrored.
3. The author **never manages `slide_id`s** — not even on the deck they edited.
   IDs are minted onto **both** decks as a byproduct of the sync, kept
   consistent across languages.

Guiding principle: an author authors in one language with full IDE support
(incl. Copilot autocompletion on the deck they're editing); the tooling handles
the cross-language mirror and identity bookkeeping.

## 2. What exists today (and the gap)

See #166 for the full inventory. In one paragraph: `clm slides sync`
(`sync.py`) walks the pair strictly by `(slide_id, role)`, asks the Ollama
`SyncJudge` for **update**-only proposals on already-paired cells, and emits
diffs (dry-run / `--interactive` / `--apply --trivial`). It **skips id-less
cells** (`sync.py` `_index_cells`), **warns and skips** cells present on only
one side (`sync.py:244-268`), and treats a `slide_id` whose cell count differs
across sides as a **hard error** (`sync.py:270-282`). `assign-ids`
(`assign_ids.py`) is a separate manual step that, run per-language on split
files, produces **divergent** ids (the #162 problem). Direction is a single
global `SyncOptions.source_lang`; `sync_direction.py` infers it from snapshot
drift (preferred) or git timestamp (fallback) and **bails to "ambiguous" when
both sides drifted**.

The gap: sync reconciles *only* already-paired UPDATEs. There is no
cross-language **insertion**, **deletion**, **move**, or **id minting**, and no
defined behavior when both decks were touched.

---

## 3. Design decisions

### 3.1 Change-detection baseline — id-less-as-new + structural watermark (Q1)

**Decision.** Do **not** anchor change detection to a git pointer (HEAD /
merge-base) the author can move. Two complementary, git-immune signals instead:

- **Adds are detected by the absence of a `slide_id`.** After a successful
  sync, *every* sync-relevant cell carries an id (sync mints them). An author
  authoring in one language never writes ids. Therefore an **id-less cell is,
  by construction, a cell added since the last sync** — and `git commit` does
  not change that (committing does not run assign-ids). This defeats the
  "author commits before syncing → `git diff HEAD` is empty → silent no-op"
  failure mode that sinks any HEAD-based baseline.
  - Generalized rule: **new = id-less OR id'd-but-unknown-to-the-watermark**
    (covers the author who hand-runs `assign-ids` or copy-pastes a slide; a
    paste that duplicates an id is a *collision* #162's validator flags, not a
    silent mis-pair).
- **Edits / removes / moves are detected against a self-managed structural
  watermark** (§4) — *not* against git HEAD.

**Git is a fallback/corroborator only** (cold start / fresh clone with no local
cache), never the source of truth. See §7 for cold-start handling.

**Guardrail (non-negotiable):** sync **never silently no-ops.** It always
reports the baseline it used and distinguishes *"0 changes — decks already
consistent"* from *"could not establish a baseline."*

Rejected: git HEAD as sole baseline (fragile to commit-before-sync);
merge-base (wrong frame — the author edits a working tree, not a divergent
branch; merge-base re-surfaces every already-synced commit).

### 3.2 Reorder vs add/remove — stable-id-on-move, no similarity matching (Q2)

**Decision.** The `slide_id` *is* the cross-move identity. With the **ordered**
watermark (§4):

| Watermark vs working tree | Classification |
|---|---|
| same id, same `content_hash`, different position | **pure move** (reposition only; skip the translation LLM) |
| same id, different `content_hash` | **edit** (± move) |
| id in watermark, absent on disk | **remove** |
| id-less on disk (or id unknown to watermark) | **add** |

Stable-id-on-move is therefore *free and deterministic* — no content-similarity
matching. The only genuine ambiguity (delete slide A + write a new id-less
slide B in its place) is resolved by **honoring the author's edit gesture**:
*keep-and-edit the cell* → stable id; *delete-and-rewrite* → new id. We
**bias toward "add"** for id-less cells because wrongly fusing B into A's id
silently corrupts the cross-language join and every downstream `for_slide` /
`unify` link, whereas a missed move is never worse than a genuine delete.

**Reorder propagation is in v1** and is LLM-free: mirror the source deck's
order onto the target by id-join; narrative companions (`voiceover`/`notes`,
keyed by the same `slide_id`) travel with their slide. New cells are positioned
via the anchor precedent (§6).

Deferred: opt-in `--detect-moves` (content-similarity) if pilot data shows
authors frequently delete-and-rewrite genuine moves. Default off.

### 3.3 Atomicity & review — per-proposal atomic, write-to-tree, `git diff` (Q3)

**Decision.** The **uncommitted working tree is the staging/review surface.**

- **Default `clm slides sync`** → compute the plan, **write both decks
  atomically to the working tree, never commit**, and print the no-silent
  summary. One command — matches the workflow. `git diff` is the review;
  `git checkout` discards.
- **`--dry-run`** → preview the plan, write nothing (the guardrail's "here's
  what I'd do"; CI; fast check).
- **`--interactive`** → per-proposal `[a]pply / [s]kip / [e]dit / [q]uit`
  gating (extends the existing walker) for authors who want inline control.

This **flips today's default** (currently `--dry-run` is default and bare
`--apply` is rejected). #166 earns a real write-by-default.

**Atomicity is per-proposal-unit across both decks.** One add = one unit =
{stamp the minted id onto the previously-id-less source cell **and** insert the
translated counterpart on the target side}. A unit writes both halves or
neither. The run applies the accepted subset; writes batch into a single
`FileState.flush` per deck. Skip/quit leaves a unit's source cell id-less →
still pending → re-detected next run (idempotent).

Two constraints that keep this sound:

1. **Anchor insert/move positions off *stable shared ids*, not off other
   in-flight proposals** — so accept/skip stays independent and a skipped
   neighbor cannot strand another proposal's position.
2. **Ids are sticky the moment they're persisted** (assign-ids rule). An
   in-review `[e]dit` to a translation does **not** re-slug the id. The id is
   computed once.

Because the write *is* the review artifact, the issue's "mint ids before review
so diffs are stable" concern is automatically satisfied in default mode.

### 3.4 Conflict handling — per-cell direction + isolate-and-refuse (Q4)

**Decision.** The watermark gives a per-cell base for **both** decks, so
"both decks were edited" splits into two cases:

- **Mixed-direction, non-overlapping — *not a conflict*.** Author added two
  slides in DE and fixed an EN typo: different cells, so handle **each cell in
  the direction it drifted** from the watermark. This replaces today's global
  `SyncOptions.source_lang` with **per-cell direction** and retires the "both
  drifted → bail" behavior. (Happy path — author edits one deck — still
  resolves to a single unambiguous direction since only one side drifts.)
- **True conflict — *same id drifted on both sides since the watermark*.**
  **Isolate and refuse, per cell:** apply every non-conflicting change, leave
  the conflicting cell **untouched on both decks**, and list it in the summary.
  In `--interactive`, render three-up (watermark base / current DE / current
  EN) with `[d]e-wins / [e]n-wins / [s]kip`. A conflict is a proposal `kind`
  that **defaults to skip** — it never blocks the rest of the sync.

**No in-file conflict markers** (`<<<<<<<`) — they would corrupt the
percent-format and break `parse_cells`. Leave-untouched + summary only.

**Order conflicts** (both decks independently reordered) → refuse to propagate
order, keep each deck's order, report; per-cell content still syncs. If only
one deck's order drifted, mirror it (§3.2).

Deferred: **full 3-way LLM merge.** The watermark *is* the common ancestor it
will need, so we build toward it — when justified, the conflict kind grows a
`[m]erge` action.

Accepted limitation: if both decks *independently add a new slide* meant to be
"the same" slide (both id-less, no shared signal), we produce two independent
adds → visible duplication in `git diff`, deduped by hand. Per §3.2 we do not
similarity-guess cross-language identity. Rare; the review surface catches it.

---

## 4. Data model: the structural watermark

A new table in the existing LLM cache DB (`clm-llm.sqlite`), superseding the
per-cell `sync_snapshots` table for #166 and reused by direction inference:

```
sync_watermarks(
  de_path     TEXT,     -- pair key (canonical absolute paths)
  en_path     TEXT,
  lang        TEXT,     -- "de" | "en": each deck has its own ordered rows
  position    INTEGER,  -- 0-based order within the deck (sync-relevant cells)
  slide_id    TEXT,     -- NULL only for hard-refusal/id-less cells (rare post-sync)
  role        TEXT,     -- "slide"|"subslide"|"voiceover"|"notes"
  content_hash TEXT,    -- cell_content_hash(): strip()+sha256 (sync_writeback)
  synced_at   TEXT      -- UTC ISO-8601
)
```

Properties:

- **Ordered** (`position`) → enables move detection and order-conflict
  detection (§3.2, §3.4).
- **Per-lang rows** → enables per-cell direction (§3.4): diff DE-working vs DE
  rows and EN-working vs EN rows independently.
- **`content_hash` reuses `sync_writeback.cell_content_hash`** (strip + sha256)
  so hashes match the existing apply-path snapshots.
- Written **only on successful apply** of accepted proposals (the watermark
  advances with the agreed state, immune to git commit cadence). This is what
  makes the baseline survive commit-before-sync and fresh clones.

Migration: keep `sync_snapshots` until direction inference is moved onto
`sync_watermarks`; then deprecate it. The watermark lives in its **own**
`SyncWatermarkCache` class (`clm.infrastructure.llm.cache`), alongside
`SyncSnapshotCache`, to honor that module's one-class-per-table convention —
same SQLite file, distinct table.

---

## 5. The sync algorithm (end to end)

```
1. Load DE/EN working trees → parse_cells.
2. Load sync_watermarks for the pair (per lang). If absent → cold start (§7).
3. Classify every sync-relevant cell per deck against its watermark:
     id-less / unknown id        → ADD       (direction = the deck it's on)
     id present, content drifted  → EDIT      (direction = the drifted side)
     id present, position moved   → MOVE      (deterministic, no LLM)
     id in watermark, gone        → REMOVE    (propagate deletion to sibling)
     same id drifted on BOTH      → CONFLICT  (isolate, default-skip)
4. Per-cell direction: each non-conflict cell carries its own de→en / en→de.
5. Build the plan (typed proposals): ADD / REMOVE / MOVE / UPDATE / CONFLICT.
     - For ADD: translate source → target (LLM), mint id from EN heading (§6),
       attach exact target-cell bytes + the id to stamp on the source cell.
     - For MOVE: target reorder ops by id-join (no LLM).
     - For UPDATE: existing SyncJudge proposal path.
6. Apply:
     - default        → write accepted units atomically to both working trees.
     - --dry-run      → print plan, write nothing.
     - --interactive  → walk proposals, write accepted/edited units.
7. On successful apply → record the new sync_watermarks rows for both decks.
8. Print the no-silent summary (counts + conflicts + baseline used).
```

LLM use is confined to **translating new-slide content** and the existing
**edit-propagation judge**. All structure (id-less detection, move/reorder,
remove, id minting mechanics) is deterministic → works with the LLM down
(adds are reported as blocked-not-dropped; see §7).

---

## 6. ID minting & EN-authority

- For a new slide, **generate the EN counterpart first**, slug the id from the
  **EN heading** (`assign_ids` slug rules: lowercase-kebab, ASCII, ≤30 chars,
  collision suffix), then **write that id to both siblings**. This preserves the
  EN-derived-id invariant even when the author only touched German.
- Reuse `assign_ids._write_slide_id` to stamp the header; reuse
  `clm.slides.pairing` slug/collision machinery and the `group_slug` caching
  idea so both siblings receive the *identical* id.
- **Stickiness/determinism:** once persisted, an id is never regenerated
  without `--force`. The `SyncCache` memoizes `(source_hash, target_hash,
  prompt_version) → translation`, so a re-run reproduces the same translation →
  same slug → same id. Minting is one-time.
- **Insertion positioning:** anchor a new target cell off the nearest
  *neighboring shared `slide_id`* — the same anchoring `_find_insertion_point`
  uses for voiceover companions (`voiceover_tools.py`). Runs of consecutive new
  slides anchor off the nearest stable id and order among themselves by source
  order.

---

## 7. Guardrails & error handling

| Situation | Handling |
|---|---|
| Commit before sync | Defeated — id-less survives the commit (§3.1) |
| Hand-assigned id on a new cell | Caught as "id unknown to watermark" → treated as ADD |
| Copy-paste duplicate id | Caught as id collision, surfaced (not mis-paired) — #162's check |
| Fresh clone, no watermark (cold start) | Pair by the decks' existing **shared ids** (committed with matching ids); an already-synced pair shows nothing to do; local un-synced edits fall back to **git-HEAD-per-deck** as the per-cell base |
| No watermark **and** no git, both sides differ | Bail to explicit `--source-lang` (today's behavior) — loud, never silent |
| First sync of a from-scratch one-language deck | All id-less → full translate + mint |
| LLM/translator unavailable | Structural ops still apply; un-translatable ADDs reported as blocked, never silently dropped |
| Both decks independently reordered | Order propagation refused + reported; per-cell content still syncs |
| Same id drifted both sides | CONFLICT: isolate, default-skip, list in summary |

**No-silent-no-op** is the universal backstop: every run states the baseline it
used and why it found 0 changes (in sync vs no baseline).

---

## 8. Component changes

| Module | Change |
|---|---|
| `clm.infrastructure.llm.cache` | New `SyncWatermarkCache` class + `sync_watermarks` table (ordered, per-lang, nullable `slide_id`); `get_deck` / `put_deck` (atomic whole-deck replace) / `has_pair` / `clear_pair`. |
| `slides/sync.py` | Replace pair-by-`(slide_id, role)` UPDATE-only walk with the §5 classifier; per-cell direction; emit typed proposals (ADD/REMOVE/MOVE/UPDATE/CONFLICT). Retire the `244-268` warnings and the `270-282` structural-mismatch error as the *normal* add/remove/move signal. |
| `slides/sync_direction.py` | Source per-cell direction from the watermark; keep global inference as the cold-start fallback. |
| `slides/sync_writeback.py` | `FileState` grows `insert` / `delete` / `move` alongside `replace_body`; watermark recording replaces/augments `record_snapshot`. |
| `slides/assign_ids.py` | Expose the EN-slug-from-translation minting path for reuse by sync (stamp both siblings, sticky). |
| New: translation path | Whole-new-slide translation prompts (distinct from the edit judge), routed through the OpenAI-compatible `_build_client` (`infrastructure/llm/client.py`) exactly like the voiceover `propagate_*` path. **Model fixed to Claude Sonnet (`anthropic/claude-sonnet-4-6`) via OpenRouter** (decided 2026-05-31), exposed as a `--translation-model` + `CLM_LLM__*` / `[tool.clm]` override so it is never hardcoded-only. Generalizing per-purpose model selection across CLM is a separate investigation (#167; `docs/claude/TODO.md` → *Uniform, per-purpose model configurability*). |
| `cli/commands/slides_sync.py` | Flip default to write-to-tree; `--dry-run` preview; extend `--interactive` walker to render ADD/REMOVE/MOVE/CONFLICT; new summary lines; exit codes account for conflicts. |
| `cli/info_topics/commands.md` | Update `clm slides sync` docs (default-flip, new behaviors) per the Info Topics Maintenance Rule. |

---

## 9. Work items (re-scoped from the issue)

| # (issue) | Item | v1 scope |
|---|---|---|
| 1 | Add / remove / move handling, not just update | **v1** — §3.2, §5 |
| 2 | Detect changes without relying on IDs | **v1** — id-less-as-new + structural watermark (§3.1, §4) |
| 3 | ID minting + EN-authority, written to both decks | **v1** — §6 |
| 4 | Insertion positioning (anchor off shared ids) | **v1** — §6 |
| 5 | ID stickiness / determinism | **v1** — §6 (SyncCache memoization → one-time mint) |
| 6 | Reframe "structural mismatch" error as normal signal | **v1** — §8 (`sync.py:270-282`) |
| 7 | Full-translation prompt suite | **v1** (with iteration room) — §8, §10 |
| — | Per-cell direction + conflict isolation | **v1** — §3.4 |
| — | Reorder propagation | **v1** — §3.2 |
| — | No-silent-no-op guardrail | **v1** — §7 |

## 10. Deferred / open (post-v1)

- **Full 3-way LLM merge** for true conflicts (watermark is the ancestor).
- **`--detect-moves`** content-similarity matching for delete-and-rewrite moves.
- **Partial-apply watermark staleness — *addressed for content-only passes in
  Phase 4 Part 2b*.** The watermark used to advance all-or-nothing
  (`_pass_is_clean`): a pass that reconciled some cells but deferred a conflict
  advanced nothing, so the reconciled edits re-surfaced as drift next run (judge
  no-ops them — safe but noisy). Phase 4 Part 2b adds a **per-cell** advance for
  a *content-only, issue-free* partial pass (only edit/conflict proposals): it
  banks every reconciled cell (applied edit, `in_sync`, unchanged) and preserves
  only the **true deferrals** (unresolved conflicts, user-skipped edits) at their
  pre-conflict baseline so they re-surface. Structural partial passes (any
  add/remove/move/rename), any pass carrying a *warning* issue (both-decks
  reorder, ambiguous de/en), and any deferral whose cell was removed on one deck
  still hold the whole watermark — the conservative, provably-safe fallback.
  Remaining noise on those held passes is the documented residual (a follow-up
  could extend the per-cell advance to structural passes, but that needs
  positional reasoning the content-only restriction sidesteps).
- **Non-uniform inter-group blank padding** (cosmetic) — group-level move-apply
  carries each group's own trailing blank padding verbatim, so reordering a deck
  whose slides have *different* numbers of trailing blank lines redistributes
  that spacing. The terminal-newline artifact is healed; genuine author
  whitespace is preserved (not normalized), so a reorder can produce a
  whitespace-only diff. Normalizing inter-slide spacing would violate the
  byte-preserving split/unify contract, so it's left as-is unless real-deck
  diffs prove noisy.
- **Prompt tuning & batching** — the *model* is settled (Claude Sonnet via
  OpenRouter, §3.3/§8); what remains open is prompt iteration, batching of
  multiple new-slide translations per call (à la voiceover `merge_batch`), and
  prompt-version cache keys. Quality is explicitly a tuning problem, not a
  blocker (#166 Decisions).
- **Uniform, per-purpose model configurability across CLM** — a separate
  investigation (#167, `docs/claude/TODO.md`): whether to unify model selection
  for every LLM purpose over
  the existing OpenAI-compatible + Ollama backends, configurable via
  `[tool.clm]` / `CLM_LLM__*` / CLI. #166 fixes Sonnet for translation now;
  that task generalizes it rather than adding more `DEFAULT_*` constants.
- **Self-contained vs git-corroborated removes** — whether to also consult
  `git diff HEAD` to corroborate removes when the watermark is stale.

## 11. Relationship to #162

- #162 = the cross-language `slide_id` **consistency invariant** and its
  detective (validator) + defensive (guard-rail) backstops.
- This issue = the **workflow** that needs it, with the assignment
  **mechanism** living inside sync (the generative directions #1/#3 of #162 are
  realized here: sibling-aware assignment / EN-authority, minted at sync time).
- #162's validator + guard-rail still matter independently, for hand-edited /
  non-sync / legacy split files the generative path never touches.

---

## 12. Phased implementation plan

Principles applied to every phase:

- **Independently testable in the fast suite** — the existing sync tests mock
  the `SyncJudge` (`judge=None` or injected); the translator is mocked the same
  way. No phase needs a live LLM to test.
- **Ships behind existing flags.** `--dry-run` stays the default until Phase 5
  flips it, so partial work never changes the command's default behavior.
- **Per-proposal atomicity and no-silent-no-op are honored from Phase 2 on.**
- Conventions: type hints on public APIs; `attrs @define` for internal
  structures / Pydantic at the worker boundary; `logging` not `print`; no
  fixed `sleep` in async tests. Gate releases on `pytest -m "not docker"`.

### Phase 1 — Structural watermark + classifier + per-cell direction (no writes, no LLM)

**Status: ✅ implemented 2026-05-31** — `SyncWatermarkCache`
(`infrastructure/llm/cache.py`) + `clm/slides/sync_plan.py` (classifier, plan
types, baseline resolution, `render_plan`). 50 tests
(`tests/slides/test_sync_plan.py` + `SyncWatermarkCache` cases in
`tests/infrastructure/llm/test_sync_cache.py`); mypy + ruff clean; the live
`clm slides sync` command is unchanged (not yet wired in).

The spine. Everything else consumes its plan.

- Add a `sync_watermarks` table + accessors to `SyncSnapshotCache`
  (`infrastructure/llm/cache.py`): ordered, per-lang, nullable `slide_id`,
  `content_hash` via the existing `cell_content_hash`.
- New classifier (new `sync_plan.py`, or within `sync.py`): parse both decks,
  diff each against its watermark + the id-less heuristic → a typed **plan** of
  proposals `{kind: add|edit|move|remove|conflict, direction, slide_id?,
  positions, hashes}`. **Per-cell direction is derived here.** ADD entries are
  marked "translation pending" (filled in Phase 3).
- Cold-start fallback: no watermark → pair by existing shared ids;
  git-HEAD-per-deck as the per-cell base; if neither is available and the sides
  differ, bail to explicit `--source-lang`.
- `--dry-run` report renders the plan; the no-silent summary distinguishes
  *in-sync* from *no-baseline*.
- **Tests:** fixtures of (deck pair + synthetic watermark rows) → assert the
  plan for each kind, per-cell direction, cold start, and no-baseline messaging.
- **Exit:** correct typed plans incl. conflicts + cold start; zero writes; zero
  LLM.

### Phase 2 — Writeback primitives + atomic apply for the deterministic kinds

**Status: ✅ implemented 2026-05-31 (incl. 2b move-apply)** — `FileState.find_cell
/ replace_cell_body / delete_cell` (keyed by `(slide_id, role)`) +
`clm/slides/sync_apply.py` (`apply_plan`: **remove** + **edit** + **move**,
atomic, watermark advances only on a clean complete apply *and* a real
baseline, via the shared `_pass_is_clean` predicate). 18 tests; mypy + ruff
clean. Live `clm slides sync` still unchanged — the apply engine is not yet
wired into the CLI.

MOVE-apply reorders the target deck's slide *groups* (slide/subslide + its
narrative companions + code) to match the source order by id-join. It commits
only when the reorder reconciles the **full `(slide_id, role)` order** with the
source (`_sync_key_order`); a narrative companion reassigned to a different
slide that a group-level reorder cannot express is deferred and surfaced, never
silently baselined.

Two adversarial-review rounds (15 agents then 8) found **11 confirmed issues,
all folded in**: cold-start `has_baseline` watermark guard; terminal-newline
preservation on last-cell delete *and* on move (the dragged `""` artifact);
language-filtered content index; `role_of`/`_cell_matches` de-duplication;
the narrative-reassignment silent-divergence (now deferred); the move-gate
`plan.has_errors` gap (now the shared `_pass_is_clean`).

- `FileState.insert / delete / move` (`sync_writeback.py`), preserving the
  header + trailing-blank round-trip the split/unify invariant depends on.
- Apply path for **MOVE** (reorder target by id-join), **REMOVE** (delete the
  sibling cell), and **EDIT** via the *existing* `SyncJudge` update proposal
  (now flowing in per-cell direction). The id-stamp mechanic reuses
  `assign_ids._write_slide_id`.
- **Per-proposal atomicity:** accepted units mutate both `FileState`s; one
  `flush` per deck; watermark rows recorded only on success. Skip/quit writes
  nothing.
- Still behind `--apply` / `--interactive`; `--dry-run` remains default.
- **Tests:** move/remove/edit-only pairs → atomic both-deck writes, watermark
  advances, re-run is an idempotent no-op, skip leaves the unit pending.
- **Exit:** deterministic + existing-edit sync round-trips atomically and
  idempotently.

### Phase 3 — New-slide translation + EN-authority ID minting (the ADD path)

**Status: ✅ implemented 2026-05-31** — `clm/slides/sync_translate.py`
(`SlideTranslator` protocol + `StaticSlideTranslator` + `OpenRouterSlideTranslator`,
model fixed to Sonnet) + the add path in `sync_apply.py` (`_apply_adds` /
`_add_one_direction`: translate → mint EN-authority id from the EN heading →
stamp both siblings → insert the counterpart at the anchor; narrative
companions inherit the slide's id). Adds run **before** moves and are sticky
via the stamp (so they apply even when the rest of the pass isn't clean).
`FileState` gained `insert_after` / `insert_before_first_sync_cell` plus
**separator-aware** placement (`separator_blanks` / `normalize_displaced_last`),
which keeps both blank-separated (real) and tight decks byte-clean and also
closed a *latent* Phase 2b move issue the tight test fixtures had masked.
29 apply tests (incl. blank-separated byte-equality). A 12-agent adversarial
review found 6 issues, all folded in: inter-cell separator on inserts; heading
extraction discarding bold/dash lead-ins; the slide-less-target append heal +
ordering; parallel id-less adds on both decks (now deferred, not duplicated);
translator leading-newline strip + `max_tokens`. Deferred follow-up:
id-carrying "missing counterpart" adds. Live `clm slides sync` still unchanged.

- Translate a new source cell → target counterpart. New prompt suite (distinct
  from the edit judge); routed through `_build_client` like the voiceover
  `propagate_*` path (structured JSON). **Model fixed: Claude Sonnet
  (`anthropic/claude-sonnet-4-6`) via OpenRouter**, with a `--translation-model`
  / `CLM_LLM__*` override. Memoize via `SyncCache`
  (`(source_hash, prompt_version) → translation`) for **stickiness**.
- Mint the id: if the source is DE, **translate EN first**, slug from the EN
  heading (`slug` / `pairing` machinery), and stamp **both** siblings the same
  id.
- **Anchored insertion** via the `_find_insertion_point` precedent; runs of
  consecutive adds order among themselves by source order off the nearest stable
  shared id.
- Wire ADD into the atomic apply (stamp source id + insert target counterpart =
  one unit).
- **Tests (translator mocked):** add an id-less slide → counterpart inserted at
  the anchor with an identical minted id on both decks; re-run idempotent (no
  longer "new"); `--force` regenerates; EN-authority holds when only DE was
  edited.
- **Exit:** full add propagation with consistent, sticky ids on both decks.

### Phase 4 — Interactive walker + conflict UX + summary / exit-codes

**Part 1 — copy-paste duplicate-id resolution ✅ implemented 2026-05-31.** A
new `rename` proposal kind. The classifier (`_resolve_duplicates`) resolves
duplicate ids at the **slide-group** level: it identifies the original group
(its slide matches the baseline) and emits one `rename` per copy slide;
companions follow. It only resolves against a real both-deck baseline and
**errors** (never guesses) when it can't identify the original or a duplicate
isn't explained by a copied slide group. The apply (`_identify_copy_slides` +
`_add_one_direction`) re-mints the copy slide **by position** (so byte-identical
copies don't bind to the wrong cell) and its trailing same-id companions by
group-adjacency. Two **fail-safe guards** — `_flag_residual_duplicates`
(in-deck) and `_flag_cross_deck_orphans` (clean-pass parity) — error so the
watermark can never advance over a corrupt/divergent state.

Two adversarial-review rounds (the first found the feature corrupted copied
slide *groups* with companions; the rewrite + a second review fixed the
phantom-`remove` regression, the identical-slide/edited-companion desync, and
the malformed-companion divergence). Known limitation (pre-existing, broader
than this feature): a structural change is flushed to the working tree even on
an erroring pass — it is surfaced + `git diff`-visible + revertible, and the
watermark never advances over it, but the working tree is mutated.

**Part 2a — Interactive review walker + conflict UX + exit codes ✅ implemented
2026-05-31.** New `clm/slides/sync_plan_walker.py` (`run_plan_walker` →
`PlanWalkResult`) renders every proposal kind and prompts per proposal, then
calls `apply_plan` **once** (atomic; the walker writes nothing itself).
`apply_plan` gained an optional `decisions` map (keyed by `id(proposal)`):
`apply`/`skip` gate edit/remove/move, `de-wins`/`en-wins` resolve a conflict by
re-casting it as an edit flowing the winning direction; `decisions=None` is the
**batch default and stays byte-identical** to the pre-change engine (63 batch
tests unchanged). Conflicts render two-up (current DE vs current EN) — *not* the
designed three-up, because the watermark stores only content hashes, not base
*content*; a true base column needs the watermark to also store bodies (or a
git-HEAD lookup in the git-fallback case), deferred as a refinement. Add/rename
are intentionally **always-applied** (non-destructive; reviewed in the resulting
`git diff`) rather than per-proposal skippable — this avoids a risky refactor of
the Part-1-reviewed add path; the interactive gate covers the judgment-heavy
kinds (edit/remove/move/conflict). Exit codes: 2 (plan/apply error) / 1
(anything deferred) / 0 (clean). The new walker lives alongside the legacy
`sync_walker.py` (which still serves the legacy `sync.py` engine wired to the
live CLI); Phase 5 swaps the CLI onto this engine. The summary is split into an
honest **decisions** line (what the author chose) and an **outcomes** line (what
the engine wrote / deferred / errored) so an accepted-but-errored edit or a
deferred id-carrying add is never reported as "applied". **Watermark stays
all-or-nothing here** (the documented-safe behavior): any skip/defer/quit
increments `deferred`, and `_pass_is_clean` holds the watermark — nothing
un-applied is ever baselined. A 9-agent adversarial-review Workflow (5 review
dimensions × adversarial verify) confirmed the safety invariant intact and found
**2 medium issues, both fixed**: the walker mislabeled id-carrying adds as
"auto-applied" (now rendered/counted as deferred), and the suite lacked a
mixed-accept+skip watermark-held test (added). 25 walker tests; 711 slides tests
green; mypy/ruff clean. Live `clm slides sync` STILL UNCHANGED.

**Part 2b — Per-cell watermark advance ✅ implemented 2026-05-31.** A
content-only, issue-free partial pass now advances per-cell: it banks every
*reconciled* cell and preserves only the *true deferrals* at their pre-conflict
baseline so those re-surface. The watermark block is gated top-level by
`not plan.issues` (both the full and partial paths), with a partial path behind
`_eligible_for_partial_advance` (content-only: zero add/remove/move/rename
proposals, real baseline, no errors, ≥1 deferral, ≥1 applied edit) + a
completeness invariant (`len(deferred_keys) == result.deferred`) +
`_record_watermark_partial`, which preserves `deferred_keys` (unresolved
conflicts + user-skipped edits) and banks the rest.

**The load-bearing semantic decision: `in_sync` is a *reconciliation*, not a
deferral.** Per `SyncProposal`, an `in_sync` verdict means "the target already
adequately reflects the source" — the judge examined both sides and decided no
write is needed. So an `in_sync` edit **banks** (advances), exactly like an
applied edit and exactly like the long-standing full-advance path. Preserving it
instead would re-propose every judge-declined cell *every run forever* (the judge
re-declines), and in real syncs most edits are `in_sync` — so banking is required
for the tool to be usable. Only **unresolved conflicts** and **user-skipped
edits** (an explicit "not now") re-surface.

This part was the **deepest rabbit hole of the issue** — four adversarial-review
rounds. The first two reviews (run under a too-strict "any un-written cell must
re-surface" invariant I had seeded) flagged `in_sync`-banking as data loss; I
over-corrected to a preserve-`in_sync` design, then recognized that re-surfacing
`in_sync` infinite-loops and reverted to the correct bank-`in_sync` semantics.
The reviews surfaced **three genuine bugs, all fixed**: (1) the full-advance path
ignored `plan.issues`, so a both-decks-reorder *warning* could be silently
baselined → fixed by gating both paths on `not plan.issues`; (2) the earlier
preserve-by-`applied_keys` variant lost data via the full path — dissolved by the
correct `in_sync`-banks semantics; (3) a **"removed on one deck / edited on the
other" collision is classified as a `conflict`, not a `remove`**, so it slipped
the structural gate, and since the cell is gone from the removing deck the
partial advance dropped the preserve-key and next run mutated it into a *phantom
add re-creating the deleted slide* → fixed by also requiring every preserve-key
to be present in **both decks' current cells** (else hold). The final fix is
monotonically safe (it only adds holds, never advances). 12
`TestPartialWatermarkAdvance` tests; 723 slides tests green; mypy/ruff clean.
Lesson logged: never seed an adversarial review with an unproven invariant — it
manufactures false positives.

Live `clm slides sync` STILL UNCHANGED (Phase 5 wires the engine + flips the
default).

Original Phase 4 spec (for reference):

- Extend the walker to render ADD / REMOVE / MOVE / CONFLICT (today
  UPDATE-only). *(done in 2a — a new `sync_plan_walker` over the new engine.)*
- Conflict three-up (watermark base / current DE / current EN) with
  `[d]e-wins / [e]n-wins / [s]kip`; non-interactive leaves conflicts untouched
  and lists them in the summary. *(2a ships a two-up; base-content column
  deferred — see above.)*
- New summary lines + exit codes: conflicts and unresolved proposals → exit 1
  ("needs review"); structural/LLM errors → exit 2 (keep today's buckets).
  *(done in 2a.)*
- **Tests:** walker over all kinds; conflict-resolution paths; the exit-code
  matrix. *(done in 2a.)*
- **Exit:** every proposal kind is reviewable; conflicts resolve or defer
  cleanly. *(met in 2a.)*

### Phase 5 — Default-flip + docs + deprecate old inference + pilot / CHANGELOG

**Status: ✅ implemented 2026-05-31.** `clm slides sync`
(`cli/commands/slides_sync.py`) is rewritten onto the new engine:
`build_sync_plan` → `apply_plan` (batch) / `run_plan_walker` (interactive) over
the `SyncWatermarkCache`. The **default now writes to the working tree**;
`--dry-run` is the explicit preview; `--interactive` prompts per proposal before
a single atomic apply. Direction is per-cell from the watermark — the **global
`--source-lang` flag and the `sync_snapshots`-based inference are gone** — and a
both-decks edit is isolated as a `conflict`. The legacy `--apply` / `--trivial`
flags are **removed** (the default already applies; `--interactive` gates). New
`--translation-model` (OpenRouter Sonnet) drives the add path. The JSON report
carries `mode` / `exit_code` / `plan` / `apply` / `walker` blocks (the pilot
accept-rate counters). Info topics (`commands.md` `clm slides sync` section +
a `migration.md` breaking-change entry) and the CHANGELOG are updated per the
**Info Topics Maintenance Rule**.

The legacy engine modules (`sync.py`, `sync_walker.py`, `sync_trivial.py`,
`sync_direction.py`) are left **dormant** (still unit-tested) rather than deleted
— nothing in `src` imports them now that the CLI is rewired; pruning them is a
follow-up so this phase's diff stays focused on the CLI + docs.

Decision (user, 2026-05-31): both `--source-lang` and `--apply`/`--trivial` are
**hard-removed**, not deprecated-and-ignored — a clean pre-1.0 surface for a
feature with no external users yet (the migration topic documents the break).

Original Phase 5 spec (for reference):

- **Flip the default** to write-to-working-tree (never commit); `--dry-run`
  becomes the explicit preview. Update CLI help text.
- Migrate direction inference fully onto the watermark; deprecate the
  `sync_snapshots` global-direction path.
- Update `clm info commands` (`cli/info_topics/commands.md`) and the migration
  topic per the **Info Topics Maintenance Rule**; add a CHANGELOG entry.
- Pilot counters (reuse the `SyncResult` counters) for the accept-rate metric;
  document the prompt-iteration hooks.
- **Exit:** the one-command workflow is live end-to-end, docs are
  version-accurate, and `pytest -m "not docker"` is green.

### Phase 6 — Code cells + auxiliary markdown (the structural pass)

**Status: ✅ implemented 2026-06-01.** Phases 1–5 scoped sync to narrative
`(slide_id, role)` markdown only; a real editing pass (the `review_w01_w06`
repro) showed that dropping every **code cell** and every untagged / `alt`
markdown cell makes the result incoherent (translated headings over stale code).
Phase 6 closes that gap **without changing the watermark schema**:

- **Role extension** (`sync_writeback.role_of`, the one predicate the classifier
  and apply engine both use): a **localized** code cell (`lang=` *and*
  `slide_id`) gets the synthetic role `"code"`; an **aux** markdown cell (a
  `slide_id` but no narrative tag) gets its first tag, else `"markdown"`. These
  flow through the existing per-cell add/edit/move/conflict machinery. A code
  `edit` is reconciled by **re-translating** the source body (a code-aware
  translator prompt), not the markdown judge.
- **id-carrying adds** (`sync_apply._add_idcarrying_one_direction`): a new cell
  minted with a `slide_id` on one side only is translated and inserted under the
  **same** id (no minting, no collision). Previously deferred as out-of-scope.
- **Structural pass** (`sync_code.apply_code_structure`), run after the per-cell
  apply: for each slide group whose **structural signature** drifted from the
  edited side, rebuild its cell order from the source — **language-neutral**
  cells (no `lang`) copied verbatim, **id-less localized** cells translated, and
  every per-cell-synced cell pulled back in by `(slide_id, role)`. The signature
  is language-agnostic (`("R", role)` for sync cells, `("S", body)` for shared,
  `("L", kind)` for id-less localized, `("J",)` for a j2 header) so a narrative
  edit or a `header_en`/`header_de` difference never triggers a rebuild, while a
  code/shared change or a cross-group code move does. Untouched groups stay
  byte-for-byte. Direction comes from the run's proposals (uniform `en->de` /
  `de->en`); a pass with no single direction skips the structural step.

Key invariants kept: language-neutral / id-less code is **never** minted a
`slide_id` (so re-runs stay no-ops via the source-vs-target signature, not the
watermark), and the j2 header macro is treated as language-specific
(target's own header is kept). Also in this phase: `clm slides sync` loads the
project `.env` (`cli/env_loading.py`, shared with `clm build`) so keys kept in
`.env` reach the judge/translator; the OpenRouter judge/translator retry
transient failures with backoff (`infrastructure/llm/retry.py`); and the local
`--llm-timeout` default is provider-aware (300s).

Known limitations (documented, surfaced — not silent): a *code-only* change with
**no** narrative/id'd proposal and identical shared cells provides no direction
signal and is not propagated (an author virtually always also touches a
slide/voiceover); and an unchanged **id-less** localized code cell inside a group
rebuilt for another reason is re-translated (churn, not corruption).

### Dependency graph

```
Phase 1 (watermark + classifier)
   ├── Phase 2 (deterministic apply)
   │      └── Phase 3 (translation + minting)  ← also needs the translator/prompt suite
   │             └── Phase 4 (walker + conflict UX)
   │                    └── Phase 5 (default-flip + docs)
   │                           └── Phase 6 (code cells + aux markdown; structural pass)
   └── (per-cell direction lands in Phase 1; old-inference deprecation in Phase 5)
```
