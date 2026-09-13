- `clm slides cpp-show` rewrites the bare display expressions of C++ decks
  to `SHOW(expr);` and adds `#include <clm/display.hpp>` (#928): the
  xeus-cpp kernel prints nothing for a cell without a trailing `;`, so decks
  show values through the `SHOW` macro (new alias of `CLM_DISPLAY` in
  `clm/display.hpp`), which the notebook worker image now installs into the
  kernel's include path. The exported program prints the same `expr = value`
  lines.
