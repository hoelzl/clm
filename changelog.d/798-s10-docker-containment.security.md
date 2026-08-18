- **Docker workers run unprivileged, with the course sources read-only for the
  notebook worker** (adversarial-review findings S10 + D7, #798). Docker-mode
  workers ran as **root** with the course tree mounted read-write, so
  course-authored notebook code executed as uid 0 and could rewrite the
  repository it was built from.

  All three worker images now declare `USER 1000:1000`, and `/source` is
  mounted **read-only for the notebook worker** — the one that executes
  arbitrary course code and writes only to `/workspace`. PlantUML and Draw.io
  keep it writable, because rendering diagrams into the source tree is what
  they do. The images are written to run under *any* uid (world-readable
  installs, world-writable `$HOME`, caches under `/tmp`), and on POSIX hosts
  the executor starts containers as the host user so bind-mount writes keep
  their ownership — the uid-remapping caveat that a build-time uid cannot
  solve, since the right uid belongs to the machine, not the image. Draw.io's
  entrypoint starts a *session* D-Bus instead of the system bus, which needed
  root.

  **Whole-volume mounts are refused.** The existing guard covered only the
  multi-target output case: a *single* output target at a drive root returned
  before the check ran, and the data dir had no guard at all — either would
  have bind-mounted an entire disk into the container. Both are now refused
  before any container starts, in one place that covers every construction
  site.

  The "does this build run a Docker notebook worker?" probe no longer swallows
  an error into a fixed answer. Its two callers have **opposite** safe
  directions: the workspace resolver must assume Docker (that path carries the
  whole-volume guard; assuming Direct silently returns the unguarded root),
  while the replay proxy must assume Direct (assuming Docker binds `0.0.0.0`
  and opens a LAN listener). Each caller now states its own default and the
  failure is logged.

  Executed notebooks keep working: the kernel now runs in a writable temporary
  directory in Docker mode too, which is what Direct mode has always done, so a
  cell that writes `data.txt` or `plot.png` still succeeds — it simply can no
  longer write into the course repository. That also removes a Docker/Direct
  behavioural divergence rather than adding one.

  **Breaking**: anyone running the images by hand on native Linux needs
  `--user "$(id -u):$(id -g)"` to write into a bind mount, and a data dir or
  output root at a drive root is now rejected before any container starts.
  Note that rebuilt images do *not* invalidate the execution cache: the
  identity in the key is the image tag, not its content (#744), so pass
  `--ignore-cache` to re-execute against the new image. One Docker-only read
  also changes — a cell reading an image from its own directory worked in
  Docker because the kernel ran inside the mount, and now behaves like Direct
  mode, which never supported it. See `clm info migration` and
  `docker/README.md`.
