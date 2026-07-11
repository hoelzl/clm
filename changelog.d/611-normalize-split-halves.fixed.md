- `clm slides normalize`: the `interleaving` operation (within-file DE/EN
  adjacency reorder + `count_mismatch`/`similarity_failure` reviews) is now
  skipped on language-split halves (`*.de.py` / `*.en.py`), matching the
  validator's split-file exemption. Every split half used to produce a
  guaranteed-noise `count_mismatch` review and exit 2, making
  `normalize --dry-run` unusable as a drift gate on split decks (#611).
