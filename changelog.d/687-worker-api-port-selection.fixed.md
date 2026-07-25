- **Docker workers are now told the port the Worker API actually bound.** The
  container's `CLM_API_URL` was rebuilt from the default port (`8765`) rather
  than read from the running server, so a server on any other port handed every
  container an address pointing at whatever else happened to be listening — or
  at nothing. Jobs then sat `pending` with no error on either side.
- **Two Worker API servers can no longer quietly share a port.** On Windows
  `SO_REUSEADDR` lets a second listener bind a port a first one is still using,
  after which the OS decides which of them receives a given container's
  callback — so a worker could register with a server owning a different jobs
  database. CLM no longer sets that option on Windows (POSIX, where it only
  affects `TIME_WAIT` rebinding, is unchanged), making an overlap a clear error.
  A default port that is already taken is handled instead by moving to a free
  one, so two builds on one machine keep working.
