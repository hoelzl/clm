- **Docker-mode multi-target builds no longer drop every target but the first.**
  A `--workers docker` build of a spec with multiple `<output-targets>` (e.g. the
  `shared`/`trainer`/`speaker` convention) mounted only the *first* target's root
  at the worker `/workspace`, so notebook-worker writes under any other target
  failed path conversion with `Path '…\output\speaker\…' is not under '…\output\shared'`
  and were silently lost (Issue #384). The Docker worker now mounts the common
  ancestor of all target roots (`Course.workspace_root`) so every target's
  container-written output resolves. Single-target and default-structure builds
  are unchanged. When the targets share no mountable common parent (e.g. different
  Windows drives), the build now fails fast with an actionable message pointing to
  the `--targets <name>` per-target workaround instead of mounting a whole volume.
