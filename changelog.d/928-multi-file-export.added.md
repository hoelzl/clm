- **C++ code export: header, workshop files and per-module CMake projects**
  (#928, phase 3). A C++ deck with workshop ranges or `global` cells is now
  exported as a file set next to `<deck>.cpp`: `<deck>.hpp` carries the
  hoisted includes and the lecture part's `global`-tagged cells (that is how
  a workshop file sees a lecture definition), and every workshop range
  becomes its own `<deck>_workshop_N.cpp` with the range's section
  functions and its own `main()` — the code-along/partial skeleton of that
  workshop, complete lecture file alongside. Workshop boundaries open
  sections, variable promotion stays within one file, and the dangling-cell
  scan stays deck-global. The generated CMake export is now one project per
  module (`add_subdirectory` from the kind root; a module directory opens
  standalone) with one executable target per deck **and per workshop
  file**, so workshop code joins the compile gate; `clm: no-compile`
  excludes a deck's workshop targets too. The extra files travel with the
  main output across the worker/CLI boundary: the worker reports them with
  the job result, the host registers them for the stray-file sweep, stores
  them in the result cache and replays them on a cache hit, and the
  provenance manifest lists them (`NotebookResult.companion_files`; the
  notebook cache-hash schema is bumped to v5, so the first build after
  upgrading re-executes every notebook). Decks without workshops or
  `global` cells are unchanged single files.
