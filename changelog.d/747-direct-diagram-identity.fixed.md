- `clm build`: direct-mode diagram cache keys now fingerprint the PlantUML
  JAR / Draw.io executable (path + size + mtime, #747) — upgrading either
  binary invalidates the affected diagram caches the same way a Docker
  image switch does, instead of silently replaying diagrams rendered by
  the old binary. An unlocatable binary degrades to the previous keying;
  notebook direct-mode identity is unchanged (covered by the template
  fingerprint). The binary resolution is now shared between the workers
  and the host-side identity (`clm.workers.diagram_tools`), so the
  fingerprint always describes the binary that actually renders.
