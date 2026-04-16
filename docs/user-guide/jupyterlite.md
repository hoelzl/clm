# JupyterLite Output

CLM can produce a self-contained JupyterLite static site from your course
notebooks. Students open the site in any modern browser — no Python install
required. The site can be hosted on GitHub Pages, any CDN, or run locally from
a USB stick.

This feature is **strictly opt-in**. Courses that do not enable it build
exactly as they do today.

## Installation

Install CLM with the JupyterLite extra:

```bash
pip install "coding-academy-lecture-manager[jupyterlite]"
```

This is also included in the `[all]` extra. The extra brings in
`jupyterlite-core`, the Pyodide and xeus-python kernel addons, and
`jupyter-server`.

## Quick Start

Add two things to your course spec:

1. A `<jupyterlite>` config block (at course or target level).
2. `<format>jupyterlite</format>` on each target that should produce a site.

```xml
<course>
    ...
    <jupyterlite>
        <kernel>xeus-python</kernel>
    </jupyterlite>

    <output-targets>
        <output-target name="students">
            <path>output/students</path>
            <kinds><kind>code-along</kind></kinds>
            <formats>
                <format>notebook</format>
                <format>jupyterlite</format>
            </formats>
        </output-target>
    </output-targets>
</course>
```

Then build:

```bash
clm build course.xml
```

The JupyterLite site appears alongside the notebook output at
`<target-output>/JupyterLite/<Kind>/`. Students run `python launch.py` in that
directory to open the site in their browser.

## Configuration Reference

The `<jupyterlite>` block supports these fields:

| Field | Required | Default | Description |
|---|---|---|---|
| `<kernel>` | Yes | — | `xeus-python` (reproducible, preinstalled wheels) or `pyodide` (runtime `%pip install`). |
| `<wheels>` | No | empty | `<wheel>` children listing wheel paths relative to course root. |
| `<environment>` | No | — | Path to `environment.yml` (xeus-python only). |
| `<launcher>` | No | `python` | `python`, `miniserve`, or `none` (see [Launchers](#launchers)). |
| `<app-archive>` | No | `offline` | `offline` bundles all assets; `cdn` references them from the network. |
| `<branding>` | No | — | UI customization (see [Branding](#branding)). |

### Placement

The `<jupyterlite>` block can appear at two levels:

- **Course-level** (child of `<course>`): default config for every target that
  opts in.
- **Target-level** (child of `<output-target>`): overrides the course-level
  block **wholesale** for that one target.

Target-level replaces course-level entirely — fields are not merged. If a
target wants to reuse most of the course-level config, copy the full block.

## Launchers

The `<launcher>` field controls how students run the built site locally.

### Python launcher (default)

```xml
<launcher>python</launcher>
```

Emits `launch.py` — a zero-dependency Python script (requires 3.8+). Starts a
local server, fixes `.wasm` MIME types on Windows, and opens the browser.

### miniserve launcher

```xml
<launcher>miniserve</launcher>
```

Bundles prebuilt [miniserve](https://github.com/svenstaro/miniserve) binaries
for Windows, macOS (x64 + ARM), and Linux (~20 MB total). No runtime
dependencies at all. Emits:

- `launch.bat` (Windows)
- `launch.command` (macOS — double-click in Finder)
- `launch.sh` (Linux)

The binaries are downloaded once at build time from a pinned GitHub release,
SHA-256 verified, and cached locally.

### No launcher

```xml
<launcher>none</launcher>
```

Skips launcher emission. Use this for hosted-only deployments (GitHub Pages,
CDN).

## Branding

Customize the JupyterLab UI with a `<branding>` block inside `<jupyterlite>`:

```xml
<jupyterlite>
    <kernel>xeus-python</kernel>
    <branding>
        <theme>dark</theme>
        <logo>assets/logo.svg</logo>
        <site-name>My Python Course</site-name>
    </branding>
</jupyterlite>
```

| Field | Description |
|---|---|
| `<theme>` | `light` or `dark`. Sets the default JupyterLab theme. |
| `<logo>` | Path to a logo image displayed in the JupyterLab top bar. |
| `<site-name>` | Title text for the JupyterLab logo area. |

All branding fields are optional. If `<branding>` is absent, the default
JupyterLab theme is used.

## Previewing a Built Site

After building, preview a site locally without re-building:

```bash
clm jupyterlite preview --target students course.xml
```

Options:

| Option | Description |
|---|---|
| `--target` | Target name (required). |
| `--kind` | Output kind (auto-detected if only one). |
| `--language` | Language code (auto-detected if only one). |

## Complete Example

A course with student and trainer targets, where the trainer gets an extra
wheel and uses miniserve:

```xml
<course>
    ...
    <jupyterlite>
        <kernel>xeus-python</kernel>
        <wheels>
            <wheel>wheels/rich-13.7.1-py3-none-any.whl</wheel>
            <wheel>wheels/ipywidgets-8.1.5-py3-none-any.whl</wheel>
        </wheels>
        <branding>
            <theme>dark</theme>
            <site-name>Python Course</site-name>
        </branding>
    </jupyterlite>

    <output-targets>
        <output-target name="students">
            <path>output/students</path>
            <kinds><kind>code-along</kind></kinds>
            <formats>
                <format>notebook</format>
                <format>jupyterlite</format>
            </formats>
        </output-target>

        <output-target name="trainer">
            <path>output/trainer</path>
            <kinds><kind>completed</kind></kinds>
            <formats>
                <format>notebook</format>
                <format>jupyterlite</format>
            </formats>
            <jupyterlite>
                <kernel>xeus-python</kernel>
                <launcher>miniserve</launcher>
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
                <format>html</format>
            </formats>
        </output-target>
    </output-targets>
</course>
```

## How Student Work Is Saved

JupyterLite stores notebook edits in the browser's IndexedDB. This means:

- Changes persist between sessions in the **same browser**.
- Switching browsers or using private/incognito mode starts fresh.
- Clearing site data erases all edits with no recovery.

Students should use **File > Download** inside JupyterLab to save permanent
copies of their work.

## Choosing a Kernel

| Kernel | Best for | Trade-offs |
|---|---|---|
| `xeus-python` | Reproducible environments, offline delivery | Wheels must be pre-staged; no runtime `%pip install`. |
| `pyodide` | Flexibility, runtime package installs | First cell execution slower; some packages not available. |

For most courses, `xeus-python` with pre-staged wheels is recommended.

## Troubleshooting

**"jupyterlite-core is not installed"**: Run `pip install -e ".[jupyterlite]"`.

**Build fails with kernel addon errors**: CLM automatically disables the
inactive kernel addon during builds. If you see errors from
`jupyterlite-xeus` or `jupyterlite-pyodide-kernel`, ensure only one kernel is
configured per `<jupyterlite>` block.

**`.wasm` MIME type errors on Windows**: The Python launcher (`launch.py`)
fixes this automatically. If using a third-party server, ensure it serves
`.wasm` files as `application/wasm`.

**Site loads but kernel won't start**: Check that the `<app-archive>` setting
matches your connectivity. `offline` (default) works without network;
`cdn` requires internet access.
