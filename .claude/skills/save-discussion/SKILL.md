---
name: save-discussion
description: Save the current conversation as a resumable discussion under docs/claude/discussions/ — clean the session transcript, update the thread's state, and append to the register. Use when a design or planning conversation is ending, or before it risks compaction.
---

# Claude Code adapter

Follow `.agents/skills/save-discussion/SKILL.md` in full. It is the canonical,
harness-neutral procedure. This adapter only preserves the `/save-discussion`
invocation name in Claude Code and introduces no separate policy.

Claude Code specifics that the canonical procedure relies on: most sessions
here run from a git worktree, so the session file lives under the worktree's
project slug (`python scripts/audit_transcripts.py` prints it), and the save
is shipped like any docs change — branch off `origin/master`, push, PR with
"Refs", CI-gated auto-merge (`/ship-a-pr`).
