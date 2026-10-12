- `clm docker build`, `build-quick` and `cache-info` honor a new
  `CLM_DOCKER_CACHE_DIR` environment variable for the local BuildKit cache
  (default `<project-root>/.docker-cache/`). Use it to keep the multi-GB cache
  export on a faster or larger drive, or to share one cache between worktrees.
