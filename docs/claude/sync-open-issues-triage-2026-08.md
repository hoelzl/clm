# Sync engine: open-issue triage and remediation (2026-08-21/22)

Full-depth triage of every open sync-engine issue (#892, #885, #826, #787),
run as three parallel code-verifying investigations against master
`5beda3da`, followed by staged remediation. Each issue was classified as
**engine bug**, **workflow defect**, or **agent misunderstanding** — the
maintainer's standing question after the v3 stabilization arc.

**Headline: none of the field reports were agent misunderstandings.** Every
report traced to a real engine or workflow defect; the one documentation
failure found was *clm's own docs* teaching a decision-document shape that
`apply` refuses (fixed in PR #893). The agents' reports were accurate to
the line.

## The cross-cutting diagnosis

All three engine defects are one invariant failing at three different
pairing authorities: **a mechanical (or directed) action executing against
a pairing/order state the engine cannot prove.**

| Issue | Authority where proof failed | What executed on the guess |
|---|---|---|
| #826 | id-less positional pool (lens cursor marriage) | `mirror_tags` / `propagate_shared_edit` / `copy_new_shared` / pool re-record |
| #885 | group bracketing under a visible order divergence | member-keyed `mirror_order` (plus an insert guard rejecting with no path out) |
| #892 | parse-precondition gates (unify-based stamp gate) | nothing executed — the *refusals* deadlocked instead (the dual failure: refusing without an escape) |

The remediations all strengthen the same doctrine ("frame, never guess";
"never advertise an answer apply must refuse"; #630's dedicated-action
freeze rule) rather than adding shape-specific patches — per the design
note's P8 and the maintainer's no-special-cases requirement.

## #826 — fork class-shift mispairing (engine bug, CRITICAL) — FIXED, PR #896

**Verified mechanism** (byte-identical repro on master): a mid-pool class
transition (fork/unify/pos→id stamp) takes a cell out of one side of a
positional pool; the lens's cursor marriage has no content check, so every
later sibling pairs with the wrong twin; the pool's mechanical rows then
execute against the guess. The filed shape overwrote a sibling's *tags*;
probe variants destroyed whole *bodies* (`propagate_shared_edit`, always
claiming a victim at the pool tail), reproduced on the unify route, banked
the mis-marriage into the ledger, and mechanically *duplicated* the cell on
the next pass — never converging.

**Fix — shifted-pool trust suspension**: the differ marks every pool whose
per-side accounting it shifted (absorb claims, the migration, and a
body-match the #644 deficit gate refuses because a backfilling add masks
the departure — separated from the pinned #644 *clone* shape by
byte-accounting, not by the match alone). In ledger mode a marked pool
suspends cross-side trust: slots not provably at base on both sides frame
one answerless `pool_pairing_shifted` row naming the causing transition;
`record_neutral` keeps executing (byte identity is proof); the action joins
the pool-freezing set **and** the shifted set rides `DeckDiff.shifted_pools`
into apply's freeze (a marked pool can be *silent* — review finding F1 —
and a landed sibling answer would otherwise re-record it). Snapshot mode
(`--since`) keeps the raw reading. Pre-merge adversarial review found and
the PR closes: the silent-pool freeze gap (F1, critical — baseline
destruction + decision-free duplication), the masked-stamp mechanical copy
(F3), the dead `mark_twin` advertisement on one-sided fork frames (F4),
plus honesty/wording items (F5–F7).

**Residue (tracked as #900)**: the transition frame itself may still
absorb the *wrong* twin — framed, with both cells visible, never silent.
Rescuing the true twin from a mispaired two-sided member needs
(id, side)-keyed absorption; deliberate follow-up, not blocking.

## #885 — apply corrupts the adopted side + silent translate_new drop (engine bugs, CRITICAL) — FIXED, PR #898

Both symptoms reproduced from reconstructed field state
(`slides_020_visualization_comparisons`, PythonCourses `87539410`).

**Symptom 2 (the damaging one)**: the `order_decision: de` answer itself
routes correctly (`_mirror_group_order` rewrites EN only). The DE wound
came from **co-executing mechanical member-keyed `mirror_order` rows** the
user never answered: earlier gate-refused passes had kept *file* writes
while withholding the *ledger* ("landed mutations stay"), so the next diff
read the engine's own corrupted EN intermediate as an authoritative author
edit with base-backed proof — proof laundering through the files/trust
desync — and mirrored the corruption onto DE, moving a slide-start cell
across its own body cells in the same pass as the 'de' answer.

**Symptom 1**: the #720 anchor-adjacency guard rejects the `translate_new`
anchor mint *indefinitely* while the order divergence stands; the rejection
is per-item on stderr, `decision_errors` stays null, `wrote:true` (sibling
rows wrote), and the differ re-frames the identical row with no memory —
an advertised answer apply must refuse, with nothing naming the blocking
order question.

**Fix (PR #898, "one order authority per pass")**: while an
`order_decision` is framed — answered or not — mechanical `mirror_order`
rows defer per item (the #824 keep-defer precedent; P7-compatible) and
re-derive from the settled order; the #720 guard's rejection becomes a
self-explaining deferral naming the open order handle; the apply envelope
gains a top-level `left_undone` summary. Its adversarial review's critical
(C1: the order answer's own cluster emitter re-manufactured the displaced
anchor, so pass 2's uncontested mirrors rewrote the preserved side and
pass 3 read clean) is closed by anchor-first cluster emission, with the
flagship test walking the loop to convergence; deferred pool-handle
mirrors additionally join the recording freeze (I1).

## #892 — report ↔ normalize --stamp-ids deadlock (workflow defect, HIGH) — partially fixed, remainder designed

**Verified circularity**: `sync report` refuses id-less cells → "run
`normalize --stamp-ids`"; stamp-ids' unifiability gate (`unify_texts` +
byte round-trip) refuses because the *new one-sided cells themselves* make
the pair non-unifiable — the greedy cursor walk structurally cannot
distinguish "one-sided new shared cell" from "diverged shared pair"; the
named escapes (`--operations interleaving`, `assign-ids`) decline by
design. Only hand-written `slide_id=`s break the cycle (7 in the field
report, across 3 report rounds because refusal classes surfaced one per
round).

Landed: **PR #894** (the soft-refusal quoting a flag `normalize` doesn't
have — the remedy is now structured, each surface renders its own);
**PR #895** (all refusal classes enumerate in ONE parse — only
`duplicate_id`, which genuinely poisons pairing, still returns early).

The dissolution itself landed as **PR #899**: not a stamp-ids carve-out
and not mint_id rows in `sync report` (that alternative is recorded as
rejected in the §13 row — it re-enters normalization into the loop §3.4
keeps out and needs apply-on-refused-deck + freshness-token carve-outs).
Instead, when the text-level unify gate fails, `--stamp-ids` stamps from
`parse_bundle` — the engine's one pairing authority — via
`ParseOutcome.provisional_deck` (strict never-diff contract): localized/
narrative twins share one EN-authority slug; one-sided cells (id-less
anchors; shared cells inside two-sided groups, whose `verify_cold` was an
answerless dead end) stamp solo; adjacent one-sided id-less anchor twins
pair by cursor per lens pool class. Its adversarial review found two NEW
circles of the same species (a second run's false "not unifiable"
refusal; the `verify_cold` remedy naming the stamper where it skips
one-sided groups) plus a false "tokens never merge" premise, a
kind-blind twin zip, and a silent empty-slug skip — all fixed pre-merge.
Deliberate residue, documented: a twin the lens cannot see mints a
wrong-but-FRAMED identity, repaired by `rename-id`.

## #787 — ceremony backlog (enhancements; not part of this arc's bug scope)

The three asks (mechanical-sweep/`keep_twin` shorthand, partial bodies,
slide-scoped answers) are real ceremony costs, explicitly needing their own
design passes — left open as the P5/P6 backlog. The thread's report_id
papercut (clm's own documented example omitted the mandatory token) was
real and is fixed (**PR #893**: both examples carry `schema` + `report_id`;
the refusal names the exact key path).

## Classification summary

| Issue | Verdict | Status |
|---|---|---|
| #826 | engine bug (silent data loss + trust corruption + livelock) | fixed — PR #896 |
| #885 | engine bugs (proof laundering + dead advertised answer + envelope gap) | fixed — PR #898 |
| #892 | workflow defect (gate circularity) + two papercuts | fixed — PRs #894, #895, #899 |
| #787 | enhancement backlog + one doc bug | doc bug fixed (#893); backlog stays open |
| — | agent misunderstandings | **none found** |

Investigation transcripts (repro probes, per-line citations) lived in the
session scratchpad; the durable findings are this file, the PR bodies, the
issues, and the design note's §13 rows.
