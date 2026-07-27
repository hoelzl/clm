- Fixed cross-install cache thrash from line-ending drift:
  `compute_template_fingerprint` hashed raw template bytes, so an editable
  install on Windows (CRLF working tree, `core.autocrlf=true`) and a
  wheel/sdist install (LF) of the *same* clm version produced different
  fingerprints — and therefore different content hashes for every notebook —
  invalidating the whole cache whenever a build switched between installs
  (#711). Template bytes are now newline-normalized (CRLF → LF) before
  hashing. Note: this changes the fingerprint value for CRLF installs once,
  causing a one-time cache refresh on their next build (same effect as a
  template change).
