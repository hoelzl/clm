- Fixed section notebook numbering assigning the same number to notebooks
  with identical file names from different topic folders (e.g. several
  `workshop.*.py` decks in one section all rendered as `03 …`). The numbering
  key is now scoped to the notebook's parent directory; split companions
  (`*.de.py` / `*.en.py`) in the same folder still share one slot.
