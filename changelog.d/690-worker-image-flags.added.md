- `clm build` gains `--plantuml-image` and `--drawio-image`, plumbed exactly
  like `--notebook-image` (#690): a per-invocation image override for the
  diagram workers, with the bare-tag shorthand expanding against each
  service's own default repository (`--drawio-image test` →
  `docker.io/mhoelzl/clm-drawio-converter:test`). Previously only the
  notebook image was reachable from the command line; the diagram workers
  required deriving `CLM_WORKER_MANAGEMENT__*__IMAGE` env-var spellings.
  The env-var route keeps working. Caveat (#744): the build caches do not
  yet key on the worker image — pass `--ignore-cache` when testing a
  rebuilt image against unchanged sources, and stop lingering reused
  workers first (reuse is image-blind).
