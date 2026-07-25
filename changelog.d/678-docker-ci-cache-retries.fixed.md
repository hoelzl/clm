- **The Docker CI job no longer re-fetches the world on every run.** Its three
  image builds now use a scoped BuildKit layer cache (`type=gha`), so on a cache
  hit the external fetches inside the Dockerfiles do not run at all — and those
  fetches were the entire source of the job's ~12% infra failure rate (Docker
  Hub timeouts, partial transfers). The two remaining un-retried downloads in
  the notebook image are fixed too: the .NET installer is wrapped in a retry
  loop, because the script does its *own* downloads and was failing with
  `curl` exit 18 (partial file), and the `uv` installer is downloaded to a file
  before execution instead of piped into `sh`, where a truncated download would
  have executed a truncated script and still reported success. The nightly reads
  the same cache but does not write it, so a cold-build flake cannot file a
  spurious `nightly-failure` issue.
