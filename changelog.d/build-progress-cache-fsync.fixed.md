- **Build progress bar no longer stalls behind the workers.** The build's
  job-completion poll loop used to advance the progress bar only after it had,
  for each finished job, read the executed output back off disk, pickled it, and
  committed the blob to the result-cache DB. That serial, fsync-per-job work
  could not keep up with the parallel workers, so the bar appeared frozen
  mid-stage (while `clm monitor`, which reads completed rows directly, raced
  ahead) and then drained gradually after the workers went idle. Two changes fix
  it: (1) the result-cache `DatabaseManager` connection now opens with
  `synchronous=NORMAL` (matching `ExecutedNotebookCache`, which writes the same
  DB) instead of inheriting the default `synchronous=FULL`, removing a full
  fsync per job; and (2) the result-cache writes now run on a background writer
  thread, so the poll loop advances the bar the instant a job completes. The
  queue is drained at the end of each build stage, so the cache is fully
  populated exactly as before.
