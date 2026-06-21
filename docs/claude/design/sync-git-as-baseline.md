# Sync: Git as the Baseline — Design Note

**Status**: Design exploration (pre-implementation prototype)
**Author**: Claude (Opus 4.8)
**Date**: 2026-06-21
**Scope**: `src/clm/slides/sync_plan.py` (baseline resolution), `sync_apply.py`
(`_record_watermark`), `src/clm/infrastructure/llm/cache.py`
(`SyncWatermarkCache` + `sync_watermark_meta`), the `clm slides sync` /
`clm slides watermark` CLI.
**Issue**: [#366](https://github.com/hoelzl/clm/issues/366) (reduce
watermark/commit coupling). Companions: #363 (watermark CLI — shipped), #364
(stale-watermark auto-heal — shipped), #365 (id-less-localized positional
conflicts — increment 1 shipped).

> This is a **discussion/prototype** note, not an agreed design. Its job is to
> make the architectural call cheaply: lay out what the watermark actually buys
> us, what a git-derived baseline can and cannot replace, the three candidate
> mechanisms for a *travelling* last-synced marker, and a staged rollout that
> never flips a breaking default blind.

---

## 1. The problem (#366)

`clm slides sync` keeps a **structural watermark** — the last-synced state of a
split pair — in the shared `clm-llm.sqlite`. It is *out-of-band stateful
coupling*: it must be kept in lockstep with commits, but nothing enforces that.
Editing both `.de.py` / `.en.py` halves and committing **without** running
`clm slides sync` silently desyncs it — the watermark falls behind, and a later
run errors/conflicts against a stale baseline even though the halves are fine.
This was the root trigger behind the whole #363–#366 batch.

The watermark is also a *separate store that can rot*: orphan rows survive a
topic renumber (a `de_path` that no longer exists), and there is no in-repo,
reviewable record of "what state did we last reconcile against?".

Issue #366 asks: can we **reduce or remove** that coupling — ideally make the
baseline something that travels *with* the repo, so there is no separate store
to go stale?

---

## 2. Current state (what already exists)

A surprising amount of the machinery a git-baseline needs is already built.

### 2.1 The baseline is already a single, source-agnostic representation

Since #289 P1, every baseline consumer reads one shape — `BaselineBundle`
(`sync_plan.py`): the membership-widened per-partition rows (`de` / `en` /
`shared`), tag maps, header hashes, and narrative anchors. Two producers fill it:

- **`_bundle_from_watermark`** — a straight read of the sqlite partitions
  `_record_watermark` stored.
- **`_bundle_from_git_ref`** (and its `_bundle_from_git_head` wrapper) —
  **re-derives the exact same rows** from the *committed* text, through the same
  `watermark_rows` / `watermark_tag_map` / `_header_hashes` /
  `watermark_anchor_map` chokepoints `_record_watermark` uses. By construction
  the two sources cannot diverge in coverage.

**Consequence:** a git-derived baseline at any ref is already a fully-functional,
drop-in baseline. `--no-cache` and the watermark-miss fallback already run on it
in production. So "git as the baseline" is **not** a from-scratch build — it is a
*priority flip* plus solving the one thing the watermark gives that a HEAD diff
does not (see §3).

### 2.2 Baseline priority today (`build_sync_plan`)

```
baseline_ref (--baseline <ref>)   # explicit git ref; no HEAD fallback if missing
  → watermark (if a pair row exists)
  → git HEAD (_bundle_from_git_head)   # the "cold start" / first-sync path
  → none (every cell reads as new)
```

### 2.3 The commit is already recorded

`sync_watermark_meta` (#364) already stores `synced_commit` per pair —
`set_synced_commit` / `get_synced_commit` — i.e. *the repo HEAD at the moment the
watermark was recorded*. The stale-watermark hint already names it as a
`--baseline <sha>` target. This is the seed of a git-travelling marker, but today
it lives in sqlite, not in the repo.

### 2.4 Recovery surface already shipped

`--baseline <ref>`, `--rebaseline` (clear + re-record off HEAD when the halves
are consistent, refuses on real divergence), and the `clm slides watermark`
CLI (list / clear / prune) are all live. Option **(D)** of #366 (auto-heal) is
effectively done via `--rebaseline`.

---

## 3. What the watermark buys that a HEAD diff does not

This is the crux. A git baseline pinned to **HEAD** is *not* equivalent to the
watermark, in two directions:

1. **The watermark can lag HEAD (the bug).** Both halves edited + committed
   without a sync → HEAD == working tree → a HEAD-baseline diff reports
   "consistent" and the edit is **invisible** (this is exactly the existing
   *cold-baseline hint* case). The watermark remembers the *last reconciled*
   state, which is older than HEAD, so it still sees the edit. **This is the
   property we must preserve** — a baseline of "HEAD" loses it.

2. **The watermark can lead HEAD (legitimately).** You can `sync` against the
   working tree and *not commit*, then keep editing and `sync` again. The
   last-synced state was never a commit, so it cannot be addressed by a commit
   ref. The watermark stores last-synced **content**, which is strictly more
   general than any commit ref.

So the real question is not "HEAD vs sqlite" but: **where do we record the
*last-synced commit/content*, and can it travel with the repo?** Property (1)
says we must record *a specific past point*, not just diff against HEAD.
Property (2) says a pure commit-ref marker only works if sync happens at commit
boundaries — which couples (A) to (B) sync-on-commit.

---

## 4. Candidate mechanisms for a travelling last-synced marker

All three replace (or shadow) `sync_watermark_meta.synced_commit` with something
in the git object graph. The baseline then becomes
`_bundle_from_git_ref(<marker commit>)` — reusing §2.1 wholesale.

### (a) Commit **trailer** on the sync commit
`clm slides sync` (or a commit hook) writes a trailer into the commit message:

```
Clm-Synced: <deck-rel-path>@<content-hash>
```

- **Pros:** travels with history, reviewable in the PR diff, no extra ref to
  push, trivially greppable (`git log --grep`).
- **Cons:** only meaningful if a sync corresponds to a commit (couples to B);
  multi-deck syncs bloat the message; rewriting history (rebase/squash) can drop
  or duplicate trailers; a content-hash trailer is not a *commit* ref, so
  re-deriving the baseline bundle needs the *content*, not the trailer alone —
  i.e. the trailer points at "the state as of *this* commit", which only works
  if that commit *is* the synced state.

### (b) **git notes** (`refs/notes/clm-sync`)
A note attached to the last-synced commit records the per-deck synced
content-hashes (or just "this commit is a sync point").

- **Pros:** does not touch the commit message; survives rebase better than
  trailers (notes can be copied with `notes.rewriteRef`); structured payload.
- **Cons:** notes are **not pushed/pulled by default** (`refs/notes/*` needs
  explicit refspec config) — a sharp edge for a multi-clone course repo; weak
  tooling/visibility; still couples the "sync point" to a commit.

### (c) A tracked **sync-state file** (content trailer that is itself a file)
A small committed file per course/stream, e.g.
`.clm-sync-state.<lang-pair>.json`, mapping `deck → last-synced content-hash`
(mirrors the existing `.clm-released.<stream>.json` precedent from #325).

- **Pros:** travels with the repo, reviewable, **pushed by default**, no commit
  message coupling, multi-deck friendly, decouples the marker from "is this
  commit a sync point". The synced *content* can be addressed by hash and
  re-derived from `git cat-file` of the blob at that hash, OR the file can store
  the full membership-widened rows (a serialized `BaselineBundle`) so no commit
  lookup is needed at all (closest to today's watermark, but in-repo).
- **Cons:** a new tracked file (churn in PRs); merge conflicts on the state file
  when two branches sync the same deck (though these are *informative* — they
  mark a real divergence); essentially relocates the sqlite store into the repo
  rather than eliminating state.

**Early lean:** **(c)** is the most faithful to #366's actual ask ("a content-hash
trailer that travels *with* the commit, reviewable in PRs … so there is no
separate store to go stale") while preserving properties (1) and (2) from §3
without forcing sync-on-commit. **(a)** is attractive for its zero-new-files
footprint but is the most coupled to commit boundaries and the most fragile under
history rewrites. **(b)** is the least visible and has the push/pull footgun.

---

## 5. Proposed staged rollout (non-breaking first)

Flipping the *default* baseline source is breaking; do it last, behind evidence.

- **Stage 0 — instrument (done / cheap).** `synced_commit` already recorded;
  stale-watermark hint already surfaces drift. Add nothing breaking.
- **Stage 1 — opt-in git baseline.** `clm slides sync --git-baseline`
  (env `CLM_SYNC_BASELINE=git`) resolves the baseline from the recorded
  last-synced marker (initially still `synced_commit`, via
  `_bundle_from_git_ref`) instead of the sqlite *content*. The sqlite watermark
  keeps working as today; this just proves the git-derived path end to end on
  real decks. Zero default change.
- **Stage 2 — travelling marker.** Implement mechanism (c) (or the chosen one):
  `sync` writes/reads the in-repo sync-state file; `--git-baseline` prefers it
  over `synced_commit`. The sqlite watermark becomes a *rebuildable cache* — if
  absent, reconstruct from the in-repo marker. Add `clm slides watermark
  rebuild` to regenerate sqlite from git.
- **Stage 3 — flip the default** (a deliberate breaking release, milestone #158
  style): git-derived baseline becomes default; sqlite is cache-only; document
  the migration. Keep `--no-git-baseline` as the escape hatch for one release.
- **Stage 4 — (optional) sync-on-commit (option B).** A pre-commit / PostToolUse
  hook runs `clm slides sync --dry-run` on a committed `.de/.en` half and
  warns/blocks on drift, so the marker advances in lockstep with commits. This
  is the piece that makes a *pure commit-ref* marker (mechanism a) viable, and
  closes the "edited + committed without syncing" gap at its source.

---

## 6. Open questions (to resolve before Stage 2)

1. **Marker payload: commit-ref vs content-hash vs full bundle.** A bare
   commit-ref marker only addresses *committed* synced states (loses §3
   property 2). A content-hash marker needs the blob to still exist
   (`git cat-file`) — true for committed states, not for sync-without-commit. A
   serialized `BaselineBundle` is self-contained (works regardless of commits)
   but is the heaviest payload and most duplicative of git. Which trade do we
   want? (Leaning: store the full bundle in mechanism (c)'s file — self-contained,
   no commit coupling, but in-repo and reviewable.)
2. **Sync without commit.** Do we *want* to keep supporting "sync against the
   working tree, don't commit, sync again"? If we tie the marker to commits, this
   workflow changes. (The course workflow is commit-heavy, so this may be
   acceptable — needs a call.)
3. **Multi-deck batch.** A directory sync touches many pairs. A per-deck
   trailer/file scales differently — (c) one file with N entries vs (a) N
   trailers in one commit message. (c) wins here.
4. **History rewrite resilience.** Rebase/squash/`--amend` must not silently
   invalidate the marker. (c) (a tracked file) follows the tree like any other
   content; (a)/(b) need `notes.rewriteRef` / trailer-preservation care.
5. **Merge semantics of the state file (c).** A sync-state conflict on merge is a
   *true positive* (both branches advanced the same deck) — should it block the
   merge, or auto-resolve to the union and re-sync? (Lean: surface it; it is the
   same signal `--rebaseline` exists for.)
6. **Cross-store consistency during migration.** While both sqlite and the
   in-repo marker exist (Stages 1–2), which wins on disagreement? (Lean: in-repo
   marker is authoritative; sqlite is a cache; on disagreement, trust git and log
   a rebuild.)

---

## 7. Relationship to the other #366 options

- **(D) auto-heal** — shipped (`--rebaseline`). Orthogonal; stays.
- **(B) sync-on-commit** — Stage 4 above; complements (A) by keeping the marker
  current. Cheap, high-leverage, independent of the marker mechanism.
- **(C) reconsider split-as-source** — out of scope here; a git-travelling,
  *id-based* correspondence (the #365 / Tier-3 direction) is what would make split
  decks effectively stateless and is the long-term escape from the watermark
  entirely. (A) is the pragmatic interim.

---

## 8. Recommendation

Adopt mechanism **(c)** (in-repo sync-state file storing a serialized
`BaselineBundle` per deck) and the **staged rollout** in §5, beginning with the
non-breaking **Stage 1 `--git-baseline` opt-in**. This reuses the entire
source-agnostic baseline pipeline (§2.1), preserves the watermark's essential
"remember the last reconciled state" property (§3), makes that state reviewable
and pushed-by-default, and demotes sqlite to a rebuildable cache without a
breaking flip until Stage 3. Resolve the §6 open questions (chiefly the marker
payload and the sync-without-commit workflow) before building Stage 2.
