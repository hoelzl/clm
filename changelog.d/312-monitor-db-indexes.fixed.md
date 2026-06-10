- `clm monitor` / `clm status` no longer take many seconds to start against a
  large jobs database: schema v9 adds indexes on `jobs(status, completed_at)`
  and `jobs(completed_at)` (existing databases migrate automatically), and the
  monitor's activity query restricts its un-indexable `COALESCE` sort to an
  index-friendly candidate set instead of sorting every finished job ever
  recorded.
