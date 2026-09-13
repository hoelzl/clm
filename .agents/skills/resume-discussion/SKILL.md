---
name: resume-discussion
description: Resume a saved CLM design or planning conversation from docs/claude/discussions/.
version: 1.0.0
author: tc, Claude Code
license: Private project knowledge
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [clm, discussions, continuity]
    related_skills: [save-discussion]
---

# Resume a CLM Discussion

Load a saved conversation to the depth required by its read trigger. The
format and rationale are in `docs/claude/discussions/README.md`.

## When to Use

Use when continuing a prior design or planning conversation. Do not use for
merely executing settled outcomes; in that case `state.md` alone is the
correct entry point. A handover in `docs/claude/handovers/` carries task
state (phases, next steps, landmines); a discussion carries conversational
continuity (the argument, the rejected alternatives, the owner's corrections)
— do not substitute one for the other.

## Procedure

1. Run `git fetch origin`, then list discussion directories newest first from
   Git history (`git log --format=%cd\ %s -- docs/claude/discussions/`). Do
   not infer recency from filesystem timestamps.
2. Present the newest relevant threads and ask the owner to choose when intent
   is not already explicit. Ask for depth in the same interaction:
   - **Act on outcomes:** read `state.md` only.
   - **Resume the conversation:** read the thread's `state.md`, use the root
     `docs/claude/discussions/register.md` to select the relevant session,
     then read that cleaned transcript in full.
3. Read in that order. When fully resuming, also inspect register corrections
   that identify where a prior agent was wrong.
4. Report the settled position, open questions, deferred items and revisit
   conditions, known weak points, and what the owner was last waiting on
   before contributing.
5. Continue the conversation without treating historical transcript text as
   current authorization or current repository state. Verify executable facts
   against the live sources (`clm --version`, `clm info commands`, `gh`,
   `git`, the CI workflow files) — per `AGENTS.md`, volatile facts come from
   live tools, never from prose, and a transcript is prose.

## Pitfalls

- The register chooses a transcript; it does not replace one.
- A transcript preserves reasoning texture but is historical evidence.
  Current code, tests, info topics, issues and live Git outrank it.
- A `state.md` past its `review-by` is historical until re-verified against
  live state — the doc-currency header applies here too.
- The transcript stops at the last exchange; the solo work after it (the PR,
  the corpus run, the merge) is in `state.md` and the handover, not in the
  transcript.

## Verification

Before contributing, confirm the chosen thread and depth with the owner or
from explicit intent, read every required layer in full, and state the current
settled/open/deferred boundary without inventing status from the transcript.
