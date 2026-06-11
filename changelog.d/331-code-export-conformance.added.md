- New deterministic validation check `code_export` (#331): verifies the structural
  invariants the planned compilable C++ project export (#333) relies on, for
  `.cpp` decks only (no-op elsewhere). Errors on variable/function/type
  redefinitions within one (language × completed) output view — which xeus-cpp
  forbids at runtime anyway — and on `main()` definitions in decks without the
  new `// clm: allow-main` header marker; warns on Jinja directives inside code
  cells; reports cells mixing definitions and statements as info. Overloads
  (parameter types + const-ness), template specializations, DE/EN-paired cells,
  and `start`/`completed` pairs are correctly not flagged. Backed by the new
  heuristic classifier `clm.slides.cpp_code_analysis`, validated against the
  full CppCourses corpus (4,818 top-level items, 99.9% classified).
