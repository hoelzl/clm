---
status: active
owner: maintainers
updated: 2026-09-13
review-by: 2027-03-13
---

# State: agent discussion continuity in this repository

The `save-discussion` / `save-knowledge` / `resume-discussion` stack, ported
from CppCourses and Cenotaph in session S1 (2026-09-13, the same session as
the `cpp-ide-export` thread — its transcript is
`cpp-ide-export/transcripts/2026-09-13-s1.md`, last owner turn onward).
Landed as clm #941.

## Settled

- The owner asked for a conversion, not a copy: the stack follows this
  repository's layout (`docs/claude/discussions/` next to
  `docs/claude/handovers/`; canonical `.agents/skills/` with `.claude/skills/`
  adapters; tools in `scripts/`), its knowledge venues (`AGENTS.md`'s
  Documentation Map / Document Placement, `clm info` topics, `changelog.d/`,
  tests under the claimed-wired rule) and its shipping workflow (worktree
  branch off `origin/master`, "Refs", CI-gated auto-merge).
- Most clm sessions run from a git worktree, each with its own Claude
  project slug, and the root slug exists on the maintainer's machine with
  both drive-letter cases: `scripts/audit_transcripts.py` covers the main
  checkout's slug and every `.claude/worktrees/<name>` slug,
  case-insensitively, from whichever checkout it runs.
- The doc-currency header convention applies only to
  `docs/claude/discussions/`; the rest of `docs/` does not use it, so
  `scripts/check_doc_currency.py` is scoped there and is not a CI gate.
- Claude Code needs no staging hook (its session files are the staged copy);
  the Hermes hook (`stage_hermes_session.py`) is available but not wired
  into any profile yet.

## Grounding facts

- When a session's working directory changes from a worktree to the main
  checkout, Claude Code moves the session file to the root slug; the cleaner
  must be pointed at the file that now holds the whole session (the audit
  shows where it is).
- The Bash tool of Claude Code collapses `\\` in heredocs: a Python patch
  script with `"\n"` in its anchors silently mismatches. Write patch scripts
  with the Write tool, then run them.

## Open

- The first saves. The audit lists 11 further Claude sessions (three
  #928 design/implementation sessions of 2026-09-12/13, the sync-engine and
  cassette conversations of August) and two long Hermes reviews from July
  2026 (#704 adversarial review, the performance-regression investigation)
  that no register mentions. They predate the stack; save the ones worth
  resuming from the raw sources, record the rest as deliberately skipped.
- Wiring `stage_hermes_session.py` into the Hermes profile config, if
  Hermes sessions on this repository continue.

## Next conversational boundary

None pending; the stack is in use as of this save (S1).
