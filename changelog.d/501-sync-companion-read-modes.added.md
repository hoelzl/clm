- **`clm slides sync` now sees separated voiceover companions (read modes).**
  When a split deck keeps its voiceover in separated companion files
  (`voiceover_*.de.py` / `voiceover_*.en.py`), `sync report` / `verify` /
  `diagnose` inline each companion in memory and reconcile the narration like any
  other cell — so a companion edited on only one language now surfaces as
  `add …/voiceover [translation pending]` instead of drifting silently until
  `clm validate` (issue #501). A standing, in-sync separated pair reports **0
  changes** (the git-HEAD baseline is projected the same way, so a companion
  present on both sides is not mistaken for a new add). Pointing `sync` at a
  `voiceover_*` file now reconciles its deck pair. A deck whose voiceover is
  stored *both* inline and in a companion (mixed), or inconsistently across the
  two languages (one inline, one separated), is refused with a normalize hint, and
  an orphaned companion cell (its slide was renamed or removed) refuses rather than
  dropping the narration. Applying the reconciliation (the four-file write-back) is
  not yet implemented — `sync apply` on a separated pair reports the drift and
  writes nothing.
