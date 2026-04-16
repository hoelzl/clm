"""Tests for the Python launcher emitter and README-offline.md.

Validates that ``_emit_python_launcher`` writes a valid launch.py with
the correct MIME type overrides, and that ``_emit_readme`` produces
OS-specific instructions depending on the launcher choice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.workers.jupyterlite.builder import _emit_python_launcher, _emit_readme


class TestEmitPythonLauncher:
    def test_writes_launch_py(self, tmp_path: Path) -> None:
        site_dir = tmp_path / "_output"
        site_dir.mkdir()
        _emit_python_launcher(tmp_path, site_dir)
        assert (tmp_path / "launch.py").is_file()

    def test_launch_py_contains_wasm_mime_fix(self, tmp_path: Path) -> None:
        site_dir = tmp_path / "_output"
        site_dir.mkdir()
        _emit_python_launcher(tmp_path, site_dir)
        content = (tmp_path / "launch.py").read_text(encoding="utf-8")
        assert "application/wasm" in content
        assert ".wasm" in content

    def test_launch_py_uses_threading_http_server(self, tmp_path: Path) -> None:
        site_dir = tmp_path / "_output"
        site_dir.mkdir()
        _emit_python_launcher(tmp_path, site_dir)
        content = (tmp_path / "launch.py").read_text(encoding="utf-8")
        assert "ThreadingHTTPServer" in content

    def test_launch_py_opens_lab_index(self, tmp_path: Path) -> None:
        site_dir = tmp_path / "_output"
        site_dir.mkdir()
        _emit_python_launcher(tmp_path, site_dir)
        content = (tmp_path / "launch.py").read_text(encoding="utf-8")
        assert "/lab/index.html" in content

    def test_launch_py_handles_ctrl_c(self, tmp_path: Path) -> None:
        site_dir = tmp_path / "_output"
        site_dir.mkdir()
        _emit_python_launcher(tmp_path, site_dir)
        content = (tmp_path / "launch.py").read_text(encoding="utf-8")
        assert "signal" in content or "KeyboardInterrupt" in content

    def test_launch_py_is_valid_python(self, tmp_path: Path) -> None:
        site_dir = tmp_path / "_output"
        site_dir.mkdir()
        _emit_python_launcher(tmp_path, site_dir)
        content = (tmp_path / "launch.py").read_text(encoding="utf-8")
        compile(content, "launch.py", "exec")


class TestEmitReadme:
    def test_python_readme_mentions_launch_py(self, tmp_path: Path) -> None:
        _emit_readme(tmp_path, launcher="python")
        content = (tmp_path / "README-offline.md").read_text(encoding="utf-8")
        assert "python launch.py" in content
        assert "IndexedDB" in content

    def test_miniserve_readme_mentions_per_os_launchers(self, tmp_path: Path) -> None:
        _emit_readme(tmp_path, launcher="miniserve")
        content = (tmp_path / "README-offline.md").read_text(encoding="utf-8")
        assert "launch.bat" in content
        assert "launch.command" in content
        assert "launch.sh" in content
        assert "IndexedDB" in content

    def test_readme_documents_usb_deployment(self, tmp_path: Path) -> None:
        _emit_readme(tmp_path, launcher="python")
        content = (tmp_path / "README-offline.md").read_text(encoding="utf-8")
        assert "USB" in content or "self-contained" in content
