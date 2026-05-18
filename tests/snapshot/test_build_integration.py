"""Integration tests for the snapshot/verify CLI flow.

These tests monkey-patch ``main_build`` to skip the real build pipeline
(which is exercised elsewhere) and instead drop pre-canned output into
the build's effective output directory. That lets us validate the full
flag-to-verifier wiring end-to-end without paying a 30-second build per
test case.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands import build as build_module


def _write_minimal_spec(path: Path) -> Path:
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <sections/>
</course>
""",
        encoding="utf-8",
    )
    return path


def _seed_output(output_dir: Path, files: dict[str, bytes]) -> None:
    """Populate *output_dir* with the given relative paths and bytes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = output_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


@pytest.fixture
def fake_build(monkeypatch):
    """Replace ``main_build`` with an async no-op that pre-populates output.

    The factory returns a function the test calls with the files it
    wants the "build" to produce; the actual stub is registered as a
    side effect.
    """

    seeded: dict[str, bytes] = {}

    async def stub_main_build(
        ctx,
        spec_file,
        data_dir,
        output_dir,
        *_args,
        **_kwargs,
    ):
        # The real build resolves output_dir lazily; we mirror that and
        # write to the resolved path used by the post-build hook.
        if output_dir is None:
            # Fall back to spec's grandparent / "output" — matches
            # resolve_course_paths' default-output behavior.
            output_dir = spec_file.absolute().parents[1] / "output"
        _seed_output(Path(output_dir), seeded)

    monkeypatch.setattr(build_module, "main_build", stub_main_build)

    def configure(files: dict[str, bytes]) -> None:
        seeded.clear()
        seeded.update(files)

    return configure


def _invoke_build(args, tmp_path: Path):
    obj = {
        "CACHE_DB_PATH": tmp_path / "cache.db",
        "JOBS_DB_PATH": tmp_path / "jobs.db",
    }
    return CliRunner().invoke(build_module.build, args, obj=obj)


class TestSnapshotCapture:
    def test_snapshot_writes_to_target_dir(self, tmp_path: Path, fake_build) -> None:
        # Spec must live under a parent that itself has a parent
        # (resolve_course_paths goes ``parents[1]``), so put it two
        # levels deep.
        spec_dir = tmp_path / "repo" / "course-specs"
        spec_dir.mkdir(parents=True)
        spec = _write_minimal_spec(spec_dir / "course.xml")
        fake_build(
            {
                "a.py": b"hello",
                "sub/b.ipynb": b"{}",
            }
        )

        snap = tmp_path / "snap"
        result = _invoke_build(
            [str(spec), "--snapshot", str(snap)],
            tmp_path,
        )

        assert result.exit_code == 0, result.output
        assert (snap / "a.py").read_bytes() == b"hello"
        assert (snap / "sub" / "b.ipynb").read_bytes() == b"{}"
        assert "Snapshot saved to" in result.output


class TestVerifyAgainst:
    def _build_baseline(self, tmp_path: Path, fake_build, payload):
        spec_dir = tmp_path / "repo" / "course-specs"
        spec_dir.mkdir(parents=True)
        spec = _write_minimal_spec(spec_dir / "course.xml")
        baseline = tmp_path / "baseline"
        fake_build(payload)
        result = _invoke_build([str(spec), "--snapshot", str(baseline)], tmp_path)
        assert result.exit_code == 0, result.output
        return spec, baseline

    def test_verify_passes_when_output_matches_snapshot(self, tmp_path: Path, fake_build) -> None:
        spec, baseline = self._build_baseline(
            tmp_path,
            fake_build,
            {"x.ipynb": b"identical", "y.py": b"py-source"},
        )
        # Second build produces the same output; verify must pass.
        fake_build({"x.ipynb": b"identical", "y.py": b"py-source"})
        out = tmp_path / "out2"
        result = _invoke_build(
            [
                str(spec),
                "--output-dir",
                str(out),
                "--verify-against",
                str(baseline),
            ],
            tmp_path,
        )
        assert result.exit_code == 0, result.output
        assert "Verification passed" in result.output

    def test_verify_fails_when_output_differs(self, tmp_path: Path, fake_build) -> None:
        spec, baseline = self._build_baseline(
            tmp_path,
            fake_build,
            {"x.ipynb": b"original"},
        )
        # Second build produces different content; verify must fail.
        fake_build({"x.ipynb": b"changed"})
        out = tmp_path / "out2"
        result = _invoke_build(
            [
                str(spec),
                "--output-dir",
                str(out),
                "--verify-against",
                str(baseline),
            ],
            tmp_path,
        )
        assert result.exit_code != 0
        assert "Verification failed" in result.output
        assert "x.ipynb" in result.output

    def test_html_default_skip_keeps_verify_passing(self, tmp_path: Path, fake_build) -> None:
        # Two different HTMLs should not cause verify to fail by default
        # — HTML is excluded because of live-kernel noise.
        spec, baseline = self._build_baseline(
            tmp_path,
            fake_build,
            {"page.html": b"<p>0x1111aaaa</p>", "kept.ipynb": b"x"},
        )
        fake_build({"page.html": b"<p>0x2222bbbb</p>", "kept.ipynb": b"x"})
        out = tmp_path / "out2"
        result = _invoke_build(
            [
                str(spec),
                "--output-dir",
                str(out),
                "--verify-against",
                str(baseline),
            ],
            tmp_path,
        )
        assert result.exit_code == 0, result.output
        assert "Verification passed" in result.output

    def test_include_html_normalizes_hex_addresses(self, tmp_path: Path, fake_build) -> None:
        spec, baseline = self._build_baseline(
            tmp_path,
            fake_build,
            {"page.html": b"<p>obj at 0x1111aaaa</p>"},
        )
        fake_build({"page.html": b"<p>obj at 0x2222bbbb</p>"})
        out = tmp_path / "out2"
        result = _invoke_build(
            [
                str(spec),
                "--output-dir",
                str(out),
                "--verify-against",
                str(baseline),
                "--include-html",
            ],
            tmp_path,
        )
        assert result.exit_code == 0, result.output

    def test_strict_verify_surfaces_html_address_diffs(self, tmp_path: Path, fake_build) -> None:
        spec, baseline = self._build_baseline(
            tmp_path,
            fake_build,
            {"page.html": b"<p>obj at 0x1111aaaa</p>"},
        )
        fake_build({"page.html": b"<p>obj at 0x2222bbbb</p>"})
        out = tmp_path / "out2"
        result = _invoke_build(
            [
                str(spec),
                "--output-dir",
                str(out),
                "--verify-against",
                str(baseline),
                "--strict-verify",
            ],
            tmp_path,
        )
        assert result.exit_code != 0
        assert "Verification failed" in result.output
