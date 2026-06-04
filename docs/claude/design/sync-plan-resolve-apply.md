# Sync: Resolve-then-Apply — Design Note

Companion to [`single-language-authoring-sync.md`](single-language-authoring-sync.md)
(the #166 engine). This note re-architects the **plan / apply boundary** so that
`clm slides sync` decides *what will happen* once, at plan time, and applying is
a decision-free mechanical replay. It is the clean design for the bug cluster
found investigating **#216** (cold-start id-less doubling) and the **dry-run /
apply divergence** it exposed.

**Status:** design accepted (2 forks settled — see §3); implementation phased
(§10). **Phase 1 DONE** (the `refuse` disposition + both-directions guard moved to
the resolver; all 5 parity/doubling xfails flipped — §9). **Phase 2 DONE (scoped):**
the edit and add paths are materialize-then-execute (the model calls moved to 2b;
execute writes mechanically). The id-migration recoverer and the `sync_code`
structural translate remain inline as **explicitly-deferred follow-ups** (§10).
**Phase 3.1 (id-less cold-pair minting) DONE** — `build_sync_plan` emits a `pending`
mint candidate, `apply_plan` verifies via the new `CorrespondenceVerifier` and mints
via `assign_ids_in_split_pair`; `--verify-cold-pairs` default-on with a provider.
**Phase 3.2 (half-id'd adopt) DONE** — `build_sync_plan` emits a `pending` **`adopt`**
candidate for a half-id'd pair (`_cold_adopt_authority` / `_maybe_emit_cold_adopt`,
run right after the mint pass and mutually exclusive with it); `apply_plan` verifies
the same way and stamps the id'd half's *existing* ids onto the id-less twin
(`_apply_cold_adopt` / `_adopt_ids_in_split_pair` — an explicit per-cell header stamp,
**not** `assign_ids_in_split_pair`, which cannot pair an id-less cell with an id'd
one). Mismatched-id and mixed-authority stay `refuse`. The whole #216 cluster is now
resolved end-to-end; the only remaining items are the Phase-2 deferred follow-ups
(the id-migration recoverer + `sync_code` structural translate).

---

## 1. The problem: apply does much more than apply

Today the pipeline is two stages:

```
build_sync_plan / classify_changes   →  SyncPlan (proposals)        [pure]
apply_plan                           →  writes + ApplyResult        [decides AND executes]
```

`apply_plan` is not an executor — it is a **second decision-maker**. It re-runs
structural logic the plan never captured:

- the both-directions id-less **refusal** (`sync_apply.py:710-718`: defer + error);
- the **absence** of that refusal for id-*carrying* both-directions adds
  (`sync_apply.py:695-705`: applies both → silent doubling);
- translation success/failure → defer (`_translate`, `_add_one_direction`);
- `process_idless` gating, move-after-add ordering, watermark advance gating.

Because those decisions live in the executor, **the plan is not a faithful
description of what will happen.** Every symptom below is one cause:

| Symptom (this session) | Why |
|---|---|
| `--dry-run` prints `N add`, the writing run defers all and writes nothing (exit 1 vs 2) | the refusal is an apply-time decision, invisible to the plan |
| cold-start **mismatched-id** / **half-id'd** pairs silently **double** both decks (`errors=[]`) | the both-directions guard covers only id-*less* adds; the id-*carrying* path is unguarded |
| `#216` cannot bootstrap a fresh deck | the plan emits N bidirectional adds; apply refuses; neither pairs |

(The id-less *symmetric* case the issue cites is in fact already *refused* by the
guard — so it is a dry-run-honesty + missing-feature bug, **not** the data
corruption the issue claimed. The real corruption is the id-carrying sibling.)

## 2. The principle: decide at resolve time, execute mechanically

Split the work by **what kind of thing it is**, with a sharp purity boundary:

```
1. Classify   [pure]            structural diff vs. baseline → raw observations
2. Resolve                      turn observations into a COMPLETE, executable plan
   2a. structural  [pure*]      decide every disposition: add / pair / refuse /
                                defer / conflict / order  (no "maybe" remains)
   2b. materialize [LLM/IO]     fill in generated content: translate / rewrite /
                                correspondence-confirm  → attach, or mark Blocked
3. Apply      [mechanical]      replay the resolved steps. No decisions, no LLM,
                                no "can this apply?". It cannot defer or refuse.
```

`*` "pure" = deterministic, read-only, **no LLM**. 2a may read file state (the
classifier already reads both decks); it must not write or call a model.

The one load-bearing property: **`--dry-run` = stages 1 + 2a**, which is pure and
**shared byte-for-byte with the writing run.** A dry-run and an apply therefore
cannot disagree on any *structural* decision — the refusal, the pairing, the
deferral, and the exit class all become plan items the preview prints.

## 3. Decisions settled

**Q-A — dry-run faithfulness: structural-faithful, no LLM.** `--dry-run` runs
1 + 2a and prints every structural disposition, marking generated content
`pending` rather than calling the model. Same cost as today's dry-run. The *only*
permitted apply-time divergence is a `pending` item the model could not
materialize/verify at write time → it downgrades to `blocked`/`refused`, and
because the preview labeled it `pending`, the downgrade is never a surprise.
(A future `--dry-run --with-content` may opt into running 2b for an exact preview.)

**Q-B — cold-start pairing: mint when correspondence confirmed, else refuse.**
A never-id'd, structurally-aligned pair is a *pairing candidate*. When a provider
is configured, a cheap, cached LLM gate (§7) confirms the halves actually
correspond, then mints shared ids; with no provider, or on a "no" verdict, it
**refuses** with a clear message. This supplies the cross-language signal that
[§3.2 of the base design](single-language-authoring-sync.md) deliberately lacked
("we do not *similarity-guess* cross-language identity") — an explicit semantic
**verification** is not a blind structural guess, so the new behavior extends
that principle rather than breaking it.

## 4. Data model: dispositions, `pending`, and `Refuse` as a first-class item

Today a `Proposal` is implicitly "a thing apply will attempt." Replace the
implicit contract with an explicit **disposition** — the resolved verdict 2a
assigns to every item:

| Disposition | Meaning | Stage-3 action |
|---|---|---|
| `apply` | a concrete mechanical op (insert / replace-body / mint-id / adopt-id / delete / move / retag) | execute it |
| `refuse(reason)` | a structural decision **not** to act (both-directions cold-start, mismatched-id pair) | no-op; hold watermark for the scoped cells; **shown in the plan** |
| `conflict(reason)` | same id drifted both sides (§3.4 isolate-and-refuse) | leave untouched; hold |
| `pending(kind)` | structurally decided, awaiting 2b content/verification (translate / correspondence) | resolved before stage 3; downgrades to `blocked`/`refused` only if 2b fails |

Key change vs. today: `refuse` and `pending` are **plan items with a reason**,
not apply-time strings. The new mechanical op kinds the redesign introduces:

- **`mint_shared_id(de_cell, en_cell, id)`** — stamp a *fresh* shared id onto two
  *existing* positionally-paired id-less cells. No translation, no insertion.
  (Today **no such op exists**; the only minter, `_add_one_direction` /
  `_place_new_cell`, is fundamentally translate-and-insert.)
- **`adopt_id(src_id → target_cell)`** — copy an existing id from the id'd half
  onto its id-less twin (the half-id'd case; safer than minting — the id already
  exists on one side).

`mint_shared_id` / `adopt_id` may delegate their byte-level work to the proven
`assign_ids_in_split_pair` machinery (`assign_ids.py:823`, round-trip-verified
EN-authority paired minting) rather than re-implementing slug derivation.

## 5. The faithfulness contract (the testable invariant)

> For any input, a `--dry-run` and a writing run **agree on every structural
> disposition** — what is added / paired / refused / deferred / conflicted, and
> the resulting exit class. The writing run may additionally downgrade a
> `pending` item to `blocked`/`refused` **iff** the model is unavailable or a
> correspondence check returns "no" — and the preview labels every such item
> `pending`, so the downgrade is disclosed in advance.

This is exactly what the parity tests in §9 assert; the redesign flips them from
`xfail` to pass.

## 6. How each found issue dissolves

| Issue | Resolution under this design |
|---|---|
| dry-run dishonesty (id-less symmetric) | 2a emits a visible `refuse` (no provider) or `pending` pair candidate (provider) → dry-run shows it; exit classes match |
| id-carrying silent doubling (mismatched-id) | 2a sees a both-direction id divergence with no baseline → `refuse` (or `conflict`), **never** a bidirectional translate-insert |
| half-id'd doubling | 2a → `adopt_id` (correspondence-gated) onto the id-less half; never doubles |
| #216 bootstrap | 2a → `mint_shared_id` candidates; 2b confirms; stage 3 stamps. `clm slides sync` becomes the proper cold-start minter (no separate `assign-ids` step) |
| "no similarity-guess" (§3.2) | preserved: pairing requires an explicit 2b **verification**, not structural similarity alone |

## 7. Cold-start pairing & the correspondence gate

**2a (pure, provider-aware).** A cold-start pair is a *candidate* only when it is
structurally alignable — equal length, equal role/cell-type sequence, and
id-less-only (no interleaved id'd cells that would move the positional anchor).
Reuse the alignment idea behind `_streams_aligned`, adapted to the cold path's
role-filtered cells. 2a branches on **provider availability** (a structural,
plan-time input):

- provider configured → emit `pending(correspondence)` pair candidates (dry-run
  shows "N pair — pending verification", exit 1);
- no provider → emit `refuse("cold-start pair; no provider to verify
  correspondence; run with a provider or `assign-ids`")` (exit 2). Dry-run and
  apply agree, because both know there is no provider.

**2b (LLM, opt-in tier — mirror `--llm-recover`).** A `CorrespondenceVerifier`
mirroring `AlignmentRecoverer` (`sync_recover.py`): a cheap model (Haiku-class),
**heading + short-body-snippet** inputs per aligned pair (**not** body-free — unlike
the recoverer, cross-language correspondence needs the heading *text*, since two
translated headings have different content hashes; see §12), **cached** by pair
fingerprint + prompt version, **validated**, **safe-abort**. It returns, per pair,
correspond = yes/no. All "yes" → the mint materializes; any "no" → the pair
downgrades to `refuse`. One call per cold-start deck, cached → re-runs are free.

**Classifier stays pure.** The verifier lives in stage 2b (apply-side tier),
never in `sync_plan.py`. This honors the base design's "pure analysis — no LLM"
classifier invariant.

## 8. Module changes

| Module | Change |
|---|---|
| `sync_plan.py` | becomes Classify + Resolve-2a. Owns *all* structural dispositions, incl. the both-directions refusal (idless **and** idd) and cold-start pair candidacy — **moved out of apply**. `build_sync_plan` returns a `ResolvedPlan` whose every item carries a disposition. |
| `sync_apply.py` | **2b materialize** (the LLM calls) is split from **3 execute** (decision-free writes) for the edit + add paths: `_materialize_edits` → `_EditOutcome` (judge / code re-translate), and `_materialize_idcarrying` / `_materialize_idless` → `_TransOutcome` cache (add translations). `_apply_edit` and the two add walks then write mechanically; a model-failure is a `blocked`/error outcome decided in 2b. `_apply_adds`' both-directions guard is deleted (now in 2a, Phase 1). **Still inline (deferred):** the id-migration recoverer (`--llm-recover`) and the `sync_code` structural translate — they call models inside their own helpers; a single bundled `MaterializedPlan` object was judged not worth the churn given the add materialize is ordering-sensitive (it must run between the id-carrying and id-less walks). |
| `sync_recover.py` | gains `CorrespondenceVerifier` beside `AlignmentRecoverer` (same opt-in/cached/validated/safe-abort shape). |
| `assign_ids.py` | `assign_ids_in_split_pair` reused by `mint_shared_id` / `adopt_id` for byte-level stamping. |
| `slides_sync.py` (CLI) | dry-run renders the resolved plan (incl. `refuse`/`pending`); exit codes derive from dispositions, so `_plan_exit_code` and `_apply_exit_code` converge by construction. New `--verify-cold-pairs` (default: on when a provider is set) gates 2b correspondence. |
| watermark | stage 3 advances it only over `apply`-disposition cells; `refuse`/`conflict`/`blocked` cells hold at baseline (the existing #202 per-cell partial-advance, simplified — the held set is now explicit in the plan). |

## 9. Tests pinning this

Originally landed as `xfail(strict=True)`; **Phase 1 flipped all five to passing**
(the markers are removed and the assertions now read the refusal outcome):

- `tests/slides/test_sync_dry_run_parity.py` — parity helper
  (`_assert_dry_run_predicts_apply`); `TestColdStartRefusalParity` (was
  `…KnownBugs`) now asserts the cold-start id-less and watermark both-sides
  cases **refuse** (`N refuse`, dry=apply=exit 1, decks byte-unchanged).
- `tests/cli/test_slides_sync.py::TestDryRunApplyParity::test_dry_run_promise_matches_apply_for_parallel_idless`
  — passes at the CLI surface: dry and apply both exit 1, `refuse` shown.
- `tests/slides/test_sync_apply.py` — the two id-carrying doubling cases
  (mismatched-id, half-id'd) now assert `applied_add == 0`, `refuse == 4`, no
  duplication, no error.
- `tests/slides/test_sync_plan.py::TestBothDirectionsRefusal` — **new** unit
  cases on `classify_changes` directly: cold parallel id-less / mismatched-id /
  half-id'd all refuse; one-directional cold start and id-carrying both-directions
  *against a baseline* still **add** (the key distinctions).

The parity assertion was written to survive either fix shape (it checks "a
writing-run error must have been foreseen by the dry-run"), so it needed no edits
when the redesign landed — only the `xfail` markers came off.

**Phase 2 boundary tests** (`tests/slides/test_sync_apply.py`): the judge is invoked
exactly once per edit and the translator exactly once per add cell — both in the
materialize pass, never re-called in execute. A higher count would mean the
execute walk fell back to the model (a leak across the 2b/3 boundary).

## 10. Phased implementation

Each phase is independently shippable and flips a named subset of the xfails.

**Phase 1 — `Refuse` as a first-class disposition; move the structural guards to
2a. [DONE]** Introduced `Proposal.disposition` (`"apply"` | `"refuse"`) and a
`refuse` kind; relocated the both-directions refusal (idless **and** the
previously-unguarded idd) from `apply_plan` into the resolver (`classify_changes`
→ `_refuse_cold_both_directions` / `_refuse_idless_both_directions`). `apply_plan`
now executes a `refuse` as a deferred no-op; the old `_apply_adds` guard and the
`process_idless` plumbing are deleted. *Flipped all 5 xfails* (the main-path
id-less refusal also covers the watermark both-sides case, so it was **5 of 5**,
not the 4 first estimated). No LLM, no new minting; the watermark holds over a
refusal. Also surfaced in `render_plan` / `--json` (`counts.refuse`,
`proposals[].disposition`), the walker (a `REFUSE` action, never prompted), and
the `migration.md` info topic.

**Phase 2 — Split apply into materialize (2b) + execute (3). [DONE — scoped to
edit + add].** The edit path materializes via `_materialize_edits` → `_EditOutcome`
(`update` / `in_sync` / `blocked`); `_apply_edit` writes mechanically (boundary
test: judge called once, in materialize). The add path materializes via
`_materialize_idcarrying` / `_materialize_idless` → a `_TransOutcome` cache keyed by
source-cell id; the two add walks read the cache through `_translate`, with a
model fallback for a cache miss that never fires because the materialize walks
enumerate a **superset** of what execute translates (boundary test: translator
called once per cell). Behavior-preserving (1106 sync tests green). **Deferred
follow-ups (call models inline still):** the id-migration recoverer and the
`sync_code` structural translate; and a single bundled `MaterializedPlan` object
(the two materialize seams suffice, and the add seam is ordering-sensitive).

**Phase 3 — Cold-start minting + correspondence gate. [3.1 (id-less) DONE; 3.2
(half-id'd) DONE — see §12].** `build_sync_plan` emits a `pending` mint candidate for
a provider-available, unifiable cold pair; a `CorrespondenceVerifier` (2b) confirms
the heading/snippet pairs; the mint delegates to `assign_ids_in_split_pair`.
**3.1 done** (id-less; mismatched-id stays `refuse`). **3.2 done** — the half-id'd
*adopt* (`kind="adopt"`), an **explicit** path **separate from
`assign_ids_in_split_pair`**, which **cannot** adopt because `unify` does not pair an
id-less cell with an id'd one (`_slide_ids_pair` is `de_id == en_id`): apply stamps the
id'd half's existing ids onto the id-less twin per-cell. Unblocks the **1.8
PythonCourses gate** (~200 id-less split halves; see `#158`).

## 11. Open questions / deferred

- **`--dry-run --with-content`** (run 2b in preview for an exact diff) — deferred;
  the structural-faithful default covers the reported pain.
- **Serializable resolved plan** (`--plan-only` → file → `--apply-plan FILE`) —
  not required; in-memory resolve→materialize→apply plus the existing
  translation/alignment caches already give replay-cheapness.
- **Correspondence on code cells** — out of scope: localized code is matched by
  id, neutral code is byte-identical, id-less code rides the structural pass.
  The gate targets id-less *narrative markdown* only.
- **Mismatched-id pairs with a provider** — Phase 3 may offer to *reconcile*
  (pick a source-lang, adopt one side's ids) instead of only refusing; **decided:
  stays `refuse` in Phase 3 v1** (§12), revisit with pilot data.

---

## 12. Phase 3 — finalized implementation plan (decisions 2026-06-04)

**Decisions (settled with the maintainer):**

- **Scope:** mint **id-less** both-directions cold pairs (the #158 case) **and**
  **half-id'd** pairs (the id-less half adopts the id'd half's ids). **Mismatched-id**
  pairs (both id'd, *different* ids) stay `refuse`.
- **Verifier inputs:** per aligned slide, the **heading + a short body snippet**
  (~first 1–2 lines) + role. A **Haiku-class** model (a tunable constant, cf. the
  recoverer's Opus). One cached call per cold deck. **Not body-free** — two translated
  headings have different content hashes carrying no cross-language signal, so
  confirming correspondence needs the heading *text* (the §7 "body-free" note is
  corrected here; only the *code* alignment could be hash-only, and code is not what
  is being paired).
- **Gate:** the verifier is a **required gate, default-on when a provider is
  configured** (`--verify-cold-pairs`); no provider → `refuse`. Honors §3.2.

**Architecture (keeps the purity boundary):**

1. **`classify_changes` (pure) — unchanged.** Still emits `refuse` for a
   both-directions cold pair (Phase 1).
2. **`build_sync_plan` (has files + a new `provider_available: bool`) — candidacy.**
   When the plan's refusals are the cold-start both-directions kind, `provider_available`
   is true, and the pair is **unifiable** (a read-only `unify→split` byte-faithful
   round-trip — the same guard `assign_ids_in_split_pair` uses), replace the refusals
   with a single **`pending` mint candidate** (`kind="mint"`, `disposition="pending"`,
   carrying the aligned `(de, en)` heading/snippet pairs from the unified deck). Else
   keep `refuse`. `provider_available` is an env fact (`OPENROUTER_API_KEY` /
   `OPENAI_API_KEY`), identical in dry-run and apply, so the two still agree.
3. **dry-run (1 + 2a)** shows `N pair — pending verification` (exit 1) when a candidate
   exists, else `refuse` (exit 1). No model call (Q-A).
4. **`apply_plan` (2b materialize) — verify.** For a `pending` mint candidate, if a
   `CorrespondenceVerifier` is configured: verify the heading/snippet pairs (cached,
   validated, safe-abort). **All pairs "yes"** → materialize the mint; **any "no" /
   abort / no verifier** → downgrade to `refuse` (deferred, watermark held — the one
   disclosed Q-A divergence). Conservative: a single mismatched pair means the streams
   are not a clean translation → refuse the whole pair, never bake a wrong shared id.
5. **execute (3) — mint *or* adopt.** A confirmed **mint** (both-id-less) delegates to
   **`assign_ids_in_split_pair(de_path, en_path, options)`** (`assign_ids.py`):
   byte-faithful EN-authority shared-id minting (id-less only — see the corrected note
   below; it does **not** adopt a half-id'd pair). Its own "not unifiable → `None`"
   return is a second safety net → `refuse`. A confirmed **adopt** (half-id'd) takes a
   separate path (`_apply_cold_adopt` / `_adopt_ids_in_split_pair`): it walks the two
   halves' localized streams positionally and stamps each authority slide_id onto its
   id-less twin (`_stamp_slide_id`, the same byte-faithful header rewrite assign-ids
   uses), writing only the id-less half. Either is the whole plan and carries no other
   apply ops, so the file-level write does not conflict with the FileState buffer and
   the watermark records from the post-write files.

**New `CorrespondenceVerifier`** (`sync_recover.py`, mirroring `AlignmentRecoverer`):

- `@runtime_checkable Protocol` with `prompt_version` + `verify(*, pairs) -> dict[int, bool]`.
- `SlidePair` (frozen): `de_heading`, `en_heading`, `de_snippet`, `en_snippet`, `role`.
- `correspondence_fingerprint(pairs)` → sha256 over the exact serialization the model
  sees (cache-key soundness).
- `validate_correspondence(verdicts, pairs)` → total over the pairs, booleans only;
  `CorrespondenceInvalid` on any failure → safe-abort → treat as "no" → refuse.
- `StaticCorrespondenceVerifier` (tests; `.calls` counter) + `OpenRouterCorrespondenceVerifier`
  (Haiku default, `response_format=json_object`, retry, fingerprint-cached via a new
  `SyncCorrespondenceCache` or a sibling table on the existing cache DB).

**CLI (`slides_sync.py`):** `--verify-cold-pairs / --no-verify-cold-pairs` (default on
when a provider is set), resolving the verifier like `_resolve_recoverer`; pass
`provider_available` into `build_sync_plan`.

**Tests:** `TestColdStartRefusalParity` cases become — mint under a confirming
`StaticCorrespondenceVerifier`, refuse under a denying one, refuse with no provider;
plus a half-id'd adopt case, a not-unifiable→refuse case, and verifier caching
(calls==1 on re-run). Dry-run shows pending; apply mints or downgrades.

**Open implementation checks — RESOLVED (3.1):**

- **Half-id'd: `assign_ids_in_split_pair` / `unify` does NOT adopt.** `unify`'s
  `_slide_ids_pair(de, en)` is `de_id == en_id`, so an id-less cell never pairs with
  an id'd one — the minter would mint *separate* ids, not adopt. So **3.2 needs an
  explicit `adopt` path** (positional-stream pairing + stamp the id'd side's id onto
  the id-less twin). Both-id-less *does* pair (`None == None` → adjacency), which is
  why 3.1's id-less mint works through `assign_ids_in_split_pair`.
- Verifier cache = a new `SyncCorrespondenceCache` (sibling table `sync_correspondences`).
- `provider_available` = `has_openrouter_api_key()` (`openrouter_client.py`).

**3.1 implementation notes (shipped):** the `pending` `mint` candidate is one
per-pair marker; apply re-derives the aligned `(de, en)` slide pairs from the
(unchanged) files by positional zip of the slide cells (`_build_slide_pairs`), then
`_resolve_correspondence` (mirrors `_resolve_alignment`) verifies + caches. The mint
short-circuits the whole `apply_plan` (a cold pair has no other ops). Verifier inputs
are heading + the lines *after* the heading (a short lead snippet) + role.

**3.2 implementation notes (shipped):** the half-id'd **adopt** mirrors the mint
shape but takes its own classifier candidate and apply path:

- **Candidacy (`_cold_adopt_authority` + `_maybe_emit_cold_adopt`, `sync_plan.py`).**
  Runs in `build_sync_plan` right after `_maybe_emit_cold_mint`, gated on the same
  `source == "none" and provider_available`. Mutually exclusive with mint **by
  construction**: mint, when it fires, removes the refusals (emptying `plan.refusals`),
  so adopt's `if not refusals: return` bails. `_cold_adopt_authority` walks the full
  positional localized stream and returns the fully-id'd side (`"de"`/`"en"`) only when:
  every pair agrees on `role_of` and cell-type; every **sync** pair (`role_of != None`)
  is XOR (exactly one side id'd) with a **consistent** authority; every **non-sync**
  pair (id-less localized code, `role_of None`) is id-less on both. Any other shape →
  `None` → the refuse stands. Because `role_of` of an aux-markdown or a localized-code
  cell *depends on* its `slide_id`, an id'd-vs-id-less twin of those has mismatched
  `role_of` → adopt declines (only narrative-tagged cells, whose role is
  id-independent, adopt — a documented, conservative boundary). Emits a single
  `kind="adopt"`, `disposition="pending"`, `direction="{authority}->{other}"`.
- **Apply (`_apply_cold_adopt` + `_adopt_ids_in_split_pair`, `sync_apply.py`).** A
  second short-circuit beside the mint one. Builds the slide pairs and verifies them
  exactly like the mint (reusing `_build_slide_pairs` / `_resolve_correspondence`); on
  **all-yes** stamps each authority slide_id onto its positional id-less twin, loading
  **both** halves with the same `FileState.load` parser (so candidacy/apply parser
  consistency holds) and flushing only the id-less half. Per-cell guards (`role_of`
  match, target is id'd, twin is id-less, equal stream lengths) make a post-plan drift
  return `0` stamped → deferral, never a mis-stamp. `applied_adopt` counts it.
- **Why not `assign_ids_in_split_pair`:** it mints fresh slugs and cannot pair an
  id-less cell with an id'd one (the `_slide_ids_pair` `de_id == en_id` gate), so it
  would create *new, divergent* ids instead of adopting the authority's existing ones.
