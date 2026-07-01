# Reconciling separated voiceover companions in `clm slides sync`

**Status:** proposed · **Issue:** [#501](https://github.com/hoelzl/clm/issues/501) ·
**Date:** 2026-07-01 · **Supersedes:** the "N-file atomicity — design not started"
deferral in `split-voiceover-hardening.md` §3 #6 / §12 #7.

Companion designs: `single-language-authoring-sync.md` (the inline sync engine),
`sync-plan-resolve-apply.md` (plan/apply model), `sync-git-as-baseline.md`
(baseline resolution), `sync-consistency-ledger.md` (the trust overlay),
`split-voiceover-hardening.md` (why companions were excluded),
`sync-voiceover-anchoring-unification.md` (`vo_anchor` placement).

---

## 1. Problem

`clm slides sync` reconciles the two **deck halves** of a split pair
(`<deck>.de.py` / `<deck>.en.py`) but is structurally blind to **separated
voiceover companion files** (`voiceover_*.de.py` / `voiceover_*.en.py`). Editing
one companion — adding, changing, or removing narration — is never propagated to
the other language, and **no `sync` subcommand reports it**. For a deck whose
voiceover lives in companions (the sticky default since PR #385), an author can
silently ship one language without narration, leaving that language
un-recordable. Only `clm validate` catches the divergence, and late (commit
time), and only where a pre-commit gate is wired.

The exclusion is deliberate and threaded through every layer:

| Layer | Where | Behavior |
|---|---|---|
| Discovery | `pairing.py::_is_split_slide_file` (`:289`) | `voiceover_*` → `False`; never enumerated by `find_split_slide_files_recursive` (`:304`) |
| Twin derivation | `pairing.py::derive_split_twin` (`:211`), `derive_split_pair_from_stem` (`:254`) | `voiceover_*` → `None` |
| Single-path guard | `sync.py::_resolve_single_path` (`:200-205`) | hard-errors: *"…is a voiceover companion, not a deck half."* |

Recorded rationale (`split-voiceover-hardening.md` §8): *"sync is deck-identity,
extract is narration-relocation"*; §3 #6 / §12 #7 defer the *"N-file atomic
commit point for separated voiceover — design not started"*, because sync's
atomic swap + watermark are modeled for exactly the 2-file `(de, en)` pair.

What already works: **inline** voiceover (a `lang`-tagged voiceover cell living
*in* the deck) is classified and translated like any other cell, so `sync
report` already shows `add de->en …/voiceover [translation pending]`. The gap is
specifically the **separated companion-file** layout.

## 2. The proposal on the issue, and the maintainer decision

The issue proposes reconciling a separated pair by **inline → sync → extract**:
inline both companions into their decks, run the existing 2-file deck sync (the
voiceover is now inline so it propagates/translates), then extract the voiceover
back into the companions. Empirically the inline→extract round-trip is
byte-faithful *on an already-in-sync pair*.

The maintainer's binding decision (2026-07-01) sets the invariant and closes two
obstacles for v1:

- **Invariant:** voiceover is *wholly inline* **or** *wholly in a sidecar
  companion*, never mixed. The only hard guarantee is that inline/extract never
  **lose** voiceover.
- **Obstacle 1 (tag-scope asymmetry)** — `inline` absorbs all narrative
  (`voiceover` *and* `notes`), `extract` defaults to `voiceover`-only.
  **Accepted, not fixed:** use the default voiceover-only `extract`; notes
  migrating from the companion into the deck is fine/preferred. Preserving notes
  in the companion is deferred.
- **Obstacle 2 (provenance / mixed decks)** — **out of scope by design:** no
  per-cell provenance tracking; partial splits unsupported.
- Two consequences: (a) the first companion-aware sync of a legacy
  `notes`-tagged companion permanently moves those notes inline (accepted);
  (b) the **watermark representation must stay consistent** — make companion
  handling automatic (no bypass) and re-baseline on the first run.

## 3. Assessment — agree with the *model*, refine the *mechanism*

**I agree with inline → sync → extract as the conceptual model.** It is the right
call: reuse the hardened deck-sync engine and its translation/conflict machinery,
and sidestep building a bespoke N-file reconciler. Teaching the engine to treat
companions as first-class extra files (the deferred "native N-file" path) is
strictly more code and a second cell-identity model for no behavior the
composition doesn't already deliver under the maintainer's invariant.

**But "inline → sync → extract" must not be implemented as three sequential
on-disk operations.** The issue's phrasing ("inline both companions *into their
decks*, run the 2-file sync, extract back") reads as an on-disk sequence, and
taken literally that is the weakest option, for two disqualifying reasons:

1. **Read modes can't inline on disk.** `report`, `verify`, and `diagnose` are
   contractually non-mutating (`build_sync_plan` *"Reads the two files; writes
   nothing"*, `sync_plan.py:3802`). A literal inline step would have to write the
   deck and delete the companion just to *report*. So we already need an
   in-memory inline for the read surface; once we have it, the disk round-trip
   earns nothing.
2. **The on-disk sequence passes through the forbidden state and has an unsafe
   crash window.** After step 1 the decks hold inlined voiceover and the
   companions are **deleted** — precisely the wholly-inline-with-no-companion
   state the invariant forbids. A crash before step 3 leaves it on disk, after
   which `resolve_companion` returns `None` and every future sync silently
   misreads the deck as a plain (inline) deck — the exact "different
   representation" hazard the maintainer forbade. Defending that requires a
   journal + per-pair lock + startup journal-scan; more machinery for a *worse*
   worst case.

The refinement is an **in-memory projection with a single atomic write-back**:

> Treat *inline* as a **pure projection** used only to build the plan, and
> *extract* as the **inverse projection** applied at write time. Read modes
> project in memory and never touch disk. Apply computes the reconciled
> deck+companion for both languages and writes **all four files in one
> `atomic_write_all` batch** — so the deck on disk is voiceover-free *before and
> after* apply, no intermediate inlined/mixed state ever exists, and a crash
> cannot corrupt the invariant. The read surface stops lying about "0 changes".

This keeps the issue's model while fixing read-mode purity and crash-safety, and
it is *cheaper* than the on-disk version because the pure primitives already
exist (§4).

## 4. Why this is cheap — the seams already exist

| Need | Existing primitive | Location | Notes |
|---|---|---|---|
| **Inline** (companion text → merged deck text), pure | `merge_voiceover_text(slide_text, companion_text, comment_token) -> (merged_text, unmatched_ids)` | `voiceover_tools.py:793` | Already used by the **build pipeline** in-memory; already reports `unmatched_ids` |
| **Extract** (deck text → deck + companion text), plan/commit split | `_plan_extraction(path, …) -> (result, writes)` returns `[(path, text), …]` **without writing** | `voiceover_tools.py:482` | Paired EN-authority variant `extract_voiceover_pair` (`:643`); scope gate `_is_extractable_cell` (`:322`, voiceover-only default) |
| **Atomic N-file commit** | `atomic_write_all(writes)` — temp-write all, then `os.replace` back-to-back | `path_utils.py:323` | Already the commit path for `split`/`unify`/paired `extract` (deck + companion) |

And the engine is already text-first at the points we hook:

- **Single plan funnel.** Every read/apply mode funnels through
  `build_sync_plan(de_path, en_path, …)` (`sync_plan.py:3787`), which reads the
  working-tree text (`de_path.read_text()`, `:3844-3845`) and resolves the
  baseline. One injection point covers report/verify/diagnose/apply/autopilot.
- **Baseline is text.** Every baseline source ends in `_bundle_from_texts(de_text,
  en_text, …)` fed by deck *text* (`_bundle_from_git_ref` `:1687`,
  `_bundle_from_explicit_paths` `:1714`). It can consume *projected* deck text.
- **Apply is in-memory.** `apply_plan(...)` (`sync_apply.py:300`) mutates
  `FileState` objects; `FileState.render()` (`sync_writeback.py:789`) produces
  text — factored out of `flush()` precisely so *"a buffered / atomic writer …
  can render to text and write via `atomic_write_all`."*

The only genuinely new plumbing is two **pure text→text funnels** factored (not
rewritten) from the existing pure cores, plus the projection/watermark wiring:

- `inline_pair_text(deck_text, companion_text, token) -> inlined_text`
  — from `merge_voiceover_text` / `inline_voiceover`'s pure core
  (`_build_slide_id_to_cell_map` → `_plan_insertion` → `_apply_insertions` →
  `_strip_author_attrs`).
- `extract_pair_text(inlined_text, deck_path, twin_id_map, token, layout,
  include_notes=False) -> (deck_text, companion_text, companion_path)`
  — from `_plan_extraction`'s pure core; the in-memory twin's inlined text/id-map
  is **threaded in** so twin-aware `slide_id` minting stays pure and
  twin-consistent (`de_id == en_id`) with no hidden disk read.

Both read and apply loci call the **same** inline funnel, so `report` provably
predicts `apply`. Non-companion decks get an identity projection (byte-untouched).

## 5. Design

### 5.1 The projection, end to end

For a resolved pair `(de_deck, en_deck)` with companions `(de_comp?, en_comp?)`:

```
                         de_comp ─┐                        ┌─ de_comp'
  de_deck ──read──►  inline(de_deck, de_comp) = de_inlined │
                                     │                      │
  en_deck ──read──►  inline(en_deck, en_comp) = en_inlined  │  (write-back,
                                     ▼                      │   apply only)
              build_sync_plan(de_inlined, en_inlined,       │
                     baseline = PROJECTED (§5.3)) ─► apply_plan
                                     │  (read modes stop here, pure)
                                     ▼                      │
                       reconciled de_inlined', en_inlined'  │
                                     │                      │
                    extract_pair_text (voiceover-only) ─────┘
                                     ▼
        atomic_write_all([de_deck', en_deck', de_comp', en_comp'])
                                     ▼
                    record watermark in *inlined* space + marker
```

Both the working tree **and** the baseline are projected the same way (§5.3);
that is the correctness keystone.

### 5.2 Companion-aware pairing (a ≤4-file *bundle*, one atomic unit)

Keep companions **out of deck discovery** — do **not** relax
`_is_split_slide_file` / `derive_split_twin`. A companion is never a deck to
pair; enumerating it would revive the phantom-solo-half warnings and the
re-extract-empties-both-companions footgun those guards prevent. Instead, after
the ordered `(de_deck, en_deck)` pair is resolved as today, attach each half's
companion via the existing `resolve_companion(deck)` (`voiceover_tools.py:252`,
finds the companion in the `voiceover/` subdir or as a sibling, or `None`).

- The sync **atomic unit remains the pair**; a companion-bearing pair expands its
  write set from 2 files to ≤4 via `atomic_write_all` (the same primitive
  `split`/`unify` already use for deck+companion).
- Replace the hard error in `_resolve_single_path` for a `voiceover_*` argument:
  pointing sync at a companion should resolve to *its deck pair* (map
  `voiceover_slides_x.de.py` → `slides_x.de.py`) and reconcile that, with a clear
  message only if the deck can't be found.

Classify each pair into one of four representations at the funnel:

| Representation | Condition | Handling |
|---|---|---|
| **plain** | neither half has a companion | existing inline path, projection is a no-op |
| **separated** | both halves separated, or one-sided (§5.5) | project + reconcile (the feature) |
| **mixed** | a half has *inline `voiceover` cells* **and** a companion | **refuse** (§5.5) |
| **cross-language asymmetry** | one half separated (companion), the other carries *inline `voiceover`* | **refuse** loudly (§5.5) |

### 5.3 Baseline projection — the keystone

`build_sync_plan` resolves the baseline in priority order `baseline_from →
baseline_ref → watermark → git HEAD → none` (`sync_plan.py:3801`). A separated
deck at git HEAD is **voiceover-free**. This is not a cosmetic detail: **the
narrative classifier diffs each side against its *own* baseline
(`_state(de_now, de_base)` / `_state(en_now, en_base)`, `sync_plan.py:2587-2588`),
never DE-vs-EN directly.** A cross-language divergence is detected *only* when one
side classifies as `edited` (`:2603-2614`); when neither does, the pair is counted
`plan.in_sync_count` (`:2616`). So with a voiceover-free baseline a narration
present on both sides now but absent from the baseline reads as a *symmetric
non-edit* — **the divergence #501 exists to catch is silently swallowed**, and an
in-sync pair otherwise diffs as a spurious symmetric `add`.

Fix: **make "inline the baseline" a property of the bundle constructor** and
apply it **uniformly across all five baseline sources**, reading the companion at
the *matching ref/paths* (including the moved companion under a rename):

- `_bundle_from_git_ref` (git-HEAD and explicit `--baseline <ref>`): also fetch
  `git show <ref>:<companion>` and inline it before `_bundle_from_texts`.
- `_bundle_from_explicit_paths` (`--baseline-from`, rename recovery): inline the
  companion at the *old* repo-root-relative path.
- `detect_rename` HEAD^ path: inline the companion at HEAD^.
- Watermark cache: see the marker rule below.
- "none": no baseline — structural-only, unchanged.

The companion's path at a ref is derived the same way `resolve_companion` derives
it on disk (subdir-first, then sibling). If the companion didn't exist at the
ref, the baseline is the raw deck — correctly modeling "voiceover added since the
ref" as a real add.

**Representation marker (both directions).** Stamp a self-describing marker in
`sync_watermark_meta` (`representation = inlined | separated`, or bump
`WATERMARK_HASH_VERSION`). Do **not** infer representation from
`resolve_companion` success. On any mismatch between the marker and the current
computed representation, **demote to a git-HEAD cold-start** (auto re-baseline)
rather than diffing across representations — reusing the proven #225 disjoint-id
demotion pattern. This covers **both** migrations automatically and with no
bypass flag:

- legacy voiceover-free watermark vs. companion-current deck (the first
  companion-aware run — the maintainer's "re-baseline/bless on first run");
- inlined watermark vs. plain-current deck (author later deleted the companions);
- the dangerous "watermark has voiceover rows but no companion resolves" flip.

**Consistency ledger.** The committed `<topic>/.clm/sync-ledger.json` trust
overlay (`sync_ledger.py`) only *suppresses* re-litigation of cells whose current
halves byte-match a recorded confirmation; it is not a baseline and cannot fix a
never-recorded cell. Bless / `accept --record` / `record_pair` must therefore
inline in-memory **before** recording, so confirmations are stored and compared
in *inlined* space under the marker — otherwise suppression silently stops
working for separated pairs.

### 5.4 Write-back — total, no-op-safe, atomic (apply only)

After `apply_plan` yields reconciled `FileState`s, `render()` each to
`de_inlined'` / `en_inlined'`, then compute the ≤4 output texts **without
touching disk**. Four rules make this safe:

**(a) Total / lossless — the primary rule (all three red-teams' blocker).**
`merge_voiceover_text` does **not** inline a companion cell whose `for_slide`
fails to resolve in the current deck; it returns it in `unmatched_ids`
(`voiceover_tools.py:833`) — and on-disk `inline_voiceover` deliberately *retains*
such a cell rather than dropping it (`:1304-1313`). A blind "extract from the
inlined deck" would **delete** that narration on write-back (the re-extract
rebuilds the companion from inlined cells only). The "byte-faithful" property
holds only for an *already-in-sync* pair; on a **renamed or removed slide** —
exactly #501's scope — it does not. Therefore: **any unmatched companion cell
(unresolvable `for_slide`, or a `vo_anchor` whose predecessor is gone) is a
blocking `PlanIssue`; the whole pair refuses and writes nothing.** Both loci
(read and apply) treat unmatched identically, so `report` predicts the refusal.
Never silently drop; never let re-extract overwrite a retained companion. (This
is exactly what `validate::_check_companion_for_slide_resolves` reports today,
now surfaced *live* in sync.)

**(b) True no-op via two gates — not "byte-faithful ⇒ no-op".** Naive extract
re-derives `vo_anchor` positionally, may re-mint ids, and collapses blank lines,
so "extract back" can churn a clean pair into a spurious 4-file diff. Instead:
1. **Empty-plan short-circuit** — if the inlined plan has no apply-kind
   proposals, write **nothing** and skip the round-trip entirely.
2. **Dirty-diff** — when the plan is non-empty, compute the ≤4-file write set and
   **drop every entry byte-identical to disk**.
Do **not** let sync mint `slide_id`s or renormalize whitespace as a side effect;
gate an id-less / unnormalized deck to a refuse-or-"normalize first" path. Prove
`inline→extract` byte-stability with a **golden round-trip suite** (hand-
formatted, multi-blank, multiple `vo_anchor`s, subdir + sibling); if some
companion isn't byte-stable, fall back to a **parsed-cell semantic-equality**
write gate rather than trusting the byte diff.

**(c) Gate re-extract on post-apply structural alignment.** If `apply_plan` left
unresolved `conflict` / `refuse` dispositions, do **not** attempt extract (it
would raise on misaligned halves) — roll to the normal "report conflicts, write
nothing" path, exactly as a plain divergent deck sync does. Extract is an
expected-outcome gate, not an exception site.

**(d) Commit atomically, deletion inside the batch.** `atomic_write_all` over the
surviving ≤4 files, `include_notes=False` (voiceover-only — notes stay inline in
the deck, per the maintainer decision), then `_prune_other_companions` per half
to clear a stale copy in the other layout (existing behavior, `:772-773`). An
**empty companion is deleted *inside* the atomic batch** (stage a zero-cell /
tombstone the resolver treats as absent, or order companion-before-deck) so a
crash cannot wedge a pair into a "deck references a removed slide, companion
gone" state. A detected deck/companion byte-skew after a crash auto-demotes to
git-HEAD and re-heals rather than hard-refusing.

### 5.5 Representation invariants (mixed / cross-language / one-sided)

- **Mixed deck — refuse.** A half with inline **`voiceover`** cells **and** a
  companion is a genuine partial split (obstacle 2, out of scope). Refuse with a
  normalize hint (`clm voiceover inline` to go fully inline, or `extract` to go
  fully separate). **The predicate is voiceover-only, never notes:** a separated
  deck legitimately keeps `notes` inline while voiceover lives in the companion
  (the post-#387 default), so "inline notes + voiceover companion" is the
  sanctioned steady state and must **not** be refused. (Use `_has_voiceover_cells`
  with `include_notes=False`.)
- **Cross-language representation asymmetry — refuse loudly.** One half separated
  (companion), the other carrying inline `voiceover`: refuse
  (*"inconsistent representation across languages; normalize first"*) rather than
  silently re-homing the inline half into a new companion. Distinct from the
  legal one-sided case below.
- **One-sided companion — legal, propagate (the core ask).** A half has a
  companion, the other has **no voiceover at all**. This is #501's "add narration
  on one side, propagate to the other" case — do **not** refuse. With the
  **inlined baseline** (§5.3), a *standing* one-sided asymmetry classifies as
  `in_sync` (no spurious propagation); only a **genuinely new** narrative
  propagates. Pin the newly-created counterpart companion's layout (subdir /
  sibling) to the existing half's layout so sync never relocates as a side
  effect.

### 5.6 Automatic, no bypass; CLI surface

Companion handling is **automatic** whenever a reconciled deck has a companion —
no `--no-voiceover` opt-out (a bypass would observe the voiceover-free
representation and re-introduce the watermark inconsistency), no new subcommand.
`report` / `verify` / `diagnose` / `apply` / `autopilot` all gain companion
awareness through the shared `build_sync_plan` funnel. The only visible surface
changes: pointing `sync` at a `voiceover_*` file now reconciles its deck pair
(§5.2), and read modes emit a one-line note that the pair is companion-aware
(separated / one-sided / mixed-refused / cross-lang-asymmetry) plus an explicit
notice when legacy notes are absorbed inline.

### 5.7 Read-mode purity (a real footgun)

`merge_voiceover_text` and `inline_voiceover` mutate `vo_cell.lines[0]` **in
place** (`_strip_author_attrs`, `_build_voiceover_header`). A pure read-mode
projection must therefore operate on **cell copies**, so header mutation cannot
leak into `classify_changes`. And `build_report` excerpts + `verify_pair` must
run over the **in-memory inlined projection**, never re-read the voiceover-free
working tree — otherwise narration excerpts show wrong positions/content. Cover
with a test that a report on a voiceover-drift pair quotes the correct narration
line and asserts no working-tree re-read.

## 6. Alternatives considered

- **A — Faithful on-disk composition** (run `inline`, `sync`, `extract` as three
  disk ops). *Rejected.* Read modes cannot inline on disk (§3.1); the on-disk
  sequence passes through the forbidden wholly-inline state and needs a journal +
  lock to be even best-effort safe, and a journal loss strands the deck
  wholly-inline where every future sync misreads it (§3.2). More machinery, worse
  worst case. Its genuinely useful ideas — the cross-language-asymmetry refusal,
  the empty-plan short-circuit, and the explicit refuse-and-write-nothing on any
  unmatched cell — are grafted onto the in-memory spine above.
- **C — Native engine awareness** (companion cells as first-class role cells; a
  true 4-file reconcile with no round-trip). *Deferred.* This is the parked
  "N-file atomicity". Under the wholly-inline-or-wholly-sidecar invariant it buys
  nothing the projection doesn't deliver, while adding a second cell-identity
  model (companion `for_slide`/`vo_anchor` vs deck `slide_id`) to the watermark
  and walker. The one idea worth borrowing — which the projection already uses —
  is `atomic_write_all` as the single ≤4-file commit, so crash-safety doesn't
  depend on the round-trip being native. A future v2 could review `vo_anchor`
  positioning natively instead of round-tripping through the `occ` ordinal.

## 7. Edge cases & failure modes

| Case | Handling |
|---|---|
| **Crash during write-back** | Single `atomic_write_all` (temp-write all, then back-to-back `os.replace`); deck is voiceover-free on disk before *and* after, so no mixed/inline state ever exists. Residual: a deck/companion byte-skew between replaces — loud, marker-detected, auto-demoted to git-HEAD, re-healable; **never silent voiceover loss**. |
| **Second plain (non-companion) sync afterward** | Impossible to misread: handling is automatic and the representation marker forces a cold-start on any mismatch. |
| **Already-in-sync pair** | Empty-plan short-circuit → zero writes (does not rely on byte-fidelity). |
| **New one-language narration → translate** | Inlined voiceover present on one side → engine emits `add …/voiceover [translation pending]` → write-back extracts the translated cell into the other companion. The headline fix. |
| **Orphaned / renamed-slide companion cell** | `merge_voiceover_text` reports it `unmatched` → **refuse the pair, write nothing** (§5.4a). Never dropped. |
| **`voiceover/` subdir vs sibling** | `resolve_companion`/`expected_companion` preserve the existing layout; a new one-sided companion is pinned to the twin's layout; `_prune_other_companions` clears stale copies. |
| **Legacy `notes`-in-companion** | Inlined, extracted voiceover-only → notes migrate into the deck (accepted, one-time; one-shot NOTICE; make inline idempotent to avoid notes doubling on a mid-crash retry). |
| **≤4 files, no open-ended N** | Atomic unit stays the deck **pair**; write set ≤ 2 decks + 2 companions. |
| **No git / dirty tree** | Baseline falls through `watermark → none` as today; projection still applies to whatever baseline is chosen. |
| **Mixed / cross-lang asymmetry** | Refused with a normalize hint (§5.5). |
| **Concurrent companion edit between plan & apply** | Same lost-update exposure the 2-file engine already has, widened to 4; deferred to an optional optimistic re-hash (§8 Phase 4). |

## 8. Implementation phases

- **Phase 0 — Pure factoring + losslessness contract + golden proof (zero
  behavior change).** Factor `inline_pair_text` and `extract_pair_text` from the
  existing pure cores; thread the twin id-map for pure twin-consistent minting;
  operate on cell **copies** (read-mode purity). Define the **total** contract
  (unmatched cell → explicit "unplaceable", identical in both loci). Ship the
  golden round-trip byte-stability suite; wire the semantic-equality fallback gate
  if not byte-stable. De-risks everything downstream.
- **Phase 1 — Detection + refusals + READ modes (non-mutating, high standalone
  value).** Companion detection at the pair funnel; classify plain / separated /
  mixed / cross-lang. Inline the working tree in `build_sync_plan` **and** all
  five baseline sources uniformly (bundle-constructor level). Representation
  marker read + bidirectional demotion. `build_report` excerpts + `verify_pair`
  over inlined text. Mixed + cross-lang refusals as blocking `PlanIssue`s. **This
  phase alone ends "only validate catches companion drift, late"** — it surfaces
  drift in report/verify/diagnose while writing nothing.
- **Phase 2 — APPLY write-back.** Empty-plan short-circuit; `apply_plan` over
  inlined `FileState`s via text-override; suppress the 2-file flush; gate
  re-extract on post-apply alignment; dirty-diff; `atomic_write_all` over ≤4 files
  with empty-companion tombstone inside the batch; watermark recorded in inlined
  space + marker (automatic first-run re-baseline); refuse-and-write-nothing on
  any unmatched cell; one-sided-companion propagation (create the missing
  companion at the twin's layout).
- **Phase 3 — Ledger / bless / accept / autopilot parity + docs.** Make
  `bless` / `record_baseline` / `record_pair` companion-aware (inline before
  recording); record/compare ledger confirmations in inlined space under the
  marker. Route autopilot through the same seam. Fix the validator suggestion
  text. Update `clm info spec-files` / `commands`; add a `changelog.d` fragment;
  correct the PythonCourses authoring instructions that already claim sync
  propagates companion voiceover.
- **Phase 4 (optional/deferred) — concurrency guard.** Optimistic re-hash of all
  four inputs at apply, aborting if any changed since plan. Deferred unless
  dogfooding shows real interleaving.

## 9. Testing

- **Byte-fidelity / no-op:** golden `inline→extract` round-trip over hand-
  formatted fixtures (multi-blank, multiple `vo_anchor`s, subdir + sibling)
  reproduces all four files byte-identically; an in-sync pair reports **0 changes
  and writes nothing** (empty-plan short-circuit, not byte luck).
- **Spurious-add regression:** an in-sync separated pair reports 0 changes against
  a *projected* baseline across each of the five baseline sources (guards §5.3).
- **Headline case:** title-VO added to one companion → `report` shows `add
  …/voiceover [translation pending]`; `apply` writes the translated cell into the
  other companion; second `report` clean.
- **Total-transform / renamed slide:** a companion cell whose owning slide was
  renamed/removed → the pair **refuses and writes nothing** (never a silent
  drop) — in both read and apply.
- **Representation marker:** legacy voiceover-free watermark on a companion deck,
  and inlined watermark on a now-plain deck, each auto-demote to git-HEAD;
  two-run idempotency (run 2 is quiet).
- **Invariant guards:** mixed deck refused; cross-lang asymmetry refused; one-
  sided companion propagates and does *not* spuriously propose on a standing
  asymmetry; a DE-only **notes** companion must **not** propose an EN note add.
- **Read purity:** report quotes the correct narration line and does not re-read
  the working tree; classify unaffected by header-mutation leakage (copies).
- **Crash-safety:** inject a failure between temp-write and replace in
  `atomic_write_all` → all four targets untouched.
- Keep these in the **fast suite** (pure-text / fixture-based, no kernel/docker),
  markers per `docs/developer-guide/testing.md`.

## 10. Smaller adjacent fixes (worth doing regardless)

- **`sync` warns on an untouched, modified companion** even before the full
  feature lands — closes the silent-no-op UX trap immediately.
- **Fix `validator.py::_check_split_companion_for_slide_parity` suggestion text**
  (`validator.py:~1454`) that tells authors to *"route the change through `clm
  slides sync`"* — misleading today, accurate once this lands.
- **`clm slides sync diagnose` surfaces companion-pair status** (separated /
  one-sided / mixed-refused / cross-lang-asymmetry) so the mode is inspectable
  without triggering apply.
- **A `--dry-run` preview listing exactly which cells move inline** on a legacy
  notes-bearing companion, so the irreversible notes-absorption is not a surprise.

## 11. Residual risks / deferred

- **N-file commit is best-effort (no journal, per the maintainer's v1 scope):** a
  crash strictly between `os.replace` calls can leave a deck/companion byte-skew.
  Bounded to loud, marker-detected, re-healable, **never silent voiceover loss**
  (no mixed on-disk state ever exists). Accepted v1 ceiling.
- **Byte-faithfulness ceiling:** where `inline→extract` isn't perfectly
  byte-stable, the no-op path leans on the empty-plan short-circuit + semantic-
  equality gate; legitimate whitespace churn on non-normalized decks is surfaced
  as "normalize first", not silently rewritten. The Phase 0 golden suite is the
  gate.
- **Legacy notes permanently move inline on the first companion-aware run**
  (accepted consequence a). One-time NOTICE; make inline idempotent (dedupe
  identical `(owning_slide_id, role, occ, body)`) — or do the notes migration as
  an explicit one-shot outside the crash-batch — to avoid notes doubling on a
  mid-crash retry.
- **`occ`-ordinal shift** when inlining inserts same-role narratives can
  invalidate stale ledger id-less trust (fail-safe re-check, noisier first pass);
  the first-run re-baseline + banner keep run 2 quiet.
- **Engine stays coupled to one DE/EN pair fanned to ≤4 files;** `vo_anchor`
  positional nuance round-trips through `occ` rather than being reviewed natively
  by the walker. A known ceiling for a future native-awareness v2.
- **Mixed decks stay unsupported by design;** the refusal is the safety net.
  Reopening partial splits reopens obstacle 2 (provenance) as a separate design.
