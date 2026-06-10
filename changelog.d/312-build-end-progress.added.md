- `clm build` now reports what it is doing after the last build stage instead
  of appearing to hang: stale-output sweep, database cleanup/VACUUM, worker
  shutdown, HTTP-replay cassette merging, and provenance-manifest writing each
  print a progress message.
