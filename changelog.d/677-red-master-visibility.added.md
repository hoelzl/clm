- **A red `master` now files an issue within minutes.** The "Docker Integration
  Tests" job is deliberately not a required status check, so a fully green PR
  can still break `master` — which is exactly what happened when a PR un-skipped
  a module containing docker-marked tests. A `report-master-failure` job now
  fires on any post-merge CI failure (not just Docker) and files, or comments
  on, a `master-red` issue.
- **`tests/infrastructure/workers/test_docker_image_tags.py`** rejects any test
  naming a Docker image CLM does not build — the sibling of the module-probe
  guard, one layer down. Repository names must carry the `clm-` prefix,
  registry-qualified references must sit under `docker.io/mhoelzl`, and bare
  tags must be built by a workflow or be a published variant. The CI-built tags
  are parsed out of the workflow files rather than duplicated, so a renamed tag
  fails the fast suite instead of only the non-required Docker job. Six
  references to a pre-rename image name in `test_worker_executor.py` are
  corrected in passing.
