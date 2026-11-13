- **C++23 per course: `<prog-lang standard="23">cpp</prog-lang>` (#1228).**
  The new `standard` attribute of the course spec's `<prog-lang>` selects the
  C++ standard of a course — `"20"` (default) or `"23"` — and drives both
  places that were hardcoded to C++20: the Jupyter kernel the notebooks are
  executed in and written for (`xcpp23`), and the `CMAKE_CXX_STANDARD` of the
  exported CMake projects. One setting, so a deck cannot be C++23 in the
  notebook and C++20 in its export. The standard is part of the notebook cache
  keys only when it is not the default: courses without the attribute keep
  their caches, and a deck shared by a C++20 and a C++23 course is cached per
  standard. An unsupported value is a spec error; a misspelt attribute is an
  `unknown_attribute` finding of `clm validate`; `clm cache explain` shows the
  standard. Needs the Ubuntu 26.04 notebook images (g++ 15) for the C++23
  library.
