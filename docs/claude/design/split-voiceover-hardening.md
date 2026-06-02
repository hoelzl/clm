# Split-Language + Separated-Voiceover Hardening — Investigation & Design Direction

**Status**: Investigation complete; pre-implementation. Direction agreed with the
maintainer (2026-06-02).
**Author**: Claude (Opus 4.8), with the maintainer.
**Date**: 2026-06-02
**Scope**: the perimeter of the split-language + separated-voiceover workflow —
`clm slides sync`, `assign-ids`, `split`/`unify`, `voiceover extract`/`inline`, the
build pipeline's treatment of split files + voiceover companions, the validator,
and the verification/test surface. **Not** a rewrite of the sync diff/apply core.
**Related**: `single-language-authoring-sync.md` (#166 engine), `sync-content-anchor-identity.md`
(#190 identity), `validator-workshop-voiceover-suppression.md`; issues
[#162](https://github.com/hoelzl/clm/issues/162) (the keystone, OPEN), #166, #190,
#198, #199.
**Provenance**: grounded in a 10-agent parallel code-read of the subsystems + a
4-agent adversarial code-trace verification pass. Claims below are tagged
**[verified]** (traced to code this session), **[corrected]** (a raw finding the
verification pass refuted/refined), or **[design]** (proposed, not yet built).

---

## 1. Executive summary — the reframing

The recent PR churn (#162/#163/#166/#190/#198/#199) makes the system *feel* brittle,
but the investigation shows the opposite where it counts: **the sync diff/apply core
is genuinely safety-conscious and mostly correct.** It has no-silent-no-op, per-cell
direction, conflict-by-default-defer, a buffered atomic temp-swap gated on an
error-free pass, a "never drop a worn id" *validated* LLM recovery tier that
safe-aborts on any failure, content-anchor identity that survives commits, and a
partial-watermark-advance path guarded by a completeness invariant.

The brittleness that actually lost work lives at **four edges plus one structural
fault line**:

| Zone | What's exposed |
|---|---|
| **A. The `clm slides sync` CLI surface** | Two bare positional path args, no matched-pair guard, writes by default |
| **B. Cross-command seams** (split/unify ↔ voiceover) | Commands are unaware of each other; ordering silently orphans data |
| **C. Separated-voiceover anchoring + build** | The companion file *leaks into student output*; `inline` destroys its own source-of-truth |
| **D. No enforced gate** | Every safety net (`validate`, `sync --dry-run`) depends on the author *remembering* to run it |
| **★ `slide_id` as the cross-language join key (#162)** | The whole split+voiceover edifice rests on `de_id == en_id`; born-split / re-`assign-ids`'d halves diverge silently |

**Strategic consequence:** don't rewrite the engine — armor its perimeter and the
cross-command seams, resolve the join-key fault before born-split authoring is
common, and **make `sync` the one safe funnel** (see §8). This is a far smaller,
safer surface than "the sync engine is unreliable."

---

## 2. Decisions locked this session (maintainer)

1. **Start with the #162 `slide_id` join-key fault** — the structural keystone; the
   other two choices depend on it.
2. **Gate philosophy = pre-commit only.** A course-repo pre-commit hook runs
   `validate` + `sync --dry-run`; `build` stays permissive (no build-time refusal of
   out-of-sync halves for now).
3. **Voiceover file model = harden the current per-language convention**
   (`voiceover_X.de.py` / `voiceover_X.en.py`, filename-inferred, `vo_anchor`-keyed) —
   fix the leaks/guards + add a companion split/unify path + a both-language
   compatibility check. Do *not* switch to a spec declaration or a single bilingual
   companion now.
4. **Deck lifecycle = both, long-term.** Some decks inherit ids from a bilingual
   ancestor; new ones may be born split. So the full #162 triad (detective +
   defensive + generative) is in scope eventually.
5. **Next build target = the edit-dynamics fault-injection harness** (§6) — the
   maintainer judges it "enormously valuable going forward." A *static* corpus census
   was explicitly rejected as grounding because it mostly grades the splitter, not
   what happens under real editing.
6. **Rethink the command surface** (§8) — some problems arise because we expose
   inherently-unsafe user-visible commands; fold their function into `sync` or hide
   them as agent/script plumbing.

---

## 3. Verified hazard inventory (ranked: severity × likelihood)

The verification pass **corrected several raw findings in both directions** — the
corrections are load-bearing for prioritization, so they are recorded explicitly.

### Tier 1 — confirmed data loss, cheap fixes (do first)

> **✅ ALL THREE FIXED (roadmap step 2, 2026-06-03).** (1) `voiceover_*.py` added to
> `SKIP_OUTPUT_FILE_PATTERNS` + `SKIP_OUTPUT_FILE_GLOBS` (output-copy + recursive-dir-copy
> vectors) — kept available as build source, suppressed only from output + kernel payload.
> (2) `inline_voiceover` now retains the companion (rewritten to the unmatched remainder,
> anchors intact) instead of unlinking it, and the CLI exits non-zero on unmatched. (3)
> `extract_voiceover` gains `force=` / `--force` and refuses to clobber an existing companion
> otherwise (checked *before* the slide is rewritten). New `VoiceoverError`; CLI/MCP wrap it.
> Harness rows `inline-after-rename` + `re-extract-over-edited-companion` flipped break-silent →
> preserve; new unit tests in `test_voiceover_tools.py` + `path_utils_test.py`. Original analysis
> retained below.

1. **Voiceover companion leaks into student output.** **[verified, worse than first
   reported].** `voiceover_*.py` is classified as a `DataFile`
   (`course_file.py:152-163`; `is_slides_file` requires `slides_`/`topic_`/`project_`
   prefix, `path_utils.py:160-165`) → `DataFile.get_processing_operation` emits a
   `CopyFileOperation` into **every** output tree, student *and* speaker
   (`data_file.py:37-52`). The "matched-companion exclusion"
   (`process_notebook.py:110-134`, esp. `:133`) is **payload-only** — it governs the
   worker `other_files` dict, never the output copy — so *even the normal matched
   companion of a built slide is copied verbatim into student output*. It survives
   the end-of-build sweep because the build itself wrote it (registry-tracked;
   `output_sweep.py:43-52,160-166,264-276`). `SKIP_OUTPUT_FILE_PATTERNS` contains only
   `*.http-cassette.yaml` (`path_utils.py:87-91`); no `voiceover_` entry.
   - *Nuance:* this is a **file** leak, not a rendered-content leak — the merged
     voiceover *cells* are correctly stripped from student kinds by `output_spec`
     (CompletedOutput/CodeAlongOutput drop them; SpeakerOutput keeps them). It is the
     raw `.py` source that lands beside the student deck.
   - **Fix (one line):** add a `voiceover_` pattern to `SKIP_OUTPUT_FILE_PATTERNS` /
     `is_ignored_file_for_output` (mirror the cassette handling) so
     `DataFile.get_processing_operation` returns `NoOperation`. Fixes matched + orphan
     at the copy site.

2. **`inline_voiceover` deletes the companion unconditionally — even on stranded
   cells.** **[verified; highest data-loss priority].** `comp.unlink()` runs on every
   non-dry-run inline regardless of `unmatched_cells`/`relocated_cells`, no backup,
   **exit 0** (`voiceover_tools.py:794-800`; CLI never sets a non-zero exit,
   `cli/commands/voiceover_tools.py:76-115`). Unmatched content survives (appended at
   EOF of the slide file) but the *clean, anchor-bearing companion* — the source of
   truth to re-run against after fixing a renamed `slide_id` — is destroyed in the
   same op. `--dry-run` exists (shows `!` relocated / `?` unmatched) but is not the
   default.
   - **Fix:** keep (or `.bak`) the companion when `unmatched_cells>0 or
     relocated_cells>0`; return a non-zero exit on unmatched; consider `--dry-run`
     default for `inline`.

3. **`extract_voiceover` clobbers a hand-edited companion with no `--force`.**
   **[verified; lower frequency].** Unconditional `comp.write_text`
   (`voiceover_tools.py:413-422`), rebuilding the companion solely from voiceover
   cells *currently in the slide file* — hand-edits and previously-extracted cells
   whose owning slide was deleted are lost. *Mitigated* by an early-return when the
   slide file has no voiceover cells (`:379-380`), so the danger is the partial /
   re-extract case. Out of step with `split_in_file` / `unify_in_file`, which both
   gate overwrites on `--force` (`split.py:271-281,558-560`).
   - **Fix:** mirror the `--force` + existence guard.

### Tier 2 — the scary one, downgraded

4. **`clm slides sync` has no matched-pair guard → *possible* destructive write.**
   **[corrected: MEDIUM, not "destructive by default"].** The literal claim
   ("destructive rewrite by default for any mismatch") is **refuted**, but the
   underlying concern is confirmed:
   - There is genuinely no guard (no `.de.py`/`.en.py` suffix check, no same-stem
     check, no `de_path != en_path`); default mode applies unconditionally
     (`slides_sync.py:87-95, 333-345`).
   - A *destructive* write requires a narrow combination: an **API key configured**
     (the happy path — sync auto-loads `.env`, `slides_sync.py:282-290`) **+** two
     decks sharing enough `slide_id`s to produce a *deterministic*
     remove/move/id-migration on an **error-free** pass. The cross-deck-orphan
     fail-safe runs *only on otherwise-clean passes* (`sync_apply.py:280-283,450-456`)
     — so it is bypassed exactly when such a write lands. The atomic flush is
     error-gated (`:298-299`), so any LLM failure (e.g. no key) makes the pass error
     and writes nothing.
   - **Swapped order** (the most likely fat-finger) is largely **self-defusing**: the
     language filter empties both localized cell sets
     (`ordered_sync_cells`, `sync_plan.py:251-278`), so the worst case is a verbatim
     neutral-cell copy, not a rewrite; and the watermark won't resolve (path-string
     keyed, `cache.py:735-741`) so it falls to git-HEAD/none.
   - **Two unrelated decks** (cold baseline) produce only *additive* spurious inserts;
     duplicate ids hard-error. **[corrected]** the raw finding's "cold baseline = mass
     add" is wrong — `_classify_cold` (`sync_plan.py:1021-1048`) counts shared-id
     pairs as `in_sync`, emits no edit/remove/move.
   - **Fix (cheap & total):** an upfront `UsageError` in the CLI command (assert
     suffixes, same deck stem, `de≠en`, per-file lang matches its suffix) closes the
     whole class *before* any baseline/LLM/flush logic.

### Tier 3 — structural / forward-looking

5. **`slide_id` cross-language divergence (#162).** **[verified, structural, OPEN].**
   The join key for voiceover sync, `unify` (`_slide_ids_pair` requires `de_id==en_id`,
   `split.py:420-431`), and extract/inline `for_slide`. `assign-ids` only guarantees
   `de_id==en_id` inside a *bilingual* file (EN-sibling slug source, `group_slug` cache,
   `assign_ids.py:301-339`). A born-split deck has no bilingual ancestor → re-running
   `assign-ids` per file slugs each from its own headings → silent divergence, masked
   today only by inheritance from pre-split ancestors. See §7.

6. **N-file atomicity for separated voiceover.** **[verified residual].** The atomic
   swap is hardcoded around the 2-path `(de, en)` pair with a residual window between
   the two `os.replace` calls (`sync_apply.py:1088-1127`). Separated voiceover means
   3–4 files per logical deck — the window widens; the watermark schema/commit point
   isn't modeled for it. Design up front.

7. **`FileTopic` bypass.** **[verified].** A split `.de.py` that resolves as a
   *single-file* topic skips **all** split routing (language filter, half-pair /
   dual-format guards) and fans out to both languages
   (`topic.py:426-445` vs `318-408`). Add a guard or refuse.

### Other confirmed issues

- **Build silently drops/relocates voiceover** when `for_slide` doesn't resolve or
  the anchor predecessor is gone — narration omitted from output (build merge:
  log-only, exit 0; `process_notebook.py:285-296`); inline relocations are *discarded*
  in the build merge path (`voiceover_tools.py:462-485`).
- **Validator cross-file parity is blind on a single file.**
  `_check_shared_cell_parity` / `_check_split_tag_parity` run *only* at dir/course
  scope (`validator.py:1433-1436,1486-1489`), never on a lone `.de.py`; and they go
  **silent on any cell-count mismatch** (`:980-981`), so a localized add/remove on one
  side is invisible. A single-file `validate slides_x.de.py` returns clean even if the
  twin diverged — false confidence.
- **Headingless slides strand their voiceover.** `extract`'s `_ensure_slide_ids` runs
  `assign-ids` with default `AssignOptions` (`accept_content_derived=False`,
  `voiceover_tools.py:149-156`), so a genuinely headingless, non-content-derivable
  solo slide gets no id → no `for_slide` → EOF-stranded on inline; `ids_generated`
  hides the skip. (A DE/EN sibling with a heading can still id it via the Phase-3
  sibling fallback.)
- **CI backstop reality.** **[corrected].** The per-cell engine fixes (item-2
  propagate-verbatim-no-LLM; item-3 reuse-not-retranslate + over-fire guards;
  def-my-fun migration; bounded recovery safe-abort) **do** run in CI as fast
  synthetic tests (`tests/slides/test_sync_limitations.py`, `test_sync_anchor.py`, no
  markers), as does the split/unify Hypothesis round-trip (80 examples,
  `tests/slides/test_split.py`). What **skips** in CI (no PythonCourses corpus): the
  corpus no-op zero-byte/zero-LLM invariant (81/212, floor 40,
  `tests/slides/test_sync_corpus_noop.py`) and the real-deck round-trip. The corpus
  "population > 1000/100" asserts guard the *measurement*, not the fix.
- **Legacy walker is NOT live.** **[corrected].** The dangerous line-number-keyed,
  per-accept-flush `sync_walker.run_interactive_walker` is **not** wired to the CLI
  (`slides_sync.py` imports `run_plan_walker` from `sync_plan_walker`). Dead-code
  cleanup candidate, not a live risk.

---

## 4. The cross-command questions, answered

- **Extract a voiceover from a unified deck, then `split` → does the voiceover get
  split? Do the split files recognize it?** No, and no. `split` has zero voiceover
  awareness; the bilingual `voiceover_X.py` is orphaned. The split halves look up
  `voiceover_X.de.py`/`.en.py` (name-derived, `voiceover_tools.py:122-136`), which
  don't exist → build merges nothing, **no warning**, and the orphan still leaks to
  output. **Only safe order today: split-first, then extract per language** (or
  `inline` before `split`). Nothing tells the user this.
- **Extracting on a split deck only does one language.** Correct — single-file CLI/MCP
  surface. **No cross-language compatibility check** (same `for_slide` set, same vo
  count, compatible anchors) exists between the `.de` and `.en` extractions.
- **Does `unify` pull voiceover back?** No — `unify` ignores companions entirely;
  `inline` into each half first, then `unify`.

---

## 5. The load-bearing thesis: correspondence is preserved *only if structural changes funnel through `sync`*

Tracing the edit operations against code yields a clean invariant: **`de_id == en_id`
is maintained as long as the author routes structural changes through `clm slides
sync`, which mints/migrates ids onto *both* halves. It breaks precisely when the
author steps *outside* that funnel** — and several of those exits are silent. This is
the conceptual foundation for both the harness (§6) and the command-surface rethink
(§8).

### Edit-dynamics table (edit op → path → id-correspondence outcome → guardrail)

| Edit a human makes to a split pair | Path | Outcome | Guardrail |
|---|---|---|---|
| Add a slide to one half | `sync` | ✅ Preserved — EN-authority id minted onto both (Phase 3) | — |
| Add a slide, then `assign-ids` **that file only** | per-file `assign-ids` | ❌ **Silent break** — slugs from its own heading | Defensive: twin-aware assign-ids |
| Born-split pair, `assign-ids` each half | per-file `assign-ids` | ❌ **Silent break** — each slugs independently | Generative: pair-aware minting |
| Reorder / delete in one half | `sync` | ✅ Preserved (move/remove mirrored) | — |
| Edit both halves of a slide | `sync` | ✅ Conflict-deferred (localized) / auto-heal+warn (neutral) | — |
| Copy-paste a slide (dup id) | `sync` | ✅ **Preserved [verified this session]** — re-mint propagates to twin as an add-like op, EN-authority id stamped on both | — |
| Split a code cell (`def-my-fun`) | `sync` | ✅ Deterministic id-migration on both halves | — |
| Rename a function while splitting a cell | `sync` | ⚠️ Construct match fails → defers / `--llm-recover` (surfaced) | — |
| Hand-edit an id in one half | any | ❌ **Silent break** — sync sees a new key; twin keeps old id | Detective gate |
| Edit a heading, `assign-ids --force` per-file | per-file `--force` | ⚠️ Sticky id normally; `--force` can regenerate divergently | Defensive + stable collision suffix |
| Add voiceover to a slide whose ids diverged | `extract` then build | ❌ **Silent** — `for_slide` matches one language; other's narration dropped | Detective + build escalation |
| Build/commit before syncing | `build` | ❌ Ships divergence — no consistency check | Pre-commit detective gate |

**Verified this session (the previously-open row):** the copy-paste `rename` is
handled exactly like an `add` (`sync_apply.py:806-834`): the copy slide is re-minted
with a fresh **EN-authority** id (`en_body = target_body if target_lang=="en" else
source body`; `new_id = resolve_collision(_slug_or_default(en_body), used_ids)`,
`:821-822`), that id is `_stamp_slide_id`'d onto the source copy **and** carried onto a
freshly-translated twin inserted on the other deck (`_place_new_cell`, `:897-900`);
companions inherit it by group-adjacency; `used_ids` spans both decks (`:684-689`).
Safety exits are loud: no baseline-identifiable original → hard error, nothing written
(`sync_plan.py:658-667`); no translator → deferred + error (`sync_apply.py:642-645`).
So this row is **safe-via-sync**, strengthening the thesis: the break is the per-file
`assign-ids` path, not sync.

---

## 6. The edit-dynamics fault-injection harness (BUILT 2026-06-02)

**Purpose.** Ground every hardening decision in *editing dynamics* rather than static
state. Not a census (which grades the splitter). The harness applies realistic edits
to parallel decks, runs the command path a user would take, and asserts the
correspondence/safety invariants — classifying each result **preserve / break-loud /
break-silent**. It executably verifies the §5 table, pins down exactly which exits
from the sync funnel are silent (= the precise spec for the §7 guardrails and the §8
command rethink), and becomes the **cross-command property suite** CI lacks today.

**Inputs.**
- Synthetic parallel `.de.py`/`.en.py` pairs — extend the existing Hypothesis
  generator in `tests/slides/test_split.py` (which today omits solo language cells; the
  harness should *include* misaligned/solo shapes as negative cases).
- Optionally **real** pairs mutated in place (the "dynamic" complement to the rejected
  static census) — gated on the PythonCourses corpus being present, like the existing
  corpus tests; never required for CI.

**Mutation catalog** (each = one realistic author action): add-slide-one-half;
add-then-assign-ids-per-file; born-split-assign-ids; reorder-one-half;
delete-one-half; edit-both-halves (localized + neutral); copy-paste-dup;
split-code-cell (def-my-fun); rename-function-while-splitting; hand-edit-id;
edit-heading-then-force; add-voiceover-with-diverged-ids;
extract-then-split; split-then-extract-per-language; inline-after-rename;
re-extract-over-edited-companion; build-before-sync.

**Command paths exercised.** `sync` (batch + the deterministic-only no-key path),
per-file `assign-ids`, `extract`/`inline`, `split`/`unify`, and `build` (companion
merge).

**Invariants asserted after each (op, path).**
1. `slide_id` set + order equal across `.de`/`.en` (the #162 invariant).
2. `for_slide` set equal across `.de`/`.en` companions; every `for_slide` resolves.
3. No voiceover EOF-stranding; no silent narration drop at build.
4. No data loss: a cell present before is present after (somewhere), or its removal
   was the explicit intent.
5. Round-trip identities where they should hold: `unify(split(x)) == x`;
   `split(unify(de,en)) == (de,en)`; extract→inline ≈ identity (modulo intended
   relocation); and the composite `unify(split(extract→companion+slides))` restores
   the original bilingual deck with voiceover.
6. Every divergence is **loud** (error/warning/non-zero exit), never silent.

**Output.** A classification table (op × path → preserve/break-loud/break-silent +
the offending file:line). The **break-silent** rows are the work-list for §7/§8.

**CI integration.** The synthetic arm runs in the fast suite (no corpus, no network),
closing the "only static round-trips run in CI" gap. The real-deck arm stays
`slow`/`integration`, dev-box-only. Consider vendoring a handful of redacted real
split+voiceover decks under `tests/` so a representative dynamic suite runs in CI.

**Engineering notes.** Reuse `clm.slides.pairing`, the `sync_plan`/`sync_apply`
classifier+apply, the `construct:` anchor, and the existing `StaticSlideTranslator` /
`judge=None` mocking so no live LLM is needed. Apply the recurring **non-unique-anchor
guard** (never key a map on a construct anchor; use ordered-hash + occurrence ordinal)
anywhere the harness pairs cells. Honor the byte-preserving split/unify contract in
assertions (don't normalize whitespace).

### Built — files, conventions, and the observed table (2026-06-02)

Shipped as the established library + CI-backstop pair (mirrors
`scripts/sync_corpus_harness.py` / `tests/slides/test_sync_corpus_noop.py`):

- **`scripts/edit_dynamics_harness.py`** — runnable report + importable library:
  split-pair/companion builders (incl. solo/misaligned shapes), the command-path
  runners (`run_sync`, `run_assign_ids`, extract/inline/split/unify/build-merge), the
  invariant checkers (`id_parity`, `for_slides`, round-trips), the mutation catalogue,
  and the preserve/break-loud/break-silent classifier.
  `python scripts/edit_dynamics_harness.py` prints the table (`--json` for machine
  form, `--path X` to filter); it exits non-zero **only on drift** of an asserted row.
- **`tests/slides/test_edit_dynamics.py`** — fast-suite backstop (no corpus, no network,
  no markers): `test_no_drift` freezes the whole table; `test_sync_funnel_always_preserves`
  is the engine-regression guard; `test_known_silent_breaks_still_surfaced` stops the
  work-list being silently zeroed out.

No-LLM mocking uses the `CountingTranslator` / `CountingJudge` stand-ins (verbatim from
`test_sync_limitations.py`), watermark seeded via `watermark_rows` into a tmp SQLite DB,
`build_sync_plan(..., allow_git_fallback=False)`. **Correction to the original note:**
`judge=None` is *not* usable — it records every edit as an LLM-unavailable error; the
counting judge is required to drive the apply path.

**Observed classification (15 mutations — matches every §3/§5 prediction):**

| Verdict | Mutations |
|---|---|
| **preserve** (8) | sync: add / reorder / delete one half; edit-both (conflict-deferred); copy-paste-dup; **hand-edit-id** — sync *heals* it via remove+add mirrored to the twin (en localized content is regenerated), **refining** the §5 prediction that it is a silent break (the break is only on the *never-synced* path); extract→inline round-trip; unify(split) round-trip |
| **break-silent** (7) | assign-ids: per-file & born-split (divergent slugs, 0 refusals); commit-without-sync (no gate, build permissive); extract-then-split (bilingual companion orphaned, split silent); inline-after-rename (clean companion destroyed, 1 cell stranded, exit 0); re-extract-over-edited-companion (hand-edit clobbered, no `--force`); build-merge-unmatched (narration dropped — **observe-only**, the build arm) |
| break-loud (0) | — every break is currently *silent*: that is the headline finding |

**The work-list is exactly the seven break-silent rows** (drives §12 steps 2–5 and the
§8 fold/hide/guard dispositions). Deferred catalogue rows (frozen verdict pending a
behaviour probe): split-code-cell (def-my-fun), rename-function-while-splitting,
edit-heading-then-force.

---

## 7. The #162 design direction (the keystone)

**The hard part:** cross-language cell correspondence *without* a pre-existing shared
id (can't pair by `slide_id` — that's circular). It decomposes by certainty:

| Cell class | Correspondence signal | Certainty |
|---|---|---|
| Shared / language-neutral | Byte-identical across halves (the `unify` invariant) | Exact |
| Localized code | The `construct:` anchor is language-agnostic; de/en twins share it (#190) | High (modulo the non-unique-construct guard) |
| Localized markdown (headings/prose) | Position within the parallel slide-group structure | Positional only — fragile under structural drift |

The slug stays **EN-authority** (derive from the EN heading, stamp the *same* id onto
both). The only new thing vs. today is doing this across a *pair* of files rather than
within one bilingual file.

**The triad** (all three needed, per "both, long-term"):

- **Detective (= the pre-commit gate, §9):** ✅ **BUILT 2026-06-03** — enforce the
  invariant every "silent break" row violates — across `.de`/`.en`, equal `slide_id`
  sets in equal order (companion `for_slide` sets still to add). Shipped as a `clm validate`
  `pairing`-group warning: `_check_split_slide_id_parity` in `validator.py`, wired at
  dir/course scope **and** the single-file path (`validate_file(cross_file_parity=True)` when
  a twin exists on disk, so the pre-commit gate / PostToolUse path catch it). Harness
  `commit-without-sync` flipped break-silent → break-loud ("detective CATCHES it"). 6 new
  unit tests. Companion `for_slide` parity (the both-language voiceover compatibility check)
  is the small remaining extension.
- **Defensive:** the highest-frequency break is **per-file `assign-ids` /
  `extract-voiceover`**. Make them twin-aware — on a split half, consult the sibling
  and refuse to mint a divergent id rather than running blind. (Strongly related to
  §8: arguably these per-file ops should not be normal user commands at all.)
- **Generative:** pair-aware `assign-ids` over *both* halves — establish
  correspondence (neutral=byte, code=construct, markdown=positional), mint EN-authority
  ids onto both, deterministic for structurally-parallel decks, escalating to the
  defensive refusal (or the existing #166 LLM sync path) on genuine misalignment.

**Open design questions for #162:**
1. How often are real `.de`/`.en` pairs structurally parallel vs misaligned? (Answered
   empirically by the harness's real-deck arm, *under mutation* — not a static
   census.) Determines how often generative covers the case vs the backstop fires.
2. On the pre-commit gate's first run, does it fire as a hard error or warning?
   (Today's per-file `assign-ids` may already have produced divergence → a hard error
   would block commits immediately, possibly needing a one-time reconciliation pass.)
3. Correspondence priority + ambiguity policy: refuse-and-surface (safe) vs.
   LLM-assisted (the #166 path exists). Lean: deterministic for parallel,
   refuse+surface otherwise, #166 sync as the opt-in heavy path.

---

## 8. Command-surface rethink (maintainer-requested)

**Observation:** several footguns exist because we expose **inherently-unsafe
user-visible commands** that operate outside the safe `sync` funnel. The §5 thesis
gives the architectural principle: **`sync` is the one operation that keeps both
halves consistent; sharp single-file tools that mutate identity or move content
outside it are footguns.** For each, decide: **(F)** fold into `sync`, **(H)** hide as
agent/script plumbing, or **(G)** harden with a guard.

| Command | Why it's risky | Disposition (proposed) |
|---|---|---|
| `assign-ids` (per file) | Per-file run on a split half mints **divergent** ids — the #1 silent #162 break. Its core job (id minting) is already done *consistently* by `sync`. | **F + H** — fold consistent minting into `sync` / a pair-aware mode (§7 generative); demote the raw per-file command to plumbing (agents/scripts), not a normal authoring step. |
| `voiceover extract` | Single-file, clobbers companion w/o `--force`; per-language only; no cross-language check. | **G now, F later** — add `--force`/guard immediately; longer-term fold extraction into a paired, twin-aware operation (or under `sync`'s umbrella). |
| `voiceover inline` | Destructive: unlinks companion even on unmatched, exit 0, no backup. | **G** — preserve/backup on unmatched, non-zero exit, `--dry-run` default. |
| `slides split` | Unaware of voiceover companions; extract-then-split silently orphans them. | **G** — refuse/warn when a sibling `voiceover_*.py` exists; add a first-class companion split (route vo cells by `lang`, preserve `for_slide`/`vo_anchor`). |
| `slides unify` | Ignores companions; can't recombine voiceover. | **G** — companion unify path (the inverse of the above). |
| `slides suggest-sync` | Old single-file read-only suggester; coexists confusingly with the split-pair `sync` (different file layout, opposite write semantics). | **H or remove** — hide as plumbing or retire; steer users to `sync`. |
| `slides sync` (the funnel) | Two bare path args, no pairing guard, writes by default. | **G** — pairing guard (§3 Tier-2), and consider a **single-path contract** (pass one half or the deck stem; derive the twin + companion) + a directory/spec **batch mode** so the author can't fumble the pair or cross decks. |

**Mechanism for "hide as plumbing."** Click supports `hidden=True` on commands (kept
functional, omitted from `--help`); or move plumbing under a `clm _internal …` / `clm
plumbing …` group, or behind a `--i-know-what-im-doing`-style gate. Agents and scripts
that need the raw single-file ops still call them; normal authors only see the safe
funnel. This preserves the dual-use value while removing the footgun from the everyday
surface. **Per the Info Topics Maintenance Rule, any command removal/hiding/folding
must update `info_topics/commands.md` and `migration.md`.**

**Guiding UX principle:** the author writes content; the tooling owns identity; the
*default* surface only offers operations that keep both halves consistent. Sharp tools
remain reachable for agents/scripts but are not the path of least resistance.

---

## 9. The pre-commit gate (the chosen enforcement)

Lives in the **course repo** (e.g. PythonCourses `.pre-commit-config.yaml`), not CLM
itself — CLM provides the check command. It runs over the staged set:
- the **#162 detective**: cross-file `slide_id` set+order equality across each
  `.de`/`.en` pair, and companion `for_slide`-set equality;
- `validate` (dir-scoped, so cross-file parity actually fires) — **with `--fail-on
  warning`** (or a split-critical-categories strict mode), because today
  missing-`slide_id`, tag-parity asymmetry, slug-format, and pair-id mismatch are all
  *warnings* (exit 0, `validator.py:753-769`), so a naive `validate && commit` lets
  them through;
- `sync --dry-run` to fail on an unsynced half (`--check`-style exit code).

`build` stays permissive for now (decision #2). If the gate proves leaky, revisit
build-time refusal later.

---

## 10. Voiceover model: harden the current convention

Keep `voiceover_X.de.py` / `voiceover_X.en.py` (filename-inferred, `vo_anchor`-keyed).
Required hardening:
- Stop the output leak (§3 Tier-1 #1).
- Stop `inline` destroying its source-of-truth (§3 Tier-1 #2); `extract` `--force`
  (§3 Tier-1 #3).
- **Companion split/unify path** so extract-then-split / unify don't orphan voiceover
  (§8): route companion cells by their owning slide's `lang`, preserving
  `for_slide`/`vo_anchor`. This is well-defined *once* `de_id==en_id` is guaranteed
  (depends on #162).
- **Both-language compatibility check** = companion `for_slide`-set equality across
  `.de`/`.en` (same invariant as the slide check; part of the detective gate).
- **Build-time escalation** of unmatched `for_slide` from log-only to a surfaced
  finding (respecting a fail-on policy), and surface inline **relocations** at build
  time (today discarded in `merge_voiceover_text`).
- **Validator companion-integrity check** (dir/course scope): every `for_slide`
  resolves; warn on orphan companions / a slide that lost its companion / contradictory
  companion sets (bilingual + split coexisting).
- **Landmine for new payload fields:** if separated voiceover adds a field to
  `NotebookPayload` routed to the build worker, the worker MUST `model_validate` the
  whole dict — a hand-listed field is silently dropped at the boundary (the #17
  landmine). Add a test that a synthetic unknown field survives.

---

## 11. Verification strategy (to convince ourselves & users)

Honest CI picture: per-cell engine fixes + split/unify round-trip **run** in CI;
corpus no-op invariant + real-deck round-trip **skip**. To build justified confidence:
1. The **edit-dynamics harness** (§6) — synthetic arm in CI.
2. **Companion-integrity validator check** + **wire cross-file parity into the
   single-file path** when a twin exists on disk (so the per-file/PostToolUse path
   isn't blind to twin divergence).
3. **Promote `_check_split_tag_parity` to fire on cell-count mismatch** (today
   self-silences, `validator.py:980-981`) + a localized-cell parity check.
4. **CI-runnable golden corpus** — vendor a few redacted real split+voiceover decks.
5. The **pre-commit gate** (§9) with `--fail-on warning`.
6. Guard the **worker-payload boundary** (`model_validate`) before adding vo fields.

---

## 12. Sequencing / roadmap

1. ~~**Build the edit-dynamics fault-injection harness**~~ ✅ **DONE 2026-06-02** (§6) —
   `scripts/edit_dynamics_harness.py` + `tests/slides/test_edit_dynamics.py` (8 preserve /
   7 break-silent / 0 error, no drift). The seven break-silent rows are the work-list for
   steps 2–5; the sync funnel is now regression-guarded in the fast suite.
2. ~~**Tier-1 data-loss fixes**~~ ✅ **DONE 2026-06-03** (§3) — vo output leak;
   inline retains-companion-on-unmatched + non-zero exit; extract `--force`. Verified by
   the harness (two rows flipped to preserve) + new unit tests; full fast suite green.
3. **#162 keystone** (§7) — **detective ✅ DONE 2026-06-03** (cross-file `slide_id` parity
   warning in `clm validate`, dir/course + single-file-with-twin; `commit-without-sync`
   flipped break-silent → break-loud). **Next: defensive** twin-aware `assign-ids`/`extract`
   (refuse to mint a divergent id on a split half) — clears the two assign-ids break-silent
   rows; then generative pair-aware minting for born-split + companion `for_slide` parity.
4. **Command-surface rethink** (§8) — fold/hide/guard, with info-topic updates.
5. **Pre-commit gate** (§9) + voiceover hardening (§10) + verification additions (§11).
6. **Sync CLI pairing guard** (§3 Tier-2) + single-path/batch UX.
7. Forward design: N-file atomicity for separated voiceover (§3 #6); `FileTopic`
   bypass guard (§3 #7).

---

## 13. Key code-location map (for future sessions)

- Sync CLI: `src/clm/cli/commands/slides_sync.py` (args `:87-95`; apply default
  `:333-345`; `.env` load `:282-290`).
- Sync engine: `sync_plan.py` (classifier; `ordered_sync_cells` lang filter `:251-278`;
  `_resolve_duplicates` `:623-717`; cold path `:1021-1048`; baseline `:1190-1246`),
  `sync_apply.py` (apply; atomic flush `:1088-1127`; flush gate `:298-299`; orphan
  fail-safe `:280-283,450-456`; add/rename `:634-858`; id-migration
  `:1214-1331,1389-1445`), `sync_recover.py` (validated LLM recovery `:212-279`),
  `sync_code.py` (structural pass), `sync_writeback.py` (`role_of`, `anchor_of`,
  `cell_content_hash`), `pairing.py`.
- IDs: `assign_ids.py` (slug/minting; `group_slug` `:301-339`; refusals `:545-568`),
  `slug.py` (`resolve_collision` `:189-201`).
- Voiceover (slide-side): `voiceover_tools.py` (`companion_path` `:122-136`; extract
  `:349-424`; inline `:706-802`; `merge_voiceover_text` `:432-485`; `vo_anchor`
  `:204-256`).
- Split/unify: `split.py` (`split_text`; `unify_texts`; `_slide_ids_pair` `:420-431`;
  force guards `:271-281,558-560`).
- Build: `build.py` (split-routing abort `:1117-1132`); `topic.py`
  (`add_files_in_dir`/`_add_slide_units` `:318-408`; `FileTopic` `:426-445`);
  `notebook_file.py` (`output_language_filter`, `companion_voiceover_path` `:108-114`);
  `process_notebook.py` (`compute_other_files` `:110-134`; vo merge `:283-296`);
  `data_file.py` (`get_processing_operation` `:37-52`); `path_utils.py` (SKIP lists
  `:87-100`, `is_ignored_file_for_output` `:244-262`); `output_sweep.py`
  (`:43-52,160-166,264-276`); `notebook_processor.py` (strips slide_id/for_slide
  `:1586-1592`).
- Validator: `validator.py` (slide_ids `:696-876`; cross-file parity `:879-1014`,
  wired only at dir/course `:1433-1436,1486-1489`; `validate_quick` `:1364-1398`).
- Tests/harness: `tests/slides/test_split.py` (round-trip property),
  `test_voiceover_tools.py` (extract/inline, positional anchors),
  `test_sync_limitations.py` + `test_sync_anchor.py` (item-2/3 fix, CI),
  `test_sync_corpus_noop.py` + `scripts/sync_corpus_harness.py` (no-op invariant,
  dev-box-only).

---

## 14. Open questions / things still UNVERIFIED

- The harness real-deck arm should answer the structural-parallel-vs-misaligned
  frequency (§7 Q1) **under mutation**, not statically.
- N-file atomic commit point for separated voiceover (§3 #6) — design not started.
- Whether `slides suggest-sync` has any remaining unique value before retiring it.
- Whether two trainers syncing the same pair on different machines (per-machine
  `clm-llm.sqlite` watermark) can produce conflicting clean-looking writes (raised by
  an agent; not traced).
