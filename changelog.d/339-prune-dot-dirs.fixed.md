Validation and normalization walks no longer descend into dot-directories
such as `.ipynb_checkpoints` (#339). Jupyter's checkpoint copies of decks
previously showed up as duplicate — or stale, contradictory — findings in
`clm validate` and related discovery walks. Dot-dirs are now pruned in the
same place as the `_`-prefixed author-parked directories from #318, so the
rule applies uniformly to topic discovery, recursive slide-file walks,
spec-orphan and pairing scans.
