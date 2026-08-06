- **Internal re-layering (Phase 8 step S2 of the A1/A3 plan, refs #802)**: the
  build contract seam descended from infrastructure into core — the `Operation`
  hierarchy (`clm.core.operation`), the abstract `Backend` (`clm.core.backend`),
  the worker payload/messaging package (`clm.core.messaging`, schemas
  unchanged — payloads cross the worker boundary as JSON), `File`, the
  copy-data classes, the build reporting data classes
  (`clm.core.build_data_classes`) and the opt-in build profiler
  (`clm.core.build_profiling`). `from clm.infrastructure import Backend,
  Operation` keeps working via the lazy compatibility exports. Import paths
  only; no CLI behavior change.
