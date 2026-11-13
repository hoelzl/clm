- **`clm validate` reports two more xeus-cpp kernel pitfalls in C++ decks
  (#1228).** New `code_export` findings: a **warning** for output the notebook
  never displays — anything a cell writes to C `stderr`
  (`std::print(stderr, ...)`, `std::println(stderr, ...)`, `fprintf(stderr,
  ...)`, `fputs(..., stderr)`, `perror`) or to `std::clog`; `std::cerr` is
  displayed — and a **warning** for a lambda written anywhere inside a
  top-level `for` or `switch` statement, which kills the kernel under `xcpp20`
  and `xcpp23` alike (also when it is bound to a name inside the loop). A
  lambda passed inline in a top-level call or expression statement, or below
  `do` / `try`, runs or kills the kernel depending on the function called,
  the C++ standard, the standard library version and earlier cells; such
  statements are listed as **information**, so that the author of a deck whose
  kernel dies finds the candidates. Lambdas in declarations, in `std::cout <<`
  statements, below `if` / `while` and in function bodies ran in every
  configuration measured and are not reported.
