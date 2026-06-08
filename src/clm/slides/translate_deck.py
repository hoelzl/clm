"""Full-deck translation engine: synthesize the other-language split half.

Phase 1 of the ``clm slides translate`` feature (Issue #232). Pure, offline and
Protocol-driven: given the *text* of a single-language split half (e.g. a
``slides_x.de.py``), :func:`translate_deck_text` produces the text of the other
half (``slides_x.en.py``) by

* translating every **localized** cell — one carrying a ``lang=`` attribute —
  via the :class:`~clm.slides.sync_translate.SlideTranslator` protocol (code
  cells through the code prompt, which keeps identifiers byte-identical;
  markdown through the prose prompt);
* copying every **language-neutral / shared** cell — no ``lang`` attribute,
  including idiomatic code — *verbatim*; and
* rewriting the j2 title macro structurally (``header_de`` ↔ ``header_en`` plus
  its ``import`` directive) while translating only the title string.

The translate-vs-copy decision keys on ``metadata.lang`` — **not** on
:func:`~clm.slides.sync_writeback.role_of`, which returns ``None`` for a
localized but id-less code cell even though that cell must still be translated.
``role_of`` / the cell type only select which translation prompt is used.

Before returning, the generated half is validated against the canonical
split/unify round-trip (``split(unify(de, en)) == (de, en)``) — the same
invariant :mod:`clm.slides.split` rests on — so a malformed twin can never
reach disk. The engine is all-or-nothing: it returns a complete target text or
raises :class:`TranslateDeckError`; it never half-writes.

This module touches neither the network nor the filesystem. File resolution,
provider/key wiring, id minting, the watermark seal, the voiceover companion
and result caching are later phases.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from clm.notebooks.slide_parser import CellMetadata
from clm.slides import split
from clm.slides.raw_cells import RawCell, reconstruct, split_cells
from clm.slides.sync_translate import SlideTranslator, TranslationError
from clm.slides.sync_writeback import CODE_ROLE, build_twin_cell, role_of

logger = logging.getLogger(__name__)

__all__ = [
    "CellTranslation",
    "TranslateDeckError",
    "TranslateDeckResult",
    "translate_deck_text",
]

# Only de/en are modelled: the title macro (``header_de`` / ``header_en``) and
# the whole split/unify machinery are de/en specific.
_SUPPORTED_LANGS = ("de", "en")

# The canonical per-language header grammar lives in :mod:`clm.slides.split`;
# reuse it rather than duplicating the regexes (divergent copies are the
# documented landmine — see the feature handover §5).
_HEADER_MACRO_RE = {"de": split._HEADER_DE_RE, "en": split._HEADER_EN_RE}
_HEADER_IMPORT_RE = {"de": split._HEADER_DE_IMPORT_RE, "en": split._HEADER_EN_IMPORT_RE}


CellKind = Literal["translated", "copied", "header", "import"]


class TranslateDeckError(Exception):
    """Raised when the other-language half cannot be produced.

    Covers an unsupported language, a translation failure on a specific cell,
    and a generated pair that fails the split/unify round-trip — i.e. anything
    that would otherwise put a malformed or incomplete twin on disk.
    """


@dataclass(frozen=True)
class CellTranslation:
    """How one source cell was mapped into the target half (for reporting)."""

    index: int  # position of the cell in the source half (0-based)
    kind: CellKind
    role: str | None  # the translation role for ``translated`` cells, else None
    slide_id: str | None
    lang: str | None


@dataclass
class TranslateDeckResult:
    """Outcome of one :func:`translate_deck_text` call."""

    target_text: str
    cells: list[CellTranslation] = field(default_factory=list)

    @property
    def translated_count(self) -> int:
        """Localized body cells whose content was sent through the translator."""
        return sum(1 for c in self.cells if c.kind == "translated")

    @property
    def copied_count(self) -> int:
        """Shared / language-neutral cells copied verbatim."""
        return sum(1 for c in self.cells if c.kind == "copied")

    @property
    def header_translated(self) -> bool:
        """Whether a title header macro was rewritten (and its title translated)."""
        return any(c.kind == "header" for c in self.cells)


def translate_deck_text(
    source_text: str,
    *,
    source_lang: str,
    target_lang: str,
    translator: SlideTranslator,
    comment_token: str = "#",
) -> TranslateDeckResult:
    """Translate a single-language split half into its other-language twin.

    ``source_text`` is the verbatim content of a ``*.<source_lang><ext>`` split
    half. Returns the text of the matching ``*.<target_lang><ext>`` half plus a
    per-cell report. ``comment_token`` is the source language's line-comment
    token (``"#"`` python/rust, ``"//"`` cpp/csharp/java/typescript). Raises
    :class:`TranslateDeckError` if a cell cannot be translated or the generated
    pair does not round-trip.
    """
    if source_lang not in _SUPPORTED_LANGS or target_lang not in _SUPPORTED_LANGS:
        raise TranslateDeckError(
            f"unsupported language pair {source_lang!r}->{target_lang!r}; "
            f"supported languages are {_SUPPORTED_LANGS}"
        )
    if source_lang == target_lang:
        raise TranslateDeckError(f"source and target language are both {source_lang!r}")

    preamble, source_cells = split_cells(source_text, comment_token)

    target_cells: list[RawCell] = []
    report: list[CellTranslation] = []

    for index, cell in enumerate(source_cells):
        meta = cell.metadata

        # 1. The title macro: rewrite header_<src> -> header_<tgt>, translating
        #    only the title argument. Structural — never run through the cell
        #    body translator.
        if meta.is_j2 and _HEADER_MACRO_RE[source_lang].search(cell.header):
            target_cells.append(_rewrite_header_macro(cell, source_lang, target_lang, translator))
            report.append(CellTranslation(index, "header", None, meta.slide_id, meta.lang))
            continue

        # 2. The header import directive: from ... import header_<src> -> header_<tgt>.
        if meta.is_j2 and _HEADER_IMPORT_RE[source_lang].match(cell.header):
            target_cells.append(_rewrite_header_import(cell, target_lang))
            report.append(CellTranslation(index, "import", None, meta.slide_id, meta.lang))
            continue

        # 3. A localized cell (carries lang=) — translate its body and swap the
        #    language. Gate on lang, NOT role_of: a localized id-less code cell
        #    has role_of() == None but must still be translated.
        if meta.lang == source_lang:
            role = _translation_role(meta)
            target_cells.append(
                _translate_localized_cell(cell, source_lang, target_lang, role, translator)
            )
            report.append(CellTranslation(index, "translated", role, meta.slide_id, meta.lang))
            continue

        # 4. Everything else — language-neutral / shared cells (no lang),
        #    non-header j2 directives, and defensively any stray foreign-language
        #    cell — is copied verbatim. Shared cells MUST stay byte-identical
        #    across the two halves or unify/validation breaks.
        target_cells.append(_clone_cell(cell))
        report.append(CellTranslation(index, "copied", None, meta.slide_id, meta.lang))

    target_text = reconstruct(preamble, target_cells)
    _assert_round_trips(source_text, target_text, source_lang, comment_token)
    return TranslateDeckResult(target_text=target_text, cells=report)


def _translation_role(meta: CellMetadata) -> str:
    """Pick the translator ``role`` for a localized cell.

    ``CODE_ROLE`` for any code cell (selects the identifier-preserving code
    prompt), otherwise the cell's narrative role (``slide`` / ``notes`` / …) or
    ``"markdown"`` — mirroring how :mod:`clm.slides.sync_apply` calls the
    translator. Only ``"code"`` changes the prompt; the rest is cosmetic prompt
    text.
    """
    if meta.cell_type == "code":
        return CODE_ROLE
    return role_of(meta) or "markdown"


def _translate_localized_cell(
    cell: RawCell,
    source_lang: str,
    target_lang: str,
    role: str,
    translator: SlideTranslator,
) -> RawCell:
    """Build the target-language twin of a localized ``cell``.

    Translates the body, swaps the language attribute, and re-appends the
    source cell's trailing blank lines so the target half keeps the same
    inter-cell spacing.
    """
    source_body = cell.body.rstrip("\n")
    try:
        translated = translator.translate(
            source_body=source_body,
            source_lang=source_lang,
            target_lang=target_lang,
            role=role,
        )
    except TranslationError as exc:
        sid = cell.metadata.slide_id or "<no id>"
        raise TranslateDeckError(
            f"could not translate cell {sid!r} (line {cell.line_number}): {exc}"
        ) from exc

    twin = build_twin_cell(cell, target_lang, translated)
    blanks = _trailing_blanks(cell)
    if blanks:
        twin.lines = [*twin.lines, *([""] * blanks)]
    return twin


def _rewrite_header_macro(
    cell: RawCell,
    source_lang: str,
    target_lang: str,
    translator: SlideTranslator,
) -> RawCell:
    """Rewrite a ``header_<src>("Title")`` macro to ``header_<tgt>("…")``.

    The title is natural language, so it is translated; everything else on the
    header line (the ``# `` prefix, any surrounding text) is preserved by
    substituting only the matched macro span.
    """
    header_line = cell.header
    match = _HEADER_MACRO_RE[source_lang].search(header_line)
    assert match is not None  # caller guarantees a match
    title = match.group(2)

    translated_title = title
    if title.strip():
        try:
            # role="title": a dedicated bare-phrase prompt. Using "markdown" here
            # makes the model add a stray "# " and skip translation (the title is
            # not a percent-format cell body). See sync_translate._TITLE_SYSTEM_PROMPT.
            translated_title = translator.translate(
                source_body=title,
                source_lang=source_lang,
                target_lang=target_lang,
                role="title",
            ).strip()
        except TranslationError as exc:
            raise TranslateDeckError(
                f"could not translate the deck title {title!r} (line {cell.line_number}): {exc}"
            ) from exc

    target_macro = f"header_{target_lang}"
    new_header = _HEADER_MACRO_RE[source_lang].sub(
        lambda _m: f'{{{{ {target_macro}("{translated_title}") }}}}',
        header_line,
    )
    return RawCell(
        lines=[new_header, *cell.lines[1:]],
        line_number=cell.line_number,
        metadata=cell.metadata,
    )


def _rewrite_header_import(cell: RawCell, target_lang: str) -> RawCell:
    """Rewrite ``from … import header_<src>`` to ``… import header_<tgt>``."""
    source_lang = "de" if target_lang == "en" else "en"
    target_macro = f"header_{target_lang}"
    new_header = _HEADER_IMPORT_RE[source_lang].sub(
        lambda m: f"{m.group(1)}{target_macro}",
        cell.header,
    )
    return RawCell(
        lines=[new_header, *cell.lines[1:]],
        line_number=cell.line_number,
        metadata=cell.metadata,
    )


def _clone_cell(cell: RawCell) -> RawCell:
    """A verbatim copy of ``cell`` (independent ``lines`` list)."""
    return RawCell(
        lines=list(cell.lines),
        line_number=cell.line_number,
        metadata=cell.metadata,
    )


def _trailing_blanks(cell: RawCell) -> int:
    """Count the blank body lines at the end of ``cell`` (separator padding)."""
    n = 0
    for line in reversed(cell.lines[1:]):
        if line == "":
            n += 1
        else:
            break
    return n


def _assert_round_trips(
    source_text: str, target_text: str, source_lang: str, comment_token: str = "#"
) -> None:
    """Guard that the generated (source, target) pair is a valid split twin.

    Re-unifies the two halves and splits them back; the result must reproduce
    both inputs byte-for-byte. This is the canonical split/unify invariant the
    rest of the slide tooling depends on, so passing it means the generated half
    pairs cleanly, keeps shared cells byte-identical, and carries matching
    slide_ids in order. Any failure raises :class:`TranslateDeckError` so a
    malformed half is never written.

    ``unify_texts`` takes ``(de, en)`` positionally, so order the two halves by
    language rather than by source/target (else an ``en->de`` run would feed the
    halves in the wrong slots).
    """
    if source_lang == "de":
        de_text, en_text = source_text, target_text
    else:
        de_text, en_text = target_text, source_text
    try:
        unified = split.unify_texts(de_text, en_text, comment_token)
        de_back, en_back = split.split_text(unified, comment_token)
    except (split.UnifyError, split.SplitError) as exc:
        raise TranslateDeckError(
            f"generated translation does not form a valid split pair: {exc}"
        ) from exc
    if (de_back, en_back) != (de_text, en_text):
        raise TranslateDeckError(
            "generated translation failed the split/unify round-trip; the source "
            "deck may use non-canonical formatting — try `clm slides normalize` first"
        )
