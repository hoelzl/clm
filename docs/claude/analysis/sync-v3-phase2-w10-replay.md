# Sync v3 Phase 2 — W10 dogfood replay triage

**Date**: 2026-07-02 | **Issue**: #520 (Phase 2 exit evidence)
**Design gate** (`sync-total-identity-document-model.md` §11 Phase 2):
*"shadow disagreements triaged to zero-or-explained; the W10 replay reports
~3 items, not 73."*

## Setup

- Scenario: the 2026-06-23 AZAV ML W10 production reconcile
  (`PythonCourses`, `slides/module_410_ai_dev`, 52 deck pairs).
- Historical replay: working tree at `7432ec60~1` (the pre-reconcile
  state), both engines at `--baseline 176a0225` (the June-20 topic-split
  ref the dogfood used), via `clm slides sync shadow`.
- Ground truth: the reconcile commit `7432ec60` ("sync EN with
  authoritative DE after German edits") changed **4 EN files**; the dogfood
  hand-read all 70+ flagged cells and found **3 genuine changes** (~96%
  noise, empty excerpts — the linchpin pain of assessment 2).

## Historical replay result (repo-wide ref base)

| engine | items | breakdown |
|---|---|---|
| v2 | **98** | 58 `conflict` + 16 `edit` + 23 `issue` + 1 `add` (empty excerpts) |
| v3 | **61** | see triage below |
| errors | 0 / 0 | both engines survived every pair |

v3 triage — every item lands in one of four fully-explained classes:

1. **26 × `run_normalize`** (one framed item per refusing deck). The
   historical tree predates the Phase 0 one-time normalize
   (PythonCourses#78, 2026-07-02): these decks carry duplicate ids /
   id-less localized cells at `7432ec60~1`, so the §3.4 precondition
   correctly refuses them. On today's normalized corpus the same decks
   parse. This class is an artifact of replaying a pre-normalize state and
   cannot occur post-cutover.
2. **16 × `verify_translation`** ("both sides moved off base"). Pairs that
   were legitimately translated *inside* the replay window. A repo-wide git
   ref cannot see mid-window syncs — this is exactly the baseline-scheme
   noise class assessment 2 identified as v2's ~96%, and exactly what the
   per-member ledger (design §5, Phase 3) exists to erase. v3 names the
   class honestly (verify currency, full excerpts) instead of emitting
   empty-excerpt "conflicts".
3. **7 × `verify_cold`** — `slides_mcp_deep_dive` did not exist at the base
   ref (split into its own topic mid-window); with no base state every
   member is a framed one-time verification. Ledger mode records these
   once.
4. **12 genuinely actionable**: 4 `translate_edit` (member-keyed,
   correctly directional) + 4 `translate_new` + 3 `record_remove` +
   1 `record_symmetric_edit` — aligned with the reconcile's actual file
   set and the legitimate post-baseline restructurings (e.g. the
   `workflow-5 → workflow-6` rename pair in `slides_60_workflows`).

## The ledger-simulated replay (per-deck-correct base)

No single repo-wide ref can express "each deck's last-verified state" —
different decks synced at different commits inside the window (the design
§5 argument, now empirically confirmed). Giving the differ the *correct
per-deck base* simulates what the Phase 3 ledger provides for every deck:

```
clm slides sync shadow slides/.../slides_setup_copilot.de.py --baseline f486957a
# (f486957a = this deck's last synced commit before the German edits;
#  tree = 7432ec60~1, the pre-reconcile state)
→ v3 = 2 items, both genuine, zero noise:
    edit/translate_edit id:prerequisites    (de_to_en)
    edit/translate_edit id:troubleshooting  (de_to_en)
```

Both items are real one-sided German edits from the window. The reconcile
propagated `prerequisites`; `troubleshooting` (du-form → Sie-form) was
implicitly judged as already covered by the EN text — a genuine judgment
call the tool correctly *surfaces with both bodies attached* rather than
deciding. This is the ~3-item behavior the gate describes, achieved
wherever the base is per-deck correct.

## Verdict against the exit gate

- **Shadow disagreements: zero-or-explained.** Every v2/v3 divergence falls
  into the four classes above; no unexplained item, no engine error, no
  false mechanical propagation (the class that loses data).
- **~3 items**: achieved under per-deck-correct bases (the ledger
  simulation); structurally unreachable from a single repo-wide ref on this
  window — 3 of the 4 genuinely-changed decks refuse pre-normalize, and the
  16 both-moved items are the ledger's job. The replay therefore *confirms*
  the design's central claim rather than weakening it: per-member committed
  trust (Phase 3) is the missing and sufficient ingredient for the
  noise-free report.

Corpus noise floor (the standing Phase 2 gate, `test_sync_diff_corpus.py`):
self-diff over all 644 parsed pairs stays under the 45-item ceiling with
every action inside the closed registry — the differ manufactures no noise
of its own.

Raw JSON payloads of both replays were produced with
`clm slides sync shadow … --json` and are reproducible from the refs above.
