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

    notebook_tree: Path
    output_dir: Path
    kernel: str
    wheels: list[Path]
    environment_yml: Path | None
    app_archive: str
    emit_launcher: bool
    target_label: str
    jupyterlite_core_version: str


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


def _emit_launcher(output_dir: Path, site_dir: Path) -> None:
    """Phase 2 placeholder launcher.

    Writes a short ``launch.py`` that serves ``site_dir`` on a free
    local port and opens a browser tab. The full launcher with the
    Windows wasm MIME fix and ``README-offline.md`` lands in Phase 3;
    this stub is enough for the Phase 2 acceptance test (manual load
    in Chrome).
    """
    launcher_text = '''"""Minimal local launcher for a built JupyterLite site.

Phase-2 stub. A complete launcher (with Windows .wasm MIME fix and a
README describing IndexedDB persistence) is planned for Phase 3.
"""

import http.server
import socketserver
import webbrowser
from pathlib import Path

SITE_DIR = Path(__file__).parent / "_output"


def main() -> None:
    handler = http.server.SimpleHTTPRequestHandler
    handler.extensions_map.setdefault(".wasm", "application/wasm")
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
        port = httpd.server_address[1]
        url = f"http://127.0.0.1:{port}/lab/index.html"
        print(f"Serving {SITE_DIR} at {url}")
        webbrowser.open(url)
        import os

        os.chdir(SITE_DIR)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
'''
    (output_dir / "launch.py").write_text(launcher_text, encoding="utf-8")


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
            notebook_tree=args.notebook_tree,
            kernel=args.kernel,
            wheels=args.wheels,
            environment_yml=args.environment_yml,
            app_archive=args.app_archive,
        )
        _run_jupyter_lite_build(lite_dir, site_dir, kernel=args.kernel)

    if args.emit_launcher:
        _emit_launcher(args.output_dir, site_dir)

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
