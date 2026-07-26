"""The bundled course logo as a same-origin asset for the tier-2 preview (#706).

The header macros embed the logo as a ``data:`` URI because *notebooks* need
self-contained HTML — and the build pipeline keeps it exactly that way;
nothing here touches student deliverables. The Studio's tier-2 preview is
different: it is a page on a server, so the logo can be a URL. This module
rewrites the **bundled** logo's ``data:`` URI to
``/api/studio/asset/logo/<prog_lang>`` *before* sanitizing (preserving the
rule that the client injects exactly what was sanitized), which lets
:mod:`clm.web.studio.sanitize` drop ``data`` from its URL schemes entirely —
the confinement rule that was its most complex hand-rolled piece, and the
site of the first bypass the #704 adversarial rounds found, is deleted
rather than hardened.

**Only the bundled logos are rewritten.** A course author's own ``data:``
images in custom macros are refused by the sanitizer like any other
unlisted-scheme content — the preview is documented as "preview, not
parity", and the alternative (a generic content-addressed store serving
decoded request bytes from this origin) was rejected precisely because it
re-introduces a hand-rolled security surface to save an edge case.
"""

from __future__ import annotations

import re
from functools import cache
from importlib import resources
from pathlib import Path

#: prog_lang → (base64 include the macro embeds, packaged image to serve,
#: media type the macro declares). The asset route serves the *source* file;
#: the base64 resource is only needed to recognize the URI in expanded text.
_LOGOS: dict[str, tuple[str, str, str]] = {
    "python": (
        "python-logo-no-text-optimized.base64",
        "python-logo-no-text-optimized.svg",
        "image/svg+xml",
    ),
    "cpp": ("cpp-logo.base64", "cpp-logo-64x64.png", "image/png"),
    "csharp": ("csharp-logo.base64", "c-sharp-c-seeklogo.png", "image/png"),
    "java": ("java-logo.svg.base64", "java-logo.svg", "image/svg+xml"),
    "typescript": ("typescript-logo.svg.base64", "typescript_logo.svg", "image/svg+xml"),
}

#: An ``<img src="data:image/…">`` in expanded text. The bundled ``*.base64``
#: includes are line-wrapped, so the attribute value spans newlines.
_IMG_DATA_SRC = re.compile(r'(<img\b[^>]*?\bsrc=")(data:image/[^"]+)(")', re.DOTALL)


def _templates_dir(prog_lang: str) -> resources.abc.Traversable:
    return resources.files("clm.workers.notebook").joinpath(f"templates_{prog_lang}")


@cache
def _bundled_data_uri(prog_lang: str) -> str | None:
    """The bundled logo's ``data:`` URI, whitespace-normalized, or ``None``.

    Normalization is whitespace-stripping and nothing else: the macro
    inserts the ``*.base64`` file verbatim (line-wrapped, maybe a trailing
    newline), so equality after removing all whitespace is exact recognition
    without parsing base64.
    """
    entry = _LOGOS.get(prog_lang)
    if entry is None:
        return None
    base64_name, _source_name, media_type = entry
    try:
        payload = _templates_dir(prog_lang).joinpath(base64_name).read_text(encoding="ascii")
    except (FileNotFoundError, ModuleNotFoundError):
        return None
    return f"data:{media_type};base64,{''.join(payload.split())}"


@cache
def logo_file(prog_lang: str) -> tuple[Path, str] | None:
    """The packaged logo file for ``prog_lang`` and its media type, or ``None``."""
    entry = _LOGOS.get(prog_lang)
    if entry is None:
        return None
    _base64_name, source_name, media_type = entry
    try:
        source = _templates_dir(prog_lang).joinpath(source_name)
    except ModuleNotFoundError:
        return None
    if not source.is_file():
        return None
    # importlib.resources.files on an installed (non-zipped) package yields
    # real paths; clm is always installed unpacked.
    return Path(str(source)), media_type


def rewrite_bundled_logo(html: str, prog_lang: str) -> str:
    """Replace the bundled logo's ``data:`` URI in ``html`` with its asset URL.

    Runs **before** sanitizing — the client injects the sanitizer's output
    verbatim, so the rewrite must be part of what gets checked. Anything that
    is not byte-recognizable as the bundled logo (after whitespace
    normalization) is left alone, and the sanitizer then refuses it:
    ``data:`` is no longer an allowed scheme at all.
    """
    bundled = _bundled_data_uri(prog_lang)
    if bundled is None:
        return html

    asset_url = f"/api/studio/asset/logo/{prog_lang}"

    def _replace(match: re.Match[str]) -> str:
        candidate = "".join(match.group(2).split())
        if candidate == bundled:
            return f"{match.group(1)}{asset_url}{match.group(3)}"
        return match.group(0)

    return _IMG_DATA_SRC.sub(_replace, html)
