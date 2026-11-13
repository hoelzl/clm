#!/usr/bin/env python3
"""Install this checkout as the global ``clm`` uv tool, but only when it is safe.

``uv tool install --force`` deletes the tool environment and rebuilds it. On
Windows that fails partway when any process still has a file in the old
environment in use. Typical holders are an agent's ``clm mcp`` server, a
running ``clm build``, or VS Code with the tool venv selected as its
interpreter. A failed delete leaves a half-installed tool behind. ``uv tool
install`` has no dry-run, so this script checks first:

1. **Process scan**: lists running processes whose executable is inside the
   tool environment, or is one of the tool's launchers in uv's bin directory
   (``~/.local/bin/clm.exe``). These are the processes to stop.
2. **Rename probe** (Windows only): renames the environment directory away and
   back. Windows refuses to rename a directory while any file in it is in use,
   so this also catches holders the scan cannot see, such as an editor with an
   open file. If the probe succeeds, nothing is holding the environment.

The install runs only if both checks pass.

Usage:
    # Check, then install editable with the [all] extra (the default).
    python scripts/install_global_clm.py

    # Only check; install nothing.
    python scripts/install_global_clm.py --check

    # Different extras, or a non-editable install.
    python scripts/install_global_clm.py --extras "mcp,recordings"
    python scripts/install_global_clm.py --no-editable

    # Pass extra arguments through to `uv tool install` after `--`.
    python scripts/install_global_clm.py -- --with "av<19"

Run it with the project interpreter (``.venv/Scripts/python.exe``) or any
Python *outside* the tool environment. If it runs from inside the tool
environment, it holds that environment itself.

Exit codes: 0 = installed (or, with ``--check``, safe to install);
1 = environment in use, nothing changed; 2 = uv failed or not found.

With an editable install you rarely need this: code changes in the checkout
take effect without reinstalling (running servers need a restart). Reinstall
only when dependencies, extras or entry points change.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE = "coding-academy-lecture-manager"
REPO_ROOT = Path(__file__).resolve().parent.parent

_TOOL_LINE = re.compile(r"^(?P<name>\S+) v\S+ \((?P<path>.+)\)$")
_ENTRY_LINE = re.compile(r"^- \S+ \((?P<path>.+)\)$")


@dataclass
class ToolPaths:
    """Where the installed tool lives; ``env_dir`` is None if not installed."""

    env_dir: Path | None
    launchers: list[Path] = field(default_factory=list)


@dataclass
class Holder:
    pid: int
    name: str
    exe: str


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def find_uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        print("error: `uv` not found on PATH", file=sys.stderr)
        sys.exit(2)
    return uv


def locate_tool(uv: str) -> ToolPaths:
    """Read the tool's env dir and launcher paths from `uv tool list`."""
    result = subprocess.run(
        [uv, "tool", "list", "--show-paths"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"error: `uv tool list` failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(2)

    paths = ToolPaths(env_dir=None)
    in_package = False
    for raw in result.stdout.splitlines():
        line = raw.strip()
        tool = _TOOL_LINE.match(line)
        if tool:
            in_package = tool["name"] == PACKAGE
            if in_package:
                paths.env_dir = Path(tool["path"])
            continue
        entry = _ENTRY_LINE.match(line)
        if entry and in_package:
            paths.launchers.append(Path(entry["path"]))
    return paths


def scan_processes(paths: ToolPaths) -> list[Holder]:
    """Processes running an executable from the tool env or its launchers."""
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        print("warning: psutil not available; skipping the process scan")
        return []

    env_prefix = _norm(paths.env_dir) + os.sep if paths.env_dir else None
    launchers = {_norm(p) for p in paths.launchers}
    holders = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        exe = proc.info.get("exe")
        if not exe:
            continue
        norm = _norm(exe)
        if (env_prefix and norm.startswith(env_prefix)) or norm in launchers:
            holders.append(Holder(proc.info["pid"], proc.info["name"] or "?", exe))
    return holders


def rename_probe(env_dir: Path) -> str | None:
    """Rename env_dir away and back; return an error message if it is in use."""
    probe = env_dir.with_name(f"{env_dir.name}.probe-{uuid.uuid4().hex[:8]}")
    try:
        env_dir.rename(probe)
    except OSError as exc:
        return str(exc)
    try:
        probe.rename(env_dir)
    except OSError as exc:  # pragma: no cover - would need a race to hit
        print(
            f"error: renamed the tool env to {probe} but could not rename it "
            f"back ({exc}). Rename it back by hand before using clm.",
            file=sys.stderr,
        )
        sys.exit(2)
    return None


def check(paths: ToolPaths) -> bool:
    """Run both checks; print findings; return True if safe to install."""
    if paths.env_dir is None or not paths.env_dir.exists():
        print(f"{PACKAGE} is not installed as a uv tool; nothing can hold it.")
        return True

    print(f"Tool environment: {paths.env_dir}")
    if _norm(sys.executable).startswith(_norm(paths.env_dir) + os.sep):
        print(
            "error: this script is running from inside the tool environment "
            "and would hold it itself. Run it with .venv/Scripts/python.exe.",
            file=sys.stderr,
        )
        return False

    holders = scan_processes(paths)
    if holders:
        print(f"\n{len(holders)} process(es) are running from the tool env:")
        for h in holders:
            print(f"  PID {h.pid:>7}  {h.name:<12} {h.exe}")
        print(
            "\nStop them first (e.g. restart the agent sessions that run the "
            "clm MCP server). To end them all in PowerShell:\n"
            f"  Stop-Process -Id {','.join(str(h.pid) for h in holders)}"
        )
        return False

    if sys.platform == "win32":
        error = rename_probe(paths.env_dir)
        if error:
            print(
                "\nNo clm process is running, but something still has a file "
                "in the tool env in use (an editor using it as interpreter, a "
                f"shell whose cwd is inside it, ...):\n  {error}"
            )
            return False

    print("Nothing is holding the tool environment.")
    return True


def install(uv: str, extras: str, editable: bool, passthrough: list[str]) -> int:
    target = str(REPO_ROOT) + (f"[{extras}]" if extras else "")
    cmd = [uv, "tool", "install", "--force"]
    if editable:
        cmd.append("--editable")
    cmd += [target, *passthrough]
    print("\nRunning:", subprocess.list2cmdline(cmd))
    return subprocess.run(cmd, cwd=REPO_ROOT).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="only run the checks; install nothing")
    parser.add_argument(
        "--extras", default="all", help='extras to install (default: "all"; "" for none)'
    )
    parser.add_argument(
        "--no-editable",
        dest="editable",
        action="store_false",
        help="install a regular (non-editable) copy",
    )
    parser.add_argument(
        "passthrough",
        nargs="*",
        help="arguments after `--` are passed to `uv tool install`",
    )
    args = parser.parse_args(argv)

    uv = find_uv()
    if not check(locate_tool(uv)):
        print("\nNot installing; the existing tool was left untouched.")
        return 1
    if args.check:
        return 0
    if install(uv, args.extras, args.editable, args.passthrough) != 0:
        print("error: `uv tool install` failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
