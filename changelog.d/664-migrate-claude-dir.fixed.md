- **`clm course migrate-generated-images` no longer enters `.claude/`.** The
  agent-state directory can hold linked git worktrees whose `slides/` copies
  belong to other sessions' checkouts; the root-scanning migration walked into
  them and moved their files (found on repos carrying `.claude/worktrees/`).
  Also fixed: `--dry-run` counted a render once per diagram *source*, so a
  `.pu`/`.drawio` pair sharing one stem over-reported the move count relative
  to the real run.
