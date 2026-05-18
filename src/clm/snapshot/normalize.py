"""Content normalization for snapshot comparison.

Only HTML normalization is implemented today. Slide code that uses live
kernel execution (e.g. ``print(obj)`` of a default-``__repr__`` object)
emits hex memory addresses that differ across runs due to ASLR; the
normalizer rewrites those to a fixed sentinel so byte comparison works.
"""

from __future__ import annotations

import re

# Matches hex memory addresses as they appear in default Python __repr__
# output: ``<__main__.Foo at 0x2733c2b8ad0>``, ``0x000002733C2BA120``, etc.
# 4+ hex digits to avoid false-matching short literals like ``0xff``.
_HEX_ADDR = re.compile(rb"0[xX][0-9a-fA-F]{4,}")
_HEX_ADDR_PLACEHOLDER = b"0xADDR"


def normalize_html(content: bytes) -> bytes:
    """Return *content* with hex memory addresses replaced by a sentinel."""
    return _HEX_ADDR.sub(_HEX_ADDR_PLACEHOLDER, content)


def normalize_for_compare(rel_path: str, content: bytes, *, include_html: bool) -> bytes:
    """Apply class-aware normalization, or return content unchanged.

    Used by the verifier to make a "harmless-diff-tolerant" byte
    comparison. The ``rel_path`` ending is the only routing input.
    """
    if include_html and rel_path.endswith(".html"):
        return normalize_html(content)
    return content
