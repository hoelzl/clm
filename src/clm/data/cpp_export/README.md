# Support headers for the C++ code export (issue #333)

`clm.core.cmake_export` copies `include/` into a course's code-output
directories when the exported code (or a deck-local header) references one
of these headers, and adds the directory to the generated CMake project's
include path.

| Header | Origin | Version |
|---|---|---|
| `include/nlohmann/json.hpp` | <https://github.com/nlohmann/json> (single-header release asset, MIT) | v3.12.0 |
| `include/xcpp/xdisplay.hpp` | CLM shim for the xeus-cpp display header | — |

To update nlohmann/json, download `json.hpp` from the desired GitHub
release and update the version here.

The `xcpp/xdisplay.hpp` shim replicates the kernel's display dispatch for
plain-terminal output: an ADL-found `mime_bundle_repr(value)` overload wins
(its `"text/plain"` entry is printed), then `operator<<`, then a
placeholder. It depends on the vendored nlohmann header (xeus's `nl`
namespace alias is provided by the shim).
