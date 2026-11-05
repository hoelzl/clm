- `clm build --only-sections` now regenerates the C++ code export's CMake
  project files for the selected sections. The section cleanup deleted each
  selected module's `CMakeLists.txt` and the post-build CMake pass was
  skipped in that mode, so the kind-root project's `add_subdirectory` for
  the rebuilt section failed and the whole course project no longer
  configured, while the build reported success. The kind-root
  `CMakeLists.txt` is rewritten too. It keeps every unselected section's
  module that holds a `CMakeLists.txt` from an earlier build, leaves those
  files untouched and never reuses their target names. It stops adding a
  rebuilt section that produced no code. The CMake files are now written
  whenever a build finishes, `--no-provenance-manifest` and timed-out
  builds included, and are skipped only for `--snapshot` /
  `--verify-against` (#1138).
