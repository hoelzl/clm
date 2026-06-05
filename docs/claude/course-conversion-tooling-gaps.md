# Tooling gaps observed during the 1.8 PythonCourses slide-id gate

Captured 2026-06-04 while bringing the PythonCourses corpus up to the CLM 1.8
validator (missing `slide_id` and DE/EN-adjacency escalated warning→error). The
work involved minting ids across ~440 bilingual decks, hand-authoring ~117 ids,
tag migration, spec-typo fixes, and triaging ~73 residual content errors.

Several steps had **no CLI support and had to be reimplemented as throwaway
Python scripts** (using `clm.core.topic_resolver.build_topic_map` +
`clm.core.course_spec.CourseSpec` directly). They recur on every course
conversion / corpus-wide validator bump, so they are good candidates for
first-class `clm` tooling. Listed high-to-low value.

---

## 1. Spec → deck resolution and reverse lookup (HIGHEST VALUE)

**What I did manually, repeatedly:** computed "which decks does a course spec
actually pull in?" by walking `build_topic_map(slides)` + `CourseSpec.from_file`
and unioning `TopicMatch.slide_files`.

**Why it matters:** my first attempt used a *deck-filename-stem* heuristic to
decide whether a deck was "shipping". That is **wrong** — a `<topic>` resolves to
a topic *directory* and CLM builds **every** `slides_*.py` in it, and the dir name
often differs from the deck filename (e.g. topic `observer` →
`slides_020_observer_advanced.py`; topic `properties` → both `slides_properties.py`
*and* `slides_property_setters.py`; topic `intro_deep_nets` →
`slides_intro_deep_nets_part1..5.py`). The heuristic silently missed 20 shipping
decks; I only caught it by switching to the resolver. A built-in command would
have prevented the mistake entirely.

**Proposed:**
- `clm spec decks <spec.xml> [--lang de|en|both] [--json]` — list the resolved
  deck file paths a spec pulls in (optionally per section/topic).
- `clm spec decks --all-specs <specs-dir>` — the union across every spec
  (the "shipping set"), with each deck annotated by which spec(s) reference it.
- `clm slides referenced-by <deck.py>` — reverse lookup: which spec/topic pulls
  this deck in (or "unreferenced").

---

## 2. Deep validation scoped to a spec / the shipping set, with a category rollup (HIGH VALUE)

**What I did manually:** `clm validate slides --kind slides --json` over the whole
tree, then a script to (a) drop `_archive`, (b) keep only decks in the shipping
set, (c) bucket findings by category, (d) separate `bilingual` vs `.de/.en`.

**Gap:** `clm validate <spec.xml>` validates only **spec structure / topic
resolution** — it does *not* deep-validate the slide content of the decks the spec
references. So "spec validates OK" does **not** mean "the decks are clean", which
is a real footgun (I nearly concluded the gate was met on that basis).

**Proposed:**
- `clm validate <spec.xml> --deep` — after resolving topics, run the full slides
  validator on each referenced deck and roll the findings up.
- `clm validate <dir-or-spec> --summary` — a category histogram
  (missing-slide_id / adjacency / cell-type-mismatch / count-mismatch / voiceover
  / start-completed / malformed-marker …) with per-deck counts, instead of a flat
  list of thousands of lines. Optionally `--shipping-only` to restrict to decks
  reachable from specs.

---

## 3. A corpus "readiness gate" orchestrator (HIGH VALUE)

**What I did manually:** the whole sequence — `assign-ids --accept-content-derived`
→ `normalize --operations tag_migration` → (sync split pairs) → re-validate →
categorize → decide mechanical-vs-author — by hand, iterating.

**Proposed:** `clm course gate <spec-or-dir> [--apply]` that runs the mechanical
passes in order and emits a **readiness report**:
- counts cleared mechanically vs remaining,
- splits remaining into *mechanically-fixable* vs *needs-author* (translation,
  structural DE/EN divergence, corrupted markers),
- names the specific decks/lines for the author bucket.

This is essentially the report I hand-built as
`docs/v18-remaining-validation-work.md` in the course repo; making it a command
would make every future validator bump a one-liner.

---

## 4. Scoping mints to part of the corpus (MEDIUM-HIGH)

**What I did manually:** to mint **only** bilingual decks (leaving `.de/.en` split
pairs for `clm slides sync`, and skipping `_archive`), I ran
`assign-ids slides --accept-content-derived` over everything and then
`git checkout` to revert the split + archive files. Clumsy and error-prone.

**Proposed flags on `assign-ids` / `normalize`:**
- `--only bilingual|split` (or `--skip-split` / `--skip-bilingual`),
- `--exclude <glob>` (e.g. `_archive`),
- `--shipping-only <specs-dir>` (only touch decks reachable from specs).

---

## 5. Hard-refusal worklist with cell context for hand-authoring (MEDIUM-HIGH)

**What I did manually:** `assign-ids --dry-run --json` gives the refused
file:line, but to actually author a good `slide_id` I needed each cell's **body +
the preceding heading/slide context**, which I extracted with a script, then
applied edits with another script.

**Proposed:**
- `assign-ids --report-refusals [--context]` — for each hard refusal, emit the
  cell marker, its body, and the nearest preceding `slide_id`/heading, so an
  agent/author can fill ids efficiently.
- An `assign-ids --interactive` mode that prompts for a slug per hard-refusal cell
  (showing context), writing both DE/EN twins at once.

---

## 6. Slug-quality report for content-derived ids (MEDIUM)

**What I did manually:** after `--accept-content-derived` minted ~3,000 ids, I
scanned for low-quality slugs (one-word like `data`/`true`/`shell`, code-identifier
derived like `cp`/`df`, mid-sentence truncations) to decide which to polish.

**Proposed:** `clm slides assign-ids --flag-low-quality` (or a `clm slides
slug-report`) listing content-derived ids that are single-token, code-identifier-
shaped, or truncated — so the author can review just those rather than all 3,000.

---

## 7. Unreferenced / orphan / cruft detection (MEDIUM)

**What I did manually:** found `.ipynb_checkpoints/` dirs, `*_old`/`*_2`/`*_partN`
decks, and classified "not referenced by any spec" vs "intentional alternate"
(length variants `_short`/`_long`, multi-part series) — the distinction matters
because blindly archiving `_part1..5` would delete real content.

**Proposed:** `clm spec orphans <specs-dir>` — list decks reachable from no spec,
grouped by likely intent (explicit `_old` = superseded; `_short`/`_long`/`_partN`
= probably intentional alternates), plus a flag to clean gitignored
`.ipynb_checkpoints/` cruft.

---

## 8. DE/EN completeness report (MEDIUM)

**What I did manually:** to separate "DE-only deck (needs translation)" from "1-cell
imbalance" among count-mismatch errors, I counted `lang="de"` vs `lang="en"` cells
per deck.

**Proposed:** `clm slides coverage-report <dir-or-spec>` — per deck: DE-only,
EN-only, balanced, or N-cell imbalance — to plan bilingual-completion / translation
work at corpus scale.

---

## 9. Assisted interleave for structurally-diverged DE/EN (MEDIUM-LOW)

**Observation:** `clm slides normalize --operations interleaving` correctly
*refuses* to auto-reorder when the DE/EN code has diverged
(`similarity_failure`), which is safe — but it leaves the author with adjacency
errors and no assistance. An `--interactive` interleave (show the DE and EN cells,
let the author confirm/adjust pairing) would help close these without risking a
bad automatic reorder.

---

## Smaller notes

- **Pair-fill awareness:** authoring a `slide_id` on one language's cell leaves the
  twin missing; `assign-ids` *does* pair-fill on a re-run, but a lint/warning
  ("you id'd one half of a DE/EN pair") would catch it sooner.
- **Tag-migration side effect:** `normalize --operations tag_migration`
  (alt→completed) can turn a clean deck into one with a `completed`-without-`start`
  error when the start/completed structure isn't cleanly DE/EN-paired. Worth a
  post-migration check or a warning.
- **`validate <spec.xml>` vs deep:** see #2 — the dispatch on input type means a
  spec only gets structural validation; this surprised me and is worth a docs note
  even if `--deep` isn't added.
