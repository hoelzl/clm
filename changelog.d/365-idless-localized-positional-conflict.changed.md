- **`clm slides sync` degrades a both-sided id-less-localized drift to a per-cell
  conflict instead of a deck-wide error (Issue #365).** A `lang=` cell with no
  `slide_id` is anchored only by content hash, so an edit to it on *both* halves
  with no propagation direction established elsewhere used to hard-error and roll
  the whole deck back. When the two halves' id-less localized cells share the same
  content-free `(group, kind)` structure — so they pair positionally within their
  slide group — each both-sided edit is now surfaced as a deferred `conflict`: the
  watermark still holds and both edits stay on disk, but the run no longer rolls
  back, so unrelated clean changes still apply, and the divergence is a located
  per-cell item. (Resolution by side is a follow-up; for now resolve by editing the
  deck or giving the cell a `slide_id`.) When the structure is *not* parallel, the
  located deck-wide error (Issue #364) is kept, since positional pairing would be
  unsound.
