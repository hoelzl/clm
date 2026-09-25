- `clm info calendar` and `clm info spec-files` now state the schedule model
  explicitly: `export schedule` renders a section as a week by convention,
  the cohort calendar places subsections as a flat sequence of teaching days
  regardless of section length, and a section that is not a Mon–Fri week is
  written with name-only subsections (#916). Design notes for the two other
  design-gated issues — the source-anchored re-recording backlog (#907,
  `docs/claude/design/recordings-rerecord-backlog.md`) and sharing diagram
  renders via source includes (#987,
  `docs/claude/design/shared-diagram-renders.md`) — are recorded without
  behaviour changes.
