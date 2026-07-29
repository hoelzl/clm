- `clm slides sync`: removing (or renaming) a slide no longer leaves its
  separated voiceover/notes companion cell as a hand-edit-only dead end
  (#650). The dangling `for_slide` was already framed (`broken_owner`) and
  blocked the write gate, but the item carried no answers — the only remedy
  was manually deleting the cell from both halves. `broken_owner` now
  accepts `{"choice": "remove"}`, which prunes the orphaned narration from
  **both** language halves mechanically; the item detail and
  `clm info sync-agents` name the answer and the hand-edit alternatives
  (retarget `for_slide`, restore the slide).
