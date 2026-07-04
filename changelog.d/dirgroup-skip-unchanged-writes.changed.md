- Dir-group copies now skip files whose size and mtime already match the
  source (rsync-style quick check) instead of rewriting the whole tree on
  every build. Large vendored trees (e.g. Catch2) no longer cause needless
  SSD writes on rebuilds.
