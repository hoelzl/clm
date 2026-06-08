"""Translation-conventions (glossary) discovery — shared by translate & sync.

A course pins target-language conventions — a short style note plus a term
glossary — in a Markdown file named ``clm-glossary.<target-lang>.md`` kept near
its slides. The text is appended verbatim to the new-slide translation prompt so
register (formal "Sie" vs. "du") and term handling (keep "Dictionary", translate
"Schleife") stay consistent across a deck. clm stays domain-agnostic: it only
*locates and reads* the file — the course repo supplies the content (see
``clm slides translate --glossary`` and ``clm slides sync --glossary-de/-en``).

Discovery mirrors the ``.env`` auto-load: walk up from the deck's directory and
take the first match. ``clm slides translate`` resolves **one** target language
(it has a single direction); ``clm slides sync`` is **bidirectional** — a new DE
slide flows to EN and a new EN slide flows to DE in the same pass — so it
resolves a glossary per target language with :func:`resolve_guidance_by_lang`.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "GLOSSARY_STEM",
    "discover_glossary",
    "glossary_name",
    "read_guidance",
    "resolve_guidance",
    "resolve_guidance_by_lang",
]

# Auto-discovered glossary filename, parameterized by target language, e.g.
# ``clm-glossary.de.md``. The first such file found walking up from the deck's
# directory supplies translation conventions for that target language.
GLOSSARY_STEM = "clm-glossary"


def glossary_name(target_lang: str) -> str:
    """The conventions filename for translations INTO ``target_lang``."""
    return f"{GLOSSARY_STEM}.{target_lang}.md"


def discover_glossary(start: Path, target_lang: str) -> Path | None:
    """First ``clm-glossary.<target_lang>.md`` found walking up from ``start``.

    ``start`` may be a deck file's directory or a directory itself; both it and
    each of its parents are checked, nearest first. Mirrors the ``.env``
    auto-load: the course repo keeps the (domain-specific, human-edited) glossary
    near its slides and clm finds it without a flag.
    """
    name = glossary_name(target_lang)
    for directory in [start, *start.parents]:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def read_guidance(path: Path) -> str:
    """Read a glossary file's text, trimmed of surrounding whitespace."""
    return path.read_text(encoding="utf-8").strip()


def resolve_guidance(
    glossary: Path | None, source_dir: Path, target_lang: str
) -> tuple[str, Path | None]:
    """Resolve conventions for a **single** target language (translate/bootstrap).

    Returns ``(guidance_text, path)``: an explicit ``--glossary`` path wins, else
    the auto-discovered ``clm-glossary.<target_lang>.md``. ``("", None)`` when no
    file is found **or** the found file is empty/whitespace-only — an empty glossary
    contributes nothing, so it is reported as absent (no path), matching
    :func:`resolve_guidance_by_lang` and so the caller emits no misleading
    "using glossary" message for a file that adds no conventions.
    """
    path = glossary if glossary is not None else discover_glossary(source_dir, target_lang)
    if path is None:
        return "", None
    text = read_guidance(path)
    if not text:
        return "", None  # an empty glossary file is treated as no glossary
    return text, path


def resolve_guidance_by_lang(
    source_dir: Path,
    *,
    explicit: dict[str, Path | None],
) -> tuple[dict[str, str], dict[str, Path]]:
    """Resolve conventions per target language for a **bidirectional** sync.

    ``explicit`` maps each target language (``"de"`` / ``"en"``) to an explicit
    ``--glossary-<lang>`` path, or ``None`` to auto-discover that language's
    ``clm-glossary.<lang>.md`` walking up from ``source_dir``. Returns
    ``(guidance_by_lang, used_paths)``: the non-empty conventions text keyed by
    target language, and the file that supplied each (for an informational
    message). A language with no explicit path and no discovered (or empty) file
    is simply absent from both maps — its direction translates with no glossary.
    """
    guidance: dict[str, str] = {}
    used: dict[str, Path] = {}
    for lang, path in explicit.items():
        resolved = path if path is not None else discover_glossary(source_dir, lang)
        if resolved is None:
            continue
        text = read_guidance(resolved)
        if not text:
            continue  # an empty glossary file is treated as no glossary
        guidance[lang] = text
        used[lang] = resolved
    return guidance, used
