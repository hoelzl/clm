# DSPy-Based Slide/Voiceover Authoring — Research Assessment

## Scope of this document

A narrowly-scoped research assessment, written in response to the question
*"could a DSPy-based tool help with planning curricula, creating slide
decks, creating voiceover, and updating courses as new requirements and
novel ML solutions appear?"*. This is a **research doc, not an
implementation plan**. It decides what to try next and what to stay away
from.

It is a superset of the narrower Phase 7 DSPy spike recorded in
`voiceover-polish-levels.md` (§"Research Spike: DSPy-Based Voiceover
Pipeline"), which focuses on polish only. Where the two overlap, this
document defers to the polish-levels design; nothing here supersedes the
concrete polish pipeline described there.

## TL;DR

**Do a narrow DSPy spike on translation + polish. Do not build a
"DSPy-based authoring system" as a single thing.** The strongest asset
(~22–24k auto-aligned DE↔EN cell pairs with zero labeling effort) and the
clearest structured task (voiceover polish, already designed in
`voiceover-polish-levels.md`) are both narrow. The open-ended work — "plan
a curriculum", "improve the flow of this topic", "update the course for a
new ML technique" — is where DSPy has the lowest chance of beating a
human iterating with Claude Code.

## When DSPy fits, and when it doesn't

DSPy is a prompt + few-shot **optimizer**. It wins when three conditions
hold simultaneously:

1. **Crisp input/output contract.** The task expresses as
   `Signature(inputs) -> outputs` with typed fields.
2. **A meaningful metric.** You can score an output without a human
   reading it — at least well enough to rank candidates.
3. **Labeled data, or a cheap way to get it.** Demos for bootstrapping
   and a held-out eval set.

Scoring the tasks raised in the original question:

| Task                                                 | Contract    | Metric                                                     | Data                                                           | Verdict                               |
|------------------------------------------------------|-------------|------------------------------------------------------------|----------------------------------------------------------------|---------------------------------------|
| DE↔EN slide translation                              | Crisp       | Tractable (BLEU/chrF + LLM-as-judge for style)             | **~22k aligned pairs already in corpus, zero labeling**        | **Strong fit**                        |
| Voiceover polish (`standard`/`heavy`/`rewrite`)      | Crisp/level | Hybrid metric in `voiceover-polish-levels.md`              | ~150 curated commits (1–2 wk of review)                        | **Good fit; already planned**         |
| Bilingual sync (edit DE → mirror in EN)              | Crisp       | Semantic-equivalence + diff-size sanity                    | Minable from git edit pairs                                    | **Good fit**                          |
| Slide validation / pedagogy checks                   | Crisp       | Agreement with human reviewer (expensive labels)           | Needs hand-labeling                                            | Marginal                              |
| Authoring a new slide set from objectives            | Open-ended  | No good automated metric                                   | No clean (goals → deck) pairs in git                           | **Poor fit**                          |
| Curriculum planning / topic flow                     | Open-ended  | No metric                                                  | No data                                                        | **Poor fit**                          |
| "Update course as ML evolves"                        | Requires freshness, web search, reading papers | No metric | No data                      | **Poor fit — Claude Code + WebSearch wins**                    |

The honest pattern: DSPy helps where you'd otherwise be writing and
re-writing prompts *to hit a quality bar on a repeatable, narrow task*.
The manual pain points you named split cleanly between
**polish/translation/sync** (good DSPy targets) and **curriculum design**
(not a DSPy problem, no matter how appealing the framing).

## Corpus survey

From scanning `C:\Users\tc\Programming\Python\Courses\Own\PythonCourses\`
and its git history (786 commits):

### Strong signals

- **890 `.py` slide files**, 715 of them bilingual with **cell-level
  automatic DE↔EN alignment** via `lang="de"`/`lang="en"` tags on
  adjacent cells. No preprocessing needed for a translation training set.
- **35,105 slide cells** (`# %%` markers) across the corpus — fine-
  grained edit targets.
- **2,937 voiceover-notes cells** (`tags=["notes"]`) distributed across
  ~167 files.
- Git history has real intent-bearing commits. Concrete examples:
  - `019c8436` "Clean up voiceover in requests GET/POST video slides"
    (529 insert / 826 delete — a clean polish exemplar).
  - `5c23dbb0` "Expand voiceovers in W02 OOP intro videos" (net growth
    of 186 lines — an expansion exemplar).
  - `f9959a67` "Replace German variable names/keys with English in shared
    code cells" (9 files, 335 insert / 294 delete — a normalization
    exemplar).

### Weak signals

- ~25–30% of commits are housekeeping (`uv.lock`, line endings, chore:)
  or touch many files with mixed intent — not every commit is a clean
  training pair.
- No commits that look like "I planned a new topic from scratch" —
  because that's not a single-commit-sized operation and probably never
  will be.
- Voiceover notes are a mix of raw transcript-ish text and actually-
  edited notes, which is signal *and* noise.

### Consequence

Translation training data is effectively free (extraction script, not
annotation). Polish training data needs ~1–2 weeks of human triage to
yield ~100–150 clean `(before, after, intent)` triples. Everything else
needs hand-labeling from scratch or has no training-data story.

## What we'd realistically gain vs. prompting Claude Code

Claude Code today is already serviceable at all of these; DSPy has to beat
it, not merely exist.

- **Translation (DE↔EN):** DSPy can plausibly beat ad-hoc Claude calls
  because the compiled program gets to see dozens of your actual cell
  pairs as demos and converges on the *specific* bilingual style
  (which varies per course and narrator). Expected gain: meaningful —
  fewer post-translation edits, consistent terminology, lower cost per
  call once compiled. The "improves with user corrections" story is
  most credible here.
- **Voiceover polish:** Moderate gain. Does MIPROv2 + a hybrid metric
  beat a carefully hand-written `.md` system prompt + 3 exemplars? Best
  guess: marginally, if the metric is well-designed. **The metric is
  the project**, not the optimizer.
- **Bilingual sync:** Small but non-zero gain. Cell pairing is already
  deterministic via `slide_id` (see `src/clm/mcp/tools.py` `suggest_sync`).
  DSPy helps if you're running this often enough to justify compilation.
- **Slide authoring from scratch:** Probably no gain. The task is
  context-heavy (course outline, prerequisites, audience, existing
  style) and creative. A human iterating with Claude explores the
  space better than a compiled DSPy program.
- **Keeping courses current with new ML:** No. This needs reading
  arXiv/blog posts/library docs and synthesizing. That's the
  agent-with-tools use case, not the prompt-optimizer use case.

## Authoring surface — structured tasks that are candidates for DSPy

From mapping the existing Claude-driven authoring surface (project-level
`CLAUDE.md`, the `.claude/commands/` and `.claude/skills/` directories in
both CLM and `PythonCourses`, and the MCP tools in
`src/clm/mcp/tools.py`), the following tasks have the crispest contracts
and would be the natural candidates for DSPy signatures. Ranked by
contract clarity.

1. **`SuggestLanguageSyncSignature`** — detect asymmetric bilingual
   edits. Inputs: slide file path, source language, git diff, slide-id
   metadata. Outputs: sync suggestion list with confidence.
2. **`MirrorBilingualCellSignature`** — given an edit in DE, produce the
   semantically-equivalent EN cell (or vice versa). Respects authoring
   rules and student profile.
3. **`PolishVoiceoverSignature`** — raw voiceover cells (from Whisper or
   hand-written) + slide content + level → polished cells + coverage
   gap report. This is what the Phase 7 spike in
   `voiceover-polish-levels.md` covers.
4. **`ExtractAndLinkVoiceoverSignature`** — slide file → companion
   voiceover file with `for_slide` metadata. Largely mechanical; DSPy's
   added value is marginal vs. the existing MCP `extract_voiceover`
   tool.
5. **`ValidateSlidePedagogySignature`** — files + rules + student
   profile → critical issues + improvements + sequencing warnings.
   Tractable as classification; expensive to label.
6. **`GenerateProjectPhaseSignature`** — phase goals + referenced slides
   + student profile → bilingual `instructions.md` + `solution.md`.
   Structured output, but scarce training pairs.
7. **`AuthorSlideSetSignature`** — topic objectives + prerequisites →
   `.py` file with percent-format cells. Structured output envelope,
   but the *content* is open-ended; likely no DSPy win over Claude.

(1)–(4) are where to invest. (5)–(7) are fine as DSPy modules in
principle but have much weaker ROI and much thinner training data.

## Effort to build

Realistic estimates, assuming part-time work alongside normal CLM
development:

| Deliverable                                                                         | Effort           | What you get                                                                |
|-------------------------------------------------------------------------------------|------------------|-----------------------------------------------------------------------------|
| Voiceover polish spike (already planned Phase 7)                                    | **~1 week**      | Go/no-go with quality/latency/$ numbers                                     |
| Translation DSPy module (DE↔EN, one course)                                         | **1–2 weeks**    | Prototype + A/B vs. current Claude prompt on held-out 200 cells             |
| Bilingual sync DSPy module                                                          | 1–2 weeks on top | Replaces ad-hoc "mirror this edit" prompting                                |
| Full integration into CLM (CLI, scope-aware profiles, recompile command, CI caching)| 3–4 weeks *after* the spikes win | Production-usable `clm translate` / `clm polish --dspy`      |
| Self-improvement loop (corrections → exemplar pool → scheduled recompile)           | 2–3 weeks        | The "gets better over time" story — non-trivial: drift detection, exemplar dedup, cost control |

Polish training-data curation adds **1–2 weeks of manual review** on top
of implementation. Translation needs ~1–2 days of extraction scripting
and a held-out split; no annotation.

Total for a genuinely useful, scoped system covering translation +
polish + sync: **2–3 months of focused effort**, front-loaded with two
~1-week spikes that let us bail before committing.

## Risks

1. **DSPy API churn.** It's still moving. Version pinning and upgrade
   pain is a real tax.
2. **Compiled program opacity.** Trainers who today edit
   `polish_levels/standard.md` to change tone will need to run
   `clm voiceover recompile` instead. Telegraph this workflow change in
   docs.
3. **Metric design is the whole game.** If we don't invest real effort
   in the metric, DSPy reduces to "a slower way to call an LLM with
   demos." Budget metric work explicitly.
4. **Temptation to over-scope.** The framing "a tool for planning
   curricula and lectures, creating slide decks, creating voiceover,
   updating courses…" is exactly the scope that makes DSPy projects
   fail. Pick one task, ship it, then add the next.
5. **Judge-model leakage.** If the LLM-as-judge is the same model family
   doing generation, we overfit to its preferences. Use a different
   judge model than the generator.
6. **Non-determinism in CI.** DSPy optimizers are non-deterministic by
   default. Seeded compiles, cached compile artifacts under
   `.clm/polish-programs/`, and explicit `clm voiceover recompile`
   command — not something that runs on every sync. (Same conclusion
   as `voiceover-polish-levels.md`.)

## Recommendation

**Step 1** — Run the Phase 7 voiceover polish spike already designed in
`voiceover-polish-levels.md` (§"Research Spike"). One week, hard
go/no-go, cheapest way to learn whether DSPy is worth anything for this
codebase.

**Step 2** — **If polish wins**, add a translation DSPy module next, not
authoring. Translation has free training data, a tractable metric, and
clearly-bounded output. It's also where repetitive work compounds.

**Step 3** — Leave curriculum planning, topic flow, and new-material
drafting to Claude Code. Invest instead in improving the Claude-Code
side: tighter skill prompts, better MCP tools (the MCP slide tooling in
`src/clm/mcp/tools.py` already does a lot of the right work), and
better authoring-rules files in `PythonCourses/.claude/docs/` and the
per-course `*.authoring.md` companions. That's where creative work
actually happens, and no optimizer will out-ideate a human-in-the-loop
agent on open-ended work.

**Do not** start by building "a DSPy-based tool for CLM." That framing
commits to a framework before validating the framework pays off on any
single task.

## Decision gates

- **Gate A (end of polish spike)**: quality delta, latency delta, $ per
  sync on a held-out set of 10 videos. Bail if quality delta ≤ the
  hand-crafted Phase 2 design at equal or higher cost.
- **Gate B (end of translation prototype)**: edit rate on translated
  cells in an unseen course, compared to current Claude-prompt output.
  Bail if edit rate is not materially lower.
- **Gate C (before full integration)**: has at least one of the two
  spikes produced a compiled program that beats Claude-Code prompting
  on its task *and* ships with a reproducible metric? If no, close out
  with the spike reports as the deliverable and move on.

## Non-goals

- A DSPy module covering curriculum planning, topic-flow improvement,
  or new-material generation.
- Fine-tuning a dedicated model. Few-shot + DSPy optimization is the
  ceiling we're evaluating; model ownership is a support burden we
  don't want.
- Replacing the existing MCP slide tooling. DSPy modules sit *alongside*
  those deterministic tools, not on top of them.

## Related documents

- `docs/claude/design/voiceover-polish-levels.md` — the concrete design
  this doc defers to for anything polish-specific. Phase 7 there is the
  first spike this doc recommends.
- `docs/claude/design/mcp-server-and-slide-tooling.md` — the deterministic
  authoring surface DSPy modules would plug into.
- `docs/claude/design/mcp-server-implementation-design.md` — MCP server
  architecture.
