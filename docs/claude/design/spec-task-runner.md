# Spec-Defined Task Runner (`clm run`)

**Status**: Implemented (branch `claude/spec-task-runner`)
**Date**: 2026-06-10

## Motivation

Course maintainers run the same course-specific sequence of clm commands at
certain points in the workflow. The motivating case: before releasing, the
calendar and outline exports must be regenerated whenever the course spec has
changed, and this must happen **before** the `clm build` step that copies
those files into the output directory. Forgetting a step ships stale
calendar/outline files.

A shell script could automate this, but:

- scripts are **machine-specific** (shell, paths, PATH setup), while the
  commands are **course-specific**;
- every step is a clm command anyway, so the course spec — already the
  canonical home for course-specific configuration — can carry the sequence
  portably.

So: declare named task sequences in the course spec, run them with a single
command. The trainer iterates with `clm build` (possibly `--watch`), and when
ready to release types:

```
clm run pre-release course.xml
```

and every export/build step runs in order with the correct arguments.

This is deliberately a **task runner**, not a hook system: nothing fires
implicitly inside other commands. `clm build` stays exactly as fast and
predictable as today; the sequence runs only when explicitly requested.

## Spec format

A new optional top-level `<tasks>` element in the course spec XML:

```xml
<course>
  ...
  <tasks>
    <task name="pre-release" description="Regenerate exports, then build">
      <step>export calendar {spec} --channel jan -f ics -o release/jan.ics</step>
      <step>export outline {spec} -o outline/</step>
      <step>build {spec} --provenance-manifest</step>
    </task>
    <task name="check">
      <step>validate {spec}</step>
      <step>calendar check {spec} --channel jan</step>
    </task>
  </tasks>
</course>
```

- `<task name="...">` — required, unique within the spec. Optional
  `description` attribute shown by the task listing.
- `<step>` — text content is one clm command line **without the leading
  `clm`**. Steps run in document order.

### Placeholders

Step text supports placeholder substitution before tokenization:

| Placeholder | Expands to |
|---|---|
| `{spec}` | Absolute path of the spec file passed to `clm run` |

An explicit placeholder is used instead of auto-injecting the spec path
because commands take `SPEC_FILE` at different positions and some steps may
not need it at all (`clm workers status`, …). Explicit text in the spec is
exactly what would be typed at the prompt — no hidden rewriting.

Unknown `{...}` placeholders are a hard error at resolution time (catches
typos before anything runs). Literal braces, should they ever be needed, are
written `{{` / `}}`.

Future placeholders (not v1): `{course-root}` (spec's parent directory), and
user parameters such as `{channel}` supplied via
`clm run pre-release --param channel=jan` — deferred until a concrete need
appears.

### Path style inside steps

Steps are tokenized with `shlex.split(..., posix=True)`, where backslash is
an escape character. Paths inside steps must therefore use **forward
slashes** (`-o release/jan.ics`), which clm accepts on all platforms and
which keeps specs portable. Quoting (`"a path/with spaces.md"`) works as in
a POSIX shell. This is documented in the spec-files info topic.

## CLI

New top-level command (no existing `run` command or alias conflicts):

```
clm run TASK SPEC_FILE [--dry-run]
clm run SPEC_FILE            # lists available tasks (TASK omitted)
clm run --list SPEC_FILE     # explicit spelling of the same
```

Both positionals are declared `required=False`; the command resolves them at
runtime: with two arguments it runs the task; with one argument that is an
existing file it lists that spec's tasks (name, description, step count);
anything else is a usage error. This keeps the natural word order
(`clm run pre-release course.xml`) while making listing cheap to discover.

Behavior while running:

- Before any step executes, **all** steps are resolved and validated
  (placeholders substituted, tokenized, first token(s) checked against the
  Click command tree). A typo in step 3 fails fast, not after 10 minutes of
  build in step 1.
- Each step is echoed as `[i/N] clm <resolved command>` before running, with
  stdout/stderr passed straight through (live build progress).
- The first non-zero exit aborts the task; `clm run` reports which step
  failed and exits with that step's exit code.
- `--dry-run` prints the fully resolved command lines and runs nothing.

## Execution model

Each step runs as a **subprocess**: `[sys.executable, "-m", "clm", *tokens]`.

Why subprocess rather than invoking the Click commands in-process:

- **Fidelity**: a step behaves exactly as if typed at the prompt — same
  argument parsing, same exit codes, same logging setup.
- **Isolation**: clm commands mutate global state (logging configuration,
  worker pools, job-queue connections). Running `build` after `export`
  in one process risks cross-contamination that subprocesses rule out.
- **Cost is irrelevant**: ~1 s of interpreter startup per step is noise next
  to export/build runtimes.

`sys.executable -m clm` (not a PATH lookup of `clm`) guarantees the steps use
the same interpreter and venv as the parent `clm run` — significant in this
repo, where each worktree has its own venv. This requires a new two-line
`src/clm/__main__.py`:

```python
from clm.cli.main import cli

cli()
```

Steps inherit the parent's working directory and environment, so relative
output paths in steps (`-o outline/`) resolve exactly as they would
interactively.

## Constraints and safety

- **clm commands only.** The runner prepends the interpreter; there is no
  shell, so steps cannot smuggle in arbitrary programs, pipes, or
  redirection. This is a feature: it is what makes tasks portable across
  machines/OSes, and it keeps "commands embedded in a data file" from
  becoming an arbitrary-code-execution channel that surprises anyone opening
  a course repo. An escape hatch for external commands is deliberately out
  of scope; if a real need appears it gets its own opt-in design (explicit
  `<step shell="...">` plus a consent story), not a quiet extension.
- **No nesting in v1.** `run` is rejected as a step verb, so tasks cannot
  invoke tasks (no cycle handling needed). If composition is wanted later,
  the cleaner form is `<step task="other-task"/>` expanded inline at
  resolution time with cycle detection.
- Validation of the first token(s) against the actual Click command tree
  (`clm.cli.main.cli`) means renamed/removed commands are caught at
  resolution time with a clear message, version-accurately.

## Implementation sketch

1. **Spec parsing** (`src/clm/core/course_spec.py`):
   - `@frozen class TaskSpec`: `name: str`, `description: str = ""`,
     `steps: tuple[str, ...]`.
   - `CourseSpec.tasks: tuple[TaskSpec, ...]` parsed from
     `root.find("tasks")` in `from_file()`. Parse-time errors: duplicate
     task names, empty `name`, task with no steps, empty step text.
2. **`src/clm/__main__.py`** — module entry point (above).
3. **CLI command** (`src/clm/cli/commands/run.py`):
   - Resolution: placeholder substitution → `shlex.split(posix=True)` →
     first-token validation against the command tree (walk `cli.commands`
     through groups for multi-word verbs like `export calendar`).
   - Execution loop with echo, pass-through output, abort-on-failure.
   - Registered in `main.py`: `cli.add_command(run_cmd, name="run")`.
4. **Tests**:
   - `tests/core/`: `<tasks>` parsing (happy path, duplicates, empty task,
     missing name).
   - `tests/cli/`: resolution + validation + `--dry-run` + listing via
     `CliRunner` (mind the Click 8.1/8.2 ctor compat pattern); failure
     propagation with a monkeypatched subprocess runner; one cheap
     end-to-end run of a trivial real step (e.g. `info commands`) to prove
     the `-m clm` re-invocation works.
5. **Docs** (mandatory per the info-topics rule):
   - `src/clm/cli/info_topics/spec-files.md`: `<tasks>`/`<task>`/`<step>`
     element, placeholder table, forward-slash rule.
   - `src/clm/cli/info_topics/commands.md`: `clm run`.
   - User guide: short "Task sequences" section (likely a new
     `docs/user-guide/tasks.md` linked from the docs map), cross-referenced
     from `solution-release.md`'s workflow steps.
   - `CHANGELOG.md`.

## Out of scope (v1)

- Arbitrary/external (non-clm) commands — see Constraints.
- Task nesting / composition.
- Parallel steps, conditionals, per-step environment variables.
- `--watch` integration (the iterate loop stays plain `clm build --watch`;
  `clm run` is the explicit "I'm ready" action).
- `--keep-going` (continue past failures).

## Resolved questions (2026-06-10)

1. **Command name**: `clm run`. Listing is the only other task-specific
   action and the no-task form covers it; easy to regroup later if the task
   system grows.
2. **Listing UX**: a single existing-file argument lists that spec's tasks;
   `--list` is the explicit spelling.
3. **`clm validate` integration**: yes — the spec validator resolves every
   step (structure, placeholders, command-tree existence), the same checks
   `clm run` performs before executing. Structural rules live in
   `CourseSpec.validate_tasks()` (also part of `CourseSpec.validate()`, so
   build/export commands reject a malformed `<tasks>` block too).
