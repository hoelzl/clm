- **Generated diagram renders move out of `img/` into `<topic>/img-generated/`
  (#664).** The topic-level `img/` directory is now exclusively hand-authored —
  the build never writes into it — while DrawIO/PlantUML renders live in the
  build-owned `img-generated/` sibling, ending the shared-namespace ambiguity
  behind the #661 nondeterminism class. Both directories collapse onto the
  output's `img/`, so slide references (`img/x.png`) never change and a
  migrated course builds a byte-identical output tree. New command
  `clm course migrate-generated-images` moves a repo's committed renders
  (spec-free, idempotent, conflict-safe, `--dry-run`); unmigrated repos keep
  building exactly as before via a transitional rule (a committed legacy
  render keeps its location until moved). Inline-image data URLs, the shared
  and duplicated image modes, the image registry, and the stray-file sweep all
  understand both layouts.
