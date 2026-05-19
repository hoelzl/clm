# CLM Repo — Next Steps Needed by PythonCourses Slide-Format-Redesign Migration

Drafted 2026-05-19 from the PythonCourses side. Hand this to an agent
working in the CLM repo (`hoelzl/clm`); it should be sufficient to pick
up the remaining work without further PythonCourses context.

## Context (one minute)

The slide-format-redesign migration is documented in two paired
handovers — both repos carry the same CLM-side copy:

- CLM repo (you are here): `docs/handover-slide-format-redesign-clm.md`
- PythonCourses side: `docs/handover-slide-format-redesign-course.md`,
  with a live action list at `docs/handover-slide-format-redesign-next-steps.md`

PythonCourses is consuming CLM phases as they ship. As of today
(2026-05-19), CLM master is at `5987e639` and **Phases 0-6 have shipped**.
PythonCourses is still pinned to the earlier `a5af7367` and will bump
locally — that's a PythonCourses operation, not anything CLM needs to do.

The items below are what is **not yet on CLM master** and is still
needed to unblock the migration's remaining phases.

---

## Priority 1 — Phase: assign-ids extraction expansion (hoelzl/clm#89)

**Status:** Issue filed, design proposal written, **not implemented**.

**Sources of truth:**
- `docs/proposals/ASSIGN_IDS_EXTRACTION_EXPANSION.md` (CLM repo — full design)
- `gh issue view 89 --repo hoelzl/clm` (same content, filed as issue)

**Why this is the top priority:** PythonCourses Phase B (slide_id rollout)
ran on `slides/module_550_ml_azav/` at commit `109fc63e` and cleared
slide_ids for most cells but left **407 missing-ID warnings** on
prose-intro subslides and `slide`/`subslide`-tagged code cells that
the current extractor can't slug from. Until this ships:

- Phase B cannot be declared complete on either ML AZAV module
- The `validate` slide_id checks (Phase 3) cannot be promoted from
  warning to error on this corpus
- The byte-identical-output gate against the A.1 baseline cannot run
  cleanly (the assign-ids re-run is part of the standard mechanical
  change sequence)

**Scope (per the design proposal — all four priorities together is
preferred; they're naturally one design discussion):**

| Priority | Adds | Clears (on ML AZAV corpus) |
|---|---|---|
| P1 — first-prose-line extractor for headingless markdown | new extractor; suggested locus `src/clm/slides/headingless.py` | ~218 of 407 warnings |
| P2 — code-cell identifier extractor (class / def / assign / import / call) | new module; suggested locus `src/clm/slides/code_cell_extract.py`, `ast`-based | ~165 |
| P3 — sibling-pair asymmetry fix (one cell extractable, sibling refused → both inherit the extractable slug) | tweak the pairing logic in `assign_ids.py` | ~10 |
| P4 — `--llm-suggest` semantics fix: fires on hard refusals; not short-circuited by `--accept-content-derived` | change the `else if` chain in step 8 of `assign_ids.py` | residual ~14 |

Combined: **all 407 warnings clear**; P1+P2 alone clear ~95%.

**Verification path:**
1. Round-trip on the `slides/module_550_ml_azav/` corpus (PythonCourses
   provides this; you can request a tarball or work cross-repo).
2. With P1+P2+P3 and no LLM flags: confirm zero hard refusals on the
   2026-05-19 corpus.
3. With P4 and `--llm-suggest --accept-content-derived`: confirm zero
   refusals total.
4. Idempotency: existing slide_ids (e.g. `wozu-eine-neue-bibliothek` in
   `slides_010_langchain_basics.py`) must be preserved without `--force`.
5. Unit tests for each extractor pattern, drawn from the sample tables
   in the issue body — see "Hard-refusal breakdown" section of #89.

**What PythonCourses does next (so you know the loop closes):** bump
the CLM pin, re-run `clm slides assign-ids` on both
`module_550_ml_azav/` and `module_545_ml_azav_cohort_2026_04/`, verify
byte-identical against the A.1 baseline, commit. The frozen-cohort
module's update lands under the existing mechanical-change exception
documented in `course-specs/machine-learning-azav.authoring.md`.

---

## Priority 2 — Phase 7: `clm slides sync`

**Status:** Designed in `docs/handover-slide-format-redesign-clm.md`
§3 Phase 7. **Not implemented.** This is the last unshipped phase that
blocks the PythonCourses Phase D pilot (language-split format on a
real ML AZAV deck under active revision).

**Scope (verbatim from the handover, repeated for the briefing):**

Cross-language sync for split-format decks. After editing `<deck>.de.py`,
propose corresponding edits to `<deck>.en.py` (or vice versa), backed by
an LLM call gated by a `SyncCache` table on
`(de_hash, en_hash, prompt_version)`.

**Modes:**
- `--dry-run` (default): show proposed diffs, no writes
- `--interactive`: walk diffs one-by-one with apply / skip / edit
- `--apply --trivial`: write proposed diffs without prompting; only
  with `--trivial` (mechanical typo fixes, identical-structure `keep`
  drift)

**Implementation notes (from the handover):**
- New `src/clm/slides/sync.py`
- New `src/clm/infrastructure/llm/sync_prompts.py`
- Extend `src/clm/infrastructure/llm/cache.py` with a `SyncCache` class
  reusing the same SQLite file as the `CoverageCache` (separate table)
- Register `sync` subcommand in `src/clm/cli/slides_cmd.py`
- Tests: `tests/infrastructure/llm/test_sync_cache.py`,
  `tests/slides/test_sync.py`
- Open UX question (handover §3 Phase 7): 3-way merge handling for the
  "both DE and EN changed since last sync" case.
  **Recommendation for v1:** flag-and-defer-to-manual is acceptable.
  An LLM-suggested merge can follow as a v2 feature; the pilot evaluation
  will tell us how often this case actually fires.

**Useful pilot instrumentation (please build in):** log per-session
accept / skip / edit counts to the cache (or to stderr) so the
PythonCourses pilot evaluation can quote a real accept rate. The
pilot's decision criterion is "human accepts proposed diff as-is in
>80% of cases" — below that, the split format is not paying for itself.
The faster we can measure this, the faster Phase E (or its cancellation)
is decided.

**Consumed by:** PythonCourses Phase D pilot. Once Phase 7 ships, the
PythonCourses agent will bump the pin, pick the pilot deck in
coordination with the ML AZAV restructure track (per the
PythonCourses-side handover §2.3), and run the pilot for 2-4 weeks.

---

## Priority 3 — Verify the Phase 4 coverage walker handles `task-*` slide-ids

**Status:** CLM commit `933da17f` ("feat(coverage): skip workshop
slides in the coverage walker") *should* address this, but the original
repro hasn't been re-confirmed end-to-end. Quick verification only —
not a heavy task.

**Original false positive (PythonCourses 2026-05-19 smoke test):**
running `clm slides coverage` on `slides_010_prompt_templates.py`
flagged `slide_id='task-1-persona-template'` for missing voiceover.
Workshop task slides per authoring rules do **not** require voiceover.

**What to check:**
- Does the workshop-skip logic catch slides identified by a `task-N-…`
  slide_id pattern, or only by a literal `workshop` tag at the slide
  level? Some workshop decks use `task-N-…` slugs on the constituent
  slides without re-tagging each one as `workshop`.
- If the latter, extend the walker so that a workshop **deck** opts
  out all of its child slides — preferable to special-casing
  `task-\d+-…` slug patterns.

**Fast repro:** grab `slides_010_prompt_templates.py` from
`hoelzl/PythonCourses` master and run `clm slides coverage` against it.
If `task-1-persona-template` appears in the findings list, the issue is
still present.

**Risk if unfixed:** noise in the Phase C coverage sweep. Not a blocker —
PythonCourses can triage and ignore — but every false positive adds
review cost when the sweep generates a few thousand findings.

---

## Priority 4 — Close hoelzl/clm#95 (housekeeping)

**Status:** Fix shipped via commit `08726876` ("fix(http-replay,snapshot):
allow_playback_repeats + spec-target snapshots (#95)"). Both sub-issues
(§A `allow_playback_repeats` missing in vcrpy bootstrap; §B `--snapshot`
ignoring spec's `<output-targets>`) are covered. **GitHub issue is still
marked OPEN.**

**Action:** Close the issue once PythonCourses confirms the bump produces
a clean snapshot/verify cycle on the AZAV ML spec. PythonCourses will
post a confirmation comment on the issue when that's verified — wait
for it before closing.

**Optional (nice to have):** add an integration test in CLM for the
`<output-targets>` honoring behavior using a synthetic spec with the
shared/trainer/speaker triplet. Current unit tests are likely focused
on the legacy public/speaker shape (which was the bug). A regression
test in CLM is cheaper than catching this again via PythonCourses
verification.

---

## Priority 5 (optional, do not start without sign-off) — `http-replay-skip` tag

**Context:** Three cells in `slides_010_prompt_templates.py` chain two
LLM calls — call 1's output feeds call 2's prompt — and call 2's request
body is therefore nondeterministic across record/replay cycles. Strict
replay (`--http-replay=replay`) fails the body matcher on call 2. This
is a deck-content issue, not a CLM bug — documented in
`docs/proposals/CLM_ISSUE_DRAFT_snapshot_replay_2026-05-19.md` §C and
in the original hoelzl/clm#95 body.

**Possible CLM-side mitigation:** a tag that tells the http-replay
machinery to skip strict-replay verification for that specific cell
(execute the request live or via cache; don't demand byte-equal
request bodies). E.g.

```python
# %% tags=["http-replay-skip"]
result_2 = llm.invoke(prompt_with(result_1))
```

**Decision needed *before* implementing:** does CLM want to support
this, or should chained-LLM-call workshops always be restructured
deck-side? PythonCourses next-steps §3 has three options open:
(1) stub the LLM in the chained cells, (2) restructure the deck to
break the dependency, (3) accept the flakiness with a tag (this
option). The course-side agent has not picked yet. **Do not build
this until the PythonCourses side decides.** If it picks option 1 or
2, this work is unneeded.

---

## Out of scope for this document

- **CLM Phase 8** (referenced in CLM commit `5987e639` as "0-6
  shipped, only 7/8 pending"). The slide-format-redesign handover
  doesn't mention Phase 8; CLM's own EXTERNAL_PLANS / internal
  planning is authoritative for what Phase 8 contains. Not a
  PythonCourses migration blocker.
- **MCP tool renames** (CLM handover §4 "blockers"). Coordinated
  with the PythonCourses-side rename commit; not on the critical
  path for the migration.
- **Other PythonCourses courses** (Python Programming, AI-Assisted
  Development). Slide-format-redesign is ML AZAV only at present;
  rollout to other courses will be considered after ML AZAV
  stabilizes.

---

## When you're done — signaling back to PythonCourses

After each priority lands on CLM master:

1. Post a comment on the relevant GitHub issue with the merge commit
   SHA and a one-line summary of what shipped.
2. Update `docs/handover-slide-format-redesign-clm.md` in *both* repos
   if you can (CLM-side directly; PythonCourses-side via a small PR or
   noted in the next-steps doc).
3. The PythonCourses agent will then bump the CLM pin via
   `uv lock --upgrade-package coding-academy-lecture-manager`, run the
   verification path documented in each priority above, and continue
   the migration.

If you discover a design problem that needs a PythonCourses-side
decision before you can proceed, file it as a comment on the
corresponding issue and flag it in `docs/handover-slide-format-redesign-next-steps.md`
(PythonCourses repo). Don't block on it silently.

---

## References

CLM repo (you are working here):
- `docs/handover-slide-format-redesign-clm.md` — full phase breakdown,
  design decisions, file lists
- `docs/proposals/ASSIGN_IDS_EXTRACTION_EXPANSION.md` — Priority 2
  design proposal
- `gh issue view 89 --repo hoelzl/clm` — same content, with metadata
- `gh issue view 95 --repo hoelzl/clm` — fixed by `08726876` but still
  OPEN

PythonCourses side (read-only context):
- `docs/handover-slide-format-redesign-course.md` — what consumes each
  CLM phase
- `docs/handover-slide-format-redesign-next-steps.md` — live action list
- `docs/proposals/CLM_ISSUE_DRAFT_snapshot_replay_2026-05-19.md` — the
  body of hoelzl/clm#95 verbatim
