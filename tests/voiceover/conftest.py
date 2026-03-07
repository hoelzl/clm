"""Skip voiceover tests when optional dependencies are not installed."""

import sys

import pytest

collect_ignore_glob = []

# Check if voiceover dependencies are available
try:
    import cv2  # noqa: F401
    import numpy  # noqa: F401
except ImportError:
    # Skip all voiceover tests that depend on cv2/numpy
    collect_ignore_glob.extend(["test_keyframes.py", "test_matcher.py", "test_aligner.py"])
