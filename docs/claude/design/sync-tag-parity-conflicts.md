# Sync v3: Tag Parity as a First-Class Diff Aspect (Issue #615)

**Status**: designed, not yet implemented.
**Issue**: [#615](https://github.com/hoelzl/clm/issues/615) — `confirm` on a
`verify_translation` item banks a one-sided tag edit; `report` goes silent
while `validate` flags the pair.

## 1. The bug, restated in engine terms

A localized member whose DE **and** EN bodies drifted off the ledger baseline
is framed as one `conflict/verify_translation` item. When the DE side *also*
carries a one-sided tag edit (`notes` → `voiceover`), that tag delta is folded
into the same item. `confirm` is "a pure ledger record; nothing mutates"
(`doc_apply._apply_choice_decision`), so the apply pass re-records the member
from a fresh snapshot of the **current** deck — including the cross-side tag
divergence (`de_tags=["voiceover"]`, `en_tags=["notes"]`). From then on:

- `sync report` is empty: `_classify_localized` compares each side only
  against **its own** recorded fingerprint; both are at base → `in_sync`.
- `sync verify` passes: its check set is unify + slide_id set symmetry +
  `(slide_id, role)` uniqueness (`sync_verify.structural_violations`); tag
  parity is not in it, and localized cells are exempt from unify's
  byte-identity oracle by design.
- `clm validate` warns via `_check_split_tag_parity` (positional pairing of
  all non-j2 cells) — and its suggestion ("run `clm slides sync`") loops,
  because sync no longer frames anything.

## 2. Root cause

**Cross-side tag parity is a *pair* invariant, but the v3 differ evaluates
localized members side-against-own-baseline only.** The invariant "tags are
language-independent and mirror across the twins" (§3.1; enforced by
`validator._check_split_tag_parity`) is checked by the differ only
*incidentally*, in the narrow states where the tag delta happens to be
isolated:

- `_classify_shared` emits `mirror_tags` only when the cross-side **bodies are
  byte-equal** (`sync_diff.py:1047`);
- `_classify_localized` reaches `_tags_only_change` only when **both bodies
  are at their per-side baselines** (`sync_diff.py:1367-1369`).

The moment a tag delta coincides with body drift, four independent gaps line
up (defense-in-depth failure):

1. **Classification gap** — the tag aspect is silently subsumed by the
   body-centric row (`verify_translation` / `translate_edit`). No row
   addresses it; no vocabulary can answer it. Note the design already treats
   *layout* and *owner* as orthogonal aspects emitted as separate rows for the
   same handle (`_check_layout` / `_check_owner`); tags never got that
   treatment.
2. **Recording gap** — resolving the body row (`confirm`, a `body` answer,
   `keep_twin`) records the member's **entire** fresh snapshot as trusted
   (`_record_item` → `_upsert` → `snapshot_deck`), tag divergence included.
   `confirm` has no invariant gate.
3. **Differ invariant-blindness** — a baseline that *itself* carries a
   cross-side tag divergence classifies as `in_sync`. Contrast: for **shared**
   members a base-carried *body* divergence gets a standing
   `pending_divergence` row; localized members have no analogous row for the
   one cross-side invariant they do have (tags).
4. **Verify gap** — `sync verify` / `structural_gate` never check tag parity,
   so the deterministic safety net that is supposed to catch what apply gets
   wrong is blind to exactly this corruption class.

The reproduction's "contrast" case (tag edit with bodies at base →
`mirror_tags`, mirrors cleanly) confirms the doctrine is right and only the
coverage is partial.

## 3. Latent sibling bugs found during analysis

These share the root cause and should be fixed by the same change (or at
minimum tracked):

- **S1 — `translate_edit` banks the same divergence.** DE body **and** tags
  edited, EN untouched → single `translate_edit` row. The `body` answer writes
  only the twin's body (`_apply_body_decision`, `_replace_body` keeps the
  twin's header); recording then banks `de_tags≠en_tags`. Same silent end
  state as #615 via a different path. Ditto `keep_twin`.
- **S2 — tag conflict on a localized member is answered by whole-cell
  propagate.** `_tags_only_change` emits `conflict_shared` when both tag sets
  moved differently (`sync_diff.py:1388-1396`). The `de`/`en` answer for
  `conflict_shared` executes `ex.propagate` — a **verbatim whole-cell copy** —
  which would overwrite the twin's *translated body* with the other language.
  (The `body` answer is equally wrong: it writes one shared body onto both
  sides of a localized pair.)
- **S3 — landing a mechanical aspect row blesses unverified siblings.** A
  landed `mirror_layout` / `mirror_owner` / (post-fix) `mirror_tags` on a
  member whose framed body row is still pending records the member's fresh
  snapshot wholesale — including the unverified drifted bodies. The "never
  bless the unresolved" doctrine exists only for positional pools
  (`_drop_unresolved_from_pools`); id-keyed members have no equivalent.
- **S4 — `verify_cold` confirm banks divergent tags.** A cold two-sided member
  with mismatched tags can be confirmed, producing the same
  report-silent/validate-flagged end state (with no baseline to attribute a
  direction, so nothing ever re-frames it — until fix F1's baseline-carried
  row, which needs recorded tags and therefore does not cover entries whose
  tag fields predate recording).

## 4. Design

Four fixes, one per gap. F1 is the core; F2–F4 are the safety nets whose
absence let F1's gap escalate to silent corruption.

### F1 — differ: split the tag aspect out as an orthogonal row

Add a `_check_tags(member, group, entry, handle)` aspect check in
`_classify_matched`, following the `_check_layout`/`_check_owner` pattern, for
matched **two-sided** members on the localized path (base-localized members
and headers; shared members are already covered — any cross-side byte
difference, tags included, keeps them in `pending_divergence` /
`conflict_shared`, and every shared resolution copies whole cells). Per-side
"tags moved" means `base tags is None or current ≠ base` (a `None` base —
e.g. the just-landed variant of the `verify_translation` "landed" branch —
counts as moved, never as trusted).

Decision table, evaluated only when the **current cross-side tag sets
differ**:

| state | row | rationale |
|---|---|---|
| exactly one side's tags moved off base | `mirror_tags` (mechanical, existing action + executor) | doctrine: tags mirror; direction is attributable; executor works regardless of body drift |
| both sides' tags moved, differently | **new framed action `conflict_tags`**, choices `("de", "en")` | no safe source side — P8 |
| neither side moved (the baseline itself carries the divergence) | `conflict_tags` | the post-#615-damage state and any historical banked divergence; no direction inferable |

`conflict_tags` execution: `ex.mirror_tags(item_with_side(item, choice),
choice)` — mirrors **only the tag set** from the chosen side. This replaces
`_tags_only_change`'s `conflict_shared` emission for localized members,
fixing S2 (`conflict_shared`'s propagate/body answers are body-destroying for
localized pairs).

The body rows become tag-agnostic; `verify_translation`'s `confirm` keeps its
meaning ("the bodies are a faithful pair").

**Co-emission rule.** A mechanical `mirror_tags` may co-emit with a framed
body row under the same key (mechanical rows never consult the decision
document — no keying conflict; this is exactly how layout/owner rows coexist
today). But decision documents are keyed by member handle alone, so **two
framed rows on one key cannot both be answered** (a pre-existing wart —
`conflict_owner` + `verify_translation` already collide). Therefore: when
`_check_tags` produces a *framed* `conflict_tags` and the content
classification would also produce a framed row, emit **only `conflict_tags`
this pass** and suppress the body row. The report stays non-silent
(`needs_agent`, non-empty counts), and the body row re-frames on the next
report once tags are reconciled. Sequencing over parallelism; converges in
two passes. (Follow-up option, out of scope: an optional `action` field on
decision rows to disambiguate same-key framed items, which would remove the
sequencing.)

Cases that keep their current behavior:

- bodies at base, one-sided tag move → `mirror_tags` (the issue's "contrast"
  case);
- bodies at base, both moved identically → `record_tags`;
- tags cross-side **equal** (even if off-base) → no tag row; whichever
  content/record row resolves the member records them.

**Fork-time tag check.** `record_fork` is the one mechanical row that
upgrades a member from one shared fingerprint to per-language fingerprints,
so it is the one row that can *legitimize* cross-side divergent bytes —
including a tag divergence — as a trusted baseline. (Its counterpart is
safe: `record_unify` requires byte-equal content fingerprints, and tags are
inside the fingerprint, so divergent tags land on `unify_choose_body`
instead. The shared-path rows all enforce byte identity or copy whole
cells.) Leaving this to the baseline-carried branch would work but has a
conditional guarantee — "caught one report later" assumes a report pass
happens, while the committed ledger advertises the divergence as verified in
the meantime — and it destroys direction information the differ still has at
fork time (the base entry holds the *shared* tag set, so a one-sided move is
still attributable). Therefore `_check_tags` also runs on the fork-complete
branch of `_classify_fork`, comparing each half's tags against the base
shared tag set:

- exactly one half's tags moved → co-emit mechanical `mirror_tags` alongside
  `record_fork`. Two *mechanical* rows on one key have no decision-keying
  collision (that problem only exists for two framed rows); both land in the
  same pass and a tag-consistent state is banked immediately.
- both halves moved differently → co-emit framed `conflict_tags`; F2's
  unresolved-key guard then defers `record_fork`'s `_upsert` automatically,
  so the divergent state is never banked and the member re-frames next pass.

### F2 — apply/recording: never bless a member with unresolved rows

1. **Unresolved-key guard** (fixes S3, required for F1's co-emission): in
   `apply_deck`, compute `unresolved_keys = {i.key for i in unresolved_items}`
   and skip the `_upsert` for any landed item whose key is in it. The file
   mutation still lands; the ledger entry stays at its old baseline and the
   member re-frames next report (e.g. post-`mirror_tags`: the mirrored twin's
   fingerprint is off base → a clean `verify_translation`, tags now in
   parity). Report such items with an honest status/reason ("applied
   (recording deferred: unresolved sibling item)") rather than "recorded".
   This extends the pool doctrine ("never bless the unresolved",
   `_drop_unresolved_from_pools`) to id-keyed members.
2. **`conflict_tags` records nothing** in `_record_item` (like frozen pools):
   its resolution mutates a header line; the member re-frames and records on
   the next pass. This avoids blessing bodies that were suppressed by the
   co-emission rule (they are not in `unresolved_items`, so guard 1 cannot see
   them).
3. **Confirm guard** (belt-and-braces; fixes S4 and any future classification
   gap): `confirm` — on `verify_translation` *and* `verify_cold` — refuses
   when the member's current DE/EN tag sets differ, with a reason ("tag sets
   diverge cross-side (de: [...], en: [...]) — tags are language-independent;
   answer the tag item / align the tag lines, then re-report"). Safe in-pass:
   `set_side` mutates `Member` in place, so a same-pass `mirror_tags`
   (emitted before the body row, executed earlier in the ordered loop) is
   visible to the guard, and a `confirm` answered in the same document still
   lands in one pass.

### F3 — verify: add a `tag-parity` check

Add a tag-parity violation kind to `sync_verify.structural_violations`
(pairing by `(slide_id, role)` for id'd cells, positionally for the id-less
remainder — mirroring the validator's approach), at **warning** severity:

- `validate`'s finding is itself a warning; verify then *agrees* with validate
  (the issue's third expectation) instead of passing silently.
- Error severity would (a) hard-fail CI for pre-existing committed
  asymmetries, and (b) flow into `structural_gate`'s error subset, making the
  per-slide/whole-deck **write gate** refuse to record a pair the apply pass
  is in the middle of reconciling (chicken-and-egg with F1's own tag rows).
  Verify's contract is "structural safety"; a tag mismatch does not corrupt
  pairing or unification. Warning severity surfaces it without repurposing
  the gate.

### F4 — docs

- `src/clm/cli/info_topics/sync-agents.md`: the new `conflict_tags` action and
  vocabulary, the confirm tag guard, the two-pass sequencing behavior.
- `src/clm/cli/info_topics/commands.md`: verify's new warning kind.
- Changelog fragment `changelog.d/615-sync-tag-parity.fixed.md`.

### Migration

None needed. Decks already carrying a banked divergence (the #615 end state)
re-frame automatically: baseline-carried divergence → `conflict_tags` on the
next report. Ledger entries whose recorded tag fields are `None` (older
`hash_version` states) classify per the `None`-means-moved rule and land on
`conflict_tags` rather than a silently-mechanical mirror.

## 5. Adversarial analysis

Attacks run against the design, and their resolutions:

- **"Sequencing hides the body conflict."** When `conflict_tags` suppresses a
  framed body row, one agent pass cannot resolve everything. *Held, but
  bounded*: the report is never silent, `needs_agent` is set, convergence is
  exactly two passes, and the alternative (same-key decision disambiguation)
  is a bigger schema change noted as follow-up. Correctness over latency.
- **"The unresolved-key guard causes re-frame loops."** E.g. `mirror_layout`
  landed + `translate_edit` pending → the layout re-records as
  `record_relayout` every pass until the body is answered. *Accepted*:
  record-only rows are idempotent, no file churn, and the loop ends with the
  framed answer. The alternative — blessing drifted bodies (status quo, S3) —
  is strictly worse.
- **"Is auto-executing `mirror_tags` safe mid-body-drift?"** Yes: the twin's
  tag set is at base (one-sided case), so no information is lost; the moved
  side's tag set is doctrine-correct on both sides. Both-moved is framed. The
  executor (`ex.mirror_tags`) touches only the header tag attribute.
- **"Same-pass confirm reads stale state."** Checked: `DeckEmitter.set_side`
  mutates the (non-frozen) `Member` in place and `_holder` resolves through
  `item.member`/`item.twin`, so a guard in `_apply_choice_decision` observes
  the same-pass tag mirror. One-pass resolution works when the agent confirms
  alongside the mechanical mirror.
- **"`record_fork` banks divergent tags."** Initially accepted as residue
  ("the baseline-carried branch catches it one report later"), then
  *promoted into F1 scope* on a second look: the one-pass-later guarantee is
  conditional on a report pass actually happening while the committed ledger
  advertises the divergence as verified, the banking destroys the direction
  attribution the differ still has at fork time, and closing the hole rides
  entirely on machinery F1/F2 already build (see F1's fork-time tag check —
  a few lines in `_classify_fork` plus two tests). *Closed.*
- **"The verify_cold guard breaks legacy onboarding."** Confirming a cold
  member with committed tag asymmetry now rejects, and in a positional pool
  the pool-coherence rule leaves the whole pool unrecorded. *Intended*:
  validate flags those pairs anyway; blessing them would recreate #615's
  silent state with no baseline to ever re-frame it. The rejection reason
  tells the agent the one-line fix. Must be documented in `sync-agents.md`.
- **"Warning-severity verify still 'passes'."** Exit code unchanged, yes — but
  the mismatch is *surfaced* in verify output, validate remains the policy
  authority, and F1/F2 ensure sync itself never again manufactures or
  strands the state. Error severity was rejected for the gate/CI reasons in
  F3.
- **"Fix confirm only (make it mirror tags) instead of the differ?"**
  Rejected: it leaves S1 (`translate_edit`/`keep_twin` bank the divergence),
  leaves banked divergences invisible to report (still needs the differ row),
  and breaks the load-bearing "confirm is a pure ledger record" simplicity.
  The differ is where the engine's contract ("anything that fits no row must
  become a framed decision carrying the member's full state — never a silent
  default", P8) says this belongs.
- **"Closed-registry costs."** `conflict_tags` must be added to
  `FRAMED_ACTIONS`, `_DECISION_VOCABULARY`, the §7.4 matrix expectations, and
  info topics. The hypothesis noise-floor property ("any single one-sided
  mutation … hard per-mutation item ceiling") may need its ceiling revisited:
  a one-sided tag+body edit now yields two rows where it yielded one. Known,
  budgeted.
- **"Scope holes."** One-sided members: no cross-side pair to check — their
  add/remove/transition rows already force resolution to a two-sided state,
  which is then checked. Shared members: covered by byte-identity rows.
  J2/preamble scopes: carry no tags. Validator remains the backstop for
  anything the member model cannot pair.

## 6. Test plan

1. **#615 end-to-end repro**: localized member, both bodies off base, DE tags
   `notes→voiceover` → report frames `mirror_tags` + `verify_translation`;
   `apply --decisions '[{key, confirm}]'` mirrors EN tags, banks bodies;
   fresh report clean; `validate` clean.
2. **Confirm guard**: divergent tags + confirm with no tag row landed →
   `rejected` with the alignment reason (both `verify_translation` and
   `verify_cold`).
3. **Baseline-carried divergence** (the damaged-deck state): ledger with
   `de_tags≠en_tags`, everything else at base → `conflict_tags`; answer
   `de` → EN tag line mirrored, nothing recorded; next report → mechanical
   record → clean.
4. **Both-moved-differently tags** on a localized member → `conflict_tags`
   (not `conflict_shared`); answering `de`/`en` must not touch bodies (S2
   regression test).
5. **S1**: DE body+tags edited → `mirror_tags` + `translate_edit`; body
   answer lands on a twin whose header already carries the mirrored tags.
6. **S3 guard**: `mirror_tags` landed, body row pending → ledger entry
   unchanged (old baseline), next report frames a clean `verify_translation`.
7. **Verify**: tag-parity warning surfaces on a mismatched pair; exit code
   unchanged; `structural_gate` (error subset) unaffected.
8. **Fork, one-sided tag move**: complete fork where one half changed its
   tags off the shared base → `mirror_tags` + `record_fork` co-emit; one
   apply pass lands both; the banked entry has matching per-side tags.
9. **Fork, divergent tag moves**: complete fork where both halves changed
   tags differently → `conflict_tags` (framed) + `record_fork`; with the
   conflict unanswered, the ledger entry is unchanged (nothing banked); after
   answering `de`/`en`, the next pass records a tag-consistent fork.
10. **Matrix/property tests**: extend the §7.4 walk with the tag axis; adjust
    the per-mutation item ceiling.
