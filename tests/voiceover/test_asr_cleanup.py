"""Read-time ASR clean-up: repeat collapsing and suspect flags (#1105).

Whisper's classic failure modes — repetition loops and trailing-silence
hallucinations — are handled after ASR, on the transcript as read (fresh or
cached). Synthetic segments only: faster-whisper is never imported here.
"""

from __future__ import annotations

from clm.voiceover.asr_cleanup import (
    clean_transcript,
    collapse_phrase_repeats,
    normalize_text,
)
from clm.voiceover.transcribe import Transcript, TranscriptSegment


def _seg(start: float, end: float, text: str, **kw) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text, **kw)


def _tx(*segments: TranscriptSegment) -> Transcript:
    return Transcript(segments=list(segments), language="en", duration=100.0)


class TestNormalize:
    def test_case_whitespace_and_clause_punctuation_are_ignored(self):
        assert normalize_text("  So, we CAN'T do that! ") == normalize_text("so we can't do that.")

    def test_operators_are_not_punctuation(self):
        """Code dictation must not compare equal across different operators."""
        assert normalize_text("x - 1") != normalize_text("x + 1")
        assert normalize_text("x * 1") != normalize_text("x / 1")


class TestSegmentRuns:
    def test_three_identical_segments_collapse_to_one_spanning_the_run(self):
        """Regression test for #1105: a repetition loop across segments."""
        tx = _tx(
            _seg(0.0, 2.0, "Intro."),
            _seg(2.0, 4.0, "So we can't do that."),
            _seg(4.0, 6.0, "so we can't do that"),
            _seg(6.0, 8.0, "So we can't do that!"),
            _seg(8.0, 9.0, "Next point."),
        )
        out = clean_transcript(tx)
        assert [s.text for s in out.segments] == [
            "Intro.",
            "So we can't do that.",
            "Next point.",
        ]
        collapsed = out.segments[1]
        assert (collapsed.start, collapsed.end) == (2.0, 8.0)
        assert collapsed.collapsed_repeats == 2
        assert "repeat_collapsed" in collapsed.flags

    def test_two_short_identical_segments_are_kept(self):
        """A short phrase said twice may be genuine ("Okay. Okay.")."""
        tx = _tx(_seg(0.0, 1.0, "Okay."), _seg(1.0, 2.0, "Okay."))
        out = clean_transcript(tx)
        assert [s.text for s in out.segments] == ["Okay.", "Okay."]
        assert all(s.flags == () for s in out.segments)

    def test_two_long_identical_segments_collapse(self):
        sentence = "The compiler checks every template instantiation at compile time."
        tx = _tx(_seg(0.0, 3.0, sentence), _seg(3.0, 6.0, sentence))
        out = clean_transcript(tx)
        assert [s.text for s in out.segments] == [sentence]
        assert out.segments[0].collapsed_repeats == 1

    def test_three_single_word_segments_are_kept(self):
        """ "Okay." / "Okay." / "Okay." is plausible speech; merging the spans
        could move speech across a slide boundary."""
        tx = _tx(_seg(0.0, 1.0, "Okay."), _seg(1.0, 2.0, "okay"), _seg(2.0, 3.0, "Okay!"))
        assert len(clean_transcript(tx).segments) == 3

    def test_non_consecutive_repeats_are_kept(self):
        tx = _tx(
            _seg(0.0, 1.0, "Thank you."),
            _seg(1.0, 2.0, "Now the loop."),
            _seg(2.0, 3.0, "Thank you."),
            _seg(3.0, 4.0, "More content here."),
            _seg(4.0, 5.0, "Thank you."),
        )
        assert len(clean_transcript(tx).segments) == 5

    def test_runs_do_not_cross_video_parts(self):
        tx = _tx(
            _seg(0.0, 1.0, "Again and again.", source_part_index=0),
            _seg(1.0, 2.0, "Again and again.", source_part_index=0),
            _seg(2.0, 3.0, "Again and again.", source_part_index=1),
        )
        assert len(clean_transcript(tx).segments) == 3

    def test_quality_metrics_of_a_run_keep_the_worst_value(self):
        tx = _tx(
            _seg(0.0, 1.0, "Bye bye.", avg_logprob=-0.2, no_speech_prob=0.1, compression_ratio=1),
            _seg(1.0, 2.0, "Bye bye.", avg_logprob=-1.4, no_speech_prob=0.05, compression_ratio=1),
            _seg(2.0, 3.0, "Bye bye.", avg_logprob=-0.5, no_speech_prob=0.7, compression_ratio=2.9),
        )
        (seg,) = clean_transcript(tx).segments
        assert seg.avg_logprob == -1.4
        assert seg.no_speech_prob == 0.7
        assert seg.compression_ratio == 2.9


class TestPhraseRuns:
    def test_repeated_clause_inside_a_segment_collapses(self):
        """Regression test for #1105: "on most computers, on most computers, …"."""
        text, removed = collapse_phrase_repeats(
            "This works on most computers, on most computers, on most computers, on most computers."
        )
        assert text == "This works on most computers."
        assert removed == 3

    def test_loop_nested_in_a_wider_loop_collapses_in_one_call(self):
        """Review finding: the widest loop wins a pass; the nested one must
        not survive it (idempotence)."""
        text, removed = collapse_phrase_repeats(" ".join(["x y x y x y z"] * 3))
        assert text == "x y z"
        assert collapse_phrase_repeats(text) == (text, 0)
        assert removed == 2 + 2

    def test_long_single_segment_is_scanned_quickly(self):
        """Review finding: cohere/granite put a whole video into one segment
        and the clean-up runs on every read (cache hits included)."""
        import random
        import time

        rng = random.Random(1105)
        words = [f"w{rng.randrange(500)}" for _ in range(5000)]
        t0 = time.perf_counter()
        collapse_phrase_repeats(" ".join(words))
        assert time.perf_counter() - t0 < 5.0

    def test_twice_repeated_short_clause_is_kept(self):
        text = "It is very, very important and very, very fast."
        assert collapse_phrase_repeats(text) == (text, 0)

    def test_single_word_stutter_is_kept(self):
        text = "No, no, no, that is wrong."
        assert collapse_phrase_repeats(text) == (text, 0)

    def test_long_sentence_said_twice_collapses(self):
        s = "we allocate the buffer once and reuse it for every request"
        text, removed = collapse_phrase_repeats(f"{s}. {s}.")
        assert text == f"{s}."
        assert removed == 1

    def test_segment_level_counts_occurrences_collapsed_inside_segments(self):
        """A loop split as one looping segment plus a repeat still collapses."""
        tx = _tx(
            _seg(0.0, 4.0, "So we can't do that. So we can't do that. So we can't do that."),
            _seg(4.0, 5.0, "So we can't do that."),
        )
        (seg,) = clean_transcript(tx).segments
        assert seg.text == "So we can't do that."
        assert seg.collapsed_repeats == 3
        assert (seg.start, seg.end) == (0.0, 5.0)


class TestFlags:
    def test_metric_thresholds(self):
        tx = _tx(
            _seg(0.0, 1.0, "Fine speech.", avg_logprob=-0.3, no_speech_prob=0.01),
            _seg(1.0, 2.0, "Mumbled.", avg_logprob=-1.3),
            _seg(2.0, 3.0, "Silence talk.", no_speech_prob=0.8),
            _seg(3.0, 4.0, "Loopy text.", compression_ratio=2.7),
            _seg(4.0, 5.0, "Real ending sentence."),
        )
        flags = [s.flags for s in clean_transcript(tx).segments]
        assert flags == [
            (),
            ("low_confidence",),
            ("no_speech",),
            ("high_compression",),
            (),
        ]

    def test_trailing_silence_phrases_are_flagged(self):
        """Regression test for #1105: "Thank you." ×9 at the end of a video."""
        tx = _tx(
            _seg(0.0, 3.0, "And that concludes templates."),
            *[_seg(3.0 + i, 4.0 + i, "Thank you.") for i in range(9)],
        )
        out = clean_transcript(tx)
        assert [s.text for s in out.segments] == ["And that concludes templates.", "Thank you."]
        assert out.segments[0].flags == ()
        assert out.segments[1].flags == ("repeat_collapsed", "trailing_phrase")
        assert out.segments[1].collapsed_repeats == 8

    def test_trailing_phrase_variants(self):
        tx = _tx(
            _seg(0.0, 1.0, "Real content."),
            _seg(1.0, 2.0, "Thanks for watching!"),
            _seg(2.0, 3.0, "Check the link in the description."),
            _seg(3.0, 4.0, "Untertitel im Auftrag des ZDF, 2017"),
        )
        flags = [s.flags for s in clean_transcript(tx).segments]
        assert flags == [(), ("trailing_phrase",), ("trailing_phrase",), ("trailing_phrase",)]

    def test_marker_word_inside_long_final_speech_is_not_flagged(self):
        """Review finding: a whole-video segment mentioning "subscribe" is
        real speech, not a trailing hallucination."""
        tx = _tx(
            _seg(
                0.0,
                60.0,
                "In the observer pattern every listener can subscribe to the subject "
                "and gets notified when its state changes.",
            )
        )
        assert clean_transcript(tx).segments[0].flags == ()

    def test_closing_phrase_mid_video_is_not_flagged(self):
        tx = _tx(_seg(0.0, 1.0, "Thank you."), _seg(1.0, 2.0, "Now the next topic."))
        assert [s.flags for s in clean_transcript(tx).segments] == [(), ()]

    def test_old_cached_segments_without_metrics_get_structural_flags_only(self):
        tx = Transcript.from_dict(
            {
                "language": "en",
                "duration": 3.0,
                "segments": [
                    {"start": 0.0, "end": 1.0, "text": "Hello."},
                    {"start": 1.0, "end": 2.0, "text": "Thank you."},
                ],
            }
        )
        out = clean_transcript(tx)
        assert out.segments[0].avg_logprob is None
        assert [s.flags for s in out.segments] == [(), ("trailing_phrase",)]


class TestIdempotence:
    def test_cleaning_twice_changes_nothing(self):
        tx = _tx(
            _seg(0.0, 1.0, "Loop one, loop one, loop one."),
            _seg(1.0, 2.0, "Intro.", avg_logprob=-2.0),
            *[_seg(2.0 + i, 3.0 + i, "Thank you.") for i in range(4)],
        )
        once = clean_transcript(tx)
        assert clean_transcript(once) == once

    def test_roundtrip_through_json_keeps_flags_and_metrics(self):
        tx = _tx(
            _seg(0.0, 1.0, "Hi.", avg_logprob=-1.5, no_speech_prob=0.2, compression_ratio=1.2),
            *[_seg(1.0 + i, 2.0 + i, "Thank you.") for i in range(3)],
        )
        once = clean_transcript(tx)
        assert Transcript.from_dict(once.to_dict()) == once
