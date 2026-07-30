- `clm slides sync`: removing (or renaming) a slide no longer leaves its
  separated voiceover/notes companion cell as a hand-edit-only dead end
  (#650). The dangling `for_slide` was already framed (`broken_owner`) and
  blocked the write gate, but the item carried no answers — the only remedy
  was manually deleting the cell from both halves. Now: `broken_owner`
  accepts `{"choice": "remove"}`, pruning the orphaned narration from every
  present half; a framed `broken_owner` suppresses the member's other rows
  for the pass (one key, one answer); and a slide **rename** the differ can
  see in the same pass never frames the removal decision at all — the new
  mechanical `retarget_owner` rewrites the companion's `for_slide` to
  follow the rename, so live narration is never steered into a prune. The
  item detail and `clm info sync-agents` name the answer and the hand-edit
  alternatives.
