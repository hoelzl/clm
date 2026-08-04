- **Generated diagram renders move out of `img/` into `<topic>/img-generated/`
  (#664).** In a migrated repo the topic-level `img/` directory is exclusively
  hand-authored — the build never writes into it — while DrawIO/PlantUML
  renders live in the build-owned `img-generated/` sibling, ending the
  shared-namespace ambiguity behind the #661 nondeterminism class. Both
  directories collapse onto the output's `img/`, so slide references
  (`img/x.png`) never change and a migrated course builds a byte-identical
  output tree. New command `clm course migrate-generated-images` moves a
  repo's committed renders (spec-free, idempotent, conflict-safe,
  `--dry-run`); unmigrated repos keep building exactly as before via a
  transitional rule (a committed legacy render keeps its location until
  moved) and get one summary warning per course load naming the diagrams
  still on the legacy target. Inline-image data URLs, the shared and
  duplicated image modes, the image registry, the provenance manifest (what
  the release pipeline copies from), and the stray-file sweep all understand
  both layouts.
