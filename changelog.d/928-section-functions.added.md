- **C++ code export: per-deck study material instead of a compile-gate
  artifact** (#928, phase 1). The `format="code"` export for
  `prog_lang="cpp"` now emits one **section function per slide** (a new
  function opens at every `slide`/`subslide` cell, named after its
  `slide_id`, e.g. `void brace_initialization()`, with a
  `== Brace initialization ==` banner from the section's heading), keeps
  the **markdown narrative as `//` comment blocks**, and labels displayed
  expressions — `CLM_DISPLAY(i1)` now prints `i1 = 10`. Top-level variables
  stay local to their section unless a later section or a namespace-scope
  definition references them, in which case the emitter **promotes them to
  namespace scope automatically**; the new code-cell tag **`global`** forces
  a cell to namespace scope. Definitions, includes and preprocessor lines
  land at namespace scope as before; blanked code-along cells leave a
  `// TODO` in their section body. The per-cell `slide_NN()` functions of
  the #333 export are gone. Header/workshop-file splitting and per-module
  CMake projects follow in later phases; the handover is
  `docs/claude/handovers/cpp-ide-export-handover.md`.
