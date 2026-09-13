---
name: save-knowledge
description: Preserve durable session findings at their canonical CLM homes (info topics, handovers, developer guide, tests, changelog fragments, issues) before saving a discussion or ending an increment. Use when a session established knowledge the next agent will need.
---

# Claude Code adapter

Follow `.agents/skills/save-knowledge/SKILL.md` in full. It is the canonical,
harness-neutral procedure. This adapter only preserves the `/save-knowledge`
invocation name in Claude Code and introduces no separate policy.

The private state the procedure lets you curate is Claude Code's memory
directory for this project (`~/.claude/projects/<slug>/memory/`, indexed by
its `MEMORY.md`): pointers and landmines, never a durable fact that is not
also at its repository home.
