C++ code exports now vendor support headers (#333): when exported code —
or a deck-local header next to it — references `nlohmann/json.hpp` or the
xeus display header `xcpp/xdisplay.hpp`, the generated CMake project gets
an `include/` directory with the vendored nlohmann single header (v3.12.0)
and a CLM shim for `xcpp::display` that replicates the kernel's display
dispatch (`mime_bundle_repr` via ADL, then `operator<<`, then a
placeholder). Decks that use the kernel's display API therefore compile
and run unchanged outside Jupyter.
