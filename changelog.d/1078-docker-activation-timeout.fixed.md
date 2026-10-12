- **Docker builds no longer abort when notebook containers are slow to start
  (#1078).** The build waited a flat 30 s for pre-registered workers to
  activate, then marked every still-starting worker dead — even healthy
  Docker containers, whose startup on Docker Desktop routinely takes 30–70 s
  when many start at once — and the late activation was then refused.
  Docker workers now get a 180 s activation budget (Direct workers keep the
  30 s fail-fast). A Docker worker whose container exits before activating
  is marked dead by the pool's health monitor right away, which ends the
  wait early, and its container output is logged. When the pool stops, the
  output of every Docker worker that never activated or died is copied into
  `clm.log` before the container is removed. The Docker-mode error messages
  now point there instead of at the local worker-log directory and
  `clm[all-workers]`.
