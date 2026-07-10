- The `clm harvest` voiceover artifact cache is now **shared and
  deck-independent** (#568). It moved from the per-deck
  `<deck dir>/.clm/voiceover-cache/` to `<shared-cache-dir>/voiceover/`,
  where the shared cache dir resolves like the LLM cache (`$CLM_CACHE_DIR` →
  `tool.clm.cache_dir` → `<project-root>/.clm-cache/`). Video-keyed entries
  (ASR transcripts, transition detection) are computed once per recording and
  reused by every deck in the repository, so forked/moved decks no longer
  re-transcribe identical videos. Existing per-deck caches are probed on a
  miss and promoted into the shared root automatically; `--cache-root` still
  overrides everything.
