`clm build --no-diagrams` skips DrawIO and PlantUML processing entirely
(#353), mirroring `--no-html`: diagram sources are excluded from the build,
so no conversion jobs are scheduled and no plantuml/drawio workers are
started. Rendered images committed next to the sources (`slides/**/img/`)
still ship as ordinary image files, so output stays complete. Intended for
machines without the diagram binaries — e.g. the code-export compile CI,
where every diagram job previously failed and forced a blanket
`--no-fail-on-error`.
