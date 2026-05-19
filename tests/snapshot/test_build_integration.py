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


def _write_multi_target_spec(path: Path) -> Path:
    """A spec with three ``<output-targets>``: shared, trainer, speaker.

    Mirrors the AZAV-ML layout from issue #95 (B) — the original
    failure case the per-target snapshot logic is meant to fix.
    """
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<course>
    <name><de>Kurs</de><en>Course</en></name>
    <prog-lang>python</prog-lang>
    <sections/>
    <output-targets>
        <output-target name="shared">
            <path>output/shared</path>
        </output-target>
        <output-target name="trainer">
            <path>output/trainer</path>
        </output-target>
        <output-target name="speaker">
            <path>output/speaker</path>
        </output-target>
    </output-targets>
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

    When a single ``dict[str, bytes]`` is passed, files are seeded into
    the effective output root (snapshot_dir / output_dir / default).
    When a ``dict[str, dict[str, bytes]]`` is passed, the outer keys
    are interpreted as per-target subdirectory names — used by the
    multi-target snapshot tests.
    """

    seeded: dict = {}

    async def stub_main_build(
        ctx,
        spec_file,
        data_dir,
        output_dir,
        *_args,
        **kwargs,
    ):
        snapshot_dir = kwargs.get("snapshot_dir")
        # The real build resolves the effective output dir based on
        # ``--snapshot`` / ``--output-dir`` / default. Mirror that here.
        if snapshot_dir is not None:
            root = Path(snapshot_dir)
        elif output_dir is not None:
            root = Path(output_dir)
        else:
            # Fall back to spec's grandparent / "output" — matches
            # resolve_course_paths' default-output behavior.
            root = spec_file.absolute().parents[1] / "output"

        # Detect per-target payload: outer value is a dict-of-bytes.
        per_target = bool(seeded) and all(isinstance(v, dict) for v in seeded.values())
        if per_target:
            for target_name, files in seeded.items():
                _seed_output(root / target_name, files)
        else:
            _seed_output(root, seeded)

    monkeypatch.setattr(build_module, "main_build", stub_main_build)

    def configure(files: dict) -> None:
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


class TestSnapshotMultiTargetLayout:
    """``--snapshot`` must honor the spec's ``<output-targets>``.

    Regression for issue #95 (B): with a spec that defines named output
    targets (``shared``, ``trainer``, ``speaker``), the old behavior
    aliased ``--snapshot DIR`` to ``--output-dir DIR``, which collapsed
    every target into the legacy ``public/`` and ``speaker/``
    toplevel directories — silently dropping ``trainer/`` and renaming
    ``shared/`` to ``public/``. The fix routes ``--snapshot`` through a
    new ``snapshot_root`` path on ``Course.from_spec`` so each spec
    target writes to ``<DIR>/<target.name>/`` and ``--verify-against``
    compares per-target.
    """

    def _setup(self, tmp_path: Path):
        spec_dir = tmp_path / "repo" / "course-specs"
        spec_dir.mkdir(parents=True)
        spec = _write_multi_target_spec(spec_dir / "course.xml")
        return spec

    def test_snapshot_uses_target_names_not_legacy_layout(self, tmp_path: Path, fake_build) -> None:
        spec = self._setup(tmp_path)
        # Per-target payload — each spec target writes its own files.
        fake_build(
            {
                "shared": {"de/index.html": b"shared-de"},
                "trainer": {"de/index.html": b"trainer-de"},
                "speaker": {"de/index.html": b"speaker-de"},
            }
        )

        snap = tmp_path / "snap"
        result = _invoke_build([str(spec), "--snapshot", str(snap)], tmp_path)

        assert result.exit_code == 0, result.output
        # Files must land under <snap>/<target.name>/, NOT under the
        # legacy <snap>/public/ or <snap>/speaker/ toplevels.
        assert (snap / "shared" / "de" / "index.html").read_bytes() == b"shared-de"
        assert (snap / "trainer" / "de" / "index.html").read_bytes() == b"trainer-de"
        assert (snap / "speaker" / "de" / "index.html").read_bytes() == b"speaker-de"
        assert not (snap / "public").exists()

    def test_verify_per_target_matches_when_layouts_align(self, tmp_path: Path, fake_build) -> None:
        spec = self._setup(tmp_path)

        # Snapshot run — writes <snap>/<name>/...
        fake_build(
            {
                "shared": {"de/a.py": b"AAA"},
                "trainer": {"de/a.py": b"BBB"},
                "speaker": {"de/a.py": b"CCC"},
            }
        )
        baseline = tmp_path / "baseline"
        result = _invoke_build([str(spec), "--snapshot", str(baseline)], tmp_path)
        assert result.exit_code == 0, result.output
        assert (baseline / "shared" / "de" / "a.py").read_bytes() == b"AAA"

        # Verify run — regular build writes to spec output-targets
        # (<course_root>/output/<name>/...). The verify helper must
        # compare per-target so the trees match.
        fake_build(
            {
                "shared": {"de/a.py": b"AAA"},
                "trainer": {"de/a.py": b"BBB"},
                "speaker": {"de/a.py": b"CCC"},
            }
        )
        result = _invoke_build(
            [str(spec), "--verify-against", str(baseline)],
            tmp_path,
        )
        assert result.exit_code == 0, result.output
        assert "Verification passed" in result.output

    def test_verify_per_target_surfaces_target_scoped_diff(
        self, tmp_path: Path, fake_build
    ) -> None:
        spec = self._setup(tmp_path)

        fake_build(
            {
                "shared": {"de/a.py": b"AAA"},
                "trainer": {"de/a.py": b"BBB"},
                "speaker": {"de/a.py": b"CCC"},
            }
        )
        baseline = tmp_path / "baseline"
        result = _invoke_build([str(spec), "--snapshot", str(baseline)], tmp_path)
        assert result.exit_code == 0, result.output

        # The second run changes only the trainer target's content.
        # The diff must be attributed to ``trainer/`` and not flagged
        # as "everything is missing" on either side.
        fake_build(
            {
                "shared": {"de/a.py": b"AAA"},
                "trainer": {"de/a.py": b"CHANGED"},
                "speaker": {"de/a.py": b"CCC"},
            }
        )
        result = _invoke_build(
            [str(spec), "--verify-against", str(baseline)],
            tmp_path,
        )
        assert result.exit_code != 0
        assert "Verification failed" in result.output
        # The differing path is prefixed with the target name.
        assert "trainer" in result.output and "a.py" in result.output

    def test_verify_per_target_catches_missing_target_dir(self, tmp_path: Path, fake_build) -> None:
        """If a target's content was silently dropped from the snapshot
        (the original issue #95 (B) bug), verify must report the missing
        files rather than passing because the trees overlap on
        ``shared/``."""
        spec = self._setup(tmp_path)

        # Snapshot only has 2 of 3 targets — simulating the legacy
        # behavior that dropped ``trainer/``.
        baseline = tmp_path / "baseline"
        baseline.mkdir()
        (baseline / "shared" / "de").mkdir(parents=True)
        (baseline / "shared" / "de" / "a.py").write_bytes(b"AAA")
        (baseline / "speaker" / "de").mkdir(parents=True)
        (baseline / "speaker" / "de" / "a.py").write_bytes(b"CCC")
        # No trainer/ subdir in baseline.

        fake_build(
            {
                "shared": {"de/a.py": b"AAA"},
                "trainer": {"de/a.py": b"BBB"},
                "speaker": {"de/a.py": b"CCC"},
            }
        )
        result = _invoke_build(
            [str(spec), "--verify-against", str(baseline)],
            tmp_path,
        )
        assert result.exit_code != 0
        assert "Verification failed" in result.output
        # The trainer target's a.py is "missing in snapshot".
        assert "trainer" in result.output
