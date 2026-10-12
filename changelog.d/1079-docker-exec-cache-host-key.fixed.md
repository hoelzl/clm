- Docker-mode builds now reuse the execution cache for Recording/Speaker HTML
  instead of re-executing every deck on every build. The Docker notebook worker
  stored executed notebooks under its container path (`/source/...`), while the
  host looked them up by the host path, so the lookup never hit. Executions are
  now keyed by the host path in both modes. The first Docker build after
  upgrading still executes once to re-populate the cache (#1079).
