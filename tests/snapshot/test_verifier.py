"""Unit tests for ``clm.snapshot.verifier``.

Build-real-courses integration is in test_build_integration.py;
these tests use synthetic directory trees so they run in <1s.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clm.snapshot import VerifyReport, verify_against, verify_against_targets


def _write(root: Path, rel: str, content: bytes) -> None:
    """Create root/rel with parent dirs and write *content*."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture
def trees(tmp_path: Path) -> tuple[Path, Path]:
    """Return (snapshot_dir, output_dir) as empty siblings under tmp_path."""
    snap = tmp_path / "snap"
    out = tmp_path / "out"
    snap.mkdir()
    out.mkdir()
    return snap, out


class TestIdenticalTrees:
    def test_zero_diffs_when_byte_identical(self, trees):
        snap, out = trees
        for rel in ("a.ipynb", "sub/b.py", "deep/nested/c.html"):
            _write(snap, rel, b"identical")
            _write(out, rel, b"identical")
        report = verify_against(snap, out)
        # HTML is skipped by default; the other two should be identical.
        assert not report.has_diffs
        assert len(report.identical) == 2
        assert len(report.skipped) == 1
        assert report.differing == []

    def test_extension_summary_populated(self, trees):
        snap, out = trees
        _write(snap, "x.ipynb", b"x")
        _write(out, "x.ipynb", b"x")
        _write(snap, "y.py", b"y")
        _write(out, "y.py", b"y")
        report = verify_against(snap, out)
        assert report.by_extension[".ipynb"].total == 1
        assert report.by_extension[".ipynb"].identical == 1
        assert report.by_extension[".py"].identical == 1


class TestDiffDetection:
    def test_modified_file_is_reported(self, trees):
        snap, out = trees
        _write(snap, "a.ipynb", b"original")
        _write(out, "a.ipynb", b"changed")
        report = verify_against(snap, out)
        assert report.has_diffs
        assert Path("a.ipynb") in report.differing
        assert report.identical == []

    def test_missing_in_output_is_reported(self, trees):
        snap, out = trees
        _write(snap, "ghost.ipynb", b"x")
        _write(snap, "kept.ipynb", b"y")
        _write(out, "kept.ipynb", b"y")
        report = verify_against(snap, out)
        assert report.has_diffs
        assert Path("ghost.ipynb") in report.missing_in_output
        assert report.differing == []

    def test_missing_in_snapshot_is_reported(self, trees):
        snap, out = trees
        _write(snap, "kept.ipynb", b"y")
        _write(out, "kept.ipynb", b"y")
        _write(out, "extra.ipynb", b"new")
        report = verify_against(snap, out)
        assert report.has_diffs
        assert Path("extra.ipynb") in report.missing_in_snapshot

    def test_format_text_contains_summary(self, trees):
        snap, out = trees
        _write(snap, "a.ipynb", b"a1")
        _write(out, "a.ipynb", b"a2")
        report = verify_against(snap, out)
        text = report.format_text()
        assert "Differing:" in text
        assert "a.ipynb" in text


class TestHtmlHandling:
    def test_html_skipped_by_default(self, trees):
        snap, out = trees
        # Two HTMLs with different content; default behavior must not
        # surface them as diffs (this is the noise-floor mitigation).
        _write(snap, "foo.html", b"<pre>0x1111aaaa</pre>")
        _write(out, "foo.html", b"<pre>0x2222bbbb</pre>")
        report = verify_against(snap, out)
        assert not report.has_diffs
        assert Path("foo.html") in report.skipped

    def test_html_hex_addresses_normalize_with_include_html(self, trees):
        snap, out = trees
        # Same content modulo memory addresses → must be reported
        # identical when --include-html is on.
        _write(snap, "foo.html", b"<pre>obj at 0x1111aaaa</pre>")
        _write(out, "foo.html", b"<pre>obj at 0x2222bbbb</pre>")
        report = verify_against(snap, out, include_html=True)
        assert not report.has_diffs
        assert Path("foo.html") in report.identical

    def test_html_real_diff_surfaces_with_include_html(self, trees):
        snap, out = trees
        # Content differs in a way normalization cannot mask.
        _write(snap, "foo.html", b"<pre>blue</pre>")
        _write(out, "foo.html", b"<pre>green</pre>")
        report = verify_against(snap, out, include_html=True)
        assert report.has_diffs
        assert Path("foo.html") in report.differing

    def test_strict_compares_html_raw(self, trees):
        snap, out = trees
        # Strict mode does not normalize — hex address diffs surface.
        _write(snap, "foo.html", b"<pre>obj at 0x1111aaaa</pre>")
        _write(out, "foo.html", b"<pre>obj at 0x2222bbbb</pre>")
        report = verify_against(snap, out, strict=True)
        assert report.has_diffs
        assert Path("foo.html") in report.differing


class TestErrors:
    def test_missing_snapshot_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            verify_against(tmp_path / "nonexistent", tmp_path)

    def test_missing_output_dir_raises(self, tmp_path):
        snap = tmp_path / "snap"
        snap.mkdir()
        with pytest.raises(FileNotFoundError):
            verify_against(snap, tmp_path / "nonexistent")


class TestReportProperties:
    def test_has_diffs_only_when_truly_different(self, trees):
        snap, out = trees
        _write(snap, "a.ipynb", b"x")
        _write(out, "a.ipynb", b"x")
        report = verify_against(snap, out)
        assert isinstance(report, VerifyReport)
        assert not report.has_diffs

    def test_total_files_counts_all_categories(self, trees):
        snap, out = trees
        _write(snap, "ok.ipynb", b"x")
        _write(out, "ok.ipynb", b"x")
        _write(snap, "diff.ipynb", b"a")
        _write(out, "diff.ipynb", b"b")
        _write(snap, "skipped.html", b"a")
        _write(out, "skipped.html", b"b")
        _write(snap, "ghost.ipynb", b"only-in-snap")
        report = verify_against(snap, out)
        # 1 identical + 1 differing + 1 skipped + 1 missing-in-output = 4
        assert report.total_files == 4


class TestVerifyAgainstTargets:
    """Per-target compare for specs with ``<output-targets>``.

    Regression for issue #95 (B): the regular build writes each target
    to its own ``output_root``; the snapshot lays them out under
    ``<snap>/<target.name>/``. Walking the trees as one monolithic
    pair produces thousands of bogus diffs because the prefixes don't
    overlap. ``verify_against_targets`` walks per-target pairs and
    prefixes each diff with the target name so the operator can see
    which target produced it.
    """

    def test_passes_when_every_target_matches(self, tmp_path):
        snap = tmp_path / "snap"
        out_shared = tmp_path / "out" / "shared"
        out_trainer = tmp_path / "out" / "trainer"
        _write(snap, "shared/de/a.py", b"AAA")
        _write(snap, "trainer/de/a.py", b"BBB")
        _write(out_shared, "de/a.py", b"AAA")
        _write(out_trainer, "de/a.py", b"BBB")

        report = verify_against_targets(snap, [("shared", out_shared), ("trainer", out_trainer)])
        assert not report.has_diffs
        # Identical paths are prefixed with the target name.
        ident = {p.as_posix() for p in report.identical}
        assert ident == {"shared/de/a.py", "trainer/de/a.py"}

    def test_differing_diff_is_attributed_to_target(self, tmp_path):
        snap = tmp_path / "snap"
        out_shared = tmp_path / "out" / "shared"
        out_trainer = tmp_path / "out" / "trainer"
        _write(snap, "shared/de/a.py", b"AAA")
        _write(snap, "trainer/de/a.py", b"BBB")
        _write(out_shared, "de/a.py", b"AAA")
        # Trainer side diverges.
        _write(out_trainer, "de/a.py", b"CHANGED")

        report = verify_against_targets(snap, [("shared", out_shared), ("trainer", out_trainer)])
        assert report.has_diffs
        diffs = {p.as_posix() for p in report.differing}
        assert diffs == {"trainer/de/a.py"}

    def test_missing_target_subdir_in_snapshot_reports_missing_in_snapshot(self, tmp_path):
        """The original issue #95 bug: ``trainer/`` silently absent from
        the snapshot. The per-target compare must surface this rather
        than reporting "thousands of extras"."""
        snap = tmp_path / "snap"
        out_shared = tmp_path / "out" / "shared"
        out_trainer = tmp_path / "out" / "trainer"
        _write(snap, "shared/de/a.py", b"AAA")
        # No <snap>/trainer/ at all.
        _write(out_shared, "de/a.py", b"AAA")
        _write(out_trainer, "de/a.py", b"BBB")

        report = verify_against_targets(snap, [("shared", out_shared), ("trainer", out_trainer)])
        assert report.has_diffs
        missing_in_snap = {p.as_posix() for p in report.missing_in_snapshot}
        assert "trainer/de/a.py" in missing_in_snap

    def test_missing_target_subdir_in_output_reports_missing_in_output(self, tmp_path):
        snap = tmp_path / "snap"
        out_shared = tmp_path / "out" / "shared"
        out_trainer = tmp_path / "out" / "trainer"
        out_trainer.mkdir(parents=True, exist_ok=True)  # exists but empty
        _write(snap, "shared/de/a.py", b"AAA")
        _write(snap, "trainer/de/a.py", b"BBB")
        _write(out_shared, "de/a.py", b"AAA")
        # out_trainer has no files.

        report = verify_against_targets(snap, [("shared", out_shared), ("trainer", out_trainer)])
        assert report.has_diffs
        missing_in_out = {p.as_posix() for p in report.missing_in_output}
        assert "trainer/de/a.py" in missing_in_out
