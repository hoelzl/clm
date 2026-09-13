---
name: save-knowledge
description: Preserve durable CLM session findings at their canonical homes before saving a discussion or ending an increment.
version: 1.0.0
metadata:
  hermes:
    tags: [clm, knowledge, continuity]
    related_skills: [save-discussion]
---

# Save what a CLM session learned

Audit what the session taught against where the next agent will encounter it.
Run at a finished-increment boundary, before `save-discussion` when the
session established durable knowledge, or when explicitly invoked.

Use the active harness's file, search and terminal tools; no particular slash
command implementation is required. Paths below are relative to this checkout.

## Re-ground

Read `AGENTS.md` (especially *Documentation Map*, *Document Placement* and
the *Info Topics Maintenance Rule*), the handover of the feature you worked
on (`docs/claude/handovers/<feature>-handover.md`) and `docs/claude/TODO.md`.
Inspect live Git state and fetch before changing shared records; re-read each
target immediately before editing. Follow the repository workflow for edits,
verification and publication (worktree branch off `origin/master`, PR,
CI-gated auto-merge — `.claude/skills/ship-a-pr`). Only inspect or curate
your own harness's private state (for Claude Code, the memory directory
under `~/.claude/projects/<slug>/memory/`), never another's.

## Pass 1 — what do the consuming documents now say falsely?

Search the subject and the OLD values/identities, then read the surrounding
claim. A keyword hit alone does not establish a contradiction. Look for:

- problems still described as unsolved after this session solved them
  (`docs/claude/TODO.md`, an open `gh issue`, a handover's *Open / deferred*
  list);
- obligations discharged by later work but still assigned to a successor in
  a handover's *Next Steps*, or a phase status line that is behind;
- procedures in `docs/developer-guide/`, `docs/user-guide/`,
  `.agents/skills/` or `.claude/skills/` whose commands, flags,
  prerequisites, output lists or gates are stale;
- a CLI flag, spec element or build behaviour that changed without its
  `src/clm/cli/info_topics/*.md` topic following (downstream course repos
  read `clm info` and will produce wrong output otherwise);
- canonical claims in `AGENTS.md` that the session falsified;
- stale progress claims in your own private memory, which should point at
  live repository/tracker state instead of restating it.

Repair the consuming record, not only the document produced by this session.
Do not rewrite historical transcripts, archived handovers
(`*-handover-archive.md`) or `docs/archive/` material to make them sound
current.

## Pass 2 — what did we establish, and where does each finding live?

**Enumerate first, then search.** List measured constraints and their sample
scope (corpus sizes, timings, flake rates), mechanisms found, mechanisms
still unknown, falsified hypotheses, rejected alternatives and reasons, tool
traps, owner decisions, and residuals being tolerated. For each, search for
the specific finding, not just its topic. Distinguish observation, inference
and unresolved mechanism explicitly.

Ask: could an agent with no session memory encounter this answer before
repeating the mistake? A discussion transcript preserves the conversation; it
does not substitute for a test, procedure or decision at its canonical home.
A commit body can be the right home for why this particular diff is shaped as
it is; do not duplicate that reason into a doc merely to satisfy this audit.

### Route by this repository's existing policy

| Finding | Canonical destination |
| --- | --- |
| Code or build behaviour that can be pinned | A test — and a build record or docstring that claims a path is wired or secure must cite the test that proves it (the *claimed-wired rule*, `docs/developer-guide/testing.md`) |
| A trap best encountered at the edit site | A comment there, explaining why rather than restating code |
| A CLI command, flag, spec element or build behaviour visible to course repositories | The matching `src/clm/cli/info_topics/*.md` (`commands.md`, `spec-files.md`, `slide-format.md`, `migration.md`) — `{version}` placeholders, never a hardcoded version |
| A user-facing change worth a release note | A `changelog.d/<pr-or-issue>-<slug>.<type>.md` fragment, never `CHANGELOG.md`'s `[Unreleased]` section |
| Architecture, caching, testing, release or logging procedure | `docs/developer-guide/` (`architecture.md`, `caching.md`, `testing.md`, `releasing.md`) |
| Installation, configuration, troubleshooting for users | `docs/user-guide/` |
| A design decision and its rationale | `docs/claude/design/<topic>.md`, or the *Design Decisions* section of the feature's handover |
| Active multi-phase feature state, landmines, next steps | `docs/claude/handovers/<feature>-handover.md` (retire finished phases to `<feature>-handover-archive.md`) |
| An investigation or cross-session analysis | `docs/claude/<topic>-investigation.md` (or `docs/claude/analysis/`) |
| An open bug, missing capability or verify-then-close item | `gh issue` in `hoelzl/clm`, or `docs/claude/TODO.md` when it is not yet issue-shaped; course-content defects go to the course repository |
| A reusable agent procedure | A repository-owned `.agents/skills/` skill with a `.claude/skills/` adapter; Claude-Code-only quirks in `.claude/skills/` |
| Session-start orientation every agent needs | `AGENTS.md` (`CLAUDE.md` only imports it) |
| A volatile fact (version, flag list, issue number, CI check list) | No document — it lives behind a live tool (`clm --version`, `clm info commands`, `gh …`, `.github/workflows/`); prose only points there |
| Conversational texture, dropped threads, agent corrections | `save-discussion`, after this audit returns |
| Private harness quirks and pointers | That harness's private memory, without copying private paths, raw sessions or secrets into Git |

Do not create a general-purpose knowledge dump that hand-describes what code,
a test or a live tool already answers. Give each finding one home plus
pointers. Measurement evidence must retain source, scope and a way to
reproduce it (the scratch script, the corpus, the log directory); an
inference must not become a measured fact merely by moving into a durable
document.

## Repair and verify

Make the smallest coherent repairs. Run the checks affected by the actual
edits: the affected `pytest` files after touching tests or code;
`uv run ruff check src/ tests/` and `uv run lint-imports` after touching
source; `python scripts/check_doc_currency.py` after touching
`docs/claude/discussions/`; `clm info <topic>` renders the topic you edited.
Do not call a prose assertion verified behaviour.

If a finding cannot be captured safely within this increment, record the gap,
evidence, reason and next action in the appropriate handover or issue instead
of inventing a test result or silently dropping it. A contradiction needing an
owner ruling is not permission to make that ruling yourself.

## Finish and return

Report the findings with canonical destinations, repairs, actual check
results and unresolved gaps. "No new durable knowledge" or "already captured"
is a valid result; repeated saves should not create duplicate entries.
This completeness assessment is a human/agent checkpoint, not a mechanical
gate that can prove no knowledge was lost.

Commit with explicit paths when authorized under the repository workflow.
Return to the caller: **do not invoke `save-discussion` recursively**. When
called from that skill, continue the pending save so it records the audit's
outcome. When called standalone, offer a discussion save only if
conversational context also needs preserving.
