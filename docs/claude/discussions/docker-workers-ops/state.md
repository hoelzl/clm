---
status: active
owner: maintainers
updated: 2026-11-08
review-by: 2027-05-08
---

# State: Docker worker images and the Docker host (ops)

Saved retroactively on 2026-11-08 from two sessions: the 1.34.2 image rebuild
(S17, 2026-10-11) and the slow-worker / cache-key / disk-move session (S18,
2026-10-12/13). Image build and push procedure: `docker/BUILDING.md` and
`clm info commands` (`clm docker`). Drive-latency measurements and the disk
location are also in the agent's private memory; this file keeps the
conversational position.

## Settled

- **The `openai<3` pin is not what protects replay** (owner correction, S17).
  A side agent warned that the 1.34.1 `full` image had openai 3.6.0 and could
  make live, billed calls. The owner pointed out that the httpx2 cassette fix
  (#1055, PR #1065) had already shipped; the agent had taken its reason from a
  stale pin comment in the course venv. Real exposure: Docker builds with the
  1.34.1 `full` image (old replay code plus openai 3.x). The pin stays only to
  keep the image identical to the course venv.
- **Rebuild images from a release, not from a branch** (owner, S18): cut 1.34.3
  first so the images carry a stable version, then build from the main checkout.
- **Docker's disk image goes to G:, not P:** (owner, S18, on the agent's
  recommendation). G: is an NVMe PCIe 4.0 TLC drive; P: is a USB QLC drive,
  the same flash type that makes C: slow, with fsync latency and
  disconnect risk for a VM disk. Prune first. Prune only clm images, by
  explicit tag (no `docker image prune -a`), so `cam-*` images stay.
- **`force-unsafe-io` does not help** (measured in S18: the plantuml apt step
  took 1,264 s with the flag versus 1,205 s without). Removed (#1083, PR #1086).
- **The Docker build cache moves off C: and its location is configurable**
  (owner's suggestion; `CLM_DOCKER_CACHE_DIR`, PR #1087).
- **The owner's teaching slots take precedence over builds** (S18): work that
  stops Docker Desktop (the disk move) waits until the course container is not
  needed; builds are cancelled before the deadline.

## Shipped

- S17: 1.34.2 images rebuilt and pushed (Dockerfile updates, stale Quick Start
  rewritten); CppCourses built with 6 Docker notebook workers.
- S18: #1078 (Docker workers get 180 s to activate; 30 s marked loaded workers
  dead) and #1079 (the executed-notebook cache key used the container path, so
  Docker-built Speaker HTML always re-executed; fixed by keying on the host
  `input_file`, PR #1080), released as 1.34.3; `.dockerignore` `.*` rule
  (PR #1085); #1083/#1086; #1087; #1088 (ASR vs PyAV 19, PR #1089).

## Open / revisit conditions

- The Ubuntu WSL disk (448 GB file, 396 GB really used) was left in place on
  C:; moving it is independent of Docker and was not decided.
- `cam-cpp` and `cam-python` were recreated on 0.6.1 (S18); the old ones are
  stopped as `cam-cpp-backup-0.6.0` and `cam-python-backup-0.6.0`. Once 0.6.1
  has proven itself, the backups and the `cam-notebook:0.6.0`/`0.6.0-cpp`
  images can go (about 27 GB). Not decided yet.

## Agent corrections recorded in the transcripts

- S17: the agent stated the httpx2 replay fix was not yet in clm, from a stale
  comment, without checking clm's history (corrected by the owner).
- S18: the agent reported the containers ran 0.6.1; they ran 0.6.0, and 0.6.1
  existed only locally, not on Docker Hub (self-corrected).
