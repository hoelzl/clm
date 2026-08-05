- **Internal re-layering (Phase 8 step A2, refs #802)**: `clm.cli.build_data_classes`
  and `clm.cli.error_categorizer` moved to `clm.infrastructure.build_data_classes` and
  `clm.infrastructure.error_categorizer`; `strip_ansi` moved from `clm.cli.text_utils`
  to `clm.infrastructure.utils.text_utils`. The infrastructure layer no longer imports
  from the CLI — backends type their reporter against the new structural
  `BuildReporterProtocol` instead of the CLI's `BuildReporter`. Import paths only; no
  CLI behavior change.
