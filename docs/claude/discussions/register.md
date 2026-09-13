---
status: active
owner: maintainers
updated: 2026-09-13
review-by: 2027-03-13
---

# Register: discussions index

One row per thread; sessions numbered S1… across the tree. Gaps mean a
lost or deliberately skipped conversation (recorded below). The format and
the reasons behind it are in `README.md`; the save procedure is
`.agents/skills/save-discussion/SKILL.md`.

## Threads

| Thread | Subject | Disposition | Landed | Sessions |
|--------|---------|-------------|--------|----------|
| `cpp-ide-export` | Student-facing C++ IDE export (#928): Phase 4 verification and rollout, the owner's review of the generated output (kernel drops bare-expression output → `SHOW(expr);` everywhere; `slide_` prefix; `section_name` attribute; plain title; HTML→Markdown comments; no promotion heuristic), the lambda-crash landmine, differential check with values | Active — all follow-ups merged; owner's student-view review of four decks still open | clm #937, #938, #939, #940; CppCourses #129, #130; `docs/claude/handovers/cpp-ide-export-handover.md` | S1 |
| `agent-discussion-continuity` | Porting the save/resume-discussion stack (skills, `docs/claude/discussions/`, transcript tooling) from CppCourses/Cenotaph to this repository | Active — stack landed and in use from this save; first saves of older sessions pending | clm #941 | S1 (last owner turn of the `cpp-ide-export` transcript) |

### Dropped threads / corrections worth keeping

- The agent proposed a deterministic "promote the whole run of consecutive
  variable cells" emitter rule as a safety net for split variable groups;
  the owner rejected any such tool heuristic in favour of an explicit
  authoring rule (tag the run `global`). Do not re-propose it. (S1)
- The agent's first display helper used a lambda; xeus-cpp 0.8 crashes on
  the second lambda wrapper after new globals are defined. Any future
  helper must stay lambda-free (overloaded comma operator now). (S1)
- The owner recalled that the Python templates already drop the title slide
  from code exports; they only gate the logo — Python code exports never
  show it because they drop markdown cells entirely. (S1)
- The agent first claimed the kernel-image display gap made `#930` urgent
  as a notebook-UX problem; the owner's interactive check turned it into a
  deck convention (always `;`, `SHOW`), which dissolved #930's premise
  rather than escalating it. (S1)

## Sessions

| S | Date | Transcript | Size | Owner/agent blocks | Source | Boundary |
|---|------|-----------|------|--------------------|--------|----------|
| S1 | 2026-09-13 | `cpp-ide-export/transcripts/2026-09-13-s1.md` | 5.91 MB → 48 kchar (122:1) | 4 / 26 | Claude Code session `a57d56f4-065d-49fc-852b-06051debb22c.jsonl` (ran in slug `C--Users-tc-Programming-Python-Projects-clm--claude-worktrees-issue-928-cpp-ide-export`, moved to slug `c--Users-tc-Programming-Python-Projects-clm` when the working directory changed) | content through 2026-09-13 23:01 (Phase 4, owner review + follow-ups incl. the SHOW rollout and gate, the discussion-stack port); this save itself is covered by the two state files |

### Deliberately skipped

| Date | Session | Reason |
|------|---------|--------|
| 2026-07-26 … 2026-09-13 | 11 Claude sessions and 2 Hermes sessions listed by `python scripts/audit_transcripts.py` at the time of S1 | Predate the discussion stack; not triaged yet, not skipped for good — the #928 design sessions (2026-09-12/13, slug `…-issue-928-cpp-ide-export`) and the two long Hermes reviews of July 2026 (#704 adversarial review, performance-regression investigation) are the candidates; the raw sources still exist on the maintainer's machine |
