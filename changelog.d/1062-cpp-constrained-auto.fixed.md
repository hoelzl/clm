- **C++ export recognizes constrained placeholder types (#1062).** A function
  whose return type is a C++20 constrained placeholder
  (`SignedNumber auto twice(SignedNumber auto x) {...}`,
  `std::integral auto f(int x) {...}`, `Concept<Args> decltype(auto) g()`)
  was classified as a display expression and wrapped in `CLM_DISPLAY(...)`
  inside its section function, so the export failed to compile. Such
  functions are now hoisted to namespace scope like other function
  templates, and `std::integral auto x = 1;` is a variable declaration.
