import logging
import re

from clm.core.utils.text_utils import Text

logger = logging.getLogger(__name__)

TITLE_REGEX = re.compile(r"{{\s*header\s*\(\s*[\"'](.*)[\"']\s*,\s*[\"'](.*)[\"']\s*\)\s*}}")
HEADER_DE_TITLE_REGEX = re.compile(r"{{\s*header_de\s*\(\s*[\"'](.*)[\"']\s*\)\s*}}")
HEADER_EN_TITLE_REGEX = re.compile(r"{{\s*header_en\s*\(\s*[\"'](.*)[\"']\s*\)\s*}}")


def find_notebook_titles(text: str, default: str | None) -> Text:
    """Find the titles from the source text of a notebook.

    Returns the raw title text without sanitization. File name sanitization
    is handled separately in NotebookFile.file_name() when generating output paths.

    Phase 6 recognises the sibling macros ``header_de("...")`` /
    ``header_en("...")`` produced by ``clm slides split`` so that a
    ``.de.py`` file's DE-side title (used in the output filename) matches
    the title the bilingual companion would have produced. Split files
    only carry one language, so the matching language's title fills both
    ``Text`` slots — the other slot is gated out by
    ``NotebookFile.output_language_filter`` at routing time anyway.
    """
    match = TITLE_REGEX.search(text)
    if match:
        return Text(de=match[1], en=match[2])
    de_match = HEADER_DE_TITLE_REGEX.search(text)
    en_match = HEADER_EN_TITLE_REGEX.search(text)
    if de_match is not None or en_match is not None:
        de_title = de_match[1] if de_match else (en_match[1] if en_match else "")
        en_title = en_match[1] if en_match else (de_match[1] if de_match else "")
        return Text(de=de_title, en=en_title)
    if default:
        return Text(de=default, en=default)
    raise ValueError("No title found.")


IMG_REGEX = re.compile(r'<img\s+src="([^"]+)"')


def find_images(text: str) -> frozenset[str]:
    return frozenset(IMG_REGEX.findall(text))


IMPORT_REGEX = re.compile(r"^\s*from\s+([^\s\"']+)\s+import|^\s*import\s+([^\s\"']+)")


def find_imports(text: str) -> frozenset[str]:
    matches = []
    for line in text.splitlines():
        match = IMPORT_REGEX.match(line)
        if match:
            matches.append(match[1] or match[2])
    return frozenset(match for match in matches)
