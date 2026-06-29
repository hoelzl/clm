"""Lightweight DE/EN content-language detection for sync diagnostics (no ML dep).

The ``clm slides sync diagnose`` classifier needs to tell a cell's *content
language* from its declared ``lang=`` tag, to separate a **mis-tag** (a German
paragraph routed into the EN half) from a genuine **content gap**. No
language-detection dependency exists in CLM, and the ``[ml]`` extra is blocked, so
this is a deliberately tiny heuristic, not a dependency: a German signal
(umlaut/eszett presence + German stop-word hits) versus an English signal (English
stop-word hits).

It **abstains** (``label == "unknown"``) on short / code-heavy / stop-word-free
text — exactly the title-only cells where any statistical detector is least
reliable — so the classifier never asserts a mis-tag on a guess (it treats
``unknown`` as "cannot assert mis-tag" and downgrades to advisory).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# High-frequency English function words (a superset of slug._STOP_WORDS plus a few
# discriminators). These appear densely in English prose and rarely in German.
_EN_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "and",
        "or",
        "but",
        "with",
        "by",
        "as",
        "this",
        "that",
        "these",
        "those",
        "it",
        "we",
        "you",
        "they",
        "he",
        "she",
        "not",
        "do",
        "does",
        "can",
        "will",
        "which",
        "from",
        "have",
        "has",
        "there",
        "their",
        "our",
        "its",
        "if",
        "then",
        "than",
        "when",
        "while",
        "into",
        "about",
    }
)
# High-frequency German function words — the DE counterparts. Short, common, and
# essentially absent from English prose.
_DE_WORDS = frozenset(
    {
        "der",
        "die",
        "das",
        "und",
        "ist",
        "sind",
        "war",
        "waren",
        "nicht",
        "mit",
        "für",
        "auf",
        "ein",
        "eine",
        "einen",
        "einem",
        "einer",
        "zu",
        "von",
        "im",
        "den",
        "dem",
        "des",
        "sich",
        "wir",
        "sie",
        "ihr",
        "auch",
        "oder",
        "aber",
        "wird",
        "werden",
        "kann",
        "durch",
        "bei",
        "als",
        "dass",
        "wenn",
        "man",
        "es",
        "noch",
        "nur",
        "hier",
        "diese",
        "dieser",
        "dieses",
        "so",
        "wie",
        "um",
    }
)
_UMLAUT_RE = re.compile(r"[äöüÄÖÜß]")
_WORD_RE = re.compile(r"[a-zA-ZäöüÄÖÜß]+")

# An umlaut/eszett is a near-certain German marker (English prose essentially never
# carries one), so it is weighted like several stop-word hits.
_UMLAUT_WEIGHT = 3.0
# Below this confidence the guess is reported as ``unknown`` — the classifier must
# not assert a mis-tag on weak evidence.
_MIN_CONFIDENCE = 0.34
# Below this many prose words there is too little signal to judge (title-only/code).
_MIN_WORDS = 5


@dataclass(frozen=True)
class LangGuess:
    """A content-language guess. ``label`` is ``"de"`` / ``"en"`` / ``"unknown"``."""

    label: str
    confidence: float  # 0.0–1.0; 0.0 when abstaining

    @property
    def confident(self) -> bool:
        """Whether the guess is strong enough to act on (assert a mis-tag)."""
        return self.label != "unknown" and self.confidence >= _MIN_CONFIDENCE


def detect(text: str) -> LangGuess:
    """Guess whether ``text`` is German or English prose, or abstain.

    Returns ``LangGuess("unknown", 0.0)`` when there is too little prose, or the DE
    and EN signals are too close to call — the safe default for the classifier.
    """
    words = [w.lower() for w in _WORD_RE.findall(text)]
    has_umlaut = bool(_UMLAUT_RE.search(text))
    if len(words) < _MIN_WORDS:
        # Too little prose to judge. An umlaut is still a strong DE marker even in a
        # short string, but report modest confidence so a title cell rarely drives a
        # mis-tag assertion on its own.
        return LangGuess("de", 0.6) if has_umlaut else LangGuess("unknown", 0.0)

    de_hits = sum(1 for w in words if w in _DE_WORDS)
    en_hits = sum(1 for w in words if w in _EN_WORDS)
    de_signal = de_hits + (_UMLAUT_WEIGHT if has_umlaut else 0.0)
    en_signal = float(en_hits)
    total = de_signal + en_signal
    if total == 0:
        return LangGuess("unknown", 0.0)

    label = "de" if de_signal > en_signal else "en"
    margin = abs(de_signal - en_signal) / total
    # Scale the margin down when the absolute evidence is thin (few stop-word hits),
    # so a single stray hit in otherwise-neutral text does not read as confident.
    evidence = min(total, 5.0) / 5.0
    confidence = round(margin * evidence, 3)
    if confidence < _MIN_CONFIDENCE:
        return LangGuess("unknown", confidence)
    return LangGuess(label, confidence)
