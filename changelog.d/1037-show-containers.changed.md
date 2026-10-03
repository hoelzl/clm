- **`SHOW` prints standard containers and other common value types (#1037).**
  `clm/display.hpp` used to print only types with an `operator<<`. Every
  `std::vector`, `std::map`, `std::pair`, `std::tuple`, `std::optional` and
  scoped enum showed `<unprintable value>`, and non-char C arrays printed their
  decayed address. Now:
  - ranges print as `{1, 2, 3}`, maps as `{k: v}`, pairs and tuples as `(a, b)`;
  - optionals print their value or `nullopt`, scoped enums their underlying value;
  - formatting recurses into nested values, quoting nested strings and chars;
  - ranges longer than 100 elements are capped.

  A user-defined `operator<<` still takes precedence, and top-level values print
  exactly as before. The formatting uses plain function templates (no lambdas),
  so it is safe in xeus-cpp. Verified in the `clm-notebook-processor` kernel
  and with g++ 13 `-Werror`. Rolling out to notebooks needs the worker image
  rebuild (bundled with the release) and a rebuild of the student notebook
  image, which copies this header.
