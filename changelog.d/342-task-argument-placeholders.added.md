`clm run` now accepts extra arguments after the spec file and exposes them
to task steps as `{args}` (all of them, expanded to one argv token per
argument as a standalone token) and `{1}`, `{2}`, … (individually,
embeddable in larger tokens) — `clm run release-week course.xml
"name:Week 09"` (#342). A task whose steps reference these placeholders
fails before any step runs when invoked without the corresponding
arguments, and arguments a task never references are an error rather than
silently dropped. `clm validate` accepts the new placeholders in `<tasks>`
blocks.
