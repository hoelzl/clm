- C++ code export (#928, Phase 4): the result cache keys a C++ code output on
  its output stem, so a deck exported under another name by a second spec
  (`07 Functions` in one course, `02 Functions` in another) no longer
  replays the first spec's header include and companion files next to the
  new output. Emitter fixes found by the CppCourses corpus: preprocessor
  lines (`#define`, `#endif`, …) no longer get a stray `;`, a `constinit`
  variable is emitted at namespace scope (a local is ill-formed), a bare
  `new` expression is a display expression instead of a declaration, and
  `thread_local` is recognised as a declaration specifier. Five previously
  failing Completed decks (`program_structure`, `more_initialization`,
  `compile_time`, `good_tests`, `observer`) compile again.
