- **Internal re-layering (Phase 8 step S1 of the A1/A3 plan, refs #802)**: shared
  vocabulary modules descended to their honest layer — prog-lang tables and
  `comment_token_for_path` to `clm.core.utils.prog_lang_utils`, slide tags /
  workshop scope / sidecar layout / deck markers / voiceover companion paths /
  HTTP-replay trace to `clm.core.*`, the JupyterLite manifest helpers (and the
  `jupyterlite-core` version pin) to `clm.core.utils.jupyterlite_manifest`,
  diagram-tool locators to `clm.infrastructure.utils.diagram_tools`, and the C++
  code analysis/emission pair to `clm.workers.notebook`. Import paths only; no
  CLI behavior change.
