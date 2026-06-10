- `clm validate`: a deck meant to be fully narrated can opt into the
  (default-off, #176) voiceover coverage check per deck with a
  `# clm: voiceover-coverage` header directive (#178) — a default
  validate run then coverage-checks that deck only, while an explicit
  `--checks`/`checks=[…]` list is still honored verbatim.
