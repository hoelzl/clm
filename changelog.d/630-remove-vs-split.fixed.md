- **Sync**: the #610 group-split guard no longer dead-ends legitimate
  removals (#630). A blocked removal is now framed as the new
  `remove_vs_split` action with a `remove` answer — a genuine deletion that
  merely coincides with a byte-identical cell in another group can be
  resolved through the decision document instead of manual file edits; the
  conflict detail and `suspected_group_split` observation name **every**
  rival group. The apply-time pool freeze is gated to `remove_vs_split`, so
  unrelated pre-existing `ambiguous_alignment` shapes (rival id stamps,
  both-sides-added pools) keep their prior recording behavior. The guard now
  also matches on the gone side's recorded **body** fingerprint, so a split
  whose moved cells only changed header attrs/tags is still blocked. A split
  whose moved cells were *also edited* still mirrors mechanically, but the
  report now emits a warn-only similar-bodies `suspected_group_split`
  observation for it (also alongside an exact match in a different group,
  so a coincidental duplicate cannot hide the real split target).
