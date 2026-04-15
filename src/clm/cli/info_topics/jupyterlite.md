# CLM {version} — JupyterLite Output Format

The `jupyterlite` output format produces a deployable static site that runs
course notebooks in the browser with no Python install. Students can access
the site through a hosted URL (GitHub Pages / nginx / any CDN) or locally via
a bundled `launch.py`.

This format is **strictly opt-in**. Courses that do not enable it build
exactly as they do today — no new dependencies required, no change in output.

> **Status (CLM {version})**: Phase 1. The spec is recognized and validated;
> the site builder itself is not yet implemented. A target that opts in today
> will log a "not yet implemented" warning and produce no JupyterLite output.

## The two opt-in gates

Both gates must be set for a single byte of JupyterLite output to be
produced. Either absent ⇒ the build behaves identically to today.

### Gate 1 — the `<jupyterlite>` config block

The `<jupyterlite>` element declares which kernel to use and what wheels to
pre-stage. It may appear in two places:

- **Course-level** (child of `<course>`): defaults for every target that opts
  in.
- **Target-level** (child of `<output-target>`): overrides the course-level
  block **wholesale** for that one target.

**Merge semantics**: target-level replaces course-level entirely — fields are
not merged. If a target wants to reuse most of the course-level config, copy
the full block. This keeps the "did I override or inherit this wheel list?"
question trivial to answer.

### Gate 2 — explicit `<format>jupyterlite</format>` per target

The string `jupyterlite` is a valid `<format>` value, but the default format
set (used when `<formats>` is omitted) is the literal `{html, notebook,
code}`. The only way a target produces JupyterLite is by listing
`<format>jupyterlite</format>` explicitly. A target that omits `<formats>`
entirely never produces JupyterLite output.

## Fields

| Field | Required | Default | Description |
|---|---|---|---|
| `<kernel>` | Yes | — | `xeus-python` (reproducible, preinstalled wheels) or `pyodide` (runtime `%pip install` possible). |
| `<wheels>` | No | empty | List of `<wheel>` children giving wheel paths (relative to course root) to pre-stage into the site. |
| `<environment>` | No | — | Path to an `environment.yml` (relative to course root). Only meaningful with `kernel=xeus-python`. |
| `<launcher>` | No | `true` | Emit `launch.py` + `README-offline.md` for local-first delivery. Set to `false` for hosted-only deployments. |
| `<app-archive>` | No | `offline` | `offline` bundles JupyterLite assets into the site (zero runtime CDN fetches); `cdn` references them externally (smaller site, requires network). |

## Complete example

A course that wants JupyterLite for its *student* and *trainer* targets but
not for its *speaker* target, with the trainer getting an extra wheel set:

```xml
<course>
    ...
    <jupyterlite>                              <!-- course-level default -->
        <kernel>xeus-python</kernel>
        <wheels>
            <wheel>wheels/rich-13.7.1-py3-none-any.whl</wheel>
            <wheel>wheels/ipywidgets-8.1.5-py3-none-any.whl</wheel>
        </wheels>
    </jupyterlite>

    <output-targets>
        <output-target name="students">
            <path>output/students</path>
            <kinds><kind>code-along</kind></kinds>
            <formats>
                <format>notebook</format>
                <format>jupyterlite</format>   <!-- opts in, uses course-level config -->
            </formats>
        </output-target>

        <output-target name="trainer">
            <path>output/trainer</path>
            <kinds><kind>completed</kind></kinds>
            <formats>
                <format>notebook</format>
                <format>jupyterlite</format>
            </formats>
            <jupyterlite>                      <!-- overrides course-level wholesale -->
                <kernel>xeus-python</kernel>
                <wheels>
                    <wheel>wheels/rich-13.7.1-py3-none-any.whl</wheel>
                    <wheel>wheels/ipywidgets-8.1.5-py3-none-any.whl</wheel>
                    <wheel>wheels/pytest-8.3.3-py3-none-any.whl</wheel>
                </wheels>
            </jupyterlite>
        </output-target>

        <output-target name="speaker-notes">
            <path>output/speaker</path>
            <kinds><kind>speaker</kind></kinds>
            <formats>
                <format>html</format>          <!-- no jupyterlite — nothing to opt in -->
            </formats>
        </output-target>
    </output-targets>
</course>
```

## Validation

At spec-parse time:

- Missing `<kernel>` inside a `<jupyterlite>` block ⇒ error.
- Invalid `<kernel>` value (anything other than `xeus-python` or `pyodide`) ⇒
  error.
- Invalid `<app-archive>` value (anything other than `offline` or `cdn`) ⇒
  error.

At course-validate time:

- A target listing `<format>jupyterlite</format>` with **no** effective
  `<jupyterlite>` config (neither target-level nor course-level) ⇒ validation
  error pointing at this topic.

## Not yet supported (v1 scope)

- Non-Python kernels. C++/C#/Java/TypeScript course targets do **not** emit
  JupyterLite.
- Runtime `%pip install` from the open internet as the primary package
  mechanism. v1 pre-stages wheels; runtime install is possible under
  `kernel=pyodide` but unsupported by CLM's default config.
- Merging multiple `<output-target>`s into one JupyterLite site. Each
  target that enables JupyterLite produces its own site.
- Per-student persistence beyond IndexedDB defaults. Student edits survive
  until site data is cleared; this is documented, not engineered around.
