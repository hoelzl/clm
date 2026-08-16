---
_security: fixed
---

MCP tool arguments are model-generated and therefore only semi-trusted:
a prompt-injected transcript or slide body can steer the model into
passing hostile paths. Every path-accepting MCP handler now resolves
its path arguments under the configured `data_dir` (symlink-correct
resolve-then-contain: the resolved target must equal `data_dir` or sit
below it; absolute paths are allowed only when they resolve inside).
This covers read tools (whose output reaches the model), mutating tools
(normalize/extract/inline), the harvest family (`_resolve_under`), the
`cache_root`/`transcript`/`alignment` overrides, and the
`course_authoring_rules` slug. Refusals return the handlers' uniform
`{"error": ...}` JSON naming the `data_dir` boundary. The CLI remains
unrestricted (trusted operator input) and is the documented escape
hatch for out-of-tree files.
