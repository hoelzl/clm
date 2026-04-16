"""Shell out to ``jupyter lite build`` and materialize a deployable site.

This module isolates the subprocess/IO concerns so the worker itself
(``jupyterlite_worker.py``) stays focused on queue and lifecycle
handling. Callers pass a ``BuildArgs`` bundle and receive a
``BuildResult`` describing where the site landed and how large it is.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from clm.workers.jupyterlite.lite_dir import assemble_lite_dir, hash_manifest

logger = logging.getLogger(__name__)


@dataclass
class BuildArgs:
    """Everything the builder needs to produce one JupyterLite site."""

    notebook_trees: dict[str, Path]
    output_dir: Path
    kernel: str
    wheels: list[Path]
    environment_yml: Path | None
    app_archive: str
    launcher: str
    target_label: str
    jupyterlite_core_version: str
    branding_theme: str = ""
    branding_logo: str = ""
    branding_site_name: str = ""


@dataclass
class BuildResult:
    """Summary of a completed build, written into the site manifest."""

    site_dir: Path
    manifest_path: Path
    cache_key: str
    files_count: int


def _run_jupyter_lite_build(lite_dir: Path, site_dir: Path, kernel: str) -> None:
    """Invoke ``jupyter lite build`` as a subprocess.

    We call the CLI directly (instead of importing ``jupyterlite_core``)
    because the CLI is the supported entry point and it shells out
    further to its own tasks — keeping this boundary explicit also makes
    the build reproducible from a developer's terminal by copy-pasting
    the command we log.

    The ``--disable-addons`` flag is load-bearing: ``jupyterlite-xeus``
    and ``jupyterlite-pyodide-kernel`` are both installed by the
    ``[jupyterlite]`` extra, but each one's ``post_build`` hook assumes
    its kernel is the active one. Leaving both enabled causes the
    non-active addon to raise (xeus demands an environment file;
    pyodide-kernel demands a wheelhouse). We turn off whichever kernel
    the course did not pick.
    """
    cmd = [
        sys.executable,
        "-m",
        "jupyterlite_core",
        "build",
        "--lite-dir",
        str(lite_dir),
        "--output-dir",
        str(site_dir),
    ]
    if kernel == "pyodide":
        cmd.extend(["--disable-addons", "jupyterlite-xeus"])
    elif kernel == "xeus-python":
        cmd.extend(["--disable-addons", "jupyterlite-pyodide-kernel"])
    logger.info("Running: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError(
            "jupyterlite-core is not installed. Install with "
            "`pip install -e '.[jupyterlite]'` and retry."
        ) from e
    except subprocess.CalledProcessError as e:
        stderr = e.stderr or ""
        stdout = e.stdout or ""
        raise RuntimeError(
            f"jupyter lite build failed (exit {e.returncode}).\n"
            f"stdout:\n{stdout}\nstderr:\n{stderr}"
        ) from e


def _emit_python_launcher(output_dir: Path, site_dir: Path) -> None:
    """Emit ``launch.py`` — a zero-dependency local launcher for students.

    Subclasses ``SimpleHTTPRequestHandler`` to force
    ``application/wasm`` for ``.wasm`` files (Windows ``mimetypes``
    reads from the registry and may return the wrong type). Picks a
    free port, opens the browser at ``/lab/index.html``, and runs
    until Ctrl+C.
    """
    launcher_text = '''\
"""Local launcher for the JupyterLite site.

Run with:  python launch.py

Opens the site in your default browser. Press Ctrl+C to stop.
Requires Python 3.8+ (no additional packages needed).
"""

import signal
import sys
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent / "_output"

EXTRA_MIME_TYPES = {
    ".wasm": "application/wasm",
    ".whl": "application/zip",
}


class JupyterLiteHandler(SimpleHTTPRequestHandler):
    """Serve static files with correct MIME types for JupyterLite."""

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=str(SITE_DIR), **kwargs)

    def guess_type(self, path):
        ext = Path(path).suffix.lower()
        if ext in EXTRA_MIME_TYPES:
            return EXTRA_MIME_TYPES[ext]
        return super().guess_type(path)

    def log_message(self, format, *args):
        pass  # suppress per-request log noise


def main() -> None:
    with ThreadingHTTPServer(("127.0.0.1", 0), JupyterLiteHandler) as httpd:
        port = httpd.server_address[1]
        url = f"http://127.0.0.1:{port}/lab/index.html"
        print(f"JupyterLite running at {url}")
        print("Press Ctrl+C to stop.")
        webbrowser.open(url)
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
'''
    (output_dir / "launch.py").write_text(launcher_text, encoding="utf-8")


def _emit_readme(output_dir: Path, *, launcher: str) -> None:
    """Emit ``README-offline.md`` describing local usage and persistence."""

    if launcher == "miniserve":
        launcher_section = """\
## Running locally

This directory contains pre-built launcher binaries for all major platforms.
Run the appropriate script for your operating system:

| OS | Script | Binary |
|---|---|---|
| Windows | `launch.bat` | `miniserve-windows.exe` |
| macOS | `launch.command` (double-click in Finder) | `miniserve-macos-x64` or `miniserve-macos-arm64` |
| Linux | `launch.sh` | `miniserve-linux` |

The launcher will start a local web server and open your browser automatically.
No Python or other runtime needed.
"""
    else:
        launcher_section = """\
## Running locally

    python launch.py

Requires Python 3.8 or later (no additional packages needed).
The launcher opens your default browser automatically.
Press Ctrl+C in the terminal to stop.
"""

    readme_text = f"""\
# JupyterLite Notebooks — Offline Readme

{launcher_section}
## How your work is saved

JupyterLite stores your notebook edits in the browser's **IndexedDB** storage.
This means:

- Your changes persist between sessions in the **same browser**.
- Switching browsers or using a private/incognito window starts fresh.
- Clearing site data (Settings → Privacy → Clear browsing data) **erases all
  edits** with no way to recover them.

To keep a permanent copy of your work, use **File → Download** inside JupyterLab
to save notebooks to your local filesystem.

## Deploying on a LAN or USB stick

The entire directory is self-contained. Copy it to a USB stick, a network share,
or any static web server. The launcher or any HTTP server that sets the correct
MIME type for `.wasm` files (`application/wasm`) will work.
"""
    (output_dir / "README-offline.md").write_text(readme_text, encoding="utf-8")


def build_site(args: BuildArgs) -> BuildResult:
    """Assemble a ``lite-dir``, run the build, and write a cache manifest.

    The lite-dir lives in a temporary directory; only the final site
    tree under ``args.output_dir / '_output/'`` is persisted.
    """
    site_dir = args.output_dir / "_output"
    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="clm-jupyterlite-") as tmp:
        lite_dir = Path(tmp) / "lite-dir"
        manifest = assemble_lite_dir(
            lite_dir,
            notebook_trees=args.notebook_trees,
            kernel=args.kernel,
            wheels=args.wheels,
            environment_yml=args.environment_yml,
            app_archive=args.app_archive,
            branding_theme=args.branding_theme,
            branding_logo=args.branding_logo,
            branding_site_name=args.branding_site_name,
        )
        _run_jupyter_lite_build(lite_dir, site_dir, kernel=args.kernel)

    if args.launcher == "python":
        _emit_python_launcher(args.output_dir, site_dir)
        _emit_readme(args.output_dir, launcher="python")
    elif args.launcher == "miniserve":
        from clm.workers.jupyterlite.miniserve import emit_miniserve_launcher

        emit_miniserve_launcher(args.output_dir, site_dir)
        _emit_readme(args.output_dir, launcher="miniserve")

    cache_key = hash_manifest(manifest, jupyterlite_core_version=args.jupyterlite_core_version)
    manifest_path = args.output_dir / "jupyterlite-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "target": args.target_label,
                "kernel": args.kernel,
                "app_archive": args.app_archive,
                "cache_key": cache_key,
                "jupyterlite_core_version": args.jupyterlite_core_version,
                "manifest": manifest,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    return BuildResult(
        site_dir=site_dir,
        manifest_path=manifest_path,
        cache_key=cache_key,
        files_count=manifest["files_count"],
    )


def build_result_to_summary(result: BuildResult) -> str:
    """Serialize a ``BuildResult`` as the JSON summary stored in the DB cache."""
    return json.dumps(
        {
            "site_dir": str(result.site_dir),
            "manifest_path": str(result.manifest_path),
            "cache_key": result.cache_key,
            "files_count": result.files_count,
        },
        sort_keys=True,
    )


__all__ = [
    "BuildArgs",
    "BuildResult",
    "build_site",
    "build_result_to_summary",
]
