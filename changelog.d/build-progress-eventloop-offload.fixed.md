- **Build progress bar no longer freezes for minutes behind job submission.**
  When many topics needed rebuilding, the `clm build` progress bar could sit
  frozen at a few dozen completed jobs for one to two minutes — while
  `clm monitor` showed hundreds of jobs finishing — and then jump far ahead. The
  bar advances only from the completion poll loop, and that loop shares one
  asyncio event loop with job *submission*, whose synchronous body (the SQLite
  job-cache probe, the worker-availability wait, the payload JSON serialization,
  and the jobs-DB INSERT) ran inline with no `await` and starved the poll loop
  during a submission burst. The previous attempt only moved the result-cache
  *write* off the poll loop, which was never what blocked the bar. The submission
  body now runs on a dedicated single-thread executor, a per-operation semaphore
  bounds how much work (notably the on-loop cache-hit replay) lands in one
  event-loop turn, the end-of-stage result-cache drain no longer blocks the loop,
  and the jobs-DB runtime connection now uses `synchronous=NORMAL` (it had been
  reverting to the fsync-on-every-commit default). On a 1,440-job synthetic
  rebuild this cut the worst progress-loop stall from ~19 s to ~1 s and reduced
  total build time. A new opt-in `CLM_PROFILE_BUILD=1` build diagnostic and the
  `scripts/profile_build_stall.py` harness make the poll-loop health measurable.
