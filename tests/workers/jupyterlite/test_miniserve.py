"""Tests for the miniserve binary download, cache, and launcher emission.

These tests never hit the network — all downloads are mocked. They verify
the checksum logic, cache-hit/miss behavior, and that the per-OS launcher
scripts are emitted with the correct content.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from clm.workers.jupyterlite import miniserve as miniserve_module
from clm.workers.jupyterlite.miniserve import (
    _ASSETS,
    MINISERVE_VERSION,
    _cache_dir,
    _sha256,
    emit_miniserve_launcher,
    ensure_cached,
)


@pytest.fixture
def fake_cache(tmp_path: Path):
    """Redirect the cache directory to a tmp_path subfolder."""
    cache = tmp_path / "cache" / "clm" / "miniserve" / MINISERVE_VERSION
    with patch.object(miniserve_module, "_cache_dir", return_value=cache):
        yield cache


@pytest.fixture
def populated_cache(fake_cache: Path) -> Path:
    """Pre-populate the cache with fake binaries that match the expected checksums."""
    fake_cache.mkdir(parents=True, exist_ok=True)
    patched_assets = {}
    for key, asset in _ASSETS.items():
        content = f"fake-{key}-binary".encode()
        sha = hashlib.sha256(content).hexdigest()
        path = fake_cache / asset["local_name"]
        path.write_bytes(content)
        patched_assets[key] = {**asset, "sha256": sha}
    with patch.dict(miniserve_module._ASSETS, patched_assets):
        yield fake_cache


class TestCacheDir:
    def test_returns_path_containing_version(self) -> None:
        path = _cache_dir()
        assert MINISERVE_VERSION in str(path)


class TestSha256:
    def test_computes_correct_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert _sha256(f) == expected


class TestEnsureCached:
    def test_returns_all_four_platforms(self, populated_cache: Path) -> None:
        result = ensure_cached()
        assert len(result) == 4
        assert set(result.keys()) == {"windows-x64", "macos-x64", "macos-arm64", "linux-x64"}
        for path in result.values():
            assert path.is_file()

    def test_cache_hit_skips_download(self, populated_cache: Path) -> None:
        with patch.object(miniserve_module, "_download_and_verify") as mock_dl:
            ensure_cached()
            mock_dl.assert_not_called()

    def test_cache_miss_triggers_download(self, fake_cache: Path) -> None:
        fake_cache.mkdir(parents=True, exist_ok=True)
        fake_content = b"downloaded-binary"
        fake_sha = hashlib.sha256(fake_content).hexdigest()

        patched_assets = {}
        for key, asset in _ASSETS.items():
            patched_assets[key] = {**asset, "sha256": fake_sha}

        def fake_download(asset_info, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(fake_content)

        with (
            patch.dict(miniserve_module._ASSETS, patched_assets),
            patch.object(
                miniserve_module, "_download_and_verify", side_effect=fake_download
            ) as mock_dl,
        ):
            result = ensure_cached()
            assert mock_dl.call_count == 4
            for path in result.values():
                assert path.is_file()

    def test_checksum_mismatch_re_downloads(self, fake_cache: Path) -> None:
        fake_cache.mkdir(parents=True, exist_ok=True)
        good_content = b"good-binary"
        good_sha = hashlib.sha256(good_content).hexdigest()

        patched_assets = {}
        for key, asset in _ASSETS.items():
            path = fake_cache / asset["local_name"]
            path.write_bytes(b"corrupted-binary")
            patched_assets[key] = {**asset, "sha256": good_sha}

        def fake_download(asset_info, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(good_content)

        with (
            patch.dict(miniserve_module._ASSETS, patched_assets),
            patch.object(
                miniserve_module, "_download_and_verify", side_effect=fake_download
            ) as mock_dl,
        ):
            ensure_cached()
            assert mock_dl.call_count == 4


class TestEmitMiniserveLauncher:
    def test_copies_all_binaries_and_scripts(self, tmp_path: Path, populated_cache: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        site_dir = output_dir / "_output"
        site_dir.mkdir()

        emit_miniserve_launcher(output_dir, site_dir)

        assert (output_dir / "miniserve-windows.exe").is_file()
        assert (output_dir / "miniserve-macos-x64").is_file()
        assert (output_dir / "miniserve-macos-arm64").is_file()
        assert (output_dir / "miniserve-linux").is_file()
        assert (output_dir / "launch.bat").is_file()
        assert (output_dir / "launch.command").is_file()
        assert (output_dir / "launch.sh").is_file()

    def test_launch_bat_invokes_windows_binary(self, tmp_path: Path, populated_cache: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        site_dir = output_dir / "_output"
        site_dir.mkdir()

        emit_miniserve_launcher(output_dir, site_dir)
        bat = (output_dir / "launch.bat").read_text(encoding="utf-8")
        assert "miniserve-windows.exe" in bat
        assert "lab/index.html" in bat

    def test_launch_command_selects_arch(self, tmp_path: Path, populated_cache: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        site_dir = output_dir / "_output"
        site_dir.mkdir()

        emit_miniserve_launcher(output_dir, site_dir)
        cmd = (output_dir / "launch.command").read_text(encoding="utf-8")
        assert "miniserve-macos-arm64" in cmd
        assert "miniserve-macos-x64" in cmd
        assert "uname -m" in cmd

    def test_launch_sh_invokes_linux_binary(self, tmp_path: Path, populated_cache: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        site_dir = output_dir / "_output"
        site_dir.mkdir()

        emit_miniserve_launcher(output_dir, site_dir)
        sh = (output_dir / "launch.sh").read_text(encoding="utf-8")
        assert "miniserve-linux" in sh
        assert "lab/index.html" in sh
