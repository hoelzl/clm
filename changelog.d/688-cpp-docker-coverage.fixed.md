- **C++ execution is now covered by CI.** Nothing automated checked that a valid
  C++ deck still compiles and runs: the one Docker test that ran a C++ notebook
  ran a deliberately *broken* one to check error attribution, and it asked for
  the `full` notebook image, which no workflow builds — so it skipped in CI and
  ran only on a machine that had pulled the 23 GB image. Three changes, no new
  images and no added build time:
  - The C++ kernels come from the Dockerfile's shared stage, not from the `full`
    variant, so every notebook image has them. C++ tests now use the image CI
    already builds.
  - A new test executes a valid C++ deck through the Docker notebook worker and
    requires the rendered deck to contain a value the C++ code *computes*, so a
    kernel that stops running code cannot pass it.
  - A new test compares the kernel names CLM asks for against the kernels the
    image installs. A rename in an image bump — the xeus-cling → xeus-cpp move
    did exactly this — breaks every deck in a language at once, and now fails in
    seconds with the kernel name in the message.
- **Known gap now recorded**: CLM configures a `rust` kernel that none of the
  images install, so a Rust deck in Docker mode fails with `NoSuchKernel`. The
  new kernel test pins this as the only expected hole, so a second one cannot
  appear unnoticed.
