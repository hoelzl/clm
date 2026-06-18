# Task Sequences (`clm run`)

Course maintenance often involves the same sequence of clm commands, run in
the same order, with the same arguments — for example regenerating calendar
and outline exports **before** the build that copies them into the output
directory. Forgetting one step ships stale files.

The `<tasks>` block in the course spec captures such sequences once, next to
the course they belong to, and `clm run` executes them with a single command.
Because tasks live in the spec (not in a shell script), they are versioned
with the course and work identically on every machine and operating system.

## Declaring tasks

Add a `<tasks>` block to the course spec:

```xml
<course>
    ...
    <tasks>
        <task name="pre-release" description="Regenerate exports, then build">
            <step>calendar generate {spec} --channel jan -f ics -o release/jan.ics</step>
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

Each `<step>` is one clm command line without the leading `clm` — exactly
what you would type at the prompt. `{spec}` expands to the absolute path of
the spec file passed to `clm run`. See `clm info spec-files` for the full
element reference (placeholders, quoting, path rules).

### Releasing to every channel in one step

Because `clm release` accepts a glob or `--all-channels` (see
[Per-Topic Solution Release](solution-release.md#releasing-to-several-channels-at-once)),
a release routine for a multi-channel cohort stays a fixed handful of steps
instead of growing one `release sync` per channel:

```xml
<task name="weekly-release" description="Build, then release this week to every cohort channel">
    <step>build {spec} --provenance-manifest</step>
    <step>release sync {spec} --all-channels --push</step>
</task>
```

The single `--all-channels` step promotes and pushes each channel in turn — a
four-channel cohort no longer needs four near-identical steps. Use a glob
(`release sync {spec} --channel 'materials/*' --push`) when a task should target
just one stream.

## Running tasks

```bash
# Iterate while authoring...
clm build course.xml --watch

# ...then run the whole pre-release sequence in one shot:
clm run pre-release course.xml

# What tasks does this course define?
clm run course.xml

# What exactly would run?
clm run pre-release course.xml --dry-run
```

Steps run in order; each is echoed as `[i/N] clm <command>` before it
executes. The first failing step aborts the task, and its exit code becomes
clm's exit code. All steps are validated (placeholders, command existence)
before the first one runs, so a typo in a late step fails immediately instead
of after a long build.

## Design constraints (and why)

- **clm commands only.** Steps run without a shell — no pipes, redirection,
  or external programs. This is what makes tasks portable; machine-specific
  automation belongs in a script outside the spec.
- **Forward slashes in paths.** Steps are parsed with POSIX quoting rules;
  clm accepts forward-slash paths on every platform, including Windows.
- **No task nesting.** A step cannot invoke `clm run`.

`clm validate course.xml` checks every declared task, so broken tasks surface
during normal validation rather than on release day.
