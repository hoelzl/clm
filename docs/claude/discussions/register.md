---
status: active
owner: maintainers
updated: 2026-09-19
review-by: 2027-03-19
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
| `agent-toolkit-candidates` | Which clm features should stop being stand-alone and become agent toolkits (the sync / release / harvest shape): survey of embedded-LLM paths, human-only output, and CLI-first affordances; three tiers filed as issues | Active — investigation and issues landed; work starts with the shared kit (#959) | umbrella clm #970, children #959–#969, `docs/claude/agent-toolkit-candidates-investigation.md` (PR #971) | S2 |

### Dropped threads / corrections worth keeping

- The owner recalled video transcription as "mostly a stand-alone feature";
  the survey showed `clm harvest` is already agent-first (#546) and only its
  revision-history family (`port`/`compare`/`backfill`/`sync-at-rev`) and the
  heuristic alignment decisions are not. Do not re-propose converting the
  curate/translate loop. (S2)
- Two subagent hand-back reports arrived as user-role rows and were cleaned
  as Owner turns on the first pass; they were removed from a temporary
  source copy (both the `user` row and its `queue-operation` twin) before the
  transcript was re-cleaned. Their substance lives in the investigation doc,
  not in the dialogue. (S2)

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
| S2 | 2026-09-19 | `agent-toolkit-candidates/transcripts/2026-09-19-s2.md` | 1.15 MB → 14 kchar (96:1, after removing two subagent hand-back rows from a temporary source copy) | 2 / 13 | Claude Code session `f666c194-70c2-4d3d-ab31-103a52e938ea.jsonl` (slug `c--Users-tc-Programming-Python-Projects-clm`, main checkout) | content through 2026-09-19 01:39 (the survey, the assessment, the doc, issues #959–#970, PR #971); this save itself is covered by the state file |

### Deliberately skipped

| Date | Session | Reason |
|------|---------|--------|
| 2026-07-26 … 2026-09-13 | 11 Claude sessions and 2 Hermes sessions listed by `python scripts/audit_transcripts.py` at the time of S1 | Predate the discussion stack; not triaged yet, not skipped for good — the #928 design sessions (2026-09-12/13, slug `…-issue-928-cpp-ide-export`) and the two long Hermes reviews of July 2026 (#704 adversarial review, performance-regression investigation) are the candidates; the raw sources still exist on the maintainer's machine |
| 2026-09-14, 2026-09-18 | `64d938aa…`, `8891edb1…` (root slug) | Empty sessions (0 owner turns); nothing to save |
| 2026-09-18 | `8c80e928…` (14 owner turns, open-issue prioritisation → the 1.29.0 release) and `4fddb4b5…` (3 owner turns, #914 / #881+#886 / #868–#870 implementation) | Not triaged at S2; the first is a candidate (prioritisation argument), the second is task work already recorded in PRs #954/#956/#957 and the release notes |
