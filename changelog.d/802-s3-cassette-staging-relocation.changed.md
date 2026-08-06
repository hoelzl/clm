- **Internal re-layering (Phase 8 step S3 of the A1/A3 plan, refs #802)**: the
  HTTP-replay cassette staging maintenance left `Course` — the pre-build orphan
  sweep and post-build mitmproxy merge are now functions in
  `clm.infrastructure.http_replay_mitm.cassette_staging`, fed by the new public
  `Course.http_replay_canonical_paths()`; the `http_replay_cassette` module
  moved from `clm.workers.notebook` to `clm.infrastructure.http_replay_mitm`.
  Sweeping is now the entry points' job (`clm build`'s pre-stage hook, watch
  mode's `FileEventHandler`) instead of a `Course.process_*` side effect. No
  CLI behavior change.
