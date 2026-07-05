- **`clm validate` on a single split half now runs the full cross-file pair
  suite** when the twin exists on disk: shared-cell byte parity and tag-set
  parity now run alongside the existing `slide_id` / voiceover `for_slide`
  parity detectives (#162). Previously those two checks ran only at
  directory/course scope, so `clm validate x.de.py` could report OK while the
  pair's shared cells had silently diverged — producing divergent DE/EN build
  output. `--quick` (the PostToolUse-hook path) still skips all cross-file
  checks by design: it fires mid-edit, where transient pair divergence is
  expected.
