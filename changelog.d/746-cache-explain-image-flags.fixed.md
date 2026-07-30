- `clm cache explain` gains `--workers` and the three image-override flags
  (#746): the cache keys follow the effective worker image since #744, so
  explain must be given the same flags as the build it explains — it used
  to read the config singleton (which CLI overrides never touch) and
  misattributed the resulting miss. With no flags it matches a no-flag
  build, as before.
