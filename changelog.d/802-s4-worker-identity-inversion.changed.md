- **Internal re-layering (Phase 8 step S4 of the A1/A3 plan, refs #802)**: the
  effective worker-image identity registry moved to `clm.core.worker_identity`;
  `clm.infrastructure.workers.image_identity` keeps all fingerprinting, records
  identities into the core registry, and registers the singleton-config
  resolver as the registry's fallback provider (eagerly at its own import and
  lazily via `clm.infrastructure.__init__`). Payload builders read the registry
  through core only. Cache-key behavior unchanged.
