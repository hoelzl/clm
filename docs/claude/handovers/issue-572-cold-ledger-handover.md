# Issue #572 — cold-ledger stale-twin — handover

**Status:** plan approved; implementing as two PRs. PR 1 = Phase A
(`clm slides rename-id`). PR 2 = Phase B (`body`/`keep_twin` on id-keyed
two-sided `verify_cold`) + Phase C-3a (reworded cold detail).

**Issue:** On a split DE/EN deck whose per-topic ledger is cold for the current
`slide_id`s, an id-keyed cell whose EN source was **edited** is framed
`verify_cold` (only answer `confirm`). `confirm` banks the stale DE twin as the
baseline; `sync verify` + `clm validate` pass. Silent stale-translation ship.

## Decision (post 3-way adversarial review)

The tempting fix — teach the differ to auto-migrate an `id→id` rename by
content fingerprint — was **rejected**. It fights v3's *total identity /
monotone ids* invariant (the only sanctioned key migration is `pos→id`, on
purpose; "inference is out"), and fingerprint collisions on boilerplate/blank/
short cells (which are the norm in CLM decks) make a single-side match
mis-migrate genuinely-new cells — a *new* silent-failure class. A freshness
check cannot exist on a cold cell anyway (no baseline by construction).

**Approved approach:**

- **Phase A (PR 1) — `clm slides rename-id <old> <new>`**: atomically rewrite
  the `slide_id` on both halves AND migrate the ledger baseline key
  (`id:old → id:new`). Design-consistent: keeps identity total across a
  deliberate rename, so a later edit frames `translate_edit` against a live
  baseline. This removes the footgun the issue's repro walks into.
- **Phase B (PR 2) — `body`/`keep_twin` on id-keyed two-sided `verify_cold`**:
  one-pass recovery for decks *already* cold. Scoped to id-keyed only
  (positional path can't target a body — it drops the EN partner). Requires a
  `Decision.side` schema addition and a fix to the pool-coherence guard.
- **Phase C-3a (PR 2) — reword the cold detail** to state `confirm`/`keep_twin`
  banks the twin as-is, no freshness guarantee. Drop `--strict-cold` (it buys
  nothing: `keep_twin ≡ confirm` in ledger effect).
- **NOT shipping:** fingerprint auto-migration (old Phase 1 / Phase D) and
  `--strict-cold`.

## Key file:line anchors

- Cold framing: `sync_diff.py:646-666` (`_diff_unmatched_current`, id-keyed
  two-sided → `verify_cold`); `:2202-2210` (`_classify_pool_news`, positional).
- Cold vocabulary: `doc_apply.py:115` (`verify_cold: ("confirm",)`).
- `confirm` = pure record, no freshness compare: `doc_apply.py:979-995`.
- Ledger always cold in ledger mode: `doc_ledger.py:332` (`complete=False`);
  hash-version drop-to-cold `:334`.
- Sanctioned migration is pos→id only: `sync_diff.py:504-522`,
  `doc_ledger.py:447-458`.
- Record-side rename plumbing (reused by Phase A): `_record_item` for
  `record_key_migration` pops `item.base.key` (id or pos) + upserts new key —
  `doc_apply.py:742-746`; `rename_group_scopes` exists in doc_ledger.
- Warm bootstrap already records a baseline: `translate_bootstrap.py:316-371`.

## Phase B blockers surfaced by review (must fix in PR 2)

1. **Pool-coherence hole:** `_incoherent_pool_confirms` keys only on
   `choice == "confirm"` (`doc_apply.py:1284`) and the guard fires only on
   `confirm` (`:1163`). `keep_twin` (choice set) / `body` (choice None) bypass
   it, yet a `pos:` item runs `rerecord_pool` wholesale (`:791-794`). Widen the
   guard to treat `keep_twin`/`body` on cold members as pool-recording answers.
2. **Positional can't take a body:** `_classify_pool_news` drops the EN partner
   (`:2185`) and yields one-sided members with `twin` unset, so `_holder(item,
   "en")` returns a `.en=None` member. Scope Phase B to id-keyed `verify_cold`;
   positional stays confirm / mint-`slide_id` (`sync-agents.md:158-163`).
3. **`side` is a real wire change:** `parse_decisions` reads only
   `key`/`choice`/`body` and silently ignores unknown fields
   (`doc_apply.py:143-161`); `Decision` is `@frozen` with three fields
   (`:99-105`). Add `Decision.side` + extraction + validation; reject `side`
   where meaningless. No existing side-targeted-body convention to reuse.
4. **Handle/tags (only relevant if the dropped Phase D is ever revisited):**
   `_classify_matched` handle derivation `sync_diff.py:767`; `_tags_only_change`
   re-reads `entry.key` at `:1370`.

## Docs / changelog to touch

- `src/clm/cli/info_topics/sync-agents.md`, `commands.md`.
- `changelog.d/572-*.added.md` / `.changed.md`.
- Info Topics Maintenance Rule: `rename-id` is a new CLI command → update
  `commands.md`.

Full plan: this repo did not commit the working plan; the reasoning is captured
above and in the PR descriptions.
