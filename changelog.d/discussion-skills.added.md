- Resumable discussions for agent sessions: `docs/claude/discussions/` (per-thread
  `state.md` + cleaned transcripts, one append-only `register.md`), the
  agent-neutral skills `.agents/skills/{save-discussion,save-knowledge,resume-discussion}`
  with `.claude/skills/` adapters (`/save-discussion`, `/save-knowledge`,
  `/resume-discussion`), and the tooling behind them in `scripts/`:
  `clean_transcript.py` (Claude Code `.jsonl` or Hermes session → redacted
  dialogue-only transcript), `audit_transcripts.py` (sessions no register
  mentions, main checkout and worktree slugs alike), `stage_hermes_session.py`
  (full-lineage Hermes staging into `~/.clm-transcripts/`) and
  `check_doc_currency.py` (status headers of the discussions tree). Ported from
  the CppCourses/Cenotaph discussion stack.
