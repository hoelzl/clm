"""Tests for the harvest (video-side) MCP tool handlers.

The handlers delegate to heavier library functions (transcribe, OCR,
keyframe detection, LLM judge).  Here we exercise the wrapping/JSON
shape by mocking the expensive layers; end-to-end behavior is covered
in the corresponding library-level tests (``tests/voiceover/...``).
The ``harvest_report`` / ``harvest_task`` handlers are pinned against a
tiny real deck via the ``--alignment``-style short-circuit (no ASR).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clm.mcp.tools import (
    handle_harvest_backfill_dry,
    handle_harvest_cache_list,
    handle_harvest_compare,
    handle_harvest_identify_rev,
    handle_harvest_report,
    handle_harvest_task,
    handle_harvest_trace_show,
    handle_harvest_transcribe,
)

# ---------------------------------------------------------------------------
# harvest_transcribe
# ---------------------------------------------------------------------------


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str):
        self.start = start
        self.end = end
        self.text = text


class _FakeTranscript:
    def __init__(self):
        self.language = "en"
        self.duration = 12.3
        self.segments = [
            _FakeSegment(0.0, 1.5, "hello world"),
            _FakeSegment(2.0, 4.0, "goodbye world"),
        ]


class TestHandleHarvestTranscribe:
    async def test_cache_hit_returns_summary(self, tmp_path: Path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"")

        with patch(
            "clm.voiceover.cache.cached_transcribe",
            return_value=(_FakeTranscript(), True),
        ):
            out = await handle_harvest_transcribe(
                str(video),
                tmp_path,
                lang="en",
            )

        data = json.loads(out)
        assert data["cache_hit"] is True
        assert data["language"] == "en"
        assert data["segment_count"] == 2
        assert data["duration_sec"] == pytest.approx(12.3)
        assert data["first_segment"]["text"] == "hello world"
        assert data["last_segment"]["text"] == "goodbye world"

    async def test_resolves_relative_video_path(self, tmp_path: Path):
        (tmp_path / "v.mp4").write_bytes(b"")

        captured: dict = {}

        def _fake(video_path, **_):
            captured["video_path"] = Path(video_path)
            return _FakeTranscript(), False

        with patch("clm.voiceover.cache.cached_transcribe", side_effect=_fake):
            await handle_harvest_transcribe("v.mp4", tmp_path, lang="en")

        assert captured["video_path"].is_absolute()
        assert captured["video_path"].name == "v.mp4"

    async def test_no_cache_disables_policy(self, tmp_path: Path):
        (tmp_path / "v.mp4").write_bytes(b"")
        captured: dict = {}

        def _fake(video_path, *, policy, **_):
            captured["enabled"] = policy.enabled
            captured["refresh"] = policy.refresh
            return _FakeTranscript(), False

        with patch("clm.voiceover.cache.cached_transcribe", side_effect=_fake):
            await handle_harvest_transcribe(
                "v.mp4", tmp_path, lang="en", no_cache=True, refresh_cache=True
            )

        assert captured["enabled"] is False
        assert captured["refresh"] is True


# ---------------------------------------------------------------------------
# harvest_identify_rev
# ---------------------------------------------------------------------------


def _rev_score(rev: str, score: float):
    from datetime import datetime, timezone

    from clm.voiceover.rev_scorer import RevisionScore

    return RevisionScore(
        rev=rev,
        date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        subject=f"subject for {rev[:6]}",
        base_score=score,
        narrative_prior=1.0,
        score=score,
        is_narrative_candidate=False,
        run_id=None,
        run_position=None,
    )


class TestHandleHarvestIdentifyRev:
    async def test_happy_path_returns_top_revs(self, tmp_path: Path):
        slide_file = tmp_path / "slides.py"
        slide_file.write_text("# %%\n", encoding="utf-8")
        video = tmp_path / "v.mp4"
        video.write_bytes(b"")

        fake_scored = [_rev_score("abcdef1234", 0.9), _rev_score("999888aaaa", 0.7)]

        with patch(
            "clm.voiceover.identify.identify_rev",
            return_value=fake_scored,
        ):
            out = await handle_harvest_identify_rev(
                str(slide_file),
                [str(video)],
                tmp_path,
                lang="de",
                top=2,
            )

        data = json.loads(out)
        assert len(data["top_revisions"]) == 2
        assert data["top_revisions"][0]["rev"] == "abcdef1234"
        assert data["top_revisions"][0]["score"] == pytest.approx(0.9)
        assert "accept_threshold" in data

    async def test_empty_fingerprint_returns_error_key(self, tmp_path: Path):
        slide_file = tmp_path / "slides.py"
        slide_file.write_text("# %%\n", encoding="utf-8")
        video = tmp_path / "v.mp4"
        video.write_bytes(b"")

        with patch(
            "clm.voiceover.identify.identify_rev",
            side_effect=ValueError("video fingerprint is empty (no OCR text extracted)"),
        ):
            out = await handle_harvest_identify_rev(
                str(slide_file),
                [str(video)],
                tmp_path,
                lang="de",
            )

        data = json.loads(out)
        assert "error" in data
        assert "fingerprint" in data["error"]


# ---------------------------------------------------------------------------
# harvest_compare
# ---------------------------------------------------------------------------


_MIN_SLIDES = """# %% [markdown] lang="en" tags=["slide"] slide_id="intro"
# Intro

Explain the goals.

# %% [markdown] lang="en" tags=["voiceover"]
- talk about goals
"""


def _mock_llm_response(content: str):
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestHandleHarvestCompare:
    async def test_returns_compare_report_json(self, tmp_path: Path):
        src = tmp_path / "old.py"
        tgt = tmp_path / "new.py"
        src.write_text(_MIN_SLIDES, encoding="utf-8")
        tgt.write_text(_MIN_SLIDES, encoding="utf-8")

        llm_payload = json.dumps(
            {
                "bullets": "- talk about goals",
                "outcomes": [
                    {
                        "status": "covered",
                        "target": "- talk about goals",
                        "source": "- talk about goals",
                    }
                ],
            }
        )
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_llm_response(llm_payload)
        )

        with patch(
            "clm.infrastructure.llm.client._build_client",
            return_value=mock_client,
        ):
            out = await handle_harvest_compare(str(src), str(tgt), tmp_path, lang="en")

        data = json.loads(out)
        assert data["language"] == "en"
        assert data["source"].endswith("old.py")
        assert data["target"].endswith("new.py")
        assert data["status_totals"]["covered"] == 1


# ---------------------------------------------------------------------------
# harvest_backfill_dry
# ---------------------------------------------------------------------------


class TestHandleHarvestBackfillDry:
    async def test_invokes_subprocess_with_dry_run(self, tmp_path: Path):
        slide_file = tmp_path / "slides.py"
        slide_file.write_text("# %%\n", encoding="utf-8")
        video = tmp_path / "v.mp4"
        video.write_bytes(b"")

        captured: dict = {}

        async def _fake_exec(*args, **_):
            captured["args"] = args

            class _Proc:
                returncode = 0

                async def communicate(self):
                    return b"stdout here\n", b""

            return _Proc()

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            out = await handle_harvest_backfill_dry(
                str(slide_file),
                [str(video)],
                tmp_path,
                lang="de",
                auto=True,
            )

        data = json.loads(out)
        assert data["returncode"] == 0
        assert "stdout here" in data["stdout"]
        # The CLI verb moved to the harvest group in this release:
        # ``python -m clm.cli.main harvest backfill ...``.
        main_idx = captured["args"].index("clm.cli.main")
        assert captured["args"][main_idx + 1 : main_idx + 3] == ("harvest", "backfill")
        # Confirm the subprocess argv includes --dry-run and --auto.
        assert "--dry-run" in captured["args"]
        assert "--auto" in captured["args"]
        assert "--apply" not in captured["args"]

    async def test_rev_overrides_auto(self, tmp_path: Path):
        slide_file = tmp_path / "slides.py"
        slide_file.write_text("# %%\n", encoding="utf-8")
        video = tmp_path / "v.mp4"
        video.write_bytes(b"")

        captured: dict = {}

        async def _fake_exec(*args, **_):
            captured["args"] = args

            class _Proc:
                returncode = 0

                async def communicate(self):
                    return b"", b""

            return _Proc()

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            await handle_harvest_backfill_dry(
                str(slide_file),
                [str(video)],
                tmp_path,
                lang="de",
                rev="abc1234",
                auto=True,
            )

        args = captured["args"]
        # When --rev is set, --auto must not be passed.
        assert "--rev" in args
        assert "abc1234" in args
        assert "--auto" not in args


# ---------------------------------------------------------------------------
# harvest_cache_list
# ---------------------------------------------------------------------------


class TestHandleHarvestCacheList:
    async def test_empty_when_missing_root(self, tmp_path: Path):
        out = await handle_harvest_cache_list(tmp_path, cache_root=str(tmp_path / "does-not-exist"))
        data = json.loads(out)
        assert data["entries"] == []
        assert data["total_bytes"] == 0

    async def test_lists_entries(self, tmp_path: Path):
        cache = tmp_path / "cache"
        (cache / "transcripts").mkdir(parents=True)
        (cache / "transcripts" / "abc123.json").write_text('{"x": 1}', encoding="utf-8")

        out = await handle_harvest_cache_list(tmp_path, cache_root=str(cache))
        data = json.loads(out)
        assert data["total_bytes"] > 0
        assert len(data["entries"]) == 1
        entry = data["entries"][0]
        assert entry["kind"] == "transcripts"
        assert entry["key"] == "abc123"


# ---------------------------------------------------------------------------
# harvest_trace_show
# ---------------------------------------------------------------------------


class TestHandleHarvestTraceShow:
    async def test_reads_trace_entries(self, tmp_path: Path):
        log = tmp_path / "trace.jsonl"
        log.write_text(
            '{"schema": "clm.voiceover.trace/1", "slide_id": "a"}\n'
            '{"schema": "clm.voiceover.trace/1", "slide_id": "b"}\n',
            encoding="utf-8",
        )

        out = await handle_harvest_trace_show(str(log), tmp_path)
        data = json.loads(out)
        assert data["entry_count"] == 2
        assert data["schema_tags"] == ["clm.voiceover.trace/1"]
        assert data["entries"][0]["slide_id"] == "a"

    async def test_resolves_relative_path(self, tmp_path: Path):
        (tmp_path / "trace.jsonl").write_text('{"slide_id": "a"}\n', encoding="utf-8")
        out = await handle_harvest_trace_show("trace.jsonl", tmp_path)
        data = json.loads(out)
        assert data["entry_count"] == 1
        # Empty schema falls back to <v0>.
        assert data["schema_tags"] == ["<v0>"]


# ---------------------------------------------------------------------------
# harvest_report / harvest_task — pinned against a tiny real deck bundle
# (alignment override short-circuits ASR/ffmpeg/OCR; mirrors
# tests/cli/test_harvest_cli.py)
# ---------------------------------------------------------------------------

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _companion_cell(slug: str, lang: str, owner: str, text: str) -> str:
    return (
        f'# %% [markdown] lang="{lang}" tags=["notes"] for_slide="{owner}" '
        f'vo_anchor="id:{owner}#0" slide_id="{slug}"\n#\n# - {text}\n\n'
    )


def _build(*parts: str) -> str:
    return "".join(parts).rstrip("\n") + "\n"


def _write_deck_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """The deck pair + companions + a dummy video; returns (de_path, video)."""
    de = _build(
        HEADER_DE,
        _slide("s0", "de", "Alpha"),
        _slide("s1", "de", "Beta"),
    )
    en = _build(
        HEADER_EN,
        _slide("s0", "en", "Alpha"),
        _slide("s1", "en", "Beta"),
    )
    de_path = tmp_path / "slides_t.de.py"
    en_path = tmp_path / "slides_t.en.py"
    de_path.write_text(de, encoding="utf-8")
    en_path.write_text(en, encoding="utf-8")

    companion_dir = tmp_path / "voiceover"
    companion_dir.mkdir()
    de_comp = _build(_companion_cell("s0-vo", "de", "s0", "Bestand Alpha."))
    en_comp = _build(_companion_cell("s0-vo", "en", "s0", "Existing alpha."))
    (companion_dir / "voiceover_t.de.py").write_text(de_comp, encoding="utf-8")
    (companion_dir / "voiceover_t.en.py").write_text(en_comp, encoding="utf-8")

    video = tmp_path / "video.mp4"
    video.write_bytes(b"not a real video")
    return de_path, video


def _slide_indices(de_path: Path) -> dict[str, int]:
    from clm.notebooks.slide_parser import parse_slides

    groups = parse_slides(de_path, "de")
    return {
        sg.cells[0].slide_id: sg.index
        for sg in groups
        if sg.slide_type != "header" and sg.cells and sg.cells[0].slide_id
    }


def _write_alignment(tmp_path: Path, de_path: Path) -> Path:
    idx = _slide_indices(de_path)
    payload = {
        "slide_notes": {
            str(idx["s0"]): {
                "slide_index": idx["s0"],
                "segments": ["Neues zu Alpha."],
                "revisited_segments": [],
            },
            str(idx["s1"]): {
                "slide_index": idx["s1"],
                "segments": ["Alles über Beta."],
                "revisited_segments": [],
            },
        },
        "unassigned_segments": [],
    }
    path = tmp_path / "alignment.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestHandleHarvestReport:
    async def test_report_items_keyed_by_slide_handle(self, tmp_path: Path):
        de_path, video = _write_deck_fixture(tmp_path)
        alignment = _write_alignment(tmp_path, de_path)

        out = await handle_harvest_report(
            str(de_path),
            [str(video)],
            tmp_path,
            lang="de",
            alignment=str(alignment),
            no_cache=True,
        )

        data = json.loads(out)
        assert data["video_language"] == "de"
        assert data["video_fingerprint"]
        by_key = {item["key"]: item for item in data["items"]}
        assert by_key["id:s0"]["class"] == "transcript_adds_material"
        assert by_key["id:s1"]["class"] == "no_existing_vo"
        assert data["summary"]["actionable"] is True

    async def test_non_normalized_bundle_returns_error(self, tmp_path: Path):
        de_path, video = _write_deck_fixture(tmp_path)
        # An id-less slide makes the v3 lens refuse the bundle.
        idless = '# %% [markdown] lang="de" tags=["slide"]\n#\n# # Ohne Id\n\n'
        de_path.write_text(de_path.read_text(encoding="utf-8") + idless, encoding="utf-8")
        alignment = _write_alignment(tmp_path, de_path)

        out = await handle_harvest_report(
            str(de_path),
            [str(video)],
            tmp_path,
            lang="de",
            alignment=str(alignment),
            no_cache=True,
        )

        data = json.loads(out)
        assert "error" in data
        assert "normalize" in data["error"]


class TestHandleHarvestTask:
    async def test_tasks_use_harvest_bullets_validator(self, tmp_path: Path):
        de_path, video = _write_deck_fixture(tmp_path)
        alignment = _write_alignment(tmp_path, de_path)

        out = await handle_harvest_task(
            str(de_path),
            [str(video)],
            tmp_path,
            lang="de",
            alignment=str(alignment),
            no_cache=True,
        )

        data = json.loads(out)
        assert data["schema"] == 1
        assert data["tool"] == "harvest"
        assert data["verb"] == "task"
        assert data["video_fingerprint"]
        assert data["tasks"], "expected at least one framed task"
        by_item = {task["item"]: task for task in data["tasks"]}
        for task in data["tasks"]:
            assert task["validator"] == "harvest-bullets"
            # The answer schema is the multi-narrative shape.
            assert "updates" in task["answer_schema"]["required"]
            assert "baseline_fingerprints" in task["answer_schema"]["required"]
        # s0 has an existing voiceover baseline, so its freshness tokens are set
        # (s1 is a no_existing_vo slide — an empty fingerprint map is correct).
        assert by_item["id:s0"]["baseline_fingerprints"]

    async def test_unknown_slide_returns_error(self, tmp_path: Path):
        de_path, video = _write_deck_fixture(tmp_path)
        alignment = _write_alignment(tmp_path, de_path)

        out = await handle_harvest_task(
            str(de_path),
            [str(video)],
            tmp_path,
            lang="de",
            slide="does-not-exist",
            alignment=str(alignment),
            no_cache=True,
        )

        data = json.loads(out)
        assert "error" in data
        assert "id:does-not-exist" in data["error"]
