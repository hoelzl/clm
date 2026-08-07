- The `clm harvest autopilot` merge/propagation flow moved out of the CLI into
  `clm.voiceover.autopilot`, and the `--transcript`/`--alignment` override
  loaders into `clm.voiceover.overrides` (Phase 8 A5 of #802). The
  cross-module private imports are gone: `langfuse_configured`
  (`clm.infrastructure.llm.client`) and `decode_alignment`
  (`clm.voiceover.cache`) are now public, and the MCP harvest tools no longer
  import a CLI command's private helpers. CLI behavior is unchanged.
