- **C++ code export: cells using digit separators (`2'000'000'000`) are
  classified correctly** (#922). The comment/string stripper treated the
  `'` as a char-literal quote and swallowed the rest of the cell, so a
  declaration followed by a statement was emitted as one namespace-scope
  item that MSVC and g++ reject. A `'` between two digits of a numeric
  literal is now kept as a separator (hex, binary, and fractional literals
  included); the statement lands in its `slide_NN()` function as for any
  other mixed cell. Prefixed char literals (`u8'a'`, `L'x'`) are unaffected.
