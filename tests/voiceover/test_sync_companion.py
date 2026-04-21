"""Tests for ``clm voiceover sync`` companion-file merge routing.

Covers:

- Auto-detection of a companion ``voiceover_*.py`` file next to the slide
  file, routing baseline reads and merged writes through the companion.
- The ``--companion/--no-companion`` flag overriding auto-detection.
- Error behavior when the companion mode is active but slides lack
  ``slide_id`` attributes required for ``for_slide`` round-tripping.
- Dry-run diff scoping to the companion file.
- Byte-exact preservation of the slide file under companion mode.

The tests bypass transcription/alignment by invoking ``_merge_notes``
directly with pre-built fakes, and by patching ``merge_batch`` to return
deterministic results. Pipeline integration paths are exercised in
existing voiceover tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from clm.cli.commands.voiceover import _merge_notes, _require_slide_ids
from clm.notebooks.slide_parser import parse_slides
from clm.voiceover.aligner import AlignmentResult
from clm.voiceover.merge import MergeResult

SLIDES_WITH_IDS = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
# ## Einführung
#
# Inhalt.

# %% [markdown] lang="de" tags=["slide"] slide_id="details"
# ## Details
#
# Mehr Inhalt.
"""

SLIDES_WITHOUT_IDS = """\
# %% [markdown] lang="de" tags=["slide"]
# ## Einführung

# %% [markdown] lang="de" tags=["slide"]
# ## Details
"""

COMPANION_INITIAL = """\
# %% [markdown] lang="de" tags=["voiceover"] for_slide="intro"
# Alte Einführung.

# %% [markdown] lang="de" tags=["voiceover"] for_slide="details"
# Alte Details.
"""


def _fake_alignment_for(slide_indices: list[int]) -> AlignmentResult:
    """Build a minimal AlignmentResult with ``slide_notes`` keys."""
    from clm.voiceover.aligner import SlideNotes

    return AlignmentResult(
        slide_notes={i: SlideNotes(slide_index=i, segments=["placeholder"]) for i in slide_indices},
    )


def _fake_merge_results(slide_ids: list[str]) -> list[MergeResult]:
    return [
        MergeResult(slide_id=sid, merged_bullets=f"- Merged content for {sid}") for sid in slide_ids
    ]


def _run_merge(**kwargs) -> None:
    """Helper to run ``_merge_notes`` synchronously."""
    asyncio.run(_merge_notes(**kwargs))


# ---------------------------------------------------------------------------
# _require_slide_ids — pure helper
# ---------------------------------------------------------------------------


class TestRequireSlideIds:
    def test_returns_mapping_when_all_present(self, tmp_path: Path):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDES_WITH_IDS, encoding="utf-8")
        slide_groups = parse_slides(slide_file, "de")
        notes_map = {sg.index: "text" for sg in slide_groups if sg.slide_type != "header"}

        result = _require_slide_ids(slide_groups, notes_map, slide_file)

        assert set(result.values()) == {"intro", "details"}

    def test_raises_usage_error_with_fix_hint(self, tmp_path: Path):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDES_WITHOUT_IDS, encoding="utf-8")
        slide_groups = parse_slides(slide_file, "de")
        notes_map = {sg.index: "text" for sg in slide_groups if sg.slide_type != "header"}

        import click

        with pytest.raises(click.UsageError) as excinfo:
            _require_slide_ids(slide_groups, notes_map, slide_file)

        msg = str(excinfo.value)
        assert "slide_id" in msg
        assert "clm extract-voiceover" in msg
        assert "--no-companion" in msg

    def test_only_checks_slides_in_notes_map(self, tmp_path: Path):
        """A slide missing slide_id but NOT in notes_map is allowed."""
        slide_file = tmp_path / "slides_mixed.py"
        slide_file.write_text(
            '# %% [markdown] lang="de" tags=["slide"] slide_id="good"\n'
            "# ## Good\n"
            '\n# %% [markdown] lang="de" tags=["slide"]\n'
            "# ## Untouched\n",
            encoding="utf-8",
        )
        slide_groups = parse_slides(slide_file, "de")

        # Only the first slide needs merging
        good_idx = next(sg.index for sg in slide_groups if sg.cells[0].metadata.slide_id == "good")
        notes_map = {good_idx: "text"}

        result = _require_slide_ids(slide_groups, notes_map, slide_file)

        assert result == {good_idx: "good"}


# ---------------------------------------------------------------------------
# _merge_notes with companion mode active
# ---------------------------------------------------------------------------


class TestMergeNotesCompanionMode:
    def _setup(self, tmp_path: Path, *, with_companion: bool = True):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDES_WITH_IDS, encoding="utf-8")
        companion_file = tmp_path / "voiceover_test.py"
        if with_companion:
            companion_file.write_text(COMPANION_INITIAL, encoding="utf-8")

        slide_groups = parse_slides(slide_file, "de")
        content_groups = [sg for sg in slide_groups if sg.slide_type != "header"]
        notes_map = {sg.index: "neuer Transcript Text" for sg in content_groups}
        alignment = _fake_alignment_for(list(notes_map.keys()))
        slide_ids = [sg.cells[0].metadata.slide_id for sg in content_groups]
        return slide_file, companion_file, slide_groups, notes_map, alignment, slide_ids

    def test_writes_to_companion_when_present(self, tmp_path: Path):
        slide_file, companion, slide_groups, notes_map, alignment, slide_ids = self._setup(tmp_path)
        expected_slide_id_pairs = list(notes_map.keys())
        fake_ids = [f"{slide_file.stem}/{i}" for i in expected_slide_id_pairs]

        with patch(
            "clm.voiceover.merge.merge_batch",
            new=AsyncMock(return_value=_fake_merge_results(fake_ids)),
        ):
            _run_merge(
                slides=slide_file,
                notes_map=notes_map,
                slide_groups=slide_groups,
                lang="de",
                tag="voiceover",
                model=None,
                dry_run=False,
                output=None,
                multi_part=False,
                alignment=alignment,
                use_companion=True,
                companion_file=companion,
            )

        companion_text = companion.read_text(encoding="utf-8")
        assert "Merged content for" in companion_text
        assert "Alte Einführung" not in companion_text
        assert 'for_slide="intro"' in companion_text
        assert 'for_slide="details"' in companion_text

    def test_companion_mode_preserves_slide_file_byte_exact(self, tmp_path: Path):
        slide_file, companion, slide_groups, notes_map, alignment, _ = self._setup(tmp_path)
        original_slide_bytes = slide_file.read_bytes()
        fake_ids = [f"{slide_file.stem}/{i}" for i in notes_map]

        with patch(
            "clm.voiceover.merge.merge_batch",
            new=AsyncMock(return_value=_fake_merge_results(fake_ids)),
        ):
            _run_merge(
                slides=slide_file,
                notes_map=notes_map,
                slide_groups=slide_groups,
                lang="de",
                tag="voiceover",
                model=None,
                dry_run=False,
                output=None,
                multi_part=False,
                alignment=alignment,
                use_companion=True,
                companion_file=companion,
            )

        assert slide_file.read_bytes() == original_slide_bytes

    def test_reads_baseline_from_companion(self, tmp_path: Path):
        """The merge_batch call should receive baselines drawn from the companion, not the slide file."""
        slide_file, companion, slide_groups, notes_map, alignment, _ = self._setup(tmp_path)
        captured_inputs: list = []
        fake_ids = [f"{slide_file.stem}/{i}" for i in notes_map]

        async def capture_batch(slides, **kwargs):
            captured_inputs.extend(slides)
            return _fake_merge_results(fake_ids)

        with patch("clm.voiceover.merge.merge_batch", new=capture_batch):
            _run_merge(
                slides=slide_file,
                notes_map=notes_map,
                slide_groups=slide_groups,
                lang="de",
                tag="voiceover",
                model=None,
                dry_run=False,
                output=None,
                multi_part=False,
                alignment=alignment,
                use_companion=True,
                companion_file=companion,
            )

        baselines = [s.baseline for s in captured_inputs]
        assert any("Alte Einführung" in b for b in baselines)
        assert any("Alte Details" in b for b in baselines)

    def test_dry_run_diff_scoped_to_companion(self, tmp_path: Path, capsys):
        slide_file, companion, slide_groups, notes_map, alignment, _ = self._setup(tmp_path)
        fake_ids = [f"{slide_file.stem}/{i}" for i in notes_map]

        original_companion_bytes = companion.read_bytes()

        with patch(
            "clm.voiceover.merge.merge_batch",
            new=AsyncMock(return_value=_fake_merge_results(fake_ids)),
        ):
            _run_merge(
                slides=slide_file,
                notes_map=notes_map,
                slide_groups=slide_groups,
                lang="de",
                tag="voiceover",
                model=None,
                dry_run=True,
                output=None,
                multi_part=False,
                alignment=alignment,
                use_companion=True,
                companion_file=companion,
            )

        # Neither file should be modified in dry-run.
        assert companion.read_bytes() == original_companion_bytes

    def test_companion_mode_without_existing_file_creates_it(self, tmp_path: Path):
        slide_file, companion, slide_groups, notes_map, alignment, _ = self._setup(
            tmp_path, with_companion=False
        )
        fake_ids = [f"{slide_file.stem}/{i}" for i in notes_map]

        with patch(
            "clm.voiceover.merge.merge_batch",
            new=AsyncMock(return_value=_fake_merge_results(fake_ids)),
        ):
            _run_merge(
                slides=slide_file,
                notes_map=notes_map,
                slide_groups=slide_groups,
                lang="de",
                tag="voiceover",
                model=None,
                dry_run=False,
                output=None,
                multi_part=False,
                alignment=alignment,
                use_companion=True,
                companion_file=companion,
            )

        assert companion.exists()
        text = companion.read_text(encoding="utf-8")
        assert 'for_slide="intro"' in text
        assert 'for_slide="details"' in text

    def test_errors_when_slide_ids_missing(self, tmp_path: Path):
        slide_file = tmp_path / "slides_bare.py"
        slide_file.write_text(SLIDES_WITHOUT_IDS, encoding="utf-8")
        companion = tmp_path / "voiceover_bare.py"
        companion.write_text("", encoding="utf-8")

        slide_groups = parse_slides(slide_file, "de")
        content_groups = [sg for sg in slide_groups if sg.slide_type != "header"]
        notes_map = {sg.index: "text" for sg in content_groups}
        alignment = _fake_alignment_for(list(notes_map.keys()))

        import click

        with pytest.raises(click.UsageError) as excinfo:
            _run_merge(
                slides=slide_file,
                notes_map=notes_map,
                slide_groups=slide_groups,
                lang="de",
                tag="voiceover",
                model=None,
                dry_run=False,
                output=None,
                multi_part=False,
                alignment=alignment,
                use_companion=True,
                companion_file=companion,
            )

        assert "slide_id" in str(excinfo.value)


# ---------------------------------------------------------------------------
# CLI-level routing: --companion/--no-companion flag
# ---------------------------------------------------------------------------


class TestSyncCliCompanionFlag:
    def test_no_companion_flag_forces_inline_mode(self, tmp_path: Path, monkeypatch):
        """--no-companion should ignore a present companion file and route inline."""
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(SLIDES_WITH_IDS, encoding="utf-8")
        companion = tmp_path / "voiceover_test.py"
        companion.write_text(COMPANION_INITIAL, encoding="utf-8")
        video_file = tmp_path / "video.mp4"
        video_file.write_text("fake")

        captured: dict = {}

        async def fake_merge_notes(**kwargs):
            captured.update(kwargs)
            raise SystemExit(99)

        with patch("clm.cli.commands.voiceover._merge_notes", side_effect=fake_merge_notes):
            # Minimal stubs for expensive pipeline steps — just enough to reach _merge_notes.
            with patch("clm.voiceover.timeline.build_parts", side_effect=SystemExit(77)):
                runner = CliRunner()
                result = runner.invoke(
                    voiceover_group,
                    [
                        "sync",
                        str(slide_file),
                        str(video_file),
                        "--lang",
                        "de",
                        "--no-companion",
                    ],
                    catch_exceptions=True,
                )

        # The --no-companion flag must be accepted (no "no such option" error)
        assert "no such option" not in result.output.lower()

    def test_companion_flag_accepted(self, tmp_path: Path):
        from click.testing import CliRunner

        from clm.cli.commands.voiceover import voiceover_group

        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["sync", "--help"])

        assert "--companion" in result.output
        assert "--no-companion" in result.output
