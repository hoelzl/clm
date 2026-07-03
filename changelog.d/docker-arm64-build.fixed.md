- **Docker images build on OSX/arm64 (Apple Silicon).** The `drawio`,
  `notebook:lite`, and `notebook:full` Dockerfiles no longer hardcode
  x86_64-only fetched assets — they select the architecture-correct
  `.deb`/binary and SHA-256 via BuildKit's `TARGETARCH`, so `clm docker build`
  succeeds on linux/arm64. The notebook `full` variant is GPU-accelerated
  (CUDA/PyTorch) on amd64 and CPU-only on arm64, where it reuses the
  `python:3.12-slim` base (no `nvidia/cuda` arm64 image exists) and skips
  GPU-only packages without an aarch64 wheel (`fastembed-gpu`). amd64 builds
  are unchanged.
