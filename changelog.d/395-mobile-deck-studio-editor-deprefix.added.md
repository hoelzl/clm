- **Mobile Deck Studio — clean-markdown editing.** The phone editor now works in
  plain markdown (type `# Title`, blank lines) instead of CLM's comment-prefixed
  source (`# # Title`, `#`); the desktop de-prefixes on read and canonically
  re-prefixes on write. The conversion is byte-exact for canonical cells (an
  unedited re-save reproduces the file unchanged), and any cell whose prefixing
  would not round-trip cleanly falls back to raw editing — so the byte-exact
  write path is never weakened. (#395)
