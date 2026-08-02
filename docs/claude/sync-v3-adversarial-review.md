# Sync v3 Adversarial Review — Design and Implementation (2026-07-29)

**Status**: review complete; no code changed. Findings verified against the
tree at `d861088a` (post-1.23.1).
**Author**: Claude (Fable 5), commissioned by the maintainer.
**Method**: five parallel adversarial code reviews (differ/order model,
apply/ledger/handles, lens/pairing/refusals, verify–validate coherence,
agent-facing contract) over the ~8.5k-line v3 core, cross-checked against
every agent-filed field report. The pairing and differ findings were
*reproduced with minimal probes*, not inferred from reading. Line citations
are to the reviewed tree and will drift.
**Evidence base**: collection issue #656 (P1–P8 pain points + four field
reports incl. the fork recipe F1–F4 and the `record_group_rename` ledger
corruption); open bugs #649, #650, #652, #653, #654, #655, #682; the closed
v3-era arc (#555, #566, #570, #572, #600, #609–#611, #615, #629, #630, #644,
#646); `docs/claude/design/sync-total-identity-document-model.md` (the design
note) and `sync-tag-parity-conflicts.md`; the 2026-07-10 friction audit
(11 sessions, ~700 decisions).
**Relation to prior assessments**: this is *not* assessment 3. Assessments 1–2
asked "keep or replace the engine"; this review's answer to that question is
**keep** — see §1 — and everything else is a hardening program.

---

## 1. Verdict

The v3 core concept — total member identity, one committed trust store, one
generic 3-way diff, per-item value-keyed apply — **held up in production and
should be kept**. The evidence is not sentiment: ~700 decisions across 11
agent sessions with zero engine faults in the loop itself; a 31-item deck
expansion synced in one round with no re-framing; a 60-fork conversion with
zero content drift, byte-verified through the ledger; the W10 noise goal
(~3 items, not 73) met. No prior engine iteration survived contact with real
maintainer edits at all. A third rewrite is not warranted.

But the review confirms a hard pattern in where v3 *does* fail, and it is not
random. Every field failure lands in one of five places:

1. **What the model demoted to second-class.** Order and positional (un-id'd)
   members sit outside the identity/trust core, and essentially every silent
   data-loss or silent-divergence bug lives exactly there (§4 G1, G2).
2. **The seams between layers.** The lens guesses where the differ is
   forbidden to (adoption by byte-equality); the write gate is weaker than
   the repo's own quality gate; handles are bound to nothing that
   guarantees freshness (§4 G2–G4).
3. **The refusal cliff.** The parser refuses whole decks for states the
   design's own escape hatch (§7.4-3: "never a refusal of the deck")
   promises to frame (§4 G2).
4. **The agent contract's ambiguous encodings.** One field (`answers: []`)
   means both "apply will do it" and "you are blocked"; the report's own
   output is not valid decision input; several advertised answers are
   guaranteed rejections (§4 G4).
5. **Ceremony the engine pushes back onto the agent.** Verbatim writes that
   fail the repo's own linter, full-cell bodies for one-line edits, a fork
   flow whose only route is the operation the doctrine forbids (§4 G5).

Tough but fair: the maintainer's stated fear is v1/v2-style patch-cascade
decay. The patch cascade has **not** recurred in the identity core — the
post-cutover fixes (#566 → #572 → #600 → #610 → #630 → #615) each landed as
transition rows or framed actions, exactly as P8 prescribes, and #615 in
particular is a model fix. The decay signs are instead at the *edges*: three
engine changes have already landed without the mandatory §13 amendments row,
the design note's §5 schema no longer matches the ledger the code writes, and
`clm validate` — the oracle users actually trust — appears nowhere in the
design at all. The cascade risk today is not "identity regimes multiply
again"; it is "the parts the model never covered (order, refusals, the
contract surface) accrete ad-hoc guards until they *become* the old engine."

---

## 2. What v3 demonstrably got right (keep, and defend)

- **Total identity for id'd members works.** Across every field report, no
  id-keyed member was ever mis-paired, mis-directed, or lost by the differ.
  The failures are all in positional identity, order, or the layers around
  the differ.
- **The committed ledger as sole trust store works.** Per-member per-side
  fingerprints made twin-drift vs source-drift distinguishable in principle
  (§7 Q5), survived reverts correctly, and let an independent reviewer verify
  60 forks by reading the ledger diff. The #448→v3 consolidation is done and
  nobody misses the watermark.
- **Per-item apply and the framed-decision model work.** Partial progress is
  never lost (one abort-path exception, A-L3); rejected answers reject
  individually with mostly-actionable messages; `keep_twin` and the
  #572 `body`+`side` recovery are exactly the right kind of vocabulary
  growth.
- **The refusal-over-guess doctrine is right** — where it is applied at the
  right granularity. #610/#630's reframe-don't-delete arc and #600's
  `stamp_vs_new` show the P8 "redesign the action" route working repeatedly.
- **#615 is the template fix.** It named the generative defect ("a pair
  invariant checked only side-against-own-baseline"), fixed it at four
  layers (differ row, recording guard, verify check, docs), and wrote the
  design down. §5 of this review is largely "apply the #615 treatment to
  order."
- **The process artifacts are the right idea.** P1–P8, the §7.4 matrix test,
  the §13 amendments log, corpus oracles, adversarial pre-merge reviews —
  no prior iteration had any of this. The findings below include process
  lapses, but the machinery being lapsed *from* is sound.

---

## 3. How to read the findings

IDs: `G#` generative defects (§4), `C#` critical, `M#` major, `N#`
minor/latent (§5), `D#` design-note/process (§6). Appendix A maps every
filed issue to findings. Severity is by *worst credible consequence*, not
frequency. "Verified" means reproduced or read directly in the cited code by
at least one reviewer; cross-cutting claims were confirmed by two or more
independent reviewers; the two highest-impact novel claims (C1's adoption
rule, C7's silent redirect) were additionally spot-checked in the primary
session.

---

## 4. The five generative defects

Assessment 2 traced ~80% of v2's defects to three generative defects. The
same exercise over the v3 field evidence yields five. Roughly 80% of the
catalog in §5 is a consequence of one of these.

### G1 — Order is outside the trust model

The design note's §6.2 promises an `order` outcome ("sequence diff over
MemberKeys; mechanical when one side moved, decision when both"). The
implementation has the machinery — three order-scope kinds, `mirror_order`,
`order_decision`, an executor that resolves both — but the *trust model
around it was never designed*: §5's ledger schema contains no order scopes at
all; they were bolted on unspecified. Four structural consequences, each
field-confirmed:

- **Order has no cold state.** A missing member entry frames `verify_cold`
  (fail-safe). A missing order scope frames *nothing* — `_compare_order`
  silently returns when the base scope is empty or fewer than two common
  handles survive (`sync_diff.py:2982-2984`), and `_diff_order` only visits
  scopes the base already has (`sync_diff.py:2939-2942`). Absence of order
  trust is indistinguishable from verified order in every consumer.
- **Order trust cannot bootstrap through the verb loop.** Order scopes are
  seeded only by a *full* `record`, `split`, or `translate-bootstrap`, or by
  a landed order item — but order items can only frame when base scopes
  already exist. A deck whose ledger was built by report → confirm → apply
  is **permanently order-blind while presenting as fully verified** (C3).
- **The write gate has no order invariant.** `structural_violations` checks
  id *set* symmetry, not sequence; `unify_texts` interleaves language-tagged
  cells permissively, so only reorders that happen to collide different-byte
  *shared* cells trip it. The ledger has certified an order-divergent pair
  in the field (#652 instance 2) (C4).
- **The executor presumes order parity.** `insert_mirrored`'s
  mirrored-predecessor placement (`doc_write.py:129-180`) is correct exactly
  when the streams are order-parallel and has no group-bracketing guard —
  under divergence it writes twins outside their group, up to file end
  (#652 instance 1) (C5).

This is *precisely* the defect shape #615 diagnosed for tags — "a pair
invariant evaluated side-against-own-baseline only" — with the same four
gaps (classification, recording, verify, docs). Tags got the four-layer fix;
order never did. The template exists in-repo.

### G2 — Identity totality breaks at the lens layer

P1/P2 hold in the differ but not in the parser that feeds it:

- **Anchor-hood is tag-derived, and anchors are excluded from by-id
  pairing** (`doc_lenses.py:447-448`). So `slide`/`subslide` tags *are* an
  identity-regime selector — the exact thing P2 forbids. A one-sided retag
  (slide → continuation, a routine authoring edit) makes the same id an
  anchor on one half and a plain member on the other; the lens then builds
  **two members with one key** and refuses the whole deck as `duplicate_id`
  with zero items (#653) — despite the deck having a perfectly consistent
  one-member interpretation. The §7.4 transition space (`langness × id ×
  layout × side`) is missing the **role/anchor axis** entirely, so by P8's
  own terms the model is being violated and nobody stopped to redesign (C2′
  = M2, D6).
- **The lens still guesses where the differ was forbidden to.** The #572
  review rejected fingerprint-inferred identity ("inference is out");
  #644's fix added a pool-deficit guard in the differ. But the lens's rule-2
  adoption (`doc_lenses.py:506`) still marries cells on *byte-equality or a
  bare `lang` attribute*, with no cardinality guard — one layer above the
  guard. Two probe-verified consequences: a new id'd cell inserted before a
  byte-identical positional cell steals its twin and the differ then frames
  a **mechanical `mirror_remove` that deletes an authored cell** (C1); a
  one-sided localized insert above a #443 pending-stamp twin silently
  acquires *another slide's translation* (C2).
- **The refusal cliff contradicts §7.4-3.** "Never a refusal of the deck"
  is quoted in `sync_diff.py`'s module docstring; `doc_lenses.py` refuses
  whole decks for repairable, agent-induced mid-edit states (one-sided
  retag; one-sided fork of an un-id'd shared cell, even body-unchanged —
  M5). The refusal *messages* compound it: `duplicate_id`'s only hint
  (`rename-id`) is wrong for every phase-3 cause and circular for the
  anchor-shape one (M2).

### G3 — Three verdict surfaces, no containment contract

`report is_clean`, `sync verify` (also the write gate), and `clm validate`
are three different oracles with **no designed relationship** — the design
note never mentions `clm validate`. They are not a strictness ladder; they
are a Venn diagram with every region populated (verified matrix, §5 T-block).
Verify is *stronger* than validate on shared-cell bytes, id sets, and
companion bytes, and *weaker* on order and cell form — so "ledger recorded,
verify PASS, validate red" (#654) and "engine writes cells its own linter
warns on" (#655) are both reachable, and were both reached in the field.
Nothing at all checks the trust store's internal consistency, which is how
`record_group_rename`'s dangling references (C6) went unnoticed until a
human read the ledger diff.

### G4 — The agent contract encodes states ambiguously and binds handles to nothing

- `answers: []` means "mechanical — apply executes it" on 21 actions and
  "blocked — hand-edit files, exit 1 until you do" on 5 framed actions
  (`fork_pending_twin`, `unify_pending_twin`, `broken_owner`,
  `kind_mismatch`, `ambiguous_alignment`). The info topic documents the
  first meaning as universal, and its own recommended filter script
  misclassifies all five (M6).
- Some advertised answers are guaranteed rejections: a one-sided positional
  `verify_cold` advertises `confirm`, which apply always rejects — the §8
  watch-item ("advertising an answer the executor then rejects is a
  defect") violated in shipping code (M6).
- A decision handle binds to nothing fresh: apply recomputes the diff and
  set-checks keys; there is no report token, so "stale handle" is a fiction
  in both directions, and the silent companion→deck redirect
  (`sync.py:139-143`) makes two CLI spellings alias one deck — the complete,
  code-verified mechanism of #649's "rejected but the write landed" (C7).
- The report's own `de`/`en` excerpts include the `# %%` line that
  `_validate_body` rejects — report output is not valid decision input; every
  agent independently rediscovers "strip line 1" (M10).

### G5 — The engine owns mechanics but pushes mechanical work back to the agent

The design's goal 4 is "the tool owns only the mechanics it can do
perfectly." In practice the agent hand-performs mechanics the engine could
own: canonical-form normalization of written bodies (#655 → a pointless
`keep_twin` round after every out-of-band fix); full-cell twin bodies for
one-line edits (~200 hand-assembled lines for 14 part-cell edits); marking a
fork twin's `lang=` attribute (the one operation the doctrine forbids, F1);
a `keep_twin` decision round per mechanical sweep that a bounded auto-answer
policy could execute (`tools/sync_sweep.py` exists downstream to prove it);
and reviewing 883-line ledger diffs in which ~0 lines carry trust changes
(provenance ping-pong + a `confirmed_commit` that provably names a commit
not containing the recorded state). The deleted `autopilot` was supposed to
be the policy loop over the verbs (§8); v3 shipped without any successor.

---

## 5. Findings catalog

### Critical — silent data loss or trust corruption

| ID | Finding | Where | Field evidence |
|---|---|---|---|
| C1 | Lens rule-2 adoption by byte-equality steals a positional cell's twin; differ then frames mechanical `mirror_remove`; **apply deletes an authored cell silently**. Probe-verified end-to-end; also breaks the one-handle-per-item contract (two items share `id:new-x`). | `doc_lenses.py:505-522` (guard-free `adopt` at 506) | lens-level sibling of closed #644; unreported, live |
| C2 | The `lang_attr is not None` adoption arm is unconditional: a new one-sided localized id'd cell above a #443 pending-stamp twin **silently acquires another slide's translation**; a cold `confirm` would bank the mis-marriage. Probe-verified. | `doc_lenses.py:506` | unreported, live |
| C3 | **Order-trust bootstrap circularity**: order items require base order scopes; the verb loop never seeds them (cold diff exits before `_diff_order`; confirm/apply record members only); only full `record`/`split`/`translate-bootstrap` seed. Confirm-seeded decks are permanently order-blind and report `is_clean` over divergent order. A deck rename (path-derived deck key) recreates the state. | `sync_diff.py:460-471, 2939-2951, 2982-2984`; `doc_apply.py:955-1007`; `doc_ledger.py:338-340` | root cause of #654 and #652-i2 |
| C4 | **The structural write gate has no order invariant** — id *set* symmetry only; `unify_texts` interleaves lang-tagged cells greedily, so localized-only group swaps pass and **the ledger certifies order-divergent pairs**. The only order-parity check in the codebase is a validator *warning*. | `sync_verify.py:150-177`; `split.py:543-584`; cf. `validator.py:1434-1515` | #652-i2 (field-proven certification) |
| C5 | `insert_mirrored` places a minted twin after its predecessor's target-side position with **no group-bracketing guard** — under order divergence it writes outside the group, through file end; the re-parse gate checks parseability only. | `doc_write.py:129-180`; `doc_apply.py:1534-1560` | #652-i1 (file-end corruption) |
| C6 | `record_group_rename` uses `rename_group_scopes`, which does **not** rewrite `member_order[].handles` values nor `pos:` members' `owner` refs (the purpose-built `migrate_ledger_key` does all four); the sole trust store carries dangling references, **no surface checks ledger self-consistency**, and order trust for renamed anchors silently evaporates via the `common_set` intersection. | `doc_ledger.py:534-575` (owner copied at 550; keys-only at 568-569); `rename_id.py:130-182`; `doc_apply.py:946-954`; erosion via `sync_diff.py:2982` | #656 comment 4 (18 dangling refs on one deck) |
| C7 | **Verdict contradicts effect (#649)**: no freshness token exists (apply recomputes the diff; "stale handle" names a report that doesn't exist at apply time), and the **silent** companion→deck redirect makes `voiceover_X` and `slides_X` alias one deck+ledger section — so a second apply finds members already recorded, rejects its decisions as "stale," while the first apply's writes stand. Additionally the "N rejected" stderr block prints *after* the JSON, so merged-stream consumers get unparseable output. | `doc_apply.py:1471-1529`; `sync.py:89-143` (redirect, no echo); `sync_v3.py:280-290` | #649 (every symptom reproduced by this mechanism) |

### Major — dead ends, blocked work, contract violations

| ID | Finding | Where | Field evidence |
|---|---|---|---|
| M1 | A group anchor renamed **and** edited in one pass fails `_detect_group_renames` (requires byte-unchanged anchor on both sides), drops the group from the order comparison's `common_set`, and the filtered sequences can equal base — the move vanishes from evidence. This, plus C3, is why "edits suppress the order item" in #652. | `sync_diff.py:686-696, 2982-2986` | #652-i1 delta |
| M2 | One-sided slide-tag shape change → whole-deck `duplicate_id` refusal, zero items, misleading message ("resolves to 2 distinct members"), wrong header (`normalize --stamp-ids` can't fix it) and **circular hint** (`rename-id` renames both halves; the split survives). Fires in both retag directions. The slide↔subslide retag, by contrast, frames fine — the coverage is exactly the anchor↔non-anchor half. | `doc_lenses.py:447-448, 848-849, 976-986`; `bilingual_doc.py:242-248, 262` | #653 (both cases — the "positional divergence" variant had a hidden missing tag underneath; position alone does **not** refuse, probe-verified) |
| M3 | Moved slides/subslides are excluded from `_diff_cross_group_moves` by design ("anchors define groups") and delegated to deck-level group order — the channel with no cold state (C3). A one-side slide move frames nothing and reports clean. | `sync_diff.py:2868-2907` | #654 |
| M4 | A single id'd cell among positional siblings is order-untrackable: not cross-group, not in the pool, `common_set < 2`. No item, no observation. | `sync_diff.py:2982-2984`; `doc_identity.py:221-222` | latent |
| M5 | **Id-less fork-in-progress refuses the whole deck** (`idless_localized`), even body-unchanged — §7.1's "a half-completed transition is directly observable" silently assumes the member is id'd. Post-normalize recovery degrades to `stamp_vs_new`+`translate_new`; and the fork identity-carry (`fork_match`) requires one side's body still at base, so the natural one-pass edit (stamp id + `lang=` + adapt translation) **silently drops ledger history** (field F3). The required two-pass sequencing is documented nowhere. | `doc_lenses.py:248-256, 696-714`; `sync_diff.py:772-777` | #656 report 3 (F1/F3/F4) |
| M6 | `answers: []` carries two opposite meanings (5 blocked framed actions vs 21 mechanical); the doc states the mechanical meaning as universal and its own example script misclassifies; a one-sided positional `verify_cold` advertises `confirm` which apply always rejects and the pool-coherence guard then blocks the whole pool. | `doc_apply.py:119-156, 1104-1108, 1274-1277, 1646-1665`; `sync_diff.py:2734-2754`; `sync-agents.md` L60-61 | #656 report 3 (F2); friction audit |
| M7 | Info-topic drift: `verify_translation` documented as accepting a `body` the engine rejects (the #1 field-reported contradiction, still present); 6 of 21 framed actions undocumented; hidden `body` options on `conflict_shared`/`unify_choose_body`; "the only key migration is pos:→id:" false since `record_group_rename`; `--dry-run` documented as validating everything while it skips the verify gate. | `sync-agents.md` L60-74, L154, L200-201, L303-313 vs `doc_apply.py:119-135` | #656 report 2 |
| M8 | **Topic-ledger lost-update race**: concurrent applies on sibling decks load→mutate→save the whole `TopicLedger`; the later save reverts the earlier deck's section (files stay new → spurious conflicts). Plus fixed `.tmp` names in `atomic_write_all` collide under same-deck concurrency. Multi-agent workflows are an explicit goal. | `sync_v3.py:221-253`; `doc_ledger.py:266-316`; `path_utils.py:349` | latent (high in multi-agent use) |
| M9 | `copy_new` copies the source cell verbatim **including `lang=`** on the `treat_as_new`/`keep` paths (`translate_new` carefully swaps it); the re-parse gate then refuses `wrong_language_cell` and **the whole pass aborts**, downgrading every co-landed item to `failed` — the per-item isolation promise (P7) broken at the gate. | `doc_apply.py:596-602, 1344-1367, 1550-1559` | latent |
| M10 | Apply writes bodies verbatim (no leading-blank-`#` normalization) → validate warns on engine output → out-of-band fix → full `keep_twin` ceremony round; report excerpts include the `# %%` line that decision bodies must exclude; `choice`/`body` exclusivity enforced-but-undocumented. | `doc_apply.py:217-227, 300-315, 424-436`; `normalizer.py:908-952`; `sync_diff.py:302-306` | #655 |
| M11 | The fork flow's only route is the hand edit the doctrine forbids: `fork_pending_twin` has no vocabulary (no `mark_twin`), its detail says "mark the twin," and `sync-agents.md` says "do not hand-edit the other language." | `sync_diff.py:1859-1867`; `doc_apply.py:119-135` | #656 report 3 (F1) |
| M12 | Surface honesty: `record`/`apply` success does not imply next-report-clean (tag advisories re-frame — undocumented); `sync verify` CLI treats unresolvable companions as warning/exit-0 while the write gate errors on the same state ("run verify in CI" is advertised); write-`OSError` leaves per-item results saying `applied`. | `sync_verify.py:434-446, 653-668`; `sync_v3.py:235-253`; `doc_apply.py:1565-1570` | verify W4/W5; latent |
| M13 | Ledger churn drowns review: `preserve_unchanged_member` keys on provenance, so record↔apply ping-pongs every touched member (and `rerecord_pool` churns untouched pool siblings wholesale); `confirmed_commit` always names a commit that does not contain the recorded state (stamped pre-commit) and freezes across reverts. 883-line diffs for 60 changed cells. | `doc_ledger.py:374-387, 492-503`; `sync_v3.py:296-304` | #656 report 3 |
| M14 | JSON-mode contract holes: decision-document parse errors exit 2 with **no JSON envelope on stdout** (the apply-refusal path emits one — gratuitous inconsistency); exit 1 overloaded (work-pending / residue / `--member` success with unrelated items / sweep partial errors); `report`/`apply` don't embed `exit_code` (verify does); report `counts` is outcome-keyed while apply `counts` is status-keyed. | `sync_v3.py:167-169, 205-218`; `doc_apply.py:369-371` | #649 (stdout); #656 report 1 (silent exit-1) |
| M15 | Slide removal orphans its voiceover companion (`for_slide` dangles) and the failure surfaces at **build time**, far from the edit. Machinery exists (`broken_owner` conflict; gate `companion-refusal` errors) but evidently does not cover the removal flow end-to-end — *not independently reproduced by this review*; needs a repro to locate the exact gap. | `sync_diff.py:1213-1224`; `sync_verify.py:434-446` | #650 |
| M16 | Discoverability routing: `rename-id` reachable only from one (wrong-for-most-causes) refusal hint; `record` never suggested when drift is twin-side-only (the review-after-translate trap — every reviewed cell reframes `translate_edit`); the fork two-pass recipe exists nowhere; "no decision vocabulary **in Phase 3**" leaks internal jargon with no remedy. | `bilingual_doc.py:242-248`; `doc_report.py:85-106`; `doc_apply.py:1104-1108` | #656 reports 1, 3, 4 |

### Minor / latent

- **N1** — `_locate_twin`'s unique-fingerprint fallback can select a
  different slot's byte-identical cell after same-pass drift → silent
  wrong-cell write. Low probability, P8-class impact
  (`doc_apply.py:546-563`).
- **N2** — Pool hygiene matches entries by `(de_fp, en_fp)` pairs;
  byte-identical duplicates make `_drop_unresolved_from_pools` /
  `_sweep_migrated_pos` discard a just-confirmed sibling's entry
  (fail-safe direction, silent work loss) (`doc_apply.py:1034-1081`).
- **N3** — Reserved `title` collisions: a deck cell id'd `"title"` → "3
  distinct members" refusal (en route violating `_pair_groups`' injective
  pair-map assumption, masked only by refusal ordering); a companion with
  `slide_id="title" for_slide="title"` evades `legacy_title_companion` into
  the generic message it exists to prevent (`doc_lenses.py:788-793,
  884-899`).
- **N4** — Companion pool keys include bare `for_slide`: retargeting a
  pending-stamp companion's owner in the same edit lands it in a different
  pool → `idless_narrative` whole-deck refusal (`doc_lenses.py:259-268`).
- **N5** — Group-split guard (#610/#630) finds rivals only among
  `verify_cold` items — inert in complete-snapshot mode; safe today only
  because apply always diffs ledger-mode. Pin with a comment/test
  (`sync_diff.py:548-549`).
- **N6** — Files land, then the gate runs, then the ledger saves: the
  crash-between window leaves "files new / trust old" and is undocumented
  (`sync_v3.py:235-253`).
- **N7** — Companion preamble bytes are stripped by projection and reach no
  oracle (gate, validate) and the differ only when previously recorded
  (`sync_companion.py:189-207`).
- **N8** — Partial `record --member` never refreshes order/preamble scopes —
  stale order lists can later mask or mint order verdicts
  (`doc_ledger.py:428-444`).
- **N9** — Narrative/voiceover member order is invisible to validate
  (slide-start ids only) and to verify; with unseeded scopes, to the differ
  too — a reordered narration sequence records silently.
- **N10** — `slide_id=""` / `"!"` refusals never say a visibly-present
  attribute was discounted as empty/marker-only (`doc_lenses.py:191-199`).
- **N11** — Mid-move members contribute their EN index to the DE-side
  scope's sequence via the merged-order group token — bounded contamination
  of same-pass order verdicts (`sync_diff.py:2953-2966`).

---

## 6. Design-note and process findings

- **D1 — The §13 discipline lapsed within days of its installation.**
  #615 (engine change, separate design doc), #644 (engine guard, no doc
  edit), and Y2/D8 (ledger gating change) all landed after #630 with no §13
  row. The standing rule says such a change "has skipped the P8 checklist."
  The audit that installed §13 (2026-07-10) identified the stale design note
  as *the* actual gap; it is stale again.
- **D2 — The design is fragmenting.** #615's design lives in
  `sync-tag-parity-conflicts.md`, not in the note that declares itself the
  target model. One note with §13 rows, or an explicit index of satellite
  design docs in the note — either works; the current state is neither.
- **D3 — §5's ledger schema is fiction on order.** `group_order`,
  `group_order_by_side`, `member_order` (with handles), `owner`,
  `tags_fp`-per-side, `confirmed_commit` — none are in the §5 entry schema.
  The bugs live exactly in the unspecified parts (C3, C6, M13).
- **D4 — §8 lists `autopilot` as a verb; Phase 4 deleted it** and no
  successor exists. The design's "human path" is currently a dangling
  pointer, and the mechanical-sweep field evidence (§7 Q6) shows the gap is
  real for agents too.
- **D5 — `clm validate` appears nowhere in the design** although it is the
  oracle users treat as authoritative; the verify/validate relationship is
  an unmanaged emergent property (G3).
- **D6 — §7.4's transition space is missing the role/anchor axis** (G2);
  §7.1's "a half-completed transition is directly observable" is false for
  un-id'd members (M5). §9's honest-residue list should also name: the
  order-bootstrap hole, `_compare_order`'s silent degradation regimes, the
  gate's missing order invariant, and placement's order-parity assumption.
- **D7 — The corpus gates are pinned to a moving private repo** (#682): one
  test doing two jobs (CLM regression gate vs course quality gate), failures
  uninterpretable, not CI-runnable. The probe fixtures built for this review
  (C1, C2, M2 reproductions) are exactly the shape the bundled corpus
  should grow.

---

## 7. Questions worth a maintainer decision

These are the places where this review questions the design itself, not the
implementation. Each has a recommendation, but all are judgment calls.

**Q1 — Should positional identity be shrunk rather than guarded?** Every
silent-loss finding (C1, C2, closed #644/#610/#646, M4, M5) lives in un-id'd
cells. §3.4 deliberately left ~13k shared cells unstamped to avoid churn;
the price is a permanent ambiguity class plus lens-level guessing.
*Recommendation*: don't revisit the global stamping decision yet — first
land the cardinality guards (Phase 0 below), which remove the *guessing*
while keeping positional identity for the aligned steady state. Then measure
what's left. If pool ambiguities keep generating framed dead ends, a
per-deck opt-in "fully id'd" mode (stamp on first sync touch) is the
escalation path — monotone under P3, reviewable per deck, no big-bang churn.

**Q2 — Should decisions bind to a report snapshot?** Today's contract
("apply revalidates against a recomputed diff") is defensible in isolation
but produces #649's verdict/effect contradiction the moment deck aliasing or
sibling passes exist. *Recommendation*: yes — `report_id = hash(bundle bytes
+ ledger deck-section)`, echoed in decision documents, refused wholesale on
mismatch (exit 2, nothing written), plus classifying already-satisfied keys
as `already_applied` rather than `rejected`. Both are additive and make
every stale-handle situation self-explanatory.

**Q3 — Is the decision-document schema due a v2?** Accumulated warts: no
`action` field (two framed rows on one key cannot both be answered — the
#615 doc calls this out and sequences around it), no `resolution`
discriminator (M6), `body` excludes the delimiter the report includes (M10),
no `mark_twin` (M11). *Recommendation*: one deliberate schema revision
(schema 4) fixing all four together — additive fields, old documents still
accepted — rather than four incremental band-aids. This is P8(c) territory
and low-risk; the cost is mostly doc and MCP updates.

**Q4 — One pair-health oracle or three?** *Recommendation*: containment
now, delegation later. Immediately: the gate's error set must contain
validate's split-pair errors, and id-sequence order parity joins
`structural_violations` (error at the gate — an order-diverged pair must
never be *recorded*; warning in the CLI if pre-existing divergences make
error disruptive; the severity split has precedent). Structurally: validate's
split-pair family becomes an adapter over `parse_bundle` +
`structural_violations`, killing the positional-artifact diagnostics
(#654's phantom tag mismatches) by construction.

> **Status (2026-08-02): the containment half is DONE; delegation stays open.**
> Order parity landed with #719. Containment is now a pinned property
> (`tests/slides/test_gate_validate_containment.py`) rather than a hope: a
> validate *error* implies a non-empty gate, over twelve corruption shapes,
> with each shape's validate severity **declared** so a check downgraded to
> `warning` cannot silently shrink the claim. Measured over the 730-pair
> corpus: **0 gaps** — containment already held, it was simply untested, and
> the gate is in fact strictly stronger (it sees shared-companion body drift
> validate misses entirely). One real defect surfaced and was fixed: the
> whole-deck gate returned its promoted `order-parity` violation still
> labelled `severity="warning"`, so any caller re-filtering the gate's own
> output on `severity == "error"` would silently reopen #652.
>
> What remains is the *structural* half — validate's split-pair family
> becoming an adapter over `parse_bundle` + `structural_violations`. The
> containment test makes that refactor safe to attempt (it pins the relation
> the refactor must preserve) but does not perform it, and the
> positional-artifact diagnostics it would kill are still live.

**Q5 — Should the report distinguish twin-side drift from source-side
drift?** The ledger has per-side fingerprints; the engine already knows the
difference; the report frames both as `translate_edit` and lets the agent
discover `keep_twin`/`record` from external docs. The review-after-translate
field report cost ~30 pointless decision items. *Recommendation*: yes —
either a `drift: source|twin|both` field per item plus a report-level hint
("all drift is twin-side — `sync record` banks a hand-reviewed twin"), or a
distinct cheap `confirm_twin` framing. The information is already computed.

> **Status (2026-08-02): DONE, via the report-level route.** The differ emits a
> deck-level `uniform_drift_side` observation when *every* `translate_edit`
> drifts on the same side, naming the side, `keep_twin`, and the opposite
> reading; `verify_translation` rows are counted in the detail so the summary
> cannot be over-read into a blanket answer. The per-item `detail` also names
> `keep_twin`, the human report prints the observation after the items, and
> `clm info sync-agents` promotes the check out of the bulk-translate bullet
> into "Reading the report".
>
> **The `drift: source|twin|both` field was deliberately not built.** Framing
> it revealed that `side` and `direction` already ship on every item, so the
> field would add no *information* — it would add an *inference*, requiring the
> engine to name one half authoritative. The engine is symmetric: it knows
> which side moved, never which is the source of truth. Asserting that is the
> guess this programme exists to remove, so the observation reports the side
> and spells out both readings instead. Additive: no `WIRE_SCHEMA` change, no
> new framed action, no classification change.

**Q6 — Sanction the two flows the doctrine pretends don't exist.** (a)
Hand-edits: four flows *require* them today (fork twin-marking, order
repair, tag-shape refusal recovery, verify_translation-with-stale-twin).
Either give each an in-engine answer (`mark_twin`; first-class order items;
anchor-shape framing; `body` on `verify_translation` — symmetric with
`verify_cold`'s #572 recovery and the doc already promises it) or rewrite
the doctrine to "hand-edit only when the report tells you to, then
`record`/re-report." Both are coherent; the current contradiction is not.
(b) Mechanical sweeps: `apply --mechanical` (auto-answer exactly
`translate_edit→keep_twin`, `verify_translation→confirm`, hard-fail on
anything else, never default-on) is the autopilot-as-policy-script §8
promised, with the safety property field-proven by the downstream sweep
driver. *Recommendation*: adopt both; they remove the two largest ceremony
classes without weakening the trust model.

**Q7 — Per-topic ledger files: keep, but harden.** The granularity itself
was not implicated in any field failure (#649's "shared ledger" hypothesis
was wrong). What is implicated: whole-file load-mutate-save concurrency
(M8) and review noise (M13). *Recommendation*: keep per-topic; add
merge-on-save or locking, UUID temp names, provenance-insensitive
`preserve_unchanged_member`, and either fix `confirmed_commit`'s definition
("HEAD at record time; state not yet committed") or drop the field.

---

## 8. Remediation program

Ordered so that silent-loss stops first and every phase is independently
shippable. Sizes are honest guesses from the mechanism analyses.

**Phase 0 — stop silent data loss and trust corruption (small PRs, high
urgency).**
1. Cardinality-guard the lens adoption rule (C1, C2): adopt only when the
   pool residues make the correspondence forced — the lens analogue of
   #644's `_pool_side_deficit`. Probe reproductions become regression
   fixtures.
2. Group-bracketing guard in `insert_mirrored` (C5): a placement outside
   the owner group's target-side span fails the item into framed residue
   (P8: never guess) instead of writing corruption.
3. Id-sequence order parity in `structural_violations`, error severity at
   the gate (C4): the ledger must stop certifying order-divergent pairs.
4. `record_group_rename` → `migrate_ledger_key`, plus a ledger
   self-consistency check on load/save (every `owner`/handle resolves;
   offenders degrade to cold) (C6).
5. `copy_new` lang-attribute swap on `treat_as_new`/`keep` paths (M9).

*Status: **complete 2026-07-29** — issues #716–#720, PRs #721–#725, merged
sequentially, each with failing-before regression tests, a §13 amendments
row in the design note, and a changelog fragment (item 1 → #716, item 2 →
#720, item 3 → #719, item 4 → #718, item 5 → #717). Two documented
deviations from the prescriptions above: item 4 shipped as an extended
`rename_group_scopes` (all four reference classes: pos keys, order-scope
keys, owner refs, member-order `id:` handles) plus a save-time
`prune_dangling_refs` sweep, rather than `migrate_ledger_key` — stale
handles are dropped and a dangling owner degrades to `None`, not cold; item
5's severity was corrected to defense-in-depth on the issue (the e2e probes
showed the suspect shapes route to `fork_pending_twin` before reaching
`copy_new`). See the design note's §13 rows for the shipped mechanisms.*

**Phase 1 — make order first-class (the #615 treatment).** Frame
`order_decision` from *current* cross-side evidence even with no/thin base
(the executor already resolves `de`/`en` from current state); keep
`mirror_order` for the directed case; seed order scopes on any apply pass
that ends with zero unresolved items and equal sequences (breaks C3's
circularity without new verbs); make a parse-observed
`group_order_divergence` suppress `is_clean` (one-line honesty). Cross-group
moves of anchors get their channel back via the deck-level row now framing
unconditionally (M1, M3, M4). Design note: new §5 order-scope spec + §13
rows + §9 residue entries (D3, D6).

**Phase 2 — the agent contract.** Report snapshot token +
`already_applied` + loud companion redirect + `deck_key`/ledger path in
every payload (C7, Q2); `resolution: mechanical|decision|manual`
discriminator and the advertised-answer fixes (M6); body normalization at
the write boundary + body-only excerpts in payloads (M10); JSON envelope on
every exit path with embedded `exit_code`, stderr block moved before the
payload (M14, C7); `verify_translation` body answer (or, minimally, fix the
doc) (M7, Q6a); `mark_twin` + the fork two-pass documentation (M5, M11);
routing hints for `rename-id` and twin-drift `record` (M16); refusal-code
split — `anchor_shape_divergence` and `reserved_id_collision` with per-side
lines and correct hints (M2, N3). Consider bundling the schema-4 revision
(Q3).

**Phase 3 — anchor-shape transitions and refusal degradation.** Merge
same-id anchor↔non-anchor pairs into one member carrying an
`anchor_shape_divergence` observation; add the role axis to §7.4 with the
mechanical `mirror_tags`-and-move-boundary row (direction from ledger
`tags_fp`); extend region slicing for group tails; frame the id-less
fork-in-progress instead of refusing (M2, M5, G2). This is the largest
engine change in the program and should get its own design addendum and
adversarial pass.

**Phase 4 — trust-surface coherence and ledger hygiene.** Validate
split-pair delegation to the engine model (Q4); `record`/`apply` honesty
about advisory residue; verify-CLI/gate severity alignment (M12);
provenance-insensitive preservation + `confirmed_commit` fix-or-drop (M13);
ledger merge-on-save + UUID temps (M8); reproduce and fix #650 (M15).

**Phase 5 — ceremony and scale.** `apply --mechanical` (Q6b); twin-drift
hint or `confirm_twin` (Q5); slide-scoped item grouping in the report (P6);
partial-body answers or an engine-side draft mechanism (P5) — the largest
open UX question, fine to defer behind everything above; corpus-gate split
per #682 with the Phase-0 probe fixtures folded into the bundled corpus
(D7).

---

## Appendix A — filed issue → findings

| Issue | Findings | One-line mechanism |
|---|---|---|
| #649 | C7, M14 | silent companion→deck aliasing + no freshness token; stderr after JSON |
| #650 | M15 | removal flow evades `broken_owner`/gate coverage — needs repro |
| #652 i1 | C5, M1, (C3) | rename+edit hides the move; insert placed by order-parallel assumption; gate catches only via shared-cell collision |
| #652 i2 | C3, C4 | empty order scopes → no items; gate blind to localized-only order divergence; ledger certifies it |
| #653 | M2, G2 | one-sided anchor-ness → two members, one key → whole-deck refusal; hints wrong/circular; position alone does **not** refuse |
| #654 | C3, M3, (Q4) | moved anchor delegated to an unseeded channel; validate's positional tiers manufacture phantom tag mismatches |
| #655 | M10, (Q3) | verbatim writes vs normalizer's canonical form; report/decision format asymmetry |
| #656 P1–P8 | G5, M6, M7, M10, M13, M16, Q3, Q5, Q6 | ceremony findings, all mechanism-confirmed |
| #656 fork (F1–F4) | M5, M11, M6 | doctrine contradiction; identity degradation on one-pass fork; byte-identity trap |
| #656 rename | C6 | `rename_group_scopes` vs `migrate_ledger_key` asymmetry |
| #682 | D7 | one test, two jobs; pin + split |

## Appendix B — review provenance

Five parallel reviewers over the v3 core, each returning code-cited
mechanism reports: differ/order (incl. the bootstrap-circularity and
placement analyses), apply/ledger/handles (incl. the #649 reconstruction —
code-verified chain, field-sequence inference explicitly flagged),
lens/pairing (probe-reproduced: #653 both variants, C1, C2, the fork
refusals, `title` collisions), verify–validate coherence (the coverage
matrix and disagreement windows), and the agent-contract audit (drift
table over `sync-agents.md`, the answers-protocol classification, JSON/exit
audit). Findings were merged, deduplicated, and the two highest-impact
novel claims re-verified in the primary session. Where a claim rests on
inference rather than reproduction, the catalog says so (M15, parts of C7).
