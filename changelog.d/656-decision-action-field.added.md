- A decision row may now name the framed **`action`** it answers
  (`{"key": "id:intro", "action": "translate_edit", "body": "…"}`). Two things
  this fixes: a member that frames two rows can have both answered in one
  document (previously the second row was a `duplicate key` error, and the
  engine's own design notes sequenced around it), and an answer aimed at a row
  the member does not currently frame is now *reported* — `rejected`, naming
  both the framed action and the one you asked for — instead of silently
  landing on whichever row the member does frame. The field is optional; a row
  without it answers whatever the member frames, exactly as before.
