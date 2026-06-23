# Sync: A Per-Slide Consistency Ledger — Design Note

**Status**: Design exploration. Direction **chosen** (§10): re-found the
watermark as a git-committed, **per-slide** sync-consistency ledger. No code yet.
**Revised 2026-06-23** with a code-grounded review pass — see **§11 Addendum**
(corrects the storage path and the resolver framing; resolves sync-without-commit;
adds the `bless`/`accept --record` confirm paths and the P0 prerequisites).
**Author**: Claude (Opus 4.8)
**Date**: 2026-06-23
**Scope**: `src/clm/infrastructure/llm/cache.py` (`SyncWatermarkCache` +
`sync_watermark_meta`), `src/clm/slides/sync_plan.py` (`BaselineBundle`, baseline
resolution in `build_sync_plan`), `sync_apply.py` (`_record_watermark`), the
`clm slides sync` verb group, and a new in-repo sidecar type (reusing the
`<sidecar-layout>` / `CLM_SIDECAR_LAYOUT` / `.clm-*` release-skip machinery).
**Issue**: [#448](https://github.com/hoelzl/clm/issues/448) (umbrella); threads
[#435](https://github.com/hoelzl/clm/issues/435) (worktree watermark key),
[#429](https://github.com/hoelzl/clm/issues/429) (reflow-insensitive hash),
[#366](https://github.com/hoelzl/clm/issues/366) (watermark/commit coupling),
[#446](https://github.com/hoelzl/clm/issues/446) (`--since` baseline),
[#447](https://github.com/hoelzl/clm/issues/447) (conflict policy) underneath it.
**Builds on**: `sync-git-as-baseline.md` (#419 — mechanism (c), the in-repo
state file) and `sync-baseline-storage-and-agent-direction.md` (the B/A/C storage
comparison + the agent-first pivot, both **settled**). This note does not re-open
those decisions; it **refines option A from per-pair to per-slide** and adds the
one thing neither note addressed: *what we are allowed to trust before the first
recorded sync*.

> Read the two prior notes first. They settled (a) the agent-first pivot — the
> deterministic engine is fast-path + verifier, the agent solves the scoped
> residue — shipped as epic #440; and (b) the storage leaning — an in-repo
> per-pair sidecar (option A / #419 mechanism (c)) demoting sqlite to a
> rebuildable cache, *designed but not built*. This note takes that sidecar to
> **per-slide granularity** and supplies its missing trust model.

---

## 1. The problem this note adds

The prior notes answered "where does the last-synced baseline live, and who
solves the ambiguous residue." They did **not** answer the problem the timeframe
reconcile surfaced when dogfooding AZAV ML week 10:

**Across *repeated* rounds of single-language edits and reconciliations, what is
the right baseline for *this* slide, *right now*?**

Concretely, the user's framing: *"point to a commit two weeks in the past, and
find out — slide X was determined in-sync at a commit three days ago, with these
changes since, so the agent takes that as its baseline; whereas slide Y was not
synced in that window, so we must actually check whether its two halves are in
sync."* Two demands fall out, and neither prior note meets them:

1. **Per-slide, not per-deck.** A deck is almost never uniformly drifted. After a
   few rounds, slide X was reconciled 3 days ago and slide Y two weeks ago and
   slide Z never. A single per-pair baseline ref (today's `--baseline REF`, or a
   per-pair sidecar) forces one answer for the whole deck — so you either
   re-litigate slide X's recent sync or miss slide Z's ancient drift. The
   baseline must be resolved **per slide identity**.

2. **There is no trustworthy point in history to anchor to.** This is the
   load-bearing observation (§3). Even the split commit — where a single-language
   deck became `.de.py` + `.en.py` — is *not* a known-good baseline, because the
   single-language era's two translations were maintained **by hand**. So the
   halves may already have been inconsistent *at the split*. There is no commit
   anywhere in history we can trust as "definitely in sync" without checking.

The corollary of #2 is the whole reason this is a *ledger* and not just "diff
against an older ref": the only reliable sync points are the ones **we record as
we make them**. Everything before a slide's first recorded sync is *unknown* and
must be *checked*, never assumed.

---

## 2. What already exists (build on, don't reinvent)

A lot of this is already built — the contribution is mostly *re-pointing* it.

- **The watermark is already per-cell and records the commit.**
  `SyncWatermarkCache` stores, per `(de_path, en_path, lang, position)`, the row
  `(slide_id, role, content_hash, construct, tags, anchor)`, and
  `sync_watermark_meta` stores the `synced_commit` (HEAD at sync time). It
  **auto-advances on every successful apply** — "immune to the author's
  git-commit cadence." So the per-slide data model this note wants is *already
  being written*; it is keyed and stored wrong for our purpose, not absent.

- **The baseline is already source-agnostic.** Since #289 P1 every consumer reads
  one `BaselineBundle`; `_bundle_from_watermark` and `_bundle_from_git_ref`
  produce identical shapes through the same `watermark_rows` / `watermark_tag_map`
  / `_header_hashes` / `watermark_anchor_map` chokepoints. A git-derived baseline
  at any ref is already a drop-in. (`sync-git-as-baseline.md` §2.)

- **Option A is already designed.** `sync-baseline-storage-and-agent-direction.md`
  §4 chose the in-repo **per-pair** sidecar (full serialized bundle, reviewable,
  pushed-by-default, self-pruning via `clm slides tidy`, merge-conflict =
  true-positive divergence) over leaving sqlite as-is (B) and over
  id-on-every-cell (C). It also issued the warning this note must obey: *persist
  the correspondence keyed the way the watermark keys it (slide_id / construct /
  anchor), **never by raw position** — positions re-introduce the #403 Phase B
  occurrence-anchor instability.*

- **The strictness knob's middle rung already exists.** `clm slides sync verify`
  is the structural oracle: byte-identical shared cells, `de_id == en_id`, no
  duplicate ids, no id dropped vs HEAD. That *is* "assume in sync if the structure
  matches."

So this note = option A, taken **per-slide**, plus a trust model, plus wiring the
existing `verify` and a new semantic oracle in as the cold-path fallbacks.

---

## 3. The load-bearing insight: append-only trust

Because no commit in history is trustworthy (§1.2), the ledger must be
**append-only trust**:

> A slide is trusted-in-sync **only from its first recorded sync forward.** Before
> that, its consistency is *unknown* and must be established by a check — never
> inherited from history.

Two consequences:

- **Inference is out.** "Both halves changed in this commit, so it was probably a
  sync" is unreliable — you cannot distinguish an agent sync from a coincidental
  double-edit. We do not recover sync points by *inferring* them from the diff;
  we recover them because we **wrote them down at the time**, as commits. "When
  was slide X last synced?" is then answerable exactly: the last commit that
  touched X's ledger entry (`git log -S<slide_id> -- '**/<ledger-file>'`). *That*
  is reliable sync-point recovery — and it is the direct answer to the question
  "can we recover these reliably?": yes, by recording, not by guessing.

- **The cold path is a *check*, with a strictness knob.** For a slide with **no**
  ledger entry (never synced, or synced before the ledger existed), the baseline
  cannot be trusted into existence. The user's three levels map exactly:

  | Level | Meaning | Mechanism | Tier |
  |---|---|---|---|
  | `assume` | trust without checking | none (record as synced) | engine |
  | `structural` | trust if structure matches | today's `verify` | engine |
  | `semantic` | trust if the LLM judges the translation correct | new `(de_cell, en_cell) → correct?` oracle | **agent / autopilot only** |

  `semantic` **must not** live in the deterministic engine (epic #440's
  model-free-engine line). It lives in `autopilot` / the agent loop. Its payoff:
  **its verdict is written back into the ledger**, so a slide you pay an LLM to
  judge once becomes a cheap ledger hit forever after. The first full reconcile of
  a legacy deck is therefore a one-time "establish the ledger" pass — `structural`
  for the cheap slides, `semantic` for the doubtful ones — and every reconcile
  after that is incremental.

This is the reframe that lets the demoted watermark be safely re-promoted: it is
no longer "the source of truth" (which is why #364/#366 demoted it for going
stale), it is **"a good place to start reconciliation"** — advisory, append-only,
and explicit that pre-record state is unverified.

---

## 4. The design

### 4.1 Data model — a per-slide ledger

> **⚠ Superseded in part by §11.1.** The path below is *not committable as
> written*: `.clm/` is gitignored wholesale (`.gitignore:267`) and walk-excluded
> (`SKIP_DIRS_FOR_COURSE`, `path_utils.py:41`). §11.1 resolves this with a
> topic-dir consolidation (cassettes fold into `.clm/cassettes/`; only scratch is
> gitignored), after which `<topic>/.clm/sync-ledger.json` *is* the committed
> home. Also **drop the `subdir`/`sibling` sidecar-layout reuse** — the ledger is
> per-topic, not per-deck-pair, so there is no layout choice to make.

A committed, per-topic sidecar (one new sidecar *type*, reusing the existing
sidecar-layout machinery — `subdir`/`sibling`, `<sidecar-layout>`,
`CLM_SIDECAR_LAYOUT`, and the `.clm-*` release-skip convention so students never
receive it). Keyed by **slide identity**, not position:

```jsonc
// <topic>/.clm/sync-ledger.json  (canonical sorted keys; per-topic to keep merges local)
{
  "schema": 1,
  "slides": {
    "<slide_id>": {
      "de_hash": "<reflow-insensitive content hash>",   // #429
      "en_hash": "<reflow-insensitive content hash>",
      "construct": "<content-anchor slug | null>",
      "confirmed_commit": "<sha at record time>",        // from sync_watermark_meta
      "confirmed_by": "apply | accept | autopilot | bless",
      "confirmed_oracle": "structural | semantic:<model> | assume"  // provenance of the trust
    }
  },
  "idless": [ /* anchor/position-keyed entries for id-less localized cells */ ]
}
```

- **Keyed by `slide_id` (+ `construct` for neutral cells, `anchor` for narrative
  rows).** Exactly the watermark's keys — heeds the prior note's "never by raw
  position" warning. Id-less localized cells (which have no `slide_id`) fall back
  to the anchor/position pair the watermark already records, in a separate
  `idless` list, and are inherently lower-trust (the #364/#365 residue class).
- **Stores hashes, not bodies.** The ledger is *metadata* ("these two halves were
  confirmed in sync, here is the fingerprint of each at that moment"), not a copy
  of the content. To reconstruct the actual baseline text for a diff, address the
  blob at `confirmed_commit` (`git cat-file`) — the hash proves it is the right
  one. (A full serialized bundle, as `sync-git-as-baseline.md` §6-Q1 leaned, is
  the alternative if we want sync-without-commit to survive; see §9.)
- **Records the oracle that confirmed it.** `confirmed_oracle` is the trust
  provenance — so a later run can *distrust a specific source* (e.g. re-check
  everything a since-deprecated model blessed) without nuking the whole ledger.

### 4.2 Per-slide baseline resolution

> **⚠ Reframed by §11.2.** A literal "per-slide resolver replacing
> `baseline_ref`" would have to splice baselines from different refs into the
> *position-based* classifier (`classify_changes` / `_baseline_index`), and a
> hash-only `(slide_id, role)` ledger cannot feed `_bundle_from_watermark`
> (which needs full per-partition rows with position/tags/anchors/header_hashes).
> The corrected model in §11.2: the ledger is a **trust overlay** — per slide,
> *is it byte-stable since its last confirmed sync?* If yes, **skip**; if no,
> **fall through to the existing single-ref bundle**, unchanged.

Replace the single `baseline_ref` in `build_sync_plan` with a **resolver** that
yields, per slide identity, one of:

- **ledger hit** → baseline = the recorded fingerprint. Diff the current half
  against it: unchanged ⇒ the slide is still in sync, **skip it** (do not
  re-litigate the prior sync — the user's "slide X synced 3 days ago" case);
  changed ⇒ that delta is the edit to propagate.
- **ledger miss** → **cold path**: establish trust at the chosen fallback ref with
  the strictness knob (§3) — the user's "slide Y never synced, must check" case.

The sqlite watermark becomes a **rebuildable cache** of this (fast local mirror);
the committed ledger is authoritative. `clm slides sync baseline rebuild`
regenerates sqlite from the ledger (kills the orphan-row class and #435 in one
move — the git tree path is stable, no absolute-path cache key to miss from a
worktree).

### 4.3 The write gate

Every ledger write is **gated on structural `verify`** — you cannot record a
slide as in-sync if it fails the structural invariants. `semantic` writes
additionally stamp `confirmed_oracle = "semantic:<model>"`. This keeps a bad agent
sync from quietly becoming a *trusted* baseline: the worst a bad write can do is
record a structurally-sound-but-semantically-wrong pairing, which the provenance
field lets you find and re-check later.

---

## 5. How it serves the workflows

- **Timeframe reconcile over repeated rounds (the motivating case).** Point at any
  past ref. Each slide resolves off **its own** last recorded sync, so a slide
  reconciled last round is skipped (its current half matches its ledger hash) and
  only genuinely-drifted slides surface. No re-litigating settled syncs. The
  global window (`--since DATE`, #446) stops being the primary baseline and becomes
  just the **cold-path scope bound** — which *un-recorded* slides we bother to
  deep-check, and how hard (the strictness knob). #446 is therefore a *sub-part* of
  this design, not a competitor.

- **Both-sided conflicts (#447).** A slide whose ledger hash matches *neither*
  current half = edited on both sides since its last sync. That is the `conflict`
  class, and `--conflict de-wins|en-wins` (#447) is the non-interactive policy for
  it. The ledger makes the conflict *precise* (we know the exact last-agreed
  state of both halves), which is what a clean de-wins re-translation needs.

- **Trust ("nothing got messed up").** `verify DIR` (structural, already batched)
  + a clean re-`report` against the ledger + the `git diff` of both the source and
  the ledger sidecar = the high-trust end state. The ledger diff is itself a
  reviewable record of *which slides were declared synced, by what oracle*.

---

## 6. Hard parts (honest)

- **slide_id churn (#366 realign).** The ledger keys on `slide_id`; an id
  migration/realign orphans entries. The id-migration path **must carry ledger
  entries across the rename** (rewrite the key, preserving `confirmed_*`). This
  couples to the existing realign/residue machinery — real work, and the sharpest
  risk. A dropped carry silently demotes a slide to the cold path (fail-safe
  direction — extra checking, never silent mis-sync — but it erodes the
  incremental win).
- **Reflow noise (#429).** The ledger hashes must be reflow-insensitive or trivial
  reformatting drops every slide to the cold path. **#429 is effectively a
  prerequisite** for a low-noise ledger.
- **Id-less localized cells.** Cannot key on `slide_id`; the `idless`
  anchor/position fallback is inherently lower-trust and is exactly the #364/#365
  residue. The ledger does not solve id-less identity; it inherits its fragility.
- **Merge semantics.** A committed ledger conflicts when two branches sync the same
  topic. Per-topic sharding + canonical sorted JSON (the `changelog.d/` and
  `.clm-released.<stream>.json` lesson) keeps it local; the union rule is
  **newest `confirmed_commit` wins per slide_id**, and a genuine conflict is a
  *true-positive* "both branches re-synced this deck" signal, surfaced not
  silenced (as `sync-baseline-storage-and-agent-direction.md` §4 argued).
- **Opaque churn.** Every sync rewrites hash lines a human cannot eyeball for
  correctness. Per-slide keeps each diff minimal and the `confirmed_oracle` field
  makes *what kind of* trust changed legible, but "reviewable in PRs" remains
  partly notional — you see *that* slide X was declared synced and *by what*, not
  *whether the translation is good*. That last judgment stays human/`semantic`.
- **Trust-of-writer.** A committed "synced" assertion is only as good as who wrote
  it; the write gate (§4.3) + provenance (`confirmed_oracle`) are the mitigations,
  not a guarantee.

---

## 7. How this resolves the prior notes' open questions

- `sync-git-as-baseline.md` §6-Q1 (marker payload: commit-ref vs content-hash vs
  full bundle) → **content-hash + `confirmed_commit`** by default (re-derive the
  baseline blob via `git cat-file`); full-bundle remains the opt-in for
  sync-without-commit (§9). Q3 (multi-deck batch) → per-topic file, one entry per
  slide, scales fine. Q5 (merge semantics) → surface, newest-commit-wins union.
- `sync-baseline-storage-and-agent-direction.md` option A's pain point "store the
  cell matches converges back onto store the watermark rows in a file" → **yes,
  and that is the point** — we *are* storing the watermark rows in a file, keyed
  per-slide, which is the faithful realization, not an over-sell. Its "relocates
  state, does not eliminate it" caution stands and is accepted: sqlite demotes to
  a rebuildable cache; the ledger is the one authoritative store.

---

## 8. Staged rollout (non-breaking first)

- **P1 — MVP, no LLM.** Emit the existing watermark as the committed per-topic
  ledger (content-hash + `confirmed_commit`, keyed per-slide), gate writes on
  `verify`, resolve baseline per-slide, cold path = `structural` at `--baseline
  REF`. Sqlite becomes a cache; add `baseline rebuild`. Reuses everything; kills
  #435 and the orphan-row class; gives reliable `git log` sync-point recovery.
- **P2 — the strictness knob.** `--fallback assume|structural|semantic`; the
  `semantic` oracle lives in `autopilot` / the agent loop and **writes its verdict
  back** into the ledger with `confirmed_oracle`.
- **P3 — ergonomics + identity.** `--since DATE` (#446) resolves the cold-path
  window; `--conflict de-wins|en-wins` (#447) for both-sided slides; id-migration
  carries ledger entries (couple to #366 realign).

Each stage is independently shippable and none flips a breaking default until the
git-derived per-slide baseline has proven out on real decks (the
`sync-git-as-baseline.md` §5 staging discipline).

---

## 9. Open questions

1. **Sync-without-commit.** ~~Content-hash + `confirmed_commit` cannot address a
   synced-but-uncommitted state (the blob does not exist). Do we keep supporting
   "sync the working tree, don't commit, sync again"?~~ **RESOLVED — dropped; see
   §11.3.** Store `hash + confirmed_commit` (no full-row). Uncommitted sync stays
   possible via the `baseline=HEAD` fallback; only *incrementality across an
   uncommitted sync* is lost (a cheap consistency judgement, not a translation).
2. **Ledger granularity of the file.** Per-topic (proposed) vs per-pair sidecar
   (note A) vs one per-course file (#419's first sketch, rejected for merge
   churn). Per-topic is the merge-locality sweet spot, but a topic with many decks
   still co-locates their entries; per-pair is maximally merge-local at the cost of
   more files. Lean per-topic; revisit if merges bite.
3. **Re-found vs parallel.** Re-found the watermark *as* this ledger (chosen —
   one store, kills #435) vs keep sqlite and add a parallel ledger (less
   disruptive to #440's demotion, but two stores to keep coherent). **Chosen:
   re-found.**
4. **Semantic oracle cost discipline.** `semantic` over a legacy deck's whole
   cold set could be many LLM calls. Bound it (the agent-first "scoped residue,
   not the whole deck" rule): `semantic` only for slides the cheaper rungs cannot
   clear, and always written back so it is paid once.

---

## 10. Recommendation (chosen)

**Re-found the watermark as a git-committed, per-slide sync-consistency ledger**
— option A from `sync-baseline-storage-and-agent-direction.md` taken to per-slide
granularity, governed by the **append-only-trust** model (§3): a slide is trusted
in-sync only from its first recorded sync forward, and the cold path is a *check*
with an `assume | structural | semantic` strictness knob, the `semantic` rung
living in the agent tier and writing its verdict back. Stage it P1→P3 (§8),
non-breaking first. This kills #435 and the orphan-row rot, makes sync points
reliably recoverable from `git log` (not inferred), makes the baseline a
reviewable repo fact, and turns the timeframe reconcile into an incremental,
per-slide operation that never re-litigates a sync it already paid for.

---

## 11. Addendum — review corrections & resolved decisions (2026-06-23)

A research + adversarial-review pass over the **actual code** (`sync_plan.py`,
`sync_apply.py`, `sync_verify.py`, `sync_writeback.py`,
`infrastructure/llm/cache.py`, the `clm slides sync` CLI, `core/include_ledger.py`,
`infrastructure/utils/path_utils.py`, `core/course_files/notebook_file.py`)
refined this note. **The direction holds.** The items below correct two concrete
errors, resolve three open decisions, and add findings the original missed.
Inline `⚠` callouts mark where earlier text is superseded.

### 11.1 Storage location — the proposed path can't be committed; do a topic-dir consolidation

- `<topic>/.clm/sync-ledger.json` (§4.1) is **uncommittable as written**: `.clm/`
  is gitignored wholesale (`.gitignore:267`), and it is *fully walk-excluded* from
  the course file map (`SKIP_DIRS_FOR_COURSE`, `path_utils.py:41`). The comment
  there states the exclusion is safe **only because** no build input lives under
  `.clm/` ("HTTP-replay cassettes live in `cassettes/`, not here").
- The committed-state precedents are `.clm-*` **files at topic root**
  (`.clm-include`, `.clm-released.<stream>.json`), kept out of student output via
  `SKIP_FILE_NAMES` (`path_utils.py:92`) — *not* inside the `.clm/` directory.
- **Decision (with the user):** rather than add another top-level entry, do a
  **topic-directory consolidation** so the ledger gets a clean committed home *and*
  topic dirs get tidier. Target layout — topic root `{voiceover/, .clm/}` (down
  from `{voiceover/, cassettes/, .clm/}`), with under `.clm/`:

  | Path under `.clm/` | git | build role |
  |---|---|---|
  | `.clm/cassettes/` | **committed** | replay input + output-suppressed |
  | `.clm/sync-ledger.json` | **committed** | the ledger; course-map- & output-excluded |
  | `.clm/voiceover-cache\|backfill\|traces/` | gitignored | scratch, fully excluded |

  `voiceover/` **stays** a top-level folder (user-edited narration).

- This is its **own P0 sub-track**, because:
  1. The walk exclusion is **name-based** (`part in SKIP_DIRS_FOR_COURSE`), so it
     cannot split `.clm/`. Keeping `.clm/cassettes/` discoverable as a build input
     while excluding scratch + ledger from the course map needs **subpath-aware**
     exclusion under `.clm/`.
  2. Cassette discovery must learn `.clm/cassettes/`: `notebook_file.py`
     (`_CASSETTE_SUBDIRS`, `_resolve_cassette`, `expected_cassette_path`), the
     `<sidecar-layout>` "subdir" target, and `clm slides tidy`.
  3. `.gitignore`: `.clm/` → the three scratch subdirs only. **Landmine: never
     gitignore `.clm/cassettes/`** — cassettes are committed replay fixtures;
     ignoring them breaks CI replay.
  4. Downstream migration: existing course repos `git mv cassettes/ .clm/cassettes/`
     + `.gitignore` updates (a `tidy`-style pass).
- **Fallback** if the consolidation is deferred: ledger at top-level
  `<topic>/.clm-sync-ledger.json`, added to `SKIP_FILE_NAMES` (plus a leak test).
  Either way, **drop** the `subdir`/`sibling` sidecar-layout reuse from §4.1 — the
  ledger is per-topic. (Leave `.clm-include` / `.clm-released.<stream>.json` at
  topic root for now; moving them touches the release/freeze + shared-release-repo
  (#325) machinery for little extra tidiness.)

### 11.2 The ledger is a trust *overlay* + git index, not a self-contained baseline

- §4.2 as written would splice per-slide baselines from different refs into the
  classifier, but `classify_changes` / `_baseline_index` are **position-based** and
  membership-widened (`sync_plan.py`), so ref-splicing hits the coverage-divergence
  / position-reindex landmine; and a hash-only `(slide_id, role)` ledger cannot
  feed `_bundle_from_watermark`, which needs full per-partition rows with
  **position, tags, anchors, header_hashes** (`sync_plan.py:294-300`).
- **Corrected model.** Per slide the ledger answers one question — *is this slide
  byte-stable since its last confirmed sync?* If **yes** (both half-hashes match)
  → **skip** it (no bundle derivation). If **no/absent** → **fall through to the
  existing single-ref bundle** machinery, unchanged. The ledger *suppresses
  re-litigation*; it does not replace the bundle.
- This preserves the watermark's reason-to-exist: a both-sided
  commit-without-sync changes the current hash, so the slide won't skip and the
  edit is still seen (the lag-behind-HEAD property, `sync-git-as-baseline.md` §3).
- "Demote sqlite to a rebuildable cache" then means: sqlite stays the fast mirror
  of the last-synced bundle *content* (already re-derivable via
  `_bundle_from_git_ref`); the ledger is the authoritative per-slide *trust*
  record on top. `baseline rebuild` **warms the cache from git** — it does not
  reconstruct the bundle from ledger hashes.
- **id-less narratives:** the `idless` list must key by
  `(owning_slide_id, role, anchor)` — the classifier's actual narrative key
  (`sync_plan.py` `_index_narratives_by_anchor`), *not* `(slide_id, role)`. A flat
  key silently misses ~15–20 % of a typical deck (the #364/#365 residue). Include
  `construct` in the code-cell key so two byte-identical code cells with different
  constructs don't collide.

### 11.3 Sync-without-commit — DROPPED (resolves §9.1)

- Dropping ledger support does **not** make uncommitted sync impossible. The
  ledger's de/en hashes give the **skip fast-path on a dirty tree regardless of
  commit**. Only the *diff-reconstruction* path needs a blob; when a slide changed
  after an uncommitted sync, it falls back to `baseline=HEAD`
  (`_bundle_from_git_head`) and diffs the whole working tree — correct, because
  with nothing committed HEAD is the true last-committed state ("at most one diff
  on top of HEAD").
- What's lost is **only incrementality across an uncommitted sync**:
  sync → don't commit → edit more → sync again re-surfaces the first sync's
  both-sided reconciliations as a `(de, en) → consistent?` **judgement** (the cheap
  verify oracle), not a translation. The normal sync → commit → continue flow is
  fully incremental.
- **Decision:** store `hash + confirmed_commit` (no full-row). UX nudge: *commit a
  reconciliation to lock in its baseline.* Ledger entries are therefore most
  meaningful at/after commit (ties to §11.4).

### 11.4 Confirm paths — `bless` (batch) + `accept --record` (per-item)

`accept` today runs `apply_plan(..., watermark_cache=None)` — no advance, no
ledger write (`sync_apply.py`). Two complementary confirm paths:

- **`bless` (batch):** record the whole now-coherent deck at the current commit,
  gated on a full `verify`. Safe default; ideal right after committing. (This is
  where the agent loop records trust after `accept`-ing the residue + a clean
  `verify`; it is also the home of `semantic` write-backs and of optional
  seed-from-watermark.) Subsumes the role of `--rebaseline`.
- **`accept --record` (per-item, ergonomic for the agent loop)** — feasible, with
  three guards:
  1. record **only the accepted slide** via the partial-advance machinery
     (`_record_watermark_partial`, `sync_apply.py:4044`), *not* the whole pair
     (else a slide is stamped "synced" while its sibling residue is unresolved);
  2. gate on a **per-slide structural verify** (`structural_violations` is
     role-aware per `(slide_id, role)`);
  3. provenance `confirmed_by=accept, confirmed_oracle=agent` — an agent asserted
     it and it passed structural validation, distinct from `semantic:<model>`; a
     later run can selectively distrust agent-confirmed entries.

Recording against a dirty tree (either path) gives skip-trust but pins
`confirmed_commit=HEAD`; per §11.3, "commit to lock it in" applies to both.
**Recommendation:** ship `bless` first; add `accept --record` once the
partial-advance + per-slide-verify path is solid.

### 11.5 Prerequisites & extra review findings (fold into P0)

- **#429 reflow hash, with hash-versioning** (declared prerequisite).
  `normalize_for_hash()` at the single chokepoint `cell_content_hash`
  (`sync_writeback.py:115`); markdown-aware (preserve fenced/indented/HTML code +
  list-indent depth; collapse soft-wrapped prose). Store a `hash_version` and
  re-normalize-on-read on mismatch — **not** a hard `watermark clear` (which
  cold-starts the whole repo at once). Note it also drives the Studio editor
  stale-check (`web/studio/service.py:484,498`).
- **#435 is solved-by-design**, not a separate prereq — a stable in-repo tree path
  has no absolute-path key to miss from a worktree.
- **Capture the id-migration map:** refactor `_migrate_one_deck` / `_apply_alignment`
  (`sync_apply.py`) to *return* `{old_id → new_id}` (today they mutate ids in place
  and return nothing). P3's ledger-carry depends on it; pulling it into P0 de-risks
  the sharpest part (a dropped carry silently demotes a slide to the cold path).
- **Lift `verify` into a reusable write-gate** function out of `structural_violations`
  (`sync_verify.py:103`), callable from the apply path.
- **Merge strategy (was unaddressed):** `include_ledger.py` has **no** union/merge
  logic, so two branches syncing one topic produce raw conflict markers. Need a
  `.gitattributes` merge driver ("union; newest `confirmed_commit` per
  `(slide_id, role)` wins") *or* documented manual resolution. Per-topic +
  per-slide-line + canonical sorted JSON makes most merges auto-resolve.
- **Migration/seeding:** existing decks have a sqlite watermark but no ledger.
  Optionally seed the ledger from the current watermark stamped
  `confirmed_oracle=assume` (cheap, but asserts unverified trust — honest under the
  strictness knob) or honest cold-start.
- **Per-phase hygiene:** each phase updates the matching `clm info` topic
  (`sync-agents.md` / `commands.md`) and adds a `changelog.d/` fragment.

### 11.6 The cheaper alternative, for the record

`#446` (`--since`) + a transient per-run git-ref baseline (no committed file, no
merge logic, no migration) buys per-deck timeframe scoping at `O(deck × runs)`
cost. What it **cannot** do, and the ledger can: **trust memoization** (it
re-judges every run) and the §1.2 "no historical commit is known-good" handling (a
bare `--since REF` trusts that ref blindly). A legitimate week-one increment if the
immediate pain is only scoping the reconcile window — but not a substitute for the
ledger's incremental-reconcile payoff.
