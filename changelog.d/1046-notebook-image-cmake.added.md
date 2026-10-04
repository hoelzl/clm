- **The notebook worker image can build the C++ code export.** Both variants
  now install `cmake` next to the g++ they already had, so the CMake projects
  `clm build` writes under `Slides/Cpp/{Completed,Code-Along}/` compile inside
  the image (`cmake -S <dir> -B <dir>/.build && cmake --build <dir>/.build`).
  A trainer who builds with Docker workers no longer needs a host toolchain to
  check that an export compiles.
