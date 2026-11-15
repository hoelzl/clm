- **`scripts/clean_transcript.py` no longer cleans harness rows as dialogue
  (#1219, #1213).** Claude sessions: `api-request-blob` rows (request copies
  that duplicated every turn and leaked skill bodies as owner text) are
  dropped, and a subagent hand-back queued as an `<agent-message>` becomes a
  one-line `[Subagent hand-back]` stub instead of an owner message. Hermes
  sessions: rows that compaction re-inserted (same role, content and tool-call
  ids, in one timestamp batch) are dropped, and harness notices stored as user
  rows (delegation results, background-process notices, a preserved task list)
  are dropped, with a skill invocation kept as a `[skill] <name>` stub. The
  header's "Content through" boundary now comes from the newest dialogue
  record's own timestamp, printed in UTC, instead of the file's mtime (Claude)
  or the session start (Hermes); a fallback says which source it used.
