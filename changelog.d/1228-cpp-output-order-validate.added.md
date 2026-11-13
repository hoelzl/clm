- **`clm validate` warns when a C++ cell's notebook output is reordered
  (#1228).** In a notebook cell the xeus-cpp kernel displays everything
  written through `std::cout` (`SHOW` / `CLM_DISPLAY` included) before
  everything written through C stdio (`std::print`, `std::println`, `printf`,
  `puts`), whatever the order in the code, while the exported program prints
  in program order. The `code_export` checks now flag the shapes the notebook
  gets wrong: a C-stdio write before a `std::cout` write, both in one loop,
  and both in one function or lambda. `std::cout` output followed by
  `std::println` output is displayed correctly and stays silent, as does
  `std::println(std::cout, ...)`. One warning per cell; `start` and `del`
  cells are not checked.
