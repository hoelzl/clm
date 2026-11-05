"""Read-time clean-up of ASR transcripts: repeat collapsing and suspect flags.

Whisper models have well-known failure modes that every curating agent
otherwise has to spot by hand (#1105): *repetition loops* (the same sentence
4–6 times, or "on most computers, on most computers, …" inside one segment)
and *hallucinations* over silence or dropouts ("Thank you." ×9 at the end of
a video, "… link in the description").

This module post-processes a :class:`~clm.voiceover.transcribe.Transcript`
**as read** — fresh from ASR or from the transcript cache — so it works on
transcripts cached before it existed and never invalidates an ASR cache
entry. It changes no decoder setting. Two passes:

**Collapse exact repeats** (conservative: only text that is identical after
case folding, whitespace normalization, and stripping *clause punctuation*
— ``. , ! ? ; : …`` and quotes — from word edges; operators such as ``-``
``+`` ``*`` ``/`` stay significant so code dictation never compares equal):

* inside a segment, a phrase of at least :data:`MIN_PHRASE_WORDS` words
  repeated back to back :data:`MIN_RUN` or more times, or a phrase of at
  least :data:`LONG_PHRASE_WORDS` words said twice in a row, keeps one
  copy (single-word stutters such as "no, no, no" and doubled short phrases
  such as "very, very" are speech, not loops, and are kept);
* consecutive segments of the same video part with identical text of at
  least :data:`MIN_PHRASE_WORDS` words ("Okay." three times stays) are
  merged when they add up to :data:`MIN_RUN` or more occurrences (counting
  copies already collapsed inside them), or to two occurrences of a phrase
  of at least :data:`LONG_PHRASE_WORDS` words. The merged segment spans the
  whole run (first start → last end) and keeps the *worst* quality metric of
  the run.

The number of removed copies is kept in ``collapsed_repeats``.

**Flag suspect segments** (never removed — the agent judges): see
:data:`FLAGS`. Metric flags use faster-whisper's own default thresholds
(``log_prob_threshold=-1.0``, ``no_speech_threshold=0.6``,
``compression_ratio_threshold=2.4``); they are unknown (absent) for
transcripts cached before the metrics were recorded and for backends that
do not report them.

The function is idempotent, so a ``--transcript`` file that was already
cleaned can be cleaned again. :data:`ASR_CLEANUP_VERSION` is part of the
alignment cache config: bump it whenever the rules change, so cached
alignments built from differently cleaned transcripts are recomputed.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence

from clm.voiceover.transcribe import Transcript, TranscriptSegment

__all__ = [
    "ASR_CLEANUP_VERSION",
    "FLAGS",
    "clean_transcript",
    "collapse_phrase_repeats",
    "normalize_text",
]

#: Part of the alignment cache config; bump when the clean-up rules change.
ASR_CLEANUP_VERSION = 1

#: A run of this many identical occurrences is a loop, whatever its length.
MIN_RUN = 3
#: Inside a segment, a looping phrase must have at least this many words.
MIN_PHRASE_WORDS = 2
#: A phrase at least this long said twice in a row is already a loop.
LONG_PHRASE_WORDS = 8
#: Longest phrase searched for inside a segment. Bounds the scan, which runs
#: on every read and over whole-video single segments (cohere/granite).
MAX_PHRASE_WORDS = 40
#: A substring hallucination marker ("… link in the description") counts
#: only in a short segment, never inside a long passage of real speech.
MAX_TRAILING_CONTAINS_WORDS = 12

#: faster-whisper's own defaults for the corresponding decoder thresholds.
LOW_AVG_LOGPROB = -1.0
HIGH_NO_SPEECH_PROB = 0.6
HIGH_COMPRESSION_RATIO = 2.4

#: The suspect flags, in the order they are reported.
FLAGS: dict[str, str] = {
    "low_confidence": f"avg_logprob below {LOW_AVG_LOGPROB}",
    "no_speech": f"no_speech_prob above {HIGH_NO_SPEECH_PROB}",
    "high_compression": f"compression_ratio above {HIGH_COMPRESSION_RATIO} (repetitive text)",
    "repeat_collapsed": "repeated copies of this text were collapsed into one",
    "trailing_phrase": "a classic trailing-silence hallucination at the end of the recording",
}

# Clause punctuation stripped from word edges before comparing.
_CLAUSE_PUNCT = ".,!?;:…\"'“”„‘’«»‚‹›¿¡"

# Classic Whisper hallucinations over trailing silence (normalized text).
_TRAILING_EXACT = frozenset(
    {
        "thank you",
        "thank you very much",
        "thank you so much",
        "thanks",
        "thanks for watching",
        "thank you for watching",
        "thank you so much for watching",
        "please subscribe",
        "bye",
        "bye bye",
        "vielen dank",
        "danke",
        "danke schön",
        "dankeschön",
        "danke fürs zuschauen",
        "vielen dank fürs zuschauen",
        "tschüss",
    }
)
_TRAILING_CONTAINS = re.compile(
    r"link in the description|subscribe|thanks for watching|thank you for watching"
    r"|fürs zuschauen|untertitel|amara org"
)


def _norm_word(word: str) -> str:
    return word.strip(_CLAUSE_PUNCT).casefold()


def normalize_text(text: str) -> str:
    """Case-, whitespace- and clause-punctuation-insensitive comparison key."""
    return " ".join(w for w in (_norm_word(t) for t in text.split()) if w)


def _qualifies(occurrences: int, words: int, *, min_words: int) -> bool:
    if words < min_words:
        return False
    return occurrences >= MIN_RUN or (occurrences >= 2 and words >= LONG_PHRASE_WORDS)


def collapse_phrase_repeats(text: str) -> tuple[str, int]:
    """Collapse back-to-back repeated phrases inside one segment's text.

    Returns the cleaned text and the number of removed copies. The kept copy
    is the first one, ending in the last copy's final word (so the sentence
    keeps its closing punctuation: "a b, a b, a b." → "a b.").
    """
    tokens = text.split()
    total = 0
    # Repeat to a fixpoint: the widest loop wins a pass, and a loop nested
    # inside its kept copy ("x y x y x y z" ×3) only shows after it.
    while True:
        tokens, removed = _collapse_phrases_once(tokens)
        if not removed:
            break
        total += removed
    if not total:
        return text, 0
    return " ".join(tokens), total


def _collapse_phrases_once(tokens: list[str]) -> tuple[list[str], int]:
    norms = [_norm_word(t) for t in tokens]
    n = len(tokens)
    out: list[str] = []
    removed = 0
    i = 0
    while i < n:
        best: tuple[int, int] | None = None  # (period, copies)
        for period in range(MIN_PHRASE_WORDS, min(MAX_PHRASE_WORDS, (n - i) // 2) + 1):
            # Cheap reject before slicing: a second copy must start here.
            if norms[i + period] != norms[i]:
                continue
            phrase = norms[i : i + period]
            if not any(phrase):
                continue
            copies = 1
            while norms[i + copies * period : i + (copies + 1) * period] == phrase:
                copies += 1
            if not _qualifies(copies, period, min_words=MIN_PHRASE_WORDS):
                continue
            if best is None or copies * period > best[0] * best[1]:
                best = (period, copies)
        if best is None:
            out.append(tokens[i])
            i += 1
            continue
        period, copies = best
        out.extend(tokens[i : i + period - 1])
        out.append(tokens[i + copies * period - 1])
        removed += copies - 1
        i += copies * period
    return out, removed


def _worst(values: Sequence[float | None], pick) -> float | None:
    present = [v for v in values if v is not None]
    return pick(present) if present else None


def _merge_run(run: list[TranscriptSegment]) -> TranscriptSegment:
    if len(run) == 1:
        return run[0]
    first = run[0]
    return dataclasses.replace(
        first,
        end=run[-1].end,
        avg_logprob=_worst([s.avg_logprob for s in run], min),
        no_speech_prob=_worst([s.no_speech_prob for s in run], max),
        compression_ratio=_worst([s.compression_ratio for s in run], max),
        collapsed_repeats=sum(1 + s.collapsed_repeats for s in run) - 1,
    )


def _collapse_segment_runs(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    result: list[TranscriptSegment] = []
    keys = [normalize_text(s.text) for s in segments]
    i = 0
    while i < len(segments):
        key = keys[i]
        j = i + 1
        while (
            j < len(segments)
            and key
            and segments[j].source_part_index == segments[i].source_part_index
            and keys[j] == key
        ):
            j += 1
        run = segments[i:j]
        occurrences = sum(1 + s.collapsed_repeats for s in run)
        if len(run) > 1 and _qualifies(occurrences, len(key.split()), min_words=MIN_PHRASE_WORDS):
            result.append(_merge_run(run))
        else:
            result.extend(run)
        i = j
    return result


def _is_trailing_phrase(text: str) -> bool:
    key = normalize_text(text)
    if not key:
        return False
    if key in _TRAILING_EXACT:
        return True
    return len(key.split()) <= MAX_TRAILING_CONTAINS_WORDS and bool(_TRAILING_CONTAINS.search(key))


def _flags(segment: TranscriptSegment, *, trailing: bool) -> tuple[str, ...]:
    found = {
        "low_confidence": segment.avg_logprob is not None and segment.avg_logprob < LOW_AVG_LOGPROB,
        "no_speech": segment.no_speech_prob is not None
        and segment.no_speech_prob > HIGH_NO_SPEECH_PROB,
        "high_compression": segment.compression_ratio is not None
        and segment.compression_ratio > HIGH_COMPRESSION_RATIO,
        "repeat_collapsed": segment.collapsed_repeats > 0,
        "trailing_phrase": trailing,
    }
    return tuple(name for name in FLAGS if found[name])


def clean_transcript(transcript: Transcript) -> Transcript:
    """Collapse exact repeats and flag suspect segments (see module doc)."""
    segments: list[TranscriptSegment] = []
    for seg in transcript.segments:
        text, removed = collapse_phrase_repeats(seg.text)
        if removed:
            seg = dataclasses.replace(
                seg, text=text, collapsed_repeats=seg.collapsed_repeats + removed
            )
        segments.append(seg)
    segments = _collapse_segment_runs(segments)

    # The trailing run of closing phrases at the end of each video part.
    trailing: set[int] = set()
    for idx in range(len(segments) - 1, -1, -1):
        seg = segments[idx]
        is_part_end = idx == len(segments) - 1 or (
            segments[idx + 1].source_part_index != seg.source_part_index
        )
        if not _is_trailing_phrase(seg.text):
            continue
        if is_part_end or (idx + 1) in trailing:
            trailing.add(idx)

    cleaned = [
        dataclasses.replace(seg, flags=_flags(seg, trailing=idx in trailing))
        for idx, seg in enumerate(segments)
    ]
    return dataclasses.replace(transcript, segments=cleaned)
