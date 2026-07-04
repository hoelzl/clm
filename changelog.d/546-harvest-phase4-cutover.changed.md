- **BREAKING — harvest cutover (epic #546 Phase 4, no aliases).** The
  video side of `clm voiceover` moved to `clm harvest`: `voiceover sync` →
  `harvest autopilot` (the legacy embedded-LLM one-shot; agents use the
  `report → task → accept` loop instead); `transcribe`, `detect`,
  `identify`, `identify-rev`, `sync-at-rev`, `port`, `compare`,
  `compare-from-inventory`, `backfill`, `extract-training-data`, `cache`,
  `trace`, and the hidden `debug` group moved keeping their names;
  `voiceover report` → `harvest compare-report`. `clm voiceover` retains
  only the text-layer verbs (`extract`, `inline`, `inline-notes`) and lost
  its cache flags. MCP tools renamed accordingly (`voiceover_transcribe` →
  `harvest_transcribe` etc.), and two read-only MCP tools were added:
  `harvest_report` and `harvest_task` (`accept` stays CLI-only). New
  `clm info harvest-agents` topic documents the canonical agent loop;
  `docs/user-guide/harvest.md` carries the video-pipeline user guide.
