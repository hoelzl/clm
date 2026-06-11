New per-deck header marker `clm: no-compile` (#333): C++ decks whose code
export legitimately cannot compile outside the notebook kernel (e.g.
xeus-specific includes, deliberate error demonstrations) become
`EXCLUDE_FROM_ALL` targets in the generated CMake projects — still
buildable explicitly, but skipped by "build all" and by the CI compile
check.
