"""Tier-2 (no-execution) cell render — design §3.8.

Expands the Jinja in an ``is_j2`` cell (header macros, ``{{ … }}`` expressions)
server-side using the **same** bundled ``macros.j2`` and line-statement prefix as
the build pipeline, but **without a kernel** — so the phone sees an expanded
header instead of raw ``{{ header_de("…") }}``. Plain (non-j2) cells need no
round-trip; the client renders their markdown directly (tier 1).

This is best-effort preview: any Jinja error (a macro that needs build-only
context, a missing include) is caught and returned as ``ok=False`` with the body
unchanged, so the preview degrades to tier-1 rather than failing. It also runs
with a **lenient** ``Undefined`` (not the build's ``StrictUndefined``) so a
missing course variable renders empty instead of raising — preview, not parity.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

#: Identity globals the build injects from the course; a header macro only needs
#: them to be *defined* for a preview, so placeholders are fine.
_PREVIEW_AUTHOR = "Preview"
_PREVIEW_ORG = ""


def render_j2_cell(deck_path: Path, body: str, lang: str | None) -> tuple[bool, str | None, str]:
    """Expand the Jinja in ``body`` for ``deck_path``. Returns ``(ok, error, text)``.

    ``ok`` True → ``text`` is the expanded body; False → ``text`` is ``body``
    unchanged and ``error`` explains why (the client falls back to tier-1). Never
    raises — a preview must not crash the request.
    """
    try:
        from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader

        from clm.infrastructure.utils.path_utils import path_to_prog_lang
        from clm.workers.notebook.utils.prog_lang_utils import jinja_prefix_for
    except Exception as exc:  # noqa: BLE001 - missing optional dep → tier-1 fallback
        return False, f"render unavailable: {exc}", body

    try:
        prog_lang = path_to_prog_lang(deck_path)
    except (KeyError, ValueError):
        prog_lang = "python"

    try:
        loaders: list = [PackageLoader("clm.workers.notebook", f"templates_{prog_lang}")]
        deck_dir = deck_path.parent
        if deck_dir.exists():
            # Lets the cell `{% include %}` a sibling file shown in a slide.
            loaders.append(FileSystemLoader(str(deck_dir)))
        env = Environment(
            loader=ChoiceLoader(loaders) if len(loaders) > 1 else loaders[0],
            autoescape=False,
            line_statement_prefix=jinja_prefix_for(prog_lang),
            keep_trailing_newline=True,
        )
        template = env.from_string(
            body,
            globals={
                "is_notebook": False,
                "is_html": True,
                "lang": lang or "de",
                "author": _PREVIEW_AUTHOR,
                "organization": _PREVIEW_ORG,
            },
        )
        return True, None, template.render()
    except Exception as exc:  # noqa: BLE001 - preview must never crash the request
        logger.debug("Studio tier-2 render failed for %s: %s", deck_path, exc)
        return False, str(exc), body
