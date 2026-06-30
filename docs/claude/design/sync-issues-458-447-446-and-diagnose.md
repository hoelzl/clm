# Slides-sync improvements: #458, #447, #446, and a `sync diagnose` verb

**Status:** ✅ all shipped (2026-06-30). #446 `--since` → PR #489; `sync diagnose`
→ PR #490; #458 comment-token hashing → PR #491; #447 `--conflict` policy
(autopilot-only + escalate) → PR #492. Implementation notes that diverged from this
proposal: `sync diagnose` runs reconcile's occurrence-pairing **and** verify (the
id-less-twin case is verify-invisible); #447 applies the policy as a conflict-only
`apply_plan(conflict_decisions=…)` overlay (a plain `decisions=` map would defer the
deterministic edits) and the escalate tier reuses `judge.propose(loser, winner)` as a
directional containment oracle (no new judge); Studio's `//`-deck hash fix (#458) is
latent because Studio is `.py`-only today. This doc is retained as the design record.
**Date:** 2026-06-29 (designed); 2026-06-30 (shipped)
**Scope:** three open issues plus tooling that automates a manual diagnostic
workflow.

This document designs fixes for three open slides-sync issues and a fourth,
larger improvement that turns a hand-run diagnostic procedure into a command.
Each design was mapped against the real code and then put through an adversarial
review; the **corrections from that review are folded in below** (and called out
explicitly so the rationale survives).

## The four threads at a glance

| # | Issue / source | What it does | Risk | Size |
|---|---|---|---|---|
| A | **#458** | Thread the real comment token through reflow-insensitive markdown hashing so `//`-decks (C++/C#/Java/TS) get #429's benefit | internal-only, needs a hash-version bump | M |
| B | **#446** | `--since DATE\|REF` to resolve a timeframe to a baseline commit (sugar over `--baseline`) | additive, no engine change | M |
| C | **#447** | A non-interactive `--conflict de-wins\|en-wins` policy for authoritative-side reconcile | **irreversible data loss** if misused; opt-in only | M–L |
| D | analysis doc | `clm slides sync diagnose` — classify each `verify` symptom into its root cause and auto-fix the mechanical ones | additive, dry-run by default | L |

## Why these belong together

The analysis doc (`diagnosing-sync-id-asymmetry.md`) and the global
`reconcile-deck-edits` skill describe **manual** procedures an agent runs today.
Three of these threads exist precisely to collapse those procedures:

- The `reconcile-deck-edits` skill **step 1** ("pick the baseline ref from the
  user's timeframe with `git rev-list -1 --before=…`") is exactly **#446**.
- The skill **step 4** ("conflicts → DE wins → re-translate → re-report, by
  hand") is exactly **#447**; the skill text even says *"There is no
  non-interactive 'DE-wins-all' policy yet — see the filed issue."*
- The analysis doc's whole diagnostic procedure ("a `verify` failure is a
  symptom, not a diagnosis; classify before fixing") is **thread D**, and
  shipping its catalog into `clm info sync-agents` makes the doc itself
  redundant downstream.

So the deliverable is not four unrelated patches — it is **closing the loop on
the agent-driven reconcile workflow** so an agent can drive a whole messy week
of edits from a timeframe hint with little intervention and high trust.

## Shared constraints (apply to every thread)

These came out of the context/overlap scan and bound all four designs:

1. **`sync` is a verb group** (`_DefaultVerbGroup`, `sync.py:1404`) with verbs
   `report / verify / apply / task / accept / baseline{show,bless,clear,prune,
   seed,establish} / autopilot`. A new `diagnose` is a **sibling leaf verb**,
   not a flag; `--since` / `--conflict` are **per-verb flags** added exactly like
   `--baseline` / `--ledger`. Bare `clm slides sync DECK` aliases `report` and is
   read-only — `diagnose` must be read-only by default too.
2. **The engine never calls a model** except in `autopilot`. So #447's policy on
   `apply`/`task` cannot translate inline — it can only *reframe* a conflict into
   an `assisted` DE→EN task that the agent fills via `task → model → accept`.
   Only `autopilot --conflict` resolves inline with the embedded model.
3. **The consistency ledger (#448) is live** (`--ledger`, `bless/seed/establish`,
   `<topic>/.clm/sync-ledger.json`). It stores `de_hash`/`en_hash` from
   `hash_cell` but has only `SCHEMA_VERSION=1` and **no per-entry hash_version**
   (unlike the watermark). This directly constrains #458 (below) and #447 (a
   de-wins overwrite invalidates a recorded EN-side trust entry).
4. **Info-topics maintenance rule** (CLAUDE.md): any CLI/behavior change updates
   `src/clm/cli/info_topics/{commands,sync-agents}.md`. None of these touch the
   course spec, so `spec-files.md` is untouched.
5. **Changelog** goes in a `changelog.d/<issue>-<slug>.<type>.md` fragment, never
   the `[Unreleased]` section.

---

## Thread A — #458: thread the comment token through markdown hashing

**Problem.** #429 (shipped PR #457) made markdown cell-content hashing
reflow-insensitive via `normalize_for_hash`, but it hard-codes `comment_token="#"`
(`sync_writeback.py:257`). `#`-decks (Python/Rust) get reflow-insensitivity;
`//`-decks (C++/C#/Java/TS) do not — the `// ` prefix stays on each prose line, so
a re-wrap moves the embedded `//` tokens to new word positions and the hash
changes. It is *safe* today (write and read both use `"#"`, so internally
consistent) but `//`-decks miss the benefit. The fix must thread the real token to
**every** markdown hash site **atomically** — a partial change makes write and
read disagree → false drift on every `//`-deck round-trip.

**Design (Option A — token rides on `CellMetadata`).** Rather than the issue's
literal Option B (add `RawCell.comment_token` + an explicit token arg on every
hash function), put the token on `CellMetadata`:

- `CellMetadata` (`slide_parser.py:22`) gains `comment_token: str = "#"`, stamped
  at the single `parse_cell_header` chokepoint (`slide_parser.py:136`, both the
  j2 early-return and the main return).
- `cell_content_hash` (`sync_writeback.py:240`) gains keyword `comment_token: str
  = "#"`, forwarded to `normalize_for_hash` **only** in the `markdown=True`
  branch (code cells / j2 headers stay byte-exact).
- `hash_cell` (`:261`) and `anchor_of` (`:291`) forward
  `metadata.comment_token`. **`row_anchor` (`:310`) is untouched** — it formats a
  *stored* hash and must never recompute; `anchor_of` and `row_anchor` stay in
  lockstep because `anchor_of` now hashes with the same token the stored hash was
  written with.

Because both `Cell` and `RawCell` carry `.metadata`, and **every** markdown hash
call site already passes `metadata`, the ~25 hash sites change **zero lines** —
which is exactly the atomicity #458 demands. `RawCell.comment_token` is therefore
*not* added (it would be redundant; `RawCell.metadata.comment_token` conveys it).

**Migration (the hash-version bump).** Bump `WATERMARK_HASH_VERSION` 2→3
(`cache.py:752`). The existing stale-version gate (`_version_current` →
`get_deck`/`has_pair` treat a stale pair as absent) then re-baselines every deck
once off git HEAD on the next sync. `#`-deck hashes are **byte-identical** after
the change (token `"#"` is the existing default), so they re-record with zero
spurious drift; `//`-decks discard the obsolete `#`-token baseline and re-record
with `//`-token hashes. **The bump must land in the same commit as the threading**
— shipping the token change alone would mismatch every `//`-deck's v2 baseline.

### Corrections from adversarial review (must be in the implementation)

1. **Studio is a *required* touch point, not a verification.**
   `web/studio/service.py:254` `_cell_views` parses with token-less
   `parse_cells(text)` (defaults `"#"`), while the write-guard
   (`service.py:484`, via `FileState.load` → `split_cells(text,
   comment_token_for_path(path))`) gets the real token. After the fix these
   diverge for `//`-decks, so the optimistic-concurrency check
   (`service.py:485`) **false-trips `StaleWriteError` on every `//`-deck markdown
   edit → those cells become uneditable in Studio.** Fix: thread `path` →
   `comment_token_for_path(path)` into `_cell_views` (and `open_deck`, which has
   `path` at `:310` but passes only `(text, lang)`). This also fixes a *latent*
   pre-existing bug at `service.py:281` (`token = cell.comment_token` is already
   wrong `"#"` for `//`-decks). The version bump does **not** protect this — the
   Studio hashes are computed live, not versioned — so it ships unguarded unless
   fixed, and the engine-only acceptance criteria would not catch it. Add a
   Studio `//`-deck round-trip test.
2. **The consistency ledger needs its own version guard.** `sync_ledger.py`
   stores `de_hash`/`en_hash` from `hash_cell` but has `SCHEMA_VERSION=1` and no
   per-entry hash_version. Changing the `//` canonical form silently invalidates
   every committed ledger entry for `//`-decks with no self-heal. **#458 must add
   a `hash_version` to the ledger entry schema (preferred — lets entries
   self-rebaseline like the watermark) or bump `SCHEMA_VERSION`.** This is a hard
   prerequisite, surfaced by the overlap scan, that the thread's own design
   missed.
3. **`Cell.comment_token` already exists** (`slide_parser.py:66`). Adding
   `CellMetadata.comment_token` creates two parallel sources on every `Cell`;
   they are set consistently in `parse_cells` but can drift in code that
   reassigns `cell.metadata`. Reconcile — make `Cell.comment_token` a property
   delegating to `metadata.comment_token`, or assert equality.
4. **Audit the ~16 token-less `parse_cell_header` calls** (grep-verified across
   `assign_ids.py:172`, `normalizer.py:250/266/300/335/405`,
   `reconcile_vo_ids.py:114/122`, `sync_apply.py:2803/2862/2881/3903`,
   `sync_writeback.py:424/497/533`, `voiceover_tools.py:556/841/1288/1391/1518`)
   so a `//`-cell rebuilt mid-flight doesn't silently fall back to `"#"`. Each has
   a `cell.metadata.comment_token` or `comment_token_for_path(path)` in scope.
   The authoritative compare always re-parses from disk via
   `comment_token_for_path` in `_record_watermark`, so these are belt-and-braces
   — but `build_twin_cell` (`:424`) must use `source_cell.metadata.comment_token`
   to be safe across the in-pass boundary.

**Tests:** mirror `test_reflow_hash.py` / `test_sync_reflow_no_edit.py` for `//`;
add a `#`-deck golden-hash regression (byte-identical pre/post); add the Studio
`//`-deck round-trip; add a ledger version-guard test.

**Verdict:** sound design, **conditional on the four corrections** (Studio +
ledger version are the load-bearing ones).

---

## Thread B — #446: `--since DATE|REF` baseline resolution

**Problem.** `--baseline REF` already reconciles a whole module against a ref over
a directory sweep. But users think in *timeframes* ("since ~2 days ago / since I
started week 10"), so the agent must hand-resolve the pre-edit commit with `git
rev-list -1 --before=<date> HEAD` (the `reconcile-deck-edits` skill step 1).

**Design (pure CLI sugar — zero engine change).** `--baseline` already threads a
ref through `build_sync_plan → _bundle_from_git_ref → _git_ref_text → _git_show`
and diffs the working tree against the deck's content at that ref. So #446 is a
front-end resolver:

- New `_resolve_since(value, cwd) -> SinceResolution` in `sync.py`, a sibling of
  `_parse_baseline_from`. **Try-ref-first disambiguation:** run `git rev-parse
  --verify --quiet <value>^{commit}`; if it resolves, the value *is* a ref (so
  `--since HEAD~1` == `--baseline HEAD~1`, and `HEAD` resolves as a ref). Else
  treat it as a git approxidate and run `git rev-list -1 --before=<value> HEAD`
  (newest-first + `-1` = the commit that was HEAD at that instant, capturing
  everything edited *since*). Empty output → `UsageError` ("no commit at/before
  '<value>'; shallow clone?").
- The resolved SHA is assigned to `baseline_ref` and flows through the **existing**
  path unchanged — inheriting the correct "no auto-heal when a baseline is pinned"
  behaviour automatically.
- Mutually exclusive with `--baseline` / `--baseline-from`; reuses the module's
  raw-subprocess git helpers (`_git_show`/`_git_repo_root` style), **not**
  `run_git` (which drags in a dry-run/auth shim) and **not** the `git log --since`
  filter (a multi-commit filter, wrong question).

### Corrections from adversarial review

1. **`autopilot` is the headline miss.** `autopilot` already has `--baseline`
   (`sync_autopilot.py:407`) and its help literally describes the #446 use case;
   "reconcile everything since Monday **in one shot**" *is* the autopilot
   workflow (`apply` only does the mechanical tier and leaves edit/realign/cold
   residue). Add `--since` to `autopilot` too, reusing `_resolve_since`, with its
   **extra** mutual-exclusion guards (`--rebaseline` / `--interactive` /
   `--no-cache`, `sync_autopilot.py:555–577`), not just the `--baseline` pair.
   Target set becomes **report / apply / task / autopilot**.
2. **Guard placement.** Put resolution + all mutual-exclusion guards at the **top
   of each command body, before the `if de_path.is_dir()` dispatch** — *not*
   "mirror lines 1806–1810" (that site is in the single-pair branch, after the
   directory early-return, so a `dir + --since + --baseline` combo would bypass
   the guard).
3. **Ref-path echo bug.** The design set `resolved_sha = value` for the ref path,
   so the human echo would print `HEAD~1` / `committed None`. Resolve the ref to
   a concrete SHA for display (the `rev-parse` you already ran returns it), or
   branch the echo by kind.
4. **JSON scope.** A `since` block touches **~6 emitters** (report single/dir,
   apply single/dir, task, the `TaskUnavailable` branch), not 3. Lower-blast-
   radius alternative: rely on the stderr echo + the already-present
   `git:<sha>` `baseline_source` label, and skip the JSON block (or scope it
   carefully).
5. **Committer-date caveat.** `git rev-list -1 --before` filters by *committer*
   date; on rebased/cherry-picked history this can be non-monotonic → a subtly
   wrong baseline. Fine for linear authoring history; document the caveat (silent
   failure otherwise).
6. **`accept` asymmetry (call it out).** An author who reconciled a window via
   `report`/`task` must then `accept --baseline <SHA>` against the same ref;
   excluding `--since` from `accept` reintroduces the hand-SHA friction #446
   removes. Defensible to exclude (accept records trust, not a reconcile window)
   but document it as a known asymmetry — or add it.

**Verdict:** **sound** (high confidence); ship with the autopilot addition and the
guard-placement fix.

---

## Thread C — #447: non-interactive `--conflict de-wins|en-wins`

**Problem.** A messy single-language session ("edited German all week; agents
partially synced English") produces `conflict` items — cells changed on both
halves. These are tier-3 ambiguity (not auto-resolved); each needs a manual edit
or `autopilot --interactive` (`[d]`/`[e]` per conflict). For the common "German
is the source of truth" reconcile, the user wants a non-interactive policy that
takes DE as authoritative, re-translates to EN, overwrites EN.

**Design.** The decision machinery already exists: `apply_plan(decisions=…)`
resolves keyed conflicts via `DECISION_DE_WINS`/`EN_WINS` (`sync_apply.py:135`),
recasting each via `_conflict_as_edit` and re-translating through
`_resolve_edit`/`_materialize_edits`/`_apply_edit`. The interactive walker is the
*only* thing that builds that decisions map today. So the core is a small policy
layer — `conflict_policy_decisions(plan, policy)` — that synthesizes the **same**
map non-interactively, wired into the writing paths, plus the model-free agent
path that *reframes* conflicts as DE→EN tasks. Default `leave` = today's
behaviour (strictly opt-in). Safety: a loud "N English cells WILL BE OVERWRITTEN
— review the git diff" banner, a `--yes` gate on batch writes, and a `--dry-run`
preview of exactly what de-wins would overwrite.

### Corrections from adversarial review (this design was *not* sound as written)

1. **The batch/directory path was entirely unwired — the primary surface.** A
   directory run does **not** go through the single-pair autopilot branch; it
   goes `_run_batch → _sync_one_pair → apply_plan` (`sync.py:487`), called
   **without** `decisions=`. So `--conflict de-wins <dir>` would silently resolve
   *nothing* across exactly the multi-deck "messy week" the issue targets. The
   policy + a per-pair `conflict_policy_decisions` must be threaded into
   `_sync_one_pair`, and the `--yes` gate wired there.
2. **Narrative-conflict excerpts are NOT free.** The design assumed #451 excerpts
   make conflict tasks framable. True for *keyed* and *id-less-localized*
   conflicts, **false for narrative** (voiceover/notes) conflicts: `slide_id` is
   None and the role isn't `localized`, so `_item_languages` returns
   `(None, None)` and a framed task would have empty bodies. **Either** add
   narrative excerpt plumbing (occurrence-anchor lookup in `sync_report`) **or**
   explicitly scope narrative conflicts *out* of the agent task/accept path in v1
   (keep them unframeable, document it).
3. **The accept-path recast is ordered backwards.** `_matching_proposal` matches
   the report item against live `plan.proposals` by `(kind, role, slide_id,
   direction, …)`; the plan still holds `kind='conflict', direction=None`.
   Recasting to an edit *before* matching matches zero proposals → `AcceptRejected`.
   The recast must happen *after* matching (transform the returned Proposal), and
   **`_accept_edit` itself must change** — it calls `_matching_proposal`
   internally and runs `apply_plan(scope_to_proposals=True)` *without*
   `decisions=`, so a pruned conflict proposal hits the conflict branch →
   `DECISION_SKIP` → deferred, nothing written.
4. **The "false-conflict downgrade still works under a decision" claim is false.**
   The equivalence probe (`sync_apply.py:1177`) runs only in the `elif`, i.e.
   when *no* decision is present. With a decision, a markdown conflict still issues
   a judge call (returns `in_sync` if equivalent — no write but a model call *is*
   consumed) and a **code** conflict is re-translated and overwritten
   *unconditionally*. State the real behaviour and the per-conflict model cost;
   don't promise "no overwrite, no winner consumed".
5. **remove-vs-edit detection is reason-string-coupled and brittle.** A
   removed-on-DE/edited-on-EN conflict carries the same structural fields as a
   both-edited one; only the `reason` string differs. A future re-word silently
   disables the guard → destructive mis-resolution (translating an empty body
   over EN's edit). Add a **structured `conflict_subtype` field** at the four emit
   sites (`sync_plan.py:2287/2310/2615/2629`), or build the policy over the
   *enriched* report (where the removed side resolves to a `None` excerpt).
   v1 recommendation: **omit remove-vs-edit from the decisions map and report it**
   ("N remove/edit conflicts need manual resolution") rather than guess.
6. **`id()`-identity invariant.** `conflict_policy_decisions` returns
   `dict[id(proposal), str]`, valid only against the **exact** plan passed to
   `apply_plan`. Autopilot rebuilds the plan in auto-heal and the batch rebuilds
   in `_sync_one_pair`; the decisions must be built from the **post-heal /
   re-planned** object or every key misses and nothing resolves.
7. **`--yes` already exists** (batch-only, `sync_autopilot.py:446`). *Extend* its
   semantics to gate single-pair `--conflict` writes and fix its now-false
   "Ignored for a single pair" help — don't "add" it.
8. **Counter + bugfix details.** Increment a dedicated `conflicts_resolved`
   counter inside `_apply_conflict` (not via the indistinguishable `applied_edit`
   bump). And fix `_conflict_as_edit` to copy *all* identity fields
   (`anchor`, `owning_slide_id`, `anchor_occ`, positions, `reason`) — today's
   interactive narrative de-wins crashes ("source narrative not found by anchor",
   `sync_apply.py:1272`) because it drops them. That fix is a real improvement to
   the existing interactive path too.

**id-less-localized conflicts (#365) stay deferred-and-reported** under any policy
(resolving "which side wins of a genuine both-sided id-less edit" needs a
winner-aware positional resolver — a separate, larger change). In the
German-all-week scenario many drifted cells *are* id-less, so de-wins resolves
fewer than expected; **reporting the residual count is mandatory** or the command
over-claims success.

**Scope boundary (must be documented):** the §7a same-cell language-neutral
divergence is governed by `CLM_SYNC__SHARED_DIVERGENCE`, **not** `--conflict`.
Users will expect `--conflict` to cover "everything that conflicts"; the help and
`clm info` must say it doesn't.

**Ledger coupling (#448):** a de-wins overwrite invalidates any recorded EN-side
trust for that `(slide_id, role)`; treat a policy resolution like a bless/accept
boundary (re-record or invalidate), or rely on the "review git diff" human gate
until that's wired.

**Verdict:** direction sound, but **conditional on corrections 1–8**; #1 (batch
path), #3 (accept ordering), and #5 (remove-vs-edit) are ship-blockers. This is
the highest-risk thread (irreversible data loss) — keep it opt-in, default
`leave`, forever.

### Revisit (2026-06-29): relax the model-free assumption → autopilot-only v1

`de-wins` is **inherently a model operation** (`_resolve_edit`/`_materialize_edits`
need a non-None `judge`/`translator`; the model-free `apply`/`task`/`accept` have
neither). The original design made it work model-free by *reframing* conflicts as
DE→EN tasks — and **that reframing is the sole source of ship-blockers #2
(narrative excerpts) and #3 (accept-recast).** Scoping v1 to **`autopilot` only**
(where the embedded model already lives, and which is already the documented
"one-shot that uses models; needs a key; never in CI") **eliminates #2 and #3
entirely**. The model-free agent loop is itself a model — it resolves conflicts
directly or invokes `autopilot --conflict de-wins` for the batch case; the
model-free `--conflict` reframing is deferred to v2.

Three model-in-the-loop refinements (now affordable since the model is present):

- **Equivalence-gate the decision** (fixes #4): run `_conflict_already_equivalent`
  (and a structural compare for code) *before* applying a de-wins decision — an
  already-in-sync conflict becomes a no-op, no needless overwrite/translation.
  Today the decided branch (`sync_apply.py:1166-1176`) skips the probe the
  undecided branch (`:1177`) runs.
- **`--dry-run` shows the proposed DE→EN translations** (not just "would
  overwrite") — review-before-destroy, the strongest mitigation of the data-loss
  risk.
- **`escalate` policy tier**: a cheap per-conflict judge call ("does EN carry
  content not derivable from DE?") **resolves the safe conflicts and defers the
  risky ones** as conflicts for human review — the skill's own "escalate when EN
  has an independent change" guardrail, automated.

**Bonus / deferred:** id-less *localized* (code/markdown) conflicts force-defer
today because "which side wins is out of scope" (`sync_apply.py:619-635`) — but a
policy *supplies* the winner, so that justification evaporates; reshaping a
both-sided id-less edit into the already-resolvable one-sided positional form is
**v2** (v1 reports the deferred count). Id-less *narration* conflicts already
resolve via the anchor path once `_conflict_as_edit` copies the identity fields
(correction #8).

---

## Thread D — `clm slides sync diagnose`: automate the diagnostic workflow

**Problem.** The analysis doc's thesis: a `verify` failure is a *symptom* with
several unrelated root causes, each needing a different fix; the anti-pattern is
"rename ids until verify passes" (which buries real gaps — the `array-limitations`
trap). Today an agent classifies each failure by hand: list both halves side by
side, compare each cell's *content language* against its `lang=` tag, then pick a
fix from a catalog.

**Design.** A new read-only `clm slides sync diagnose <file|dir>` backed by a
`clm.slides.sync_diagnose` classifier. It runs the existing symptom producer
(`structural_violations`) and, for each violation, disambiguates the root cause by
reasoning over **both halves' full cell index** (including id-less cells that
`_slide_id_role_list` drops), occurrence-under-slide pairing
(`reconcile_vo_ids._narrative_index`), `lang_coverage` tag-counts, and a **new
lightweight DE/EN content-language signal**. Each diagnosis emits `{root_cause,
evidence, prescribed_fix, fix_class: MECHANICAL|AUTHORING}`. Dry-run by default; a
guarded `--apply` performs **only** the identity-preserving mechanical fixes,
each re-gated through `structural_gate` so a write never records a still-broken
pair.

**Root-cause catalog → label:** `DUPLICATE-NARRATION-OVERSTAMP`, `MIS-TAG`,
`ID-LESS-TWIN`, `MIS-PAIRED`, `CONTENT-GAP`, `WHOLE-DECK-GAP`, `UNIFY-ALIGNMENT`,
`DROPPED-ID`. Auto-fixable (identity-preserving): narration over-stamp strip and
id-less-twin stamp of an **already-existing** twin id. Everything else (mis-paired
re-id, content gap, whole-deck gap) is **advisory** — a worklist entry, never an
auto-rename. The classifier carries a hard invariant: **never suggest or apply an
id rename to silence an asymmetry.**

**Content-language signal (the linchpin).** No detector exists (no
langdetect/lingua/fasttext; the `[ml]` extra is blocked per project memory). Add a
hand-rolled `clm.slides.content_lang.detect(text) -> LangGuess` using
umlaut/eszett ratio + an EN stopword set (`slug._STOP_WORDS`) vs a new German
frozenset. **Crucially it abstains (`unknown`, low confidence) on short/title-only
text** — exactly the duplicate-`title` case where any statistical detector is
weakest — and the classifier treats `unknown` as "cannot assert mis-tag" →
downgrades to advisory rather than guessing. A wrong MIS-TAG assertion is itself
an authoring-corrupting error, so MIS-TAG defaults to AUTHORING (advisory), never
an auto cell-move.

### Corrections from adversarial review

1. **The flagship case is invisible to `verify` (core gap).** The canonical
   reconcile case — a narrative cell id'd in one half, id-less occurrence-twin in
   the other — produces **no** `id-asymmetry` (the `slide_id` is the owning
   slide's id, carried by the slide cell in *both* halves) and **no**
   `duplicate-id` (slide-role ≠ voiceover-role). A purely violation-driven
   classifier never sees it, so the headline `ID-LESS-TWIN` auto-fix would
   **almost never fire**, and diagnose would report PASS on pairs `reconcile`
   still has work on. Fix: run `reconcile`'s own occurrence-pairing detection
   (`_narrative_index`) **independently** of any `VerifyViolation`. **`diagnose`
   must be a read-only superset of both `verify` *and* `reconcile`'s detection**,
   not just verify.
2. **Non-narrative `duplicate-id` has no row.** `duplicate-id` fires for any
   `(slide_id, role)` including `slide`/`subslide`/`code`/`markdown`. Add a
   catalog row (or an explicit "advisory, unclassified" fallback) so those don't
   fall through unlabelled.
3. **`lang_coverage` must combine across halves.** `count_languages` counts within
   a single text, so a split DE half always yields `(N, 0)`. `WHOLE-DECK-GAP` must
   replicate `scan_coverage`'s combination (DE count from the DE half, EN from the
   EN half) before `classify_counts`, or it labels everything `DE_ONLY`/`EN_ONLY`.
   And `classify_counts` returns `DE_ONLY`/`EN_ONLY` only at *exactly* `en==0` /
   `de==0` — a half with a few stub cells is `IMBALANCED`, so the "checked first
   so it doesn't masquerade as N content-gaps" guarantee holds **only** for the
   strictly-empty half (document the `IMBALANCED` middle ground).
4. **Auto-fix is partial/best-effort, not a clean guarantee.** When occurrence
   counts differ across halves, the `structural_gate` re-gate correctly refuses a
   partial write — so the result must surface "could not fully reconcile", not a
   silent no-op or an implied clean fix. Also: the asymmetric over-stamp (DE ×4
   id'd / EN ×4 id-less) is reconcile-visible (`TO_IDLESS`), but the **symmetric**
   over-stamp (both halves ×4 id'd) is `already_symmetric` and needs the new
   `collapse_intra_half_duplicates` path — acceptance criterion #2 must
   distinguish them.
5. **Lazy imports** (PR #312): import `content_lang`/`sync_diagnose` inside the
   verb body (mirror `_load_ledger_if`), not at module top, or the LazyGroup
   startup win is lost.
6. **Cell-model mismatch:** `language_tools.get_language_view` consumes
   `slide_parser.Cell`, while verify/reconcile use `raw_cells.RawCell` via
   `split_cells`. Reusing `get_language_view` for the human DE|EN table is a
   *third* parse; "parse once" can't share output across the two cell models —
   accept the cost (diagnose is interactive, not the hot build path) or build the
   table from the rich RawCell index.
7. **Verb placement:** the `diagnose` decorator belongs beside `sync_verify_cmd`
   (~`sync.py:1675`), not at the `_run_verify` *helper* (`:982`). Surface the
   `VerifyViolation.role` field that's intentionally dropped today (`sync.py:1039`)
   in the JSON envelope.

**Ship the catalog into `clm info`.** Add a "root-cause catalog + diagnose"
section to `sync-agents.md` and the verb to `commands.md`, so downstream
course-repo agents get the catalog **version-accurately** — which is what makes
the hand-maintained analysis doc redundant.

**Verdict:** sound and additive (medium confidence) **once the ID-LESS-TWIN
framing is reworked** to run independently of verify (correction #1). That rework
is the difference between a useful tool and one that reports PASS on real work.

---

## How this collapses the manual workflows

| Manual step today | After |
|---|---|
| `reconcile-deck-edits` step 1: hand-resolve baseline from `git rev-list -1 --before` | `clm slides sync report DIR --since "2 days ago"` (**#446**) |
| step 4: per-conflict DE-wins by hand / `autopilot --interactive` | `clm slides sync apply DIR --since … --conflict de-wins` (**#447**), or de-wins-framed `task`/`accept` for the agent path |
| analysis-doc diagnostic procedure (eyeball halves, compare content-lang vs tag, pick from catalog) | `clm slides sync diagnose DIR [--apply]` (**thread D**) |
| the analysis doc lives in each course repo's `.claude/docs/` | `clm info sync-agents` ships the catalog version-accurately |

Net effect: a whole-week reconcile becomes roughly
`sync diagnose DIR` → `sync apply DIR --since <when> --conflict de-wins` →
`sync verify DIR` → `git diff` review — agent-drivable from a timeframe hint, with
the diagnostic knowledge baked into the tool instead of a copied doc.

## Locked decisions (2026-06-29)

Settled with the maintainer before implementation:

- **#447 strategy:** autopilot-only in v1 (model-free agent reframing → v2).
- **#447 scope:** include the **`escalate`** policy tier; equivalence-gate decided
  conflicts; `--dry-run` shows proposed translations. id-less-localized
  resolution → v2 (v1 reports the deferred count). remove-vs-edit →
  omit-and-report via a structured `conflict_subtype` field (not a reason-string).
- **#458:** add a per-entry `hash_version` to the ledger (self-heal), don't bump
  `SCHEMA_VERSION`.
- **#446:** add `--since` to `report`/`apply`/`task`/`autopilot` **and `accept`**
  (full symmetry, removes the hand-SHA friction end-to-end).
- **Thread D:** id-less-twin auto-fix is narrative-only in v1 (slide-cell id-less
  twin stays advisory); content-language detector is hand-rolled with abstention.

## Recommended sequencing

1. **#446 first** — lowest risk, no engine change, unblocks the timeframe half of
   the reconcile workflow immediately. (Add the `autopilot` target.)
2. **Thread D (`diagnose`)** — additive, dry-run by default; high leverage
   (replaces a hand procedure) and surfaces the catalog. Land the `content_lang`
   detector + the verify∪reconcile detection rework.
3. **#458** — needs the hash-version bump **and the ledger version guard**
   landed atomically; coordinate with whoever owns the ledger schema. Independent
   of the others but touches shared hashing, so do it as its own clean PR.
4. **#447 last / most carefully** — highest risk (data loss), most corrections,
   depends on getting the batch path and accept ordering right. Ship `autopilot
   --conflict` + the dry-run preview first; the agent `task`/`accept` reframing
   and narrative-conflict support can be a fast-follow.

Each is an independent PR with its own `changelog.d/` fragment and `clm info`
update; none requires the others to land first (B and D are pure additions; A is
self-contained; C reuses existing machinery).

## Open questions for the maintainer

- **#458:** add a `hash_version` to the ledger entry (self-heal) **or** bump
  `SCHEMA_VERSION` (invalidate)? The former is friendlier; the latter is simpler.
- **#447:** for remove-vs-edit conflicts under de-wins — propagate the removal to
  EN, or omit-and-report (v1 safe default)? And should the agent `task`/`accept`
  reframing ship in v1 or as a fast-follow?
- **#447:** wire the ledger invalidation now, or rely on the git-diff human gate
  until the ledger-coupling is designed?
- **#446:** add `--since` to `accept` too (symmetry) or document the asymmetry?
- **Thread D:** restrict the `ID-LESS-TWIN` auto-fix to *narrative* roles in v1
  (slide-cell id-less-twin stays advisory, since stamping a slide id is
  higher-stakes)?
