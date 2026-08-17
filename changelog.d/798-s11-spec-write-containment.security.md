- **Spec-driven writes are contained, and the destructive output operations
  now require proof of ownership** (adversarial-review finding S11, #798).
  A course spec decides what `clm build` writes *and deletes*: the post-build
  sweep removes everything under an output root the build did not write, and
  `--clean` wipes the root outright. Neither the paths nor the deletions were
  bounded. Two layers now bound them.

  **Spec validation** (fails before any job runs): an
  `<output-target><path>` is refused when it is absolute, blank, contains a
  `..` segment, or resolves onto the course data directory itself — the
  one-character `<path>.</path>` typo that used to aim the sweep at the
  course sources, and its likelier sibling, a pretty-printed empty
  `<path>` element that resolved to the course root on Windows. Both sides
  of the overlap check are resolved, so a path that reaches the course root
  through a symlink is caught too. `OutputTarget.from_spec` enforces the
  same rules, and the three commands that read the spec path without
  building a `Course` — `clm git`, `clm release`, `clm zip` — validate
  explicitly (surfacing a usage error, not a traceback), so no command acts
  on a path `clm build` refuses. `<dir-group><path>` and each `<subdir>` go
  through the canonical `<include>`-path validator (course-root relative,
  no `..`), and `<dir-group><name>` is sanitized per path segment the way
  section names always were — nesting (`Code/Solutions`) still works,
  traversal no longer does. `sanitize_file_name` never returns a directory
  reference again (`.`, `..`, and on Windows any run of dots, which
  collapses the same way), closing the same hole for section names, course
  names and notebook titles.

  **Ownership gate**: `--clean` and the sweep only act inside an output root
  clm can prove is its own — one that was empty or absent at build start,
  that carries the `.clm-manifest.json` provenance index from an earlier
  build, or (sweep only) whose entire content is accounted for by the build's
  write registries. Anything else is refused with the directory named:
  `--clean` fails the build having deleted nothing (the check runs before
  `git_dir_mover` moves anything), and the sweep leaves that root untouched.
  The sweep is now plan-then-execute so a refusal cannot leave a half-swept
  tree, and a root clm could not prove it owns gets **no provenance
  manifest** — the manifest is the evidence the next build's gate reads, so
  writing it would hand that build the permission this one declined. That
  holds whether or not a sweep ran, so `--no-sweep` / `--incremental` (and a
  build with errors) cannot quietly mark an unverified tree either; targets
  that swept cleanly still get theirs. The new
  `clm build --allow-unowned-output` overrides the gate — that build sweeps
  the tree *and* marks it, so later builds are ungated; `--clean`
  deliberately does not override, since it is the operation being gated.

  **Breaking**: absolute `<output-target><path>` values are refused — make
  the path course-relative and move (or symlink) the tree under the course
  root. Output trees that predate the provenance manifest (CLM < 1.8), or
  were built with `--no-provenance-manifest`, and that hold files the build
  does not produce are refused on their next `--clean` or sweep; the refusal
  is permanent until the directory changes or `--allow-unowned-output`
  adopts the tree. See `clm info migration`.
