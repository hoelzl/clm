import logging
import re

from clx.utils.text_utils import Text, sanitize_file_name

logger = logging.getLogger(__name__)

TITLE_REGEX = re.compile(
    r"{{\s*header\s*\(\s*[\"'](.*)[\"']\s*,\s*[\"'](.*)[\"']\s*\)\s*}}"
)


def find_notebook_titles(text: str, default: str | None) -> Text:
    """Find the titles from the source text of a notebook."""
    match = TITLE_REGEX.search(text)
    if match:
        return Text(de=sanitize_file_name(match[1]), en=sanitize_file_name(match[2]))
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
