The `format="code"` output for C++ decks is now a compilable translation
unit instead of a jupytext concatenation (#333, phase 1). Each top-level
item of each code cell is routed to its proper place: `#include` lines are
hoisted to the top and deduplicated, definitions and global variables stay
at namespace scope in cell order, statements are wrapped in per-cell
`void slide_NN()` functions called in order from a generated `main()`, and
bare display expressions are wrapped in a `CLM_DISPLAY` helper that prints
the value when an `operator<<` exists. Decks that define their own `main()`
suppress the generated one. Outputs for other programming languages and
formats are unchanged.
