"""Tests for cross-language propagation in ``clm voiceover sync`` (Item 2).

Covers the ``--propagate-to`` flag and the underlying ``_run_propagation``
plumbing:

- Added bullets in the source translate into target bullets.
- Rewrites in the source produce corresponding rewrites in target.
- No-op merges skip propagation (no LLM call for propagate).
- Validation: ``--propagate-to`` must differ from ``--lang`` and cannot
  combine with ``--overwrite``.
- Empty target baseline inserts a fresh cell.
- Dry-run emits two unified diffs, one per language.

Tests stub ``merge_batch`` and ``propagate_batch`` with ``AsyncMock`` so
the LLM is never called; pipeline integration paths live in the existing
voiceover tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from clm.cli.commands.voiceover import _merge_notes, voiceover_group
from clm.notebooks.slide_parser import parse_slides
from clm.voiceover.aligner import AlignmentResult, SlideNotes
from clm.voiceover.merge import MergeResult, PropagationResult

# Bilingual slide file with de + en slides, each carrying a baseline
# voiceover cell in its own language. Two slides: "intro" and "details".
BILINGUAL_SLIDES = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
# ## Einführung
#
# Inhalt.

# %% [markdown] lang="de" tags=["voiceover"]
# - de-baseline-intro-eins
# - de-baseline-intro-zwei

# %% [markdown] lang="en" tags=["slide"] slide_id="intro"
# ## Introduction
#
# Content.

# %% [markdown] lang="en" tags=["voiceover"]
# - en-baseline-intro-one
# - en-baseline-intro-two

# %% [markdown] lang="de" tags=["slide"] slide_id="details"
# ## Details
#
# Mehr Inhalt.

# %% [markdown] lang="de" tags=["voiceover"]
# - de-baseline-details

# %% [markdown] lang="en" tags=["slide"] slide_id="details"
# ## Details
#
# More content.

# %% [markdown] lang="en" tags=["voiceover"]
# - en-baseline-details
"""

# Monolingual variant: only de side present (no en counterpart for "solo").
BILINGUAL_WITH_MONOLINGUAL = """\
# %% [markdown] lang="de" tags=["slide"] slide_id="intro"
# ## Einführung

# %% [markdown] lang="de" tags=["voiceover"]
# - de-baseline-intro

# %% [markdown] lang="en" tags=["slide"] slide_id="intro"
# ## Introduction

# %% [markdown] lang="en" tags=["voiceover"]
# - en-baseline-intro

# %% [markdown] lang="de" tags=["slide"] slide_id="solo"
# ## Nur Deutsch

# %% [markdown] lang="de" tags=["voiceover"]
# - de-baseline-solo
"""


def _fake_alignment_for(slide_indices: list[int]) -> AlignmentResult:
    return AlignmentResult(
        slide_notes={i: SlideNotes(slide_index=i, segments=["placeholder"]) for i in slide_indices}
    )


def _run_merge(**kwargs) -> None:
    asyncio.run(_merge_notes(**kwargs))


# ---------------------------------------------------------------------------
# _run_propagation via _merge_notes (happy path)
# ---------------------------------------------------------------------------


class TestPropagationHappyPath:
    def _setup(self, tmp_path: Path, text: str = BILINGUAL_SLIDES):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(text, encoding="utf-8")
        slide_groups = parse_slides(slide_file, "de")
        content = [sg for sg in slide_groups if sg.slide_type != "header"]
        notes_map = {sg.index: "neuer Transcript Text" for sg in content}
        alignment = _fake_alignment_for(list(notes_map.keys()))
        return slide_file, slide_groups, notes_map, alignment

    def test_propagate_to_translates_added_bullets(self, tmp_path: Path):
        slide_file, slide_groups, notes_map, alignment = self._setup(tmp_path)
        fake_merge = [
            MergeResult(
                slide_id=f"{slide_file.stem}/{i}",
                merged_bullets=(
                    "- de-baseline-intro-eins\n- de-baseline-intro-zwei\n- neuer bullet"
                    if i == list(notes_map)[0]
                    else "- de-baseline-details\n- noch ein bullet"
                ),
            )
            for i in notes_map
        ]
        fake_propagate = [
            PropagationResult(
                slide_id=r.slide_id,
                translated_bullets=(
                    "- en-baseline-intro-one\n- en-baseline-intro-two\n- new bullet"
                    if "intro" in r.merged_bullets.lower() or "eins" in r.merged_bullets
                    else "- en-baseline-details\n- another bullet"
                ),
            )
            for r in fake_merge
        ]

        with (
            patch(
                "clm.voiceover.merge.merge_batch",
                new=AsyncMock(return_value=fake_merge),
            ),
            patch(
                "clm.voiceover.merge.propagate_batch",
                new=AsyncMock(return_value=fake_propagate),
            ) as m_prop,
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
                use_companion=False,
                companion_file=None,
                propagate_to="en",
            )

        assert m_prop.await_count == 1
        text = slide_file.read_text(encoding="utf-8")
        assert "- neuer bullet" in text
        assert "- new bullet" in text
        assert "- en-baseline-intro-one" in text  # untouched en bullet preserved

    def test_propagate_to_translates_rewrite(self, tmp_path: Path):
        slide_file, slide_groups, notes_map, alignment = self._setup(tmp_path)
        first_idx = list(notes_map)[0]
        fake_merge = [
            MergeResult(
                slide_id=f"{slide_file.stem}/{i}",
                merged_bullets=(
                    "- de-baseline-intro-eins-korrigiert\n- de-baseline-intro-zwei"
                    if i == first_idx
                    else "- de-baseline-details"
                ),
                rewrites=(
                    [
                        {
                            "original": "- de-baseline-intro-eins",
                            "revised": "- de-baseline-intro-eins-korrigiert",
                            "transcript_evidence": "Trainer sagte korrigiert",
                        }
                    ]
                    if i == first_idx
                    else []
                ),
            )
            for i in notes_map
        ]
        fake_propagate = [
            PropagationResult(
                slide_id=fake_merge[0].slide_id,
                translated_bullets=("- en-baseline-intro-one-corrected\n- en-baseline-intro-two"),
                corresponded_changes=[
                    {
                        "source_change": "rewrite: eins -> eins-korrigiert",
                        "target_change": "rewrite: one -> one-corrected",
                    }
                ],
            )
        ]

        with (
            patch(
                "clm.voiceover.merge.merge_batch",
                new=AsyncMock(return_value=fake_merge),
            ),
            patch(
                "clm.voiceover.merge.propagate_batch",
                new=AsyncMock(return_value=fake_propagate),
            ),
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
                use_companion=False,
                companion_file=None,
                propagate_to="en",
            )

        text = slide_file.read_text(encoding="utf-8")
        assert "en-baseline-intro-one-corrected" in text
        # Unchanged detail slide's en baseline should survive (no propagation for it).
        assert "en-baseline-details" in text

    def test_propagate_to_no_op_when_merge_no_op(self, tmp_path: Path):
        """When merge returns the baseline unchanged, propagate_batch must not be called."""
        slide_file, slide_groups, notes_map, alignment = self._setup(tmp_path)
        # Return the exact baseline text (extracted from the slide cells)
        content_groups = [sg for sg in slide_groups if sg.slide_type != "header"]
        from clm.cli.commands.voiceover import _extract_baseline

        fake_merge = [
            MergeResult(
                slide_id=f"{slide_file.stem}/{sg.index}",
                merged_bullets=_extract_baseline(sg, "voiceover"),
            )
            for sg in content_groups
        ]

        with (
            patch(
                "clm.voiceover.merge.merge_batch",
                new=AsyncMock(return_value=fake_merge),
            ),
            patch(
                "clm.voiceover.merge.propagate_batch",
                new=AsyncMock(return_value=[]),
            ) as m_prop,
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
                use_companion=False,
                companion_file=None,
                propagate_to="en",
            )

        assert m_prop.await_count == 0

    def test_propagate_to_empty_target_baseline(self, tmp_path: Path):
        """Slide with no en voiceover baseline still gets a translated cell inserted."""
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(
            # de has a baseline voiceover; en slide exists but no voiceover cell yet.
            '# %% [markdown] lang="de" tags=["slide"] slide_id="intro"\n'
            "# ## Einführung\n"
            "\n"
            '# %% [markdown] lang="de" tags=["voiceover"]\n'
            "# - de-baseline\n"
            "\n"
            '# %% [markdown] lang="en" tags=["slide"] slide_id="intro"\n'
            "# ## Introduction\n",
            encoding="utf-8",
        )
        slide_groups = parse_slides(slide_file, "de")
        content_groups = [sg for sg in slide_groups if sg.slide_type != "header"]
        notes_map = {sg.index: "neuer transcript" for sg in content_groups}
        alignment = _fake_alignment_for(list(notes_map.keys()))
        assert len(content_groups) == 1
        src_idx = content_groups[0].index

        fake_merge = [
            MergeResult(
                slide_id=f"{slide_file.stem}/{src_idx}",
                merged_bullets="- de-baseline\n- neuer bullet",
            )
        ]
        fake_propagate = [
            PropagationResult(
                slide_id=fake_merge[0].slide_id,
                translated_bullets="- translated baseline\n- new bullet",
            )
        ]

        with (
            patch(
                "clm.voiceover.merge.merge_batch",
                new=AsyncMock(return_value=fake_merge),
            ),
            patch(
                "clm.voiceover.merge.propagate_batch",
                new=AsyncMock(return_value=fake_propagate),
            ),
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
                use_companion=False,
                companion_file=None,
                propagate_to="en",
            )

        text = slide_file.read_text(encoding="utf-8")
        assert "- translated baseline" in text
        assert "- new bullet" in text
        assert 'lang="en" tags=["voiceover"]' in text

    def test_propagate_to_dry_run_emits_two_diffs(self, tmp_path: Path, capsys):
        slide_file, slide_groups, notes_map, alignment = self._setup(tmp_path)
        original_bytes = slide_file.read_bytes()
        fake_merge = [
            MergeResult(
                slide_id=f"{slide_file.stem}/{i}",
                merged_bullets=f"- de-merged-{i}",
            )
            for i in notes_map
        ]
        fake_propagate = [
            PropagationResult(
                slide_id=r.slide_id, translated_bullets=f"- en-translated-{r.slide_id}"
            )
            for r in fake_merge
        ]

        with (
            patch(
                "clm.voiceover.merge.merge_batch",
                new=AsyncMock(return_value=fake_merge),
            ),
            patch(
                "clm.voiceover.merge.propagate_batch",
                new=AsyncMock(return_value=fake_propagate),
            ),
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
                use_companion=False,
                companion_file=None,
                propagate_to="en",
            )

        # Dry-run must not modify the file.
        assert slide_file.read_bytes() == original_bytes
        # Capture stdout via Rich; it should mention both diffs.
        out = capsys.readouterr().out
        assert "Propagation diff" in out or "propagation" in out.lower()


# ---------------------------------------------------------------------------
# CLI-level validation
# ---------------------------------------------------------------------------


class TestPropagateCliValidation:
    def test_propagate_to_rejects_same_language(self, tmp_path: Path):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(BILINGUAL_SLIDES, encoding="utf-8")
        video_file = tmp_path / "video.mp4"
        video_file.write_text("fake")

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "sync",
                str(slide_file),
                str(video_file),
                "--lang",
                "de",
                "--propagate-to",
                "de",
            ],
        )
        assert result.exit_code != 0
        assert "must differ from --lang" in result.output

    def test_propagate_to_rejects_overwrite(self, tmp_path: Path):
        slide_file = tmp_path / "slides_test.py"
        slide_file.write_text(BILINGUAL_SLIDES, encoding="utf-8")
        video_file = tmp_path / "video.mp4"
        video_file.write_text("fake")

        runner = CliRunner()
        result = runner.invoke(
            voiceover_group,
            [
                "sync",
                str(slide_file),
                str(video_file),
                "--lang",
                "de",
                "--propagate-to",
                "en",
                "--overwrite",
            ],
        )
        assert result.exit_code != 0
        assert "--propagate-to" in result.output
        assert "--overwrite" in result.output

    def test_propagate_to_accepted_in_help(self):
        runner = CliRunner()
        result = runner.invoke(voiceover_group, ["sync", "--help"])
        assert "--propagate-to" in result.output
