---
name: save-discussion
description: Preserve a CLM conversation for later resumption — cleaned transcript, current state, append-only register.
version: 1.0.0
author: tc, Claude Code
license: Private project knowledge
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [clm, discussions, transcripts, continuity]
    related_skills: [save-knowledge, resume-discussion]
---

# Save a CLM Discussion

Preserve the current conversation as a cleaned transcript, current state, and
an append-only thread register under `docs/claude/discussions/`. The
canonical format and rationale are in `docs/claude/discussions/README.md`;
read it before changing the stack.

## When to Use

- A design or planning conversation is ending (an adversarial review, a
  sync-model debate, an export design, an owner review of generated output).
- The session is long enough that compaction could lose conversational
  texture.
- A finished increment produced decisions, rejected alternatives, or open
  questions that task artifacts (commits, PRs, handovers, issues) do not
  preserve — the *argument* for a decision, in the owner's words.
- Re-run at the first natural boundary and again near the end; extending a
  saved session overwrites its transcript with a longer snapshot.

Do not use this as a substitute for a handover, a test, an info topic, an
issue, or a commit message. Route each kind of knowledge to the venue
defined by `AGENTS.md`'s *Documentation Map* and *Document Placement*
tables; conversational texture is the only kind whose home is here.

## Procedure

0. **Save knowledge first.** If the session established durable knowledge,
   load `.agents/skills/save-knowledge/SKILL.md` and execute its two passes
   before saving the discussion. Use that file directly if the harness cannot
   invoke a skill by name. It returns here; do not recursively invoke this
   skill. Carry its findings, destinations and unresolved gaps into the
   discussion's state/register as appropriate. If nothing durable was learned,
   say so rather than manufacturing a knowledge edit. Re-run for new findings
   when extending a save; already-captured facts need no duplicate entry.

1. **Re-ground before writing.** Run `git fetch origin` and inspect
   `git status`. Re-read the target thread's `state.md` and the root
   `docs/claude/discussions/register.md` immediately before editing; another
   checkout (the main repo, another worktree) may have updated the text
   without creating a merge conflict.

2. **Check for unsaved sessions.** Run
   `python scripts/audit_transcripts.py`. It audits the Claude project slugs
   of this repository — the main checkout's *and* every
   `.claude/worktrees/<name>` slug, whichever checkout you run it from —
   and, when `HERMES_HOME` is available, that profile's `state.db`; use
   `--repo`, `--claude-projects`, or `--hermes-db` for a moved slug or
   another Hermes profile, and repeat `--hermes-workspace <path>` for a
   session recorded under another repository that belongs here. Save
   conversations worth resuming; append a deliberately-skipped row for the
   rest so numbering gaps are explained.

3. **Choose the thread.** Reuse an existing directory under
   `docs/claude/discussions/` when this continues a topic. For a new thread,
   use the subject rather than the date, such as `cpp-ide-export` or
   `sync-identity-model`. A thread directory carries `state.md` and
   `transcripts/`; the register is the single root
   `docs/claude/discussions/register.md` for the whole tree. Sessions are
   numbered S1, S2, … across all threads: one sequence whose gaps mean a lost
   or skipped conversation.

4. **Create the cleaned transcript outside-to-inside.** Never commit a raw
   harness transcript.

   Claude Code source (the usual case in this repo):

   Derive the project slug from the checkout the session ran in rather than
   copying a literal path; `scripts/audit_transcripts.py` prints every slug
   that belongs to this repository and the slug of each unsaved session. A
   session run from a worktree lives under the worktree's slug, not the
   main checkout's. Then:

   `python scripts/clean_transcript.py ~/.claude/projects/<slug> -o docs/claude/discussions/<thread>/transcripts/<YYYY-MM-DD>-s<N>.md --stats`

   (a directory means "the newest `.jsonl` in it"; pass the file itself to
   pick an older session).

   Hermes source:

   a. Identify the exact current ID with `hermes sessions list --limit 20`.
   b. Prefer a staged full-lineage copy:
      `python scripts/stage_hermes_session.py --session-id <id>` writes
      `~/.clm-transcripts/<id>.jsonl`, reading the profile DB read-only.
      This works on an open session and includes archived pre-compaction
      rows that no CLI export returns.
   c. If no staged copy can be produced (another machine's profile), export a
      redacted temporary copy outside the repository with
      `hermes sessions export "$LOCALAPPDATA/Temp/session.jsonl" --format jsonl --session-id <id> --redact --yes`
      on Windows, or the platform temporary directory elsewhere.
   d. Clean it with
      `python scripts/clean_transcript.py <source> -o docs/claude/discussions/<thread>/transcripts/<YYYY-MM-DD>-s<N>.md --stats`.

   **Path-shape trap (Windows/MSYS):** the scripts run under native Python,
   where an MSYS path like `/c/Users/...` is not understood — `$HOME`-based
   paths can arrive mangled as `\c\Users\...`. Pass `C:/Users/...`-style
   forward-slash native paths, and verify the output file exists where you
   intended before proceeding.

   The current turn is written only after it completes. Note that boundary,
   and re-run if work continues. The cleaner refuses to overwrite an existing
   transcript when the new clean loses any prior dialogue line, after
   normalizing intended redactions; the doc-currency frontmatter is metadata
   and never counts as dialogue. Use the fuller live source or merge the
   later tail; pass `--allow-narrowing` only for a reviewed, intentional
   removal.

   After Hermes compaction, a CLI export contains only the active
   post-compaction view. The staged full-lineage copy is the fix; when even
   that is unavailable, the archived rows can be read directly:
   `sqlite3 file:<state.db>?mode=ro "SELECT role, content, timestamp FROM messages WHERE session_id='<id>' AND active=0 ORDER BY rowid"`.
   Hermes message schemas evolve; run `PRAGMA table_info(messages)` before
   querying. `_compressed_summary = 1` identifies harness-authored summary
   rows and is the exclusion predicate — `compacted = 1` also marks ordinary
   archived dialogue, so treating it as a summary marker destroys the
   pre-compaction lineage. Treat the compaction summary itself as
   harness-authored, never as the owner's words.

5. **Rewrite `state.md`.** Keep the doc-currency frontmatter
   (`status: active`, `updated:` today, `review-by:` +6 months). Keep current
   position only: settled decisions, open questions, deferred items and
   revisit conditions, known weak points, and the next conversational
   boundary. Include work done after the final owner exchange — in this repo
   that is usually the PR, the corpus check, the merge — because the
   transcript cannot record later solo work. Point at the handover
   (`docs/claude/handovers/`) for task state rather than duplicating it. Do
   not append stale states.

6. **Append to the root `docs/claude/discussions/register.md`.** Bump its
   `updated:`/`review-by:` headers. Add one row per thread with subject,
   disposition, landing place (PR numbers, handover, design doc, issue), and
   sessions. Add the cleaned transcript to the session table with its size,
   owner/agent block counts (the cleaner's `--stats` output), and content
   boundary. Record the Claude `.jsonl` name plus its project slug, or a
   Hermes session ID and profile — never a copied raw transcript or an
   absolute private profile path. Preserve dropped threads and corrections
   where an agent's prior was wrong; both are expensive to rediscover.

7. **Verify.** Read the cleaned transcript, `state.md`, and `register.md` back
   in full. Confirm the transcript contains owner and agent dialogue, tool
   output is absent, the state is current, register history was only
   appended, and no absolute profile path or secret remains (the cleaner
   rewrites home paths to `~`, Hermes profile paths to
   `[REDACTED PROFILE PATH]`, and e-mail addresses to `<email>` — check it
   caught every occurrence, cassette secrets and tokens included). Run
   `python scripts/check_doc_currency.py` — all three layers must pass.
   Stage only explicit paths.

8. **Integrate the save.** Follow the repository workflow (`AGENTS.md`, and
   `.claude/skills/ship-a-pr` for the details): discussion saves are
   documentation changes, so they go on a branch in a **git worktree** off
   `origin/master` (e.g. `claude/save-s<N>-<thread>`), never on `master` in
   the main checkout — and a worktree never switches to literal `master`
   (reset its own branch onto `origin/master` instead). Commit with explicit
   paths, push (the pre-push hook runs the smoke tier), open a PR that says
   "Refs" (never "closes") and describes the save as preservation-only with
   the verification results above; auto-merge is CI-gated (`gh pr merge
   --merge --auto`). A docs-only PR skip-satisfies the Docker job.

## Pitfalls

- The register is an index, not a summary. If prose can be replaced by "load
  the transcript" without loss, it does not belong there.
- `state.md` is rewritten; `register.md` is append-only; transcripts are
  snapshots that may be regenerated to extend the same session.
- A staged/exported snapshot can stop before the live session. Never replace
  an existing transcript with a narrower clean merely because the command
  succeeded.
- A clean merge does not prove a shared state file was current. Fetch and
  re-read immediately before writing.
- Most CLM sessions run from a worktree: the session file is under the
  worktree's project slug, and the `docs/claude/discussions/` tree you edit
  is the worktree's copy of the branch — both are fine, as long as the PR is
  opened fresh off `origin/master`.
- `hermes sessions export` on a session that is **still open** can emit only
  the session-header line — zero message rows, no error. Check the export has
  per-message rows before cleaning; the staged full-lineage copy or the direct
  DB query are the working sources for an open session.
- The cleaner's Hermes path expects ONE session object per line
  (`{"id":..., "started_at":..., "last_activity_at":..., "messages":[...]}`).
  Bare `{"role":...}` JSONL rows are silently all-dropped (stats show
  `0 owner blocks, N records dropped`). When assembling a source from a direct
  DB query, wrap the rows in that shape and include `started_at`/
  `last_activity_at` (epoch seconds from the first/last row).
- Inspect assistant-role text too, not only owner text: harness-authored
  wrappers (compaction summaries, task-list injections, background-task
  notifications) have been observed surviving as apparent agent dialogue.
  Remove the inspected harness row from a temporary source and re-clean;
  never substitute reasoning text for missing public commentary.
- Raw transcript files carry machinery, personal context, and possibly
  secrets (this repo's sessions handle API keys and cassette contents). Only
  the redacted, cleaned artifact belongs in Git.

## Verification

Completion requires all of the following:

- the knowledge audit ran when durable findings existed, with destinations and
  any unresolved gaps recorded, or the no-new-knowledge outcome was stated;
- the selected session has a cleaned transcript with its source identity and
  content boundary;
- `state.md` covers current conversational and post-conversation work, with
  its frontmatter bumped;
- `register.md` gained append-only index/session entries and explains skipped
  numbering;
- the three layers agree on settled, open, and deferred topics;
- `python scripts/check_doc_currency.py` passes on all touched files;
- explicit-path staging contains no raw export, absolute profile path, secret,
  or unrelated file;
- the save is committed on a branch in a worktree and handed over as a PR
  (auto-merge armed), or explicitly reported incomplete with the candidate
  and concrete blocker.
