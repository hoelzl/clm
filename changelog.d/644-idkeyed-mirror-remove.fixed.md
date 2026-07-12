- **sync v3**: a brand-new one-sided id-keyed shared cell whose body is
  byte-identical to an un-id'd positional cell still present in the pool was
  misframed as mechanical `mirror_remove` — `apply` then deleted the
  freshly-authored cell from its authoring side. The §7.3 pos→id key
  migration now requires the positional pool to actually be missing a cell
  on every side the id'd member populates, so the new cell frames
  `copy_new_shared` as documented (#644).
