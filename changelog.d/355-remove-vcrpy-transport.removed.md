- **BREAKING: the legacy in-process `vcrpy` HTTP-replay transport was
  removed** (issue #355). mitmproxy has been the default transport since 1.10;
  the `CLM_HTTP_REPLAY_TRANSPORT=vcrpy` escape hatch is gone and now **fails
  the build** with a migration pointer instead of being silently ignored.
  Courses still building against vcrpy-recorded cassettes must re-record once
  (`clm build … --http-replay=refresh`) — cassettes recorded under mitmproxy
  are unaffected. This deletes the ~540-line in-kernel vcrpy bootstrap with
  its eight workarounds for upstream vcrpy bugs (forked vcrpy internals,
  scoped `force_reset`, eager-append, deep-copy persister, …), the per-worker
  cassette staging/seed/merge path (recording is proxy-side only), and the
  vcrpy pin guard. The `vcrpy` dependency itself remains in the `[replay]`
  extra as a cassette serialization library, but its restrictive `<8.2` upper
  bound — which existed only to protect the forked internals — is lifted.
  Stage 2 of issue #355 will replace the dependency with owned code while
  keeping the on-disk cassette format unchanged.
