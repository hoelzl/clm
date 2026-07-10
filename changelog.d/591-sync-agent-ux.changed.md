- `clm slides sync` agent ergonomics, driven by a transcript audit of 11 real
  agent sessions: the first decision-document error now teaches the full JSON
  shape (with the body format) instead of one field per round-trip; `apply
  --help`'s `--decisions` shows the schema inline; every report item now
  carries `answers` (`[]` = mechanical) so drivers need no missing-key guard;
  an all-cold report emits a `hint` pointing at `sync record` (text + JSON);
  rejected decisions are echoed to stderr in both output modes with their
  reasons; `duplicate_id` refusals name `clm slides rename-id` as the fix;
  `report`/`apply`/`record --help` point at `clm info sync-agents`; and
  `clm validate`'s shared-cell-drift suggestions now recommend `sync
  report`/`apply` over the legacy `unify` + `split` round-trip. The
  `sync-agents` info topic gains the apply-result JSON envelope, the
  report field names, a confirm-all-vs-`record` rule, and a "working
  patterns for agents" section (stdin decisions, script-generated decision
  documents, answers-aware batching, parallel-sweep capture discipline).
