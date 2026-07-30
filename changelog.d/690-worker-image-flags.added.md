- `clm build` gains `--plantuml-image` and `--drawio-image`, plumbed exactly
  like `--notebook-image` (#690): a per-invocation image override for the
  diagram workers, with the bare-tag shorthand expanding against each
  service's own default repository (`--drawio-image test` →
  `docker.io/mhoelzl/clm-drawio-converter:test`). Previously only the
  notebook image was reachable from the command line; the diagram workers
  required deriving `CLM_WORKER_MANAGEMENT__*__IMAGE` env-var spellings.
  The env-var route keeps working. Caveat: worker reuse is image-blind —
  stop lingering reused workers when switching images (cache keys follow
  the image since #744).
