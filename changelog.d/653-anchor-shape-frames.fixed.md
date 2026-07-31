- **A one-sided `slide` tag change no longer refuses the deck** (#653). Removing
  (or adding) `tags=["slide"]` on one half made the same id a slide anchor on
  one side and a continuation cell on the other; the lens built two members
  under one key and refused the whole deck with
  `duplicate_id: member key id:X resolves to 2 distinct members` — zero items
  framed, and a `rename-id` hint that renames *both* halves and therefore
  cannot fix it.

  Slide-hood is a **presentation** attribute — the tags select the transition
  shown when a cell appears, and authors flip them because a rendered slide
  looks too full — so anchor-hood is now a property of the **pair**: a boundary
  only one half draws opens no group on either side. The cell stays an ordinary
  member, pairs by id like any other, and the halves simply differ in one tag —
  which is the mechanical `mirror_tags` row every other tag difference has been
  since #615. The report also carries an `anchor_shape_divergence` observation
  naming both halves and their line numbers.

  Design: `docs/claude/design/sync-slide-hood-is-presentation.md`. The
  positional keys of cells inside the affected span still churn (they are
  scoped by the owning anchor); making them immune is the second half of that
  design and needs a ledger migration.
