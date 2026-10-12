- Docker images no longer pick up local, gitignored state from the build
  checkout. `.dockerignore` now excludes every root-level dot entry, so
  `.scratch/`, `.clm/`, `.clm-cache/`, `.clm-test-corpus/`, `.hypothesis/` and
  `.import_linter_cache/` stay out of `/app/clm`. Previously, a build from a
  working checkout baked ~455 MB of agent scratch into the images and made the
  final `chmod -R` layer take over an hour on a slow disk (#1084).
