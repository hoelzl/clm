# Content-Anchor Cell Identity for Sync — Design Note

**Status**: Design agreed with the maintainer (pre-implementation)
**Author**: Claude (Opus 4.8), with the maintainer
**Date**: 2026-06-01
**Scope**: `src/clm/slides/sync_plan.py`, `sync_code.py`, `sync_apply.py`,
`sync_writeback.py`, `src/clm/infrastructure/llm/cache.py`
(`SyncWatermarkCache`), and the `clm slides sync` CLI
**Issues**: [#190](https://github.com/hoelzl/clm/issues/190) (the three known
limitations) · builds on [#166](https://github.com/hoelzl/clm/issues/166) (the
single-language authoring workflow) · serves
[#162](https://github.com/hoelzl/clm/issues/162) (the cross-language `slide_id`
invariant)

Companion to `single-language-authoring-sync.md` (the #166 engine this extends).
That note built the per-`(slide_id, role)` engine; this one fixes the three
limitations #190 tracks **without changing that engine's contracts** — it is
additive.

---

## 1. The three limitations (and which are serious)

From #190, against the engine shipped in #166 Phase 6:

1. **Partial-apply atomicity.** A pass that errors mid-way (e.g. an LLM failure)
   still flushes the edits that succeeded. State is reviewable via `git diff` and
   the watermark is held, but the write is not transactional. *(Maintainer: not
   bothersome; a buffered temp-swap is fine.)*
2. **A code-only change with no narrative/id'd signal is not propagated.** The
   structural pass takes its direction from the run's narrative proposals; a pass
   that edits *only* a shared/language-neutral code cell has no proposal, so no
   direction, so nothing propagates. *(Maintainer: **serious** — this happens
   **frequently** when fine-tuning a deck.)*
3. **An unchanged id-less localized code cell is re-translated on a rebuild.**
   When a slide group is rebuilt for any reason, every id-less localized code
   cell in it is re-translated, because its structural signature `("L", kind)`
   cannot prove it is unchanged. *(Maintainer: **not acceptable** — they go to
   great lengths to keep content byte-identical across translations.)*

## 2. Root cause — two gates, not one

Cell identity is decided by **one predicate**, `sync_writeback.role_of()`: a
cell participates in per-cell reconciliation only if it is narrative markdown, an
id-carrying aux markdown cell, or a **localized** code cell (has *both* `lang=`
*and* `slide_id`). Everything else returns `None` and is handled — coarsely — by
the structural pass in `sync_code.py`.

But broadening `role_of` is **not sufficient**, because there is a **second
gate** the obvious fix misses:

- `sync_plan.ordered_sync_cells()` drops every cell whose `lang != expected_lang`
  (sync_plan.py:211). A **language-neutral** cell (`lang is None`) is therefore
  dropped from *both* the `de` and `en` cell lists.
- The classifier only computes `edit` / `move` / `remove` for cells that **carry
  a `slide_id`** (`_index_by_key` routes id-less cells to an "idless" list;
  `_baseline_index` skips `slide_id is None`). An id-less cell can only ever
  become an `add`.

> **Consequence:** stamping a `slide_id` onto a language-neutral code cell does
> **not** make the engine see it. It is still dropped by the `lang` filter, and
> still modeled as a `de`/`en` *pair* when it is really *one shared cell*.

This is why the intuitive fix — *"before anything else, assign a unique id to
every cell"* — is the right **goal** but the wrong **mechanism**. §3 measures the
cost; §4 picks the mechanism.

## 3. What the real decks look like

Census of the PythonCourses course repo (`slides/`, 212 split decks, 19,787
cells), 2026-06-01:

| Population | Count | Relevance |
|---|--:|---|
| markdown cells | 10,861 | mostly already handled |
| code cells | 8,926 | — |
|   • language-neutral, id-less (`# %%`, `tags=["keep"]`) | ~6,700 | **item 2** (~89.5% of code) |
|   • localized (`lang=`), of which ~740 id-less | 936 | **item 3** |
|   • code already carrying `slide_id` | 548 | the `def-my-fun` pattern — real, ~6% |
| **any** cell already carrying `slide_id` | 6,638 | author has already adopted ids widely |

Two facts drive the design:

- **A blanket "id on every cell" is a ~13,000-id write** (≈66.5% of all cells) —
  a one-line `git diff` on two-thirds of every deck, i.e. exactly the churn on
  content-unchanged cells the author wants to avoid. The cure becomes the
  disease.
- **Both failure populations are large and want different treatment.** Item 2 is
  ~6,700 *shared* cells (copy verbatim); item 3 is ~740 *localized* cells
  (translate once, then never again). A fix must serve both.

Maintainer note: the neutral fraction is ~90% here, perhaps 80–85% on the AZAV ML
course, and authoring style can be adjusted to *reduce* the localized-id-less
population if that smooths the process — so the design should make the
language-neutral path the cheap, dominant one and keep the localized path simple.

**Phase-0 measured baseline (2026-06-01, `scripts/sync_corpus_harness.py`).** The
harness reproduces the §3 census exactly (md 10,861; code 8,926; localized
744 id-less + 192 id'd = 936; def-my-fun neutral+localized id'd = 356 + 192 =
548; cells-with-id 6,638; 20,635 cells − 848 `# j2` = 19,787), and pins the
churn the two serious limitations expose today:

| Baseline | Count | Phase that drives it to 0 |
|---|--:|---|
| **item 2** silent-drop exposure (neutral cells with no per-cell role) | **8,014** (7,634 neutral id-less code + 356 neutral id'd code + 24 neutral md) | Phase 3 |
| **item 3** needless-re-translation exposure (id-less localized cells) | **1,702** (744 code + 958 md) | Phase 2 |
| item-3 blast radius | 948 groups expose ≥1, max 10 in one group | — |
| no-op invariant | 81 / 212 pairs already post-sync-clean; **0 violations** | every phase must hold |

(The other 131 pairs are *not yet synced* — they carry 3,793 id-less narrative
cells that predate #166 adoption, a data condition, not engine churn.) The
mechanism for each limitation is pinned as a fast, synthetic flip-test in
`tests/slides/test_sync_limitations.py`; the corpus invariant + magnitude in the
`slow`/`integration` `tests/slides/test_sync_corpus_noop.py`.

## 4. Core decision — identity in the DB, via content anchors

**Give every cell a content-derived *anchor*, computed at sync time and stored in
the watermark. Never write ids into id-less cells.**

```
anchor(cell):
    1. hand-authored slide_id           # top precedence — the author keeps full control
    2. extract_from_code() construct     # "function my_fun", "class X", "import time" — deterministic AST name
    3. sha256(stripped body)             # fallback for output / magic / unparsable cells
```

`extract_from_code` (already used by `assign-ids`) yields a stable, deterministic
construct name by AST precedence (class → def → assign → import → call). The
anchor is a **pure function of content**, so it is always re-derivable,
git-commit-immune, and adds **zero** header churn.

**Why the DB, not the file** (id-visibility call): the anchor is re-derivable and
the watermark already plays exactly this baseline-snapshot role (written only on
a clean apply, immune to commit cadence). Storing identity in the file would mean
the ~13k-id churn of §3 *and* a `unify` hazard (§7). The **only** time sync
writes a `slide_id` into a file is a genuine, author-wanted *correction* (§9) —
never a bulk stamp.

Honest costs of going invisible, all judged acceptable:

- a stale hand-authored id can sit on the "wrong" cell until a correction fires
  (cosmetic; sync stays correct);
- identity can drift from a file edited entirely outside the tool — covered by
  the existing `_baseline_from_git_head` re-deriving anchors from HEAD;
- "why did it sync that way" needs a `clm slides sync --explain` that dumps the
  anchor diff, since the `.py` no longer tells the whole story.

Crucially, because anchors are re-derivable and hand-ids are top-precedence,
**authors who want stable readable ids keep full control** — the scheme is
additive, never a replacement.

## 5. Data model — the widened watermark

Today `sync_watermarks` (cache.py:506) stores only `role_of != None` cells, as
4-tuples `(position, slide_id, role, content_hash)`, partitioned by `lang ∈
{de, en}`. Three additive changes:

1. **A `construct` column** (nullable TEXT). `SyncWatermarkCache._migrate`
   currently handles only the empty-table case — add the additive branch:
   `if "construct" not in columns: ALTER TABLE sync_watermarks ADD COLUMN
   construct TEXT`. Widen `get_deck` / `put_deck` to 5-tuples
   `(position, slide_id, role, content_hash, construct)`.
2. **A `"shared"` partition** for language-neutral cells — the single-entity
   model. Neutral cells are tracked **once** (not duplicated under `de` and
   `en`), under `lang="shared"`. Relax the `put_deck` guard from
   `lang in ("de","en")` to `lang in ("de","en","shared")`. The invariant on the
   shared partition: the `.de.py` and `.en.py` physical copies are byte-identical
   (the same invariant `unify` enforces — §7); a divergence is the item-2a
   heal/conflict case.
3. **Membership widening** — the watermark records *every* non-`j2` cell, not just
   `role_of != None` ones. A neutral cell's row carries `slide_id` (it may have a
   hand-id, e.g. the 548), `role` = a synthetic kind (`"neutral-code"` /
   `"neutral-markdown"`), `content_hash`, and `construct`. A localized id-less
   cell carries `slide_id = NULL`, `role` = `"localized-code"` /
   `"localized-markdown"`, plus `construct`.

The anchor is **derived** from a row (`slide_id or construct-slug or
content_hash`), so no separate `anchor` column is needed.

Schema/path hygiene (recurring CLM hazards): the anchor's `sha256` must hash the
**same canonical stripped form** as `cell_content_hash` (or CRLF/LF flapping
re-introduces item-3 churn), and `de_path`/`en_path` must be normalized
identically to the other caches at every touch (Windows casing/separators).

## 6. The new anchor-keyed diff pass (the load-bearing piece)

The missing component every naive approach assumes but none specifies: a diff
pass that runs **alongside** the existing `(slide_id, role)` classifier and emits
into the **same `SyncPlan`** the apply engine already consumes — because the
keyed classifier *structurally cannot* see id-less or language-neutral cells
(§2).

```
align_anchored(current_cells, baseline_rows, partition) -> proposals
  - extract every non-j2 cell with its anchor + content_hash + kind + lang
  - diff current-vs-baseline BY ANCHOR:
        same anchor, same content_hash   -> UNCHANGED  (reuse verbatim — fixes item 3)
        same anchor, diff content_hash   -> EDIT
        anchor in baseline, gone         -> REMOVE
        anchor new                       -> ADD
  - reuse the existing LCS / _moved_keys machinery on the stable-anchor subset
        for MOVE detection (unchanged content, repositioned)
  - feed edit/move/add/remove Proposals into the SAME SyncPlan
```

This runs for the cells the keyed classifier cannot reach (the `"shared"`
partition + localized id-less cells); the keyed classifier keeps owning narrative
+ id'd cells unchanged. The two passes are reconciled **at the proposal level**
(one `SyncPlan`), and the §11 corpus-wide no-op harness guards against
double-emission or drops.

## 7. Item 2 — code-only changes propagate

Split by cell class, because they want different handling:

### 7a. Language-neutral shared cell, edited one side (≈90% of code) — no LLM

`unify` requires a no-lang shared cell to be **byte-identical** across decks
(split.py:377 → `UnifyError`). So an author editing a shared `# %%` cell in
`.de.py` only has put the pair into a **unify-violating** state. The fix is not
translation or direction inference — it is a **deterministic verbatim copy** to
the twin, restoring the invariant. The anchor diff (§6, `"shared"` partition)
detects "edited on the deck that drifted from baseline" with no narrative signal
needed.

**Divergence (both sides edited the shared cell differently since baseline)** —
the genuine conflict. Maintainer decision: **auto-heal + warning**, with a config
fallback to hard-error.

- Winner selection: (i) the run's established edit direction if one exists (the
  deck the author touched this session — the common case); else (ii) the
  newer-mtime file as a tiebreak; else (iii) no signal at all → treat as an error
  even in auto-heal mode (cannot heal without a winner).
- Always emit a `warning` naming the cell.
- Knob `[tool.clm] sync.shared_divergence = auto-heal | error` (env
  `CLM_SYNC__SHARED_DIVERGENCE`), default `auto-heal`. Flip to `error` if
  auto-heal proves problematic in the pilot.

### 7b. Localized id-less code cell, edited (≈740 cells) — translate, with direction

Genuinely needs translation. Direction comes from the source the engine already
trusts for narrative — the per-pair `SyncSnapshotCache` direction auto-detection
/ explicit `--direction` — **not git** (§10). The anchor diff classifies it as an
`edit`; the apply re-translates only that cell.

## 8. Item 3 — unchanged localized code is never re-translated

The cheapest, highest-value change, doable first and in isolation:

- In `sync_code._signature`, replace `("L", kind)` with `("L", anchor)` so the
  signature carries identity, not just kind.
- In `sync_code._rebuild_region`, add an **anchor + content_hash reuse path**:
  when a source cell's anchor and content_hash match the existing target twin
  (located by anchor, the data §5 now stores), splice the **existing target
  `RawCell` verbatim** — no `_translate` call.

A group rebuilt for a *sibling's* sake now re-translates **only** the genuinely
changed cell. This uses only data the widened watermark already stores and
touches one well-isolated function.

## 9. The `def-my-fun` split — deterministic id-migration

The maintainer's real example: an author adds an `import` by splitting a cell,
leaving the existing `slide_id="def-my-fun"` on the *import* half:

```
BEFORE                                   AFTER
# %% tags=[...] slide_id="def-my-fun"     # %% tags=[...] slide_id="def-my-fun"
def my_fun():                             import time
    print("foo")                          # %% tags=["keep"]
                                          def my_fun():
                                              time.sleep(1)
                                              print("foo")
```

Handled deterministically, no LLM, **one** targeted header write:

1. Baseline records id `def-my-fun` on a cell whose `construct` is
   `function my_fun`.
2. After the edit the cell *wearing* `def-my-fun` has `construct` `import time`
   (id says def, content says import), while a different, id-less current cell has
   `construct` `function my_fun`, matching the baseline.
3. This **id-vs-construct mismatch** is unambiguous — the id follows the
   construct. So: move `slide_id="def-my-fun"` down to the def cell
   (`_write_slide_id`, lines[0]-only, byte-contract-safe), and mint a fresh
   content-slug (`import-time`) on the orphaned import cell.
4. The def cell here is **language-neutral** (no `lang` in the example), so it is
   byte-identical in both files including the id — the move is one logical edit
   applied identically to both physical copies; `unify` byte-identity is
   preserved with **no asymmetry**. *(If the cell were **localized** (`lang=`),
   the move+mint must be applied symmetrically to both decks' twins so
   `_slide_ids_pair`'s `de_id == en_id` holds — route every localized id-write
   through one paired chokepoint.)*
5. The def **body** changed (`time.sleep(1)`), so it is a legitimate `edit`,
   translated once if localized; every other cell in the group, proven unchanged
   by anchor+hash, is reused verbatim (§8).

Maintainer decision: this id-move **writes to the file by default** — it corrects
an id the author already placed onto the content it belongs to; it only ever
touches already-id'd, drifted cells. Escalation to the LLM tier (§10) happens
*only* if the author also renamed the function in the same edit (so the construct
match also fails) — a genuine ambiguity.

## 10. Bounded LLM (Opus) recovery — the residue only

For the genuine residue — simultaneous rename, true N:1 merge/split, ambiguous
ties (two `def my_fun`, many bare imports) — escalate to Claude, **bounded**:

- A new `sync_alignments` cache table, keyed by
  `(base_region_hash, current_region_hash, prompt_version)`.
- A **body-free, alignment-only** call: the model returns an id↔cell *map*, never
  free-form edits. Validate every map (each current cell mapped once; ids reused
  only from the base set; unchanged-anchors pinned; kind/lang-compatible) and
  **safe-abort to no-change-plus-flag** on any validation failure.
- Maintainer decision: **defer-by-default** — an ambiguous group is left
  untouched and re-surfaces next run; the LLM fires only under an explicit
  `--llm-recover`.

Change-signal reliability (the maintainer asked which sources to trust):

| Signal | Use |
|---|---|
| **watermark** | primary baseline |
| **content anchors** | the new identity spine (§4) |
| **git HEAD of the file** | cold-start corroborator only (already used) |
| **git inter-commit (`HEAD~1`)** | **avoid** — CLM squash-merges collapse it; rename detection across squashes is fragile |
| **`SyncCache` translation memo** | **avoid as identity** — keyed by `source_hash→translation`; it is a memo, not a twin *index*; it cannot locate a cross-language counterpart |
| **Claude (Opus) alignment** | bounded last tier (above) |

## 11. Atomicity (item 1)

`sync_apply.apply_plan` calls `de_state.flush()` / `en_state.flush()`
unconditionally (sync_apply.py:225-226); the error guard at line 240 only
protects the *watermark* advance, not the disk write. Wrap the two flushes in a
**buffered temp-swap**: build both decks' new text in memory, and write
(temp-file + atomic replace, per deck) **only if the whole pass is error-free**;
on any error, write nothing and surface the deferral. This is a clean
prerequisite that every later phase relies on.

**Shipped (Phase 0).** `FileState.render()` factors the exact flush bytes out of
`flush()` (no behaviour change for the v1/v2 walkers that still call `flush`),
and `apply_plan` now calls `_flush_states_atomically(de_state, en_state)` —
gated on `not result.has_errors` — which renders both decks in memory and swaps
each in with `_atomic_write_text` (same-dir temp file + `os.replace`, utf-8/LF,
byte-identical to the old path). A deferred-but-error-free pass still writes (the
applied edits + partial-advance are the designed outcome); only a genuine error
rolls the whole pass back. The one residual gap — the window *between* the two
`os.replace` calls — is as small as a two-file write allows. Interactive
`--interactive` inherits this (the walker routes through `apply_plan`).

## 12. Guardrails & failure modes

| Situation | Handling |
|---|---|
| Shared cell edited one side | Verbatim copy to twin (restores `unify` invariant) — §7a |
| Shared cell diverged both sides | Auto-heal to winner + warning; `sync.shared_divergence=error` to harden — §7a |
| Unchanged localized code, group rebuilt | Anchor+hash match → spliced verbatim, no translate — §8 |
| Id drifted onto wrong content (split) | Deterministic id-migration, one header write — §9 |
| Localized id-write | Symmetric both-deck chokepoint (`de_id==en_id`) — §9 |
| Non-unique construct (two `def f`, many imports) | `content_hash` tiebreak (order-invariant); else escalate — §10 |
| `sha256`-fallback cells (unnameable output/magic) | Churn on every edit (look like remove+add); honest residual — hand-id the rare ones |
| Author commits before sync | Anchors survive (content-derived); watermark immune — §4 |
| Re-run after clean sync | Zero writes, zero LLM, zero churn (the §11 harness invariant) |
| Cold start / first post-upgrade run | `_baseline_from_git_head` re-derives anchors; HEAD-identical cells treated as `same` |
| File edited outside the tool | Identity re-derived from content; git-HEAD fallback corroborates |

## 13. Component changes

| Module | Change |
|---|---|
| `infrastructure/llm/cache.py` | `SyncWatermarkCache`: additive `construct` migration; 5-tuple `get_deck`/`put_deck`; accept `"shared"` partition; membership widened to all non-j2 cells. |
| `slides/sync_writeback.py` | `role_of` unchanged (keystone preserved); add an `anchor_of(cell)` chokepoint (`hand-id > construct > sha256`); synthetic kinds for neutral/localized-id-less. |
| `slides/sync_plan.py` | New `align_anchored` pass (§6) feeding the same `SyncPlan`; `ordered_all_cells` (broad extraction) alongside `ordered_sync_cells`; single-entity neutral model in `classify_changes`. |
| `slides/sync_code.py` | `_signature` `("L", anchor)`; `_rebuild_region` anchor+hash verbatim-reuse; consume the shared-partition heal. |
| `slides/sync_apply.py` | Buffered temp-swap atomicity (§11); deterministic id-migration apply (§9); symmetric localized id-write chokepoint. |
| New `sync_recover.py` (Phase 5) | Bounded, validated, cached Opus alignment recovery; `sync_alignments` table. |
| `cli/commands/slides_sync.py` | `--llm-recover` (default off); `--explain` (anchor-diff dump); `sync.shared_divergence` knob. |
| `cli/info_topics/{commands,migration}.md` | Per the **Info Topics Maintenance Rule** — new flags + the auto-heal behavior. |

## 14. Phased implementation plan

Each phase is independently testable in the fast suite and preserves the no-op
invariant. Gate releases on `pytest -m "not docker"`.

0. **Atomicity + harness. ✅ Shipped (2026-06-01).** Buffered temp-swap (§11).
   A no-op regression harness (`scripts/sync_corpus_harness.py`) over the real
   212-deck / ~20k-cell corpus asserting a clean re-run writes zero bytes and
   makes zero LLM calls — backstop in `tests/slides/test_sync_corpus_noop.py`
   (`slow`/`integration`), mechanism flip-tests in
   `tests/slides/test_sync_limitations.py` (fast). Measured baseline in §3:
   item-2 exposure 8,014, item-3 exposure 1,702, 0 invariant violations.
1. **Widen the watermark** (§5): `construct` migration, 5-tuples, `"shared"`
   partition, membership. `anchor_of` chokepoint. No behavior change yet.
2. **Item 3 first** (§8): anchor+hash verbatim reuse in `sync_code`. Highest
   value, lowest risk, one isolated function.
3. **Item 2** (§7): the `align_anchored` pass (§6) + single-entity neutral model
   + auto-heal/warn. The largest new module; default-on-overridable (mirroring
   the #166 default-flip).
4. **Deterministic `def-my-fun` id-migration** (§9): the one gated file-write,
   strictly scoped to already-id'd, drifted cells; symmetric localized chokepoint.
5. **Bounded Opus recovery** (§10): `sync_alignments` table; `--llm-recover`
   (default off); validate-and-safe-abort.
6. **Docs + `--explain`** (§13): info topics, migration entry, anchor-diff dump.

## 15. Open / deferred

- **Non-unique construct anchors** in multi-code groups (1,211 groups have 2+
  code cells): `content_hash` tiebreak handles most; deliberate reorders of
  *byte-identical* boilerplate cells are invisible to content-anchors (accepted —
  re-pairing by position is fine).
- **`sha256`-fallback cells** churn on every edit; reduce by encouraging a hand-id
  or a nameable first statement (authoring-style lever the maintainer is open to).
- **Full 3-way LLM merge** for true content conflicts — the watermark is the
  ancestor; out of scope here (tracked in `single-language-authoring-sync.md`
  §10).

## 16. Relationship to #166 / #162

- This note is **additive** to the #166 engine: same `SyncPlan`, same apply
  pipeline, same watermark file — a new diff pass + a richer baseline + three
  scoped apply behaviors. It does not rewrite `sync.py`/`sync_walker.py`/etc.
- It realizes #162's generative direction for *code* cells: identity minted at
  sync time, EN-authority preserved, written to files only on correction.

## 17. Decisions locked with the maintainer (2026-06-01)

1. **Neutral fraction ~90%** (perhaps 80–85% on AZAV ML, not lower); authoring
   style can be adapted to cut the localized-id-less population. → make the
   language-neutral verbatim path the cheap, dominant one.
2. **`def-my-fun` id-move writes to the file by default.**
3. **Diverged shared cell → auto-heal + warning**, with a config fallback to
   hard-error if it proves problematic.
4. **Opus recovery is defer-by-default** (`--llm-recover` opt-in).
