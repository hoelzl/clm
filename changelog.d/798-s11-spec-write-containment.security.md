- **Spec-driven writes are contained, and the destructive output operations
  now require proof of ownership** (adversarial-review finding S11, #798).
  A course spec decides what `clm build` writes *and deletes*: the post-build
  sweep removes everything under an output root the build did not write, and
  `--clean` wipes the root outright. Neither the paths nor the deletions were
  bounded. Two layers now bound them.

  **Spec validation** (fails before any job runs): an
  `<output-target><path>` is refused when it is absolute, contains a `..`
  segment, or resolves onto the course data directory itself — the
  one-character `<path>.</path>` typo that used to aim the sweep at the
  course sources. The overlap check resolves symlinks rather than comparing
  strings. `OutputTarget.from_spec` enforces the same rule, so `Course`
  construction paths that do not validate first (`clm git`, the release
  tooling, the MCP server) refuse too. `<dir-group><path>` and each
  `<subdir>` go through the canonical `<include>`-path validator
  (course-root relative, no `..`), and `<dir-group><name>` is sanitized per
  path segment the way section names always were — nesting
  (`Code/Solutions`) still works, traversal no longer does.
  `sanitize_file_name` never returns `.` or `..` again, which closes the
  same hole for section names, course names and notebook titles.

  **Ownership gate**: `--clean` and the sweep only act inside an output root
  clm can prove is its own — one that was empty or absent at build start,
  that carries the `.clm-manifest.json` provenance index from an earlier
  build, or (sweep only) whose entire content is accounted for by the build's
  write registries. Anything else is refused with the directory named:
  `--clean` fails the build having deleted nothing (the check runs before
  `git_dir_mover` moves anything), and the sweep leaves that root untouched.
  The sweep is now plan-then-execute so a refusal cannot leave a half-swept
  tree. The new `clm build --allow-unowned-output` overrides the gate;
  `--clean` deliberately does not, since it is the operation being gated.

  **Breaking**: absolute `<output-target><path>` values are refused — use
  course-relative paths, or `clm build --output-dir DIR` to build elsewhere.
  Output trees that predate the provenance manifest (CLM < 1.8) or were built
  with `--no-provenance-manifest` are refused on their next `--clean` or
  sweep until one normal build writes the manifest. See `clm info migration`.
