"""ASR suspect flags reach the curating agent (#1105).

The quality metrics faster-whisper reports per segment are carried into the
transcript, cleaned at read time, kept through the alignment (cache and
override files), and surfaced in the harvest report the curate task embeds.
No real model and no real video: the backend is mocked, the pipeline stages
around ASR are patched.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from clm.cli.commands.harvest import harvest_group
from clm.voiceover.aligner import AlignmentResult, SegmentAssignment, SlideNotes
from clm.voiceover.asr_cleanup import ASR_CLEANUP_VERSION
from clm.voiceover.cache import (
    AlignmentsCache,
    CachePolicy,
    SlidesKey,
    decode_alignment,
    encode_alignment,
    video_key_for,
)
from clm.voiceover.matcher import TimelineEntry
from clm.voiceover.timeline import VideoPart, offset_transcript
from clm.voiceover.transcribe import FasterWhisperBackend, Transcript, TranscriptSegment

# ---------------------------------------------------------------------------
# The backend carries faster-whisper's per-segment metrics
# ---------------------------------------------------------------------------


def _fw_segment(start, end, text, **metrics):
    seg = MagicMock()
    seg.start, seg.end, seg.text = start, end, text
    for name, value in metrics.items():
        setattr(seg, name, value)
    return seg


@patch("clm.voiceover.transcribe.load_whisper_audio", return_value=[0.0])
@patch("clm.voiceover.transcribe.FasterWhisperBackend._get_model")
def test_backend_carries_segment_quality_metrics(mock_get_model, _audio, tmp_path):
    model = MagicMock()
    model.transcribe.return_value = (
        iter(
            [
                _fw_segment(
                    0.0, 1.0, " Hi. ", avg_logprob=-0.25, no_speech_prob=0.5, compression_ratio=1.5
                ),
                # A segment object without usable metrics (MagicMock attrs).
                _fw_segment(1.0, 2.0, " Bye. "),
            ]
        ),
        MagicMock(language="en", duration=2.0),
    )
    mock_get_model.return_value = model

    tx = FasterWhisperBackend(model_size="tiny").transcribe(tmp_path / "a.wav", language="en")

    first, second = tx.segments
    assert (first.avg_logprob, first.no_speech_prob, first.compression_ratio) == (-0.25, 0.5, 1.5)
    assert (second.avg_logprob, second.no_speech_prob, second.compression_ratio) == (
        None,
        None,
        None,
    )
    # The worker hands the transcript to the parent as JSON: metrics survive.
    assert Transcript.from_dict(json.loads(json.dumps(tx.to_dict()))) == tx
    # Decoder settings are out of scope for #1105 — unchanged.
    _, kwargs = model.transcribe.call_args
    assert kwargs == {"language": "en", "beam_size": 5, "vad_filter": True}


def test_old_transcript_dicts_load_without_metrics():
    seg = TranscriptSegment.from_dict({"start": 0.0, "end": 1.0, "text": "x"})
    assert seg.avg_logprob is None and seg.flags == () and seg.collapsed_repeats == 0
    assert seg.to_dict() == {"start": 0.0, "end": 1.0, "text": "x"}


def test_offset_transcript_keeps_metrics_and_flags():
    tx = Transcript(
        segments=[
            TranscriptSegment(
                0.0, 1.0, "x", avg_logprob=-2.0, flags=("low_confidence",), collapsed_repeats=2
            )
        ],
        language="en",
        duration=1.0,
    )
    (seg,) = offset_transcript(
        tx, VideoPart(index=1, path=Path("b.mp4"), duration=1, offset=10)
    ).segments
    assert (seg.start, seg.source_part_index) == (10.0, 1)
    assert seg.avg_logprob == -2.0
    assert seg.flags == ("low_confidence",)
    assert seg.collapsed_repeats == 2


# ---------------------------------------------------------------------------
# The alignment encoding keeps the flags (cache entries and override files)
# ---------------------------------------------------------------------------


def _flagged(start=5.0, end=6.0, text="Thank you."):
    return TranscriptSegment(
        start,
        end,
        text,
        no_speech_prob=0.9,
        collapsed_repeats=3,
        flags=("no_speech", "repeat_collapsed", "trailing_phrase"),
    )


def test_alignment_encoding_roundtrips_flags():
    seg = _flagged()
    alignment = AlignmentResult(
        slide_notes={1: SlideNotes(slide_index=1, segments=[seg.text])},
        unassigned_segments=[seg],
        assignments=[SegmentAssignment(segment=seg, slide_index=1, reason="clear")],
    )
    decoded = decode_alignment(json.loads(json.dumps(encode_alignment(alignment))))
    assert decoded.assignments[0].segment == seg
    assert decoded.unassigned_segments == [seg]


def test_unflagged_segment_encoding_is_unchanged():
    """Alignment fingerprints of pre-#1105 alignments must not move."""
    alignment = AlignmentResult(
        slide_notes={},
        unassigned_segments=[TranscriptSegment(0.0, 1.0, "x")],
    )
    assert encode_alignment(alignment)["unassigned_segments"] == [
        {"start": 0.0, "end": 1.0, "text": "x", "source_part_index": 0}
    ]


# ---------------------------------------------------------------------------
# run_pipeline cleans the transcript at read time
# ---------------------------------------------------------------------------


def _run_pipeline_with(tmp_path, transcript, policy):
    from clm.voiceover.harvest import run_pipeline

    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    slides = tmp_path / "slides_x.de.py"
    slides.write_text("# %% [markdown]\n# x\n", encoding="utf-8")
    part = VideoPart(index=0, path=video, duration=100.0, offset=0.0)
    match = MagicMock(
        timeline=[TimelineEntry(slide_index=1, start_time=0.0, end_time=100.0, match_score=90)]
    )
    with (
        patch("clm.voiceover.timeline.build_parts", return_value=[part]),
        patch("clm.voiceover.keyframes.detect_transitions", return_value=([], None)),
        patch("clm.voiceover.matcher.match_events_to_slides", return_value=match),
    ):
        return (
            run_pipeline(
                slides,
                [video],
                "en",
                [],
                policy=policy,
                transcript_override=transcript,
            ),
            video,
            slides,
        )


def _looping_transcript():
    return Transcript(
        segments=[
            TranscriptSegment(0.0, 2.0, "Templates are compiled lazily."),
            *[TranscriptSegment(2.0 + i, 3.0 + i, "Thank you.") for i in range(9)],
        ],
        language="en",
        duration=100.0,
    )


def test_run_pipeline_collapses_and_flags_the_transcript(tmp_path):
    """Regression test for #1105: the agent sees one flagged "Thank you."."""
    artifacts, _, _ = _run_pipeline_with(tmp_path, _looping_transcript(), CachePolicy.disabled())
    notes = artifacts.alignment.slide_notes[1]
    assert notes.segments == ["Templates are compiled lazily.", "Thank you."]
    last = artifacts.alignment.assignments[-1].segment
    assert last.flags == ("repeat_collapsed", "trailing_phrase")
    assert (last.start, last.end) == (2.0, 11.0)


def test_alignment_cached_before_cleanup_is_not_served(tmp_path):
    """The alignment cache config names the clean-up version, so an alignment
    computed from a raw transcript misses once and is recomputed."""
    cache_root = tmp_path / "cache"
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    slides = tmp_path / "slides_x.de.py"
    slides.write_text("# %% [markdown]\n# x\n", encoding="utf-8")
    raw = AlignmentResult(
        slide_notes={1: SlideNotes(slide_index=1, segments=["Thank you."] * 9)},
    )
    AlignmentsCache(cache_root).put(
        video_key_for([video]),
        SlidesKey.from_path(slides),
        {"lang": "en", "multi_part": False},
        raw,
    )

    artifacts, _, _ = _run_pipeline_with(
        tmp_path, _looping_transcript(), CachePolicy(cache_root=cache_root)
    )
    assert artifacts.alignment.slide_notes[1].segments == [
        "Templates are compiled lazily.",
        "Thank you.",
    ]
    hit = AlignmentsCache(cache_root).get(
        video_key_for([video]),
        SlidesKey.from_path(slides),
        {"lang": "en", "multi_part": False, "asr_cleanup": ASR_CLEANUP_VERSION},
    )
    assert hit is not None


# ---------------------------------------------------------------------------
# The report surfaces the flags
# ---------------------------------------------------------------------------

HEADER_DE = "# j2 from 'macros.j2' import header_de\n# {{ header_de(\"Titel DE\") }}\n\n"
HEADER_EN = "# j2 from 'macros.j2' import header_en\n# {{ header_en(\"Title EN\") }}\n\n"


def _slide(slug: str, lang: str, title: str) -> str:
    return f'# %% [markdown] lang="{lang}" tags=["slide"] slide_id="{slug}"\n#\n# # {title}\n\n'


def _write_deck(tmp_path: Path) -> tuple[Path, Path, int]:
    de = HEADER_DE + _slide("s0", "de", "Alpha").rstrip("\n") + "\n"
    en = HEADER_EN + _slide("s0", "en", "Alpha").rstrip("\n") + "\n"
    de_path = tmp_path / "slides_t.de.py"
    de_path.write_text(de, encoding="utf-8")
    (tmp_path / "slides_t.en.py").write_text(en, encoding="utf-8")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"not a real video")

    from clm.core.slide_text.slide_parser import parse_slides

    (idx,) = [sg.index for sg in parse_slides(de_path, "de") if sg.slide_type != "header"]
    return de_path, video, idx


def _report(tmp_path: Path, alignment: AlignmentResult, *extra: str):
    de_path, video, _ = _write_deck(tmp_path)
    path = tmp_path / "alignment.json"
    path.write_text(json.dumps(encode_alignment(alignment)), encoding="utf-8")
    result = CliRunner().invoke(
        harvest_group,
        ["report", str(de_path), str(video), "--lang", "de", "--alignment", str(path), *extra],
        catch_exceptions=False,
    )
    return result


def _flagged_alignment(idx: int) -> AlignmentResult:
    good = TranscriptSegment(0.0, 4.0, "Alpha ist wichtig.", avg_logprob=-0.2)
    bad = _flagged()
    stray = TranscriptSegment(
        9.0, 10.0, "Untertitel im Auftrag des ZDF", flags=("trailing_phrase",)
    )
    return AlignmentResult(
        slide_notes={idx: SlideNotes(slide_index=idx, segments=[good.text, bad.text])},
        unassigned_segments=[stray],
        assignments=[
            SegmentAssignment(segment=good, slide_index=idx, reason="clear"),
            SegmentAssignment(segment=bad, slide_index=idx, reason="clear"),
            SegmentAssignment(segment=stray, slide_index=None, reason="no_overlap"),
        ],
    )


def test_report_lists_suspect_segments_per_slide(tmp_path):
    _, _, idx = _write_deck(tmp_path)
    result = _report(tmp_path, _flagged_alignment(idx), "--json")
    report = json.loads(result.output[result.output.index("{") :])

    (item,) = report["items"]
    assert item["transcript"]["suspect_segments"] == [
        {
            "start": 5.0,
            "end": 6.0,
            "text": "Thank you.",
            "source_part_index": 0,
            "flags": ["no_speech", "repeat_collapsed", "trailing_phrase"],
            "no_speech_prob": 0.9,
            "collapsed_repeats": 3,
        }
    ]
    (stray,) = report["unmatched_speech"]
    assert stray["flags"] == ["trailing_phrase"]
    assert report["summary"]["suspect_segments"] == 2


def test_human_report_mentions_suspect_segments(tmp_path):
    _, _, idx = _write_deck(tmp_path)
    result = _report(tmp_path, _flagged_alignment(idx))
    assert "suspect_segments: 2" in result.output


def test_suspects_on_a_stale_slide_index_are_surfaced_and_counted(tmp_path):
    """Review finding: every counted suspect must be findable in the report."""
    _, _, idx = _write_deck(tmp_path)
    bad = _flagged()
    alignment = AlignmentResult(
        slide_notes={99: SlideNotes(slide_index=99, segments=[bad.text])},
        assignments=[SegmentAssignment(segment=bad, slide_index=99, reason="clear")],
    )
    result = _report(tmp_path, alignment, "--json")
    report = json.loads(result.output[result.output.index("{") :])
    (stale,) = report["unmatched_speech"]
    assert stale["slide_index"] == 99
    assert stale["suspect_segments"][0]["flags"] == [
        "no_speech",
        "repeat_collapsed",
        "trailing_phrase",
    ]
    assert report["summary"]["suspect_segments"] == 1


def test_alignment_without_assignment_trail_has_no_suspect_list(tmp_path):
    """No trail → flags unknown: the key is absent, not an empty list."""
    _, _, idx = _write_deck(tmp_path)
    alignment = AlignmentResult(slide_notes={idx: SlideNotes(slide_index=idx, segments=["Hallo."])})
    result = _report(tmp_path, alignment, "--json")
    report = json.loads(result.output[result.output.index("{") :])
    assert "suspect_segments" not in report["items"][0]["transcript"]
    assert report["summary"]["suspect_segments"] == 0
