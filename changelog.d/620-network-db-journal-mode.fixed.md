- **SQLite databases on network shares no longer use WAL journaling.** WAL's
  index lives in a memory-mapped `-shm` file that is not coherent across
  machines, which SQLite documents as unsupported on network filesystems. With
  a jobs database shared over SMB (a supported CLM setup — see the mode-tagged
  job claiming added in 1.19), that broke the atomicity of job claiming, so two
  machines could each believe they had claimed the same job, and interleaved
  checkpoints could corrupt the database file. CLM now detects network-hosted
  databases (UNC paths, Windows mapped network drives, and network mount types
  on POSIX) and uses DELETE journaling with `synchronous=FULL` and a longer
  busy timeout for them; local databases keep WAL and are unaffected. If a
  network-hosted database cannot be moved off WAL — normally because another
  machine still has it open — CLM now fails with an explanatory error instead
  of continuing unsafely. Journal configuration for every CLM database moved
  into one place so that connections can no longer disagree about the mode.
  This is an interim measure: cross-machine access will move to the worker API
  so that exactly one machine owns the file.
