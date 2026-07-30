- `clm build`: direct-mode diagram cache keys now fingerprint the PlantUML
  JAR / Draw.io executable (path + size + mtime, #747) — upgrading either
  binary invalidates the affected diagram caches the same way a Docker
  image switch does, instead of silently replaying diagrams rendered by
  the old binary. Direct-mode users get a one-time full diagram re-render
  on the first build after upgrading (the identity value changed).
  Binaries configured in a config file's `[external_tools]` section are
  fingerprinted too — resolution follows the worker executor's exact
  injection precedence. An unlocatable binary degrades to the previous keying;
  notebook direct-mode identity is unchanged (covered by the template
  fingerprint). The binary resolution is now shared between the workers
  and the host-side identity (`clm.workers.diagram_tools`), so the
  fingerprint always describes the binary that actually renders.
