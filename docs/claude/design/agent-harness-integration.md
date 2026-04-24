# CLM as a Specialized Agent: Should we integrate into an LLM harness?

**Status:** Discussion draft — not a decision
**Date:** 2026-04-24
**Context:** CLM keeps growing LLM-dependent features (translation, voiceover
merge/port/compare/propagate, polish, summarize). The CLI is increasingly
awkward for those workflows. Is re-architecting around an agent harness a
better fit than the current `clm <verb>` CLI?

This report answers the five questions you raised, notes additional questions
that surfaced during the investigation, and is structured so we can work
through it section by section.

---

## 1. Is this worth pursuing?

**Short answer: Partially — yes for the LLM-heavy workflows, no as a wholesale
replacement of the CLI.**

The evidence that CLM has crossed the threshold into "agent-shaped" territory
is strong:

- **Seven distinct LLM touchpoints already exist:** `summarize`, `polish`,
  `voiceover sync`, `voiceover port-voiceover`, `voiceover compare`,
  `voiceover propagate-to`, and training-data extraction (see
  `src/clm/voiceover/{merge,port,compare}.py`, `src/clm/notebooks/polish.py`,
  `src/clm/cli/commands/{summarize,polish,voiceover}.py`,
  `src/clm/infrastructure/llm/client.py`). Every one of them produces
  structured JSON that code then interprets — i.e. they're already
  tool-use-shaped, not chat-shaped.

- **Ambiguity and retry loops are hardcoded:** `merge_batch()` falls back to
  per-slide on JSON parse failure; `polish_and_port()` falls back to baseline
  on LLM failure; users have no lever to guide the retry. This is the
  canonical pattern an agent loop handles better than a fixed pipeline.

- **Composition is manual:** a full "record → transcribe → identify → sync →
  propagate → compare" flow is 5–6 separate CLI invocations today, with the
  user moving outputs between them. That's the exact shape an agent replaces
  well.

- **The MCP surface already exists.** `clm.mcp` exposes 19 tools
  (resolve_topic, validate_spec, validate_slides, voiceover_compare,
  voiceover_backfill_dry, etc. — see `src/clm/mcp/tools.py`). Roughly 60% of
  the read/inspect path is already agent-callable. The unfinished work is
  mutation tools, orchestration skills, and a packaging story.

**Where the cost/benefit flips the other way:**

- **Batch build pipelines** (the core of CLM — `clm build`, workers, job
  queue, notebook/plantuml/drawio execution) are deterministic, performance-
  sensitive, and run headless in CI. An agent loop over these would be
  slower, more expensive, and less reliable. They should stay pure CLI.
- **The CLI is load-bearing for CI, pre-commit, IDE integrations.** Removing
  it is not on the table; the question is only whether to *add* an agent
  layer on top.

So the right framing is not "replace CLI with agent harness" but **"layer an
agent harness on top of the existing CLI + MCP server to provide a new,
optional, LLM-friendly front end for the LLM-heavy workflows."** That is
worth pursuing.

---

## 2. Which parts of CLM would benefit?

Ranked by expected ROI:

### Strongly benefit
1. **Voiceover pipeline end-to-end.** This is the clearest fit: ambiguous
   inputs, multi-step workflow, JSON retries, language propagation, compare-
   then-backfill decisions. Users routinely say "this slide went wrong, what
   do I do?" and today the answer is "read trace logs and pick from six
   flags." An agent can read the trace, re-run with different parameters,
   and propose a patch.
2. **Translation / bilingual sync.** Today `clm slides suggest-sync` and
   `clm voiceover sync --propagate-to` are mechanical — they detect
   mismatches but don't fix them. An agent can read both language variants,
   decide the target structure, apply the translation using the existing
   `propagate_*` prompts, and verify.
3. **Spec/slide validation + repair.** `clm validate-spec` and
   `clm validate-slides` surface diagnostics; the agent-shaped step is
   "given this diagnostic, suggest a fix and write it." Today the human
   closes that loop.
4. **Polish / notes editorial.** `clm polish` is already one-shot LLM; the
   benefit is turning it into "polish this slide, I want the notes tighter
   and more formal" — natural-language knobs instead of hardcoded prompts.

### Moderately benefit
5. **Recording workflow guidance.** "Is my OBS recording complete? What's
   next?" — heuristics today, good LLM-judgment territory. Dashboard UX
   might be the better home than an agent, though.
6. **Topic resolution / fuzzy search.** `resolve_topic` is purely lexical;
   a semantic fallback would help when users misremember topic IDs.
7. **Authoring rule violations.** Today `clm authoring-rules` surfaces rule
   hits; the agent-shaped step is explaining *why* a rule fired and
   proposing a conforming rewrite.

### Do not benefit (keep pure CLI / workers)
8. Notebook/PlantUML/DrawIO workers, job queue, SQLite state machine, the
   `build` verb, pre-commit and CI hooks. These are deterministic, need to
   be fast and reproducible, and are already well-architected.

---

## 3. What would this look like from a user perspective?

Three user personas, three distinct UX paths:

### Persona A: Course author, working interactively
**Today:**
```
> clm voiceover transcribe video.mp4 --output transcript.json
> clm voiceover sync transcript.json slide.py --propagate-to en
> clm voiceover compare slide.py@HEAD~1 slide.py --format markdown > diff.md
> # read diff.md, decide what to fix, maybe re-run
```
**With agent harness:**
```
> claude
> /clm:voiceover-sync video.mp4 slide.py
Claude: I'll transcribe the video, merge it into slide.py, propagate to
EN, and show you what changed. Ready?
...
Claude: Sync produced 3 rewrites and 2 dropped bullets. Let me show you
the most surprising one on slide 14 — the transcript contradicts the
existing notes about "async generators". Should I rewrite, keep, or open
the slide for manual editing?
> rewrite
Claude: Done. EN propagation also applied. Changes in slide.py@HEAD.
```
The value here is **conversational iteration on ambiguous results** — today
the user has to invoke `compare`, read JSON, and re-run manually.

### Persona B: Course author, working offline / batched
**Today:** cron/Makefile invoking `clm voiceover sync` over all the day's
recordings, hoping nothing goes wrong, reading trace logs after.
**With agent harness:** same Makefile — the CLI doesn't go away. If the
agent wrapper exists, it's optional, and for batch the CLI is simpler.

### Persona C: Downstream automation (downstream course repos, CI)
**Today:** `clm info`, `clm validate-spec`, `clm build` in CI scripts.
**With agent harness:** unchanged. CI doesn't want an agent in the loop.
However, *authoring-time* work in the course repo ("I just edited a spec,
do I need to regenerate anything?") is a good fit for an agent running
inside the course repo's Claude Code session via the CLM plugin.

**Key UX insight:** the agent harness is a *new front door*, not the only
door. Power users and CI keep the CLI; authors doing creative/editorial
work get the agent.

---

## 4. Advantages and disadvantages

### Advantages

- **Dynamic composition.** The agent can run `transcribe → sync → compare`
  and branch based on results (empty baseline → port from prior rev;
  contradictions detected → ask user). Today the user is the controller.
- **Natural-language parameters.** "Be strict about factual contradictions"
  or "polish more aggressively" currently require code changes; an agent
  can route them to prompt adjustments.
- **Better diagnostics.** When merge fails JSON parsing, an agent can
  inspect the trace, propose a fix (different model? smaller batch?
  reformatted prompt?), and retry. Today the user reads logs.
- **Context preservation across a task.** An agent keeps the spec, the
  slide, the transcript, and the trace all in context; the human sees a
  synthesized summary instead of raw JSON.
- **Reuses the existing MCP server and CLI.** We're not rewriting the
  build; we're adding a thin layer. Risk is localized.
- **Already aligned with industry direction.** MCP is now the common
  substrate between Claude Code, Hermes Agent, Cursor, ChatGPT, etc.
  Investing in `clm.mcp` pays off across harnesses.

### Disadvantages

- **Added cost per operation.** LLM reasoning about which CLI flag to use
  can be several API calls where today there were zero. For every-day
  "just rebuild this one course", the CLI is cheaper and faster.
- **Reliability regression.** Agents make judgment errors that the
  deterministic CLI can't. We must be careful to keep irreversible
  operations (writes to .py slide files, DB state) gated behind explicit
  confirmation.
- **Second system to maintain.** Skills, subagents, plugin packaging, MCP
  tool surfaces, documentation — all new. `clm info` topics must expand.
- **Harness lock-in risk.** If we build deeply into Claude Code's subagent
  system or Hermes' skill system, we're tied to that harness's lifecycle.
  MCP mitigates this; skills/subagents don't.
- **User support complexity.** "Why did CLM do X?" now has three possible
  answers: CLI bug, MCP tool bug, or LLM reasoned wrongly. Trace logs
  already help here (`.clm/voiceover-traces/`) but we'd need more.
- **Windows-first friction.** Both harnesses work on Windows, but plugin
  install paths, shell-escaping, and MCP server launch scripts tend to
  assume Unix. Extra QA load.
- **Cost attribution.** If the agent is running as "CLM", whose API key
  pays for the reasoning? `CLM_LLM_MODEL` today is explicit; agent-layer
  usage is implicit and can surprise users on their bills.

---

## 5. Architecture

### Option A (recommended starting point): MCP + plugin for Claude Code

Keep `clm.mcp` as the authoritative tool surface. Add a `clm[agent]` extra
that ships a Claude Code plugin next to the Python package:

```
clm/
├── src/clm/                     # unchanged Python package
│   ├── core/ infrastructure/ workers/ cli/
│   ├── mcp/                      # existing MCP server — grow mutation tools
│   ├── voiceover/ recordings/ ...
├── .claude-plugin/               # new
│   ├── plugin.json
│   ├── skills/
│   │   ├── voiceover-sync/SKILL.md
│   │   ├── translate-slides/SKILL.md
│   │   ├── polish-notes/SKILL.md
│   │   ├── validate-course/SKILL.md
│   │   └── fix-authoring-rule/SKILL.md
│   ├── agents/                   # specialized subagents
│   │   ├── clm-validator/        # Haiku, read-only spec+slide tools
│   │   ├── clm-translator/       # Opus, propagation + MCP write tools
│   │   └── clm-voiceover/        # Sonnet, voiceover pipeline tools
│   └── mcp.json                  # declares the `clm` MCP server
```

Responsibilities:

- **MCP server** owns the tool boundary: `voiceover_merge`, `voiceover_port`,
  `voiceover_compare`, `voiceover_propagate`, `slide_read`, `slide_write`,
  `spec_validate`, etc. Today's 19 read-mostly tools grow to ~30 with
  mutation variants that take explicit "apply=true" flags (mirroring
  `--dry-run`).
- **Skills** are prompt playbooks: "when the user asks to sync voiceover,
  follow steps 1–5 using `voiceover_*` tools, ask for confirmation before
  applying". They're Markdown + frontmatter and ship with the plugin.
- **Subagents** handle context-heavy or risk-differentiated work:
  validator runs on cheap Haiku with read-only tools; translator runs on
  Opus with mutation tools and needs explicit confirmation.
- **CLI** is unchanged. Skills and subagents *call* the CLI/MCP; they
  don't replace it. Batch and CI users ignore the plugin entirely.

Claude Code discovers the plugin via `.claude/settings.json` in a course
repo or in the user's home. Course repos already have `.claude/`
directories — the CLM plugin slots in naturally.

### Option B: Hermes Agent plugin (secondary target)

Hermes Agent's architecture is strikingly similar — Skills + Tools +
Plugins, with MCP client/server support. A Hermes integration would
reuse the MCP server unchanged and re-author the skills in Hermes' format
(agentskills.io). Advantages:

- Hermes' *autonomous skill creation* loop ("after a complex task, agent
  writes a skill") could generate CLM workflow skills from user activity —
  interesting for course authors who don't want to learn our CLI at all.
- Hermes runs over Telegram/Slack/email/etc., which is attractive for
  "notify me when Auphonic is done processing".
- Hermes' subagent model (isolated Python RPC scripts) fits batch
  voiceover processing well.

But Hermes is a newer, smaller ecosystem, so it should be a second target
after Claude Code proves the architecture.

### Option C: Claude Agent SDK / Managed Agents (batch)

For nightly batch work ("translate all 50 courses", "validate all specs"),
a Managed Agent backed by the MCP server is compelling: parallel sessions,
sandboxed containers, no local state. But we don't have a user-identified
need for this yet; defer until demand is concrete.

### Option D (not recommended): full rewrite as SDK-based agent

Writing a bespoke agent loop on top of the Anthropic SDK and dropping the
CLI. Too big, too risky, loses headless/CI use cases, and the existing
four-layer architecture (core/infra/workers/cli) is working well.

### Data flow (Option A, typical voiceover sync)

```
 author in course repo
     │
     ▼  (prompt)
 Claude Code session
     │
     │  matches SKILL voiceover-sync
     ▼
 /clm:voiceover-sync skill prompt loaded
     │
     │  delegates to subagent `clm-voiceover`
     ▼
 clm-voiceover subagent (Sonnet)
     │
     │  MCP tool calls to `clm` server
     ▼
 clm.mcp.server (subprocess)
     │
     │  calls into Python library
     ▼
 clm.voiceover.merge / compare / port
     │
     │  calls LLM via clm.infrastructure.llm.client (OpenRouter)
     ▼
 result → subagent → skill → author
```

Two LLM calls per step (one at the harness level for reasoning, one inside
`clm.voiceover` for the actual merge). The inside-library call keeps
determinism where it matters (fixed prompts, structured outputs); the
outside call handles orchestration.

### Migration stance

- No breaking CLI changes. `clm voiceover sync` keeps working.
- `clm info` grows a new topic (`clm info agent`) describing the plugin.
- Trace log schema gets a documented public contract (currently internal
  in `.clm/voiceover-traces/`), because agents will read it.
- Mutation MCP tools land incrementally; first ones are the voiceover
  mutations (`voiceover_merge_apply`, `voiceover_port_apply`).

---

## 6. Additional questions that surfaced during investigation

These aren't in your original five but matter for any decision:

1. **Where should LLM calls happen — inside `clm.voiceover.merge` or inside
   the agent harness?** Today it's inside the library (fixed prompts,
   structured JSON, deterministic-ish). If the agent also reasons, we have
   two models in the loop. Cheaper and more predictable to keep LLM calls
   inside the library and have the agent only orchestrate; but it forgoes
   "natural-language parameter" benefits. **Probably: keep library calls
   for structural work (merge, port, compare schemas), let the agent
   control strictness/tone via a small prompt-adjustment surface.**

2. **How do we handle API-key/cost attribution?** `CLM_LLM_MODEL` is the
   user's key today. If an agent harness also uses LLMs on the user's
   behalf via Claude Code's session, the user sees two line items on their
   bill. We should document this and ideally let users route both through
   the same provider.

3. **Does the mutation gate belong in MCP or in the skill?** Today mutating
   operations are CLI-only by design (see the memory note on MCP's safety
   boundary). If we open mutations to MCP, we need a clear confirmation
   protocol — likely "dry-run + diff + confirmation token" pattern.

4. **What's the test story?** The LLM-library tests mock OpenAI; the agent
   layer can't easily be mocked the same way. We likely need "scripted
   agent" fixtures — canned responses that a test harness replays against
   the skill/subagent. This is non-trivial and worth scoping before
   committing.

5. **Does the Hermes learning loop conflict with our deterministic-build
   philosophy?** Hermes auto-creates skills from user activity. For
   authoring, that's fine. For build/CI, we emphatically don't want an
   agent learning new "skills" it applies silently. If we ship a Hermes
   plugin, we need to disable auto-skill-creation in CLM's subagents.

6. **Do we document agent workflows in `clm info <topic>`?** Downstream
   course repos rely on `clm info` for canonical docs (see CLAUDE.md's
   "Info Topics Maintenance Rule"). If agent workflows become part of
   the product, `clm info agent` must exist and stay version-accurate.

7. **Where does the voiceover prompt catalog live?** Today prompts are in
   `src/clm/voiceover/prompts/*.md` (merge_en, port_de, propagate_en_to_de,
   etc.). For skills to tune tone/strictness, they need a public prompt
   interface — probably a small template layer with variables.

8. **Plugin distribution: marketplace or GitHub?** Anthropic's plugin
   marketplace requires review; GitHub is immediate. For a Windows-first
   tool with a modest user base, GitHub + `clm` docs pointing to
   `/plugin install <url>` is pragmatic.

9. **Is there a risk we fork the `clm.mcp` audience?** Today the MCP server
   is "whoever wants CLM tools in their agent." If we bundle with a
   Claude-Code-specific plugin, we risk implying MCP-only consumers are
   second-class. Keep `clm mcp-server` and the tool docs as the primary
   contract.

10. **What's the minimum viable experiment?** Before building a plugin,
    ship one skill (`/clm:voiceover-fix` — runs compare, reads trace,
    suggests fix) as a local `.claude/skills/` file in a single course
    repo and see whether users actually reach for it vs the CLI.

---

## 7. Recommendation

**Phase 0 (1-2 days, low risk):** Write one skill — `/clm:voiceover-fix` —
that calls existing MCP read tools and proposes a plan. Ship it in one
course repo's `.claude/` directory. Observe whether it gets used.

**Phase 1 (1-2 weeks):** Build the `clm[agent]` plugin with 3–4 skills
(voiceover-sync, translate-slides, polish-notes, validate-course) and 2
subagents (validator, voiceover). Grow MCP mutation tools only as needed
by these skills (dry-run-first, confirmation gate). Document in
`docs/claude/design/agent-harness-integration.md` (this file) and
`clm info agent`.

**Phase 2 (optional, demand-driven):** Hermes plugin reusing the MCP
server; Managed Agents batch runner for translate-all/validate-all.

**Not doing:** replacing the CLI, running every `clm` invocation through
an agent, or betting on a single harness. The CLI and MCP server remain
the canonical contracts.

---

## 8. Points for discussion

- Is the recommended scope (skills + subagents + MCP, not replace CLI) the
  right ambition level, or do you want to go further (e.g. make the agent
  the default authoring interface)?
- How much prompt surface do we want to expose for tuning? (strictness
  dial? tone dial? free-form "instructions"?)
- Which harness target goes first — Claude Code (bigger ecosystem) or
  Hermes Agent (better fit for learning-over-time course authoring)?
- Are mutation MCP tools OK now, or do we want to keep the "MCP is
  read-mostly" safety boundary a while longer?
- Do we want the agent integration to be a separate package
  (`clm-agent`) or an extra (`clm[agent]`)?
