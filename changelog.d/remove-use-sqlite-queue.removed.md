- **Removed the `USE_SQLITE_QUEUE` flag and the `workers.use_sqlite_queue`
  config field.** A leftover from the multi-queue era — SQLite is the only job
  queue now, so the flag had no consumer (the pool manager set it in the worker
  environment but nothing read it). Hard cut; see `clm info migration` for the
  full removed/renamed configuration-variable table.
