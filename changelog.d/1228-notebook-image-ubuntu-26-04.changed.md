- **Notebook worker images: Ubuntu 26.04 under both variants, g++ 15, CUDA 13
  (#1228).** `lite` moves from `python:3.12-slim` (Debian) to `ubuntu:26.04`
  and `full`/amd64 from `nvidia/cuda:12.6.1-…-ubuntu24.04` to
  `nvidia/cuda:13.3.1-cudnn-runtime-ubuntu26.04`, with PyTorch from the CUDA
  13.0 wheel index. The system g++ — the C++ standard library of both the
  xeus-cpp kernel and the code-export compile — is now g++ 15 in every
  variant (it was 14 under `lite` and 13 under `full`), which is what makes
  the C++23 library usable: `<print>`, `std::ranges::to`, `<generator>`,
  `<flat_map>`. The kernel stack is pinned (`xeus-cpp` 0.10.0, `cppinterop`
  1.9.0 = clang 21) instead of resolving anew on every image build; the conda
  `libstdcxx-devel` package, which the kernel never read, is no longer
  installed; Java stays at 21 (`openjdk-21-jdk`). A `full` container started
  with `--gpus` now needs a host NVIDIA driver that supports CUDA 13.3 (CLM's
  own Docker worker mode passes no GPUs).
  CLM still requests the `xcpp20` kernel for every C++ deck; selecting C++23
  per course is a follow-up.
