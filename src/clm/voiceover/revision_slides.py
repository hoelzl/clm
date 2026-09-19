"""Companion-aware, model-free inputs for revision-history tasks."""

from pathlib import Path

from clm.core.slide_text.slide_parser import SlideGroup, group_slides, parse_cells
from clm.core.slide_text.voiceover_merge import merge_voiceover_text
from clm.core.utils.prog_lang_utils import comment_token_for_path
from clm.core.voiceover_companions import resolve_companion


def revision_slides(path: Path, lang: str) -> list[SlideGroup]:
    """Read deck + selected companion in memory; refuse unplaceable narration."""
    text = path.read_text(encoding="utf-8")
    token = comment_token_for_path(path)
    companion = resolve_companion(path)
    if companion is not None:
        text, unmatched = merge_voiceover_text(text, companion.read_text(encoding="utf-8"), token)
        if unmatched:
            raise ValueError(
                f"unmatched companion narration in {companion}: {', '.join(unmatched)}"
            )
    return group_slides(parse_cells(text, token), lang, include_header=True)
