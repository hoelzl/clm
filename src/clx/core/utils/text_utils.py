import io
import logging
import re
from pathlib import Path
from pprint import pprint

from attr import define

logger = logging.getLogger(__name__)


@define
class Text:
    de: str
    en: str

    def __getitem__(self, item):
        return getattr(self, item)

    @classmethod
    def from_string(cls, text):
        return cls(de=text, en=text)


TEXT_MAPPINGS = {
    "de": Text(de="De", en="De"),
    "en": Text(de="En", en="En"),
    "html": Text(de="Html", en="Html"),
    "notebook": Text(de="Notebooks", en="Notebooks"),
    "code-along": Text(de="Code-Along", en="Code-Along"),
    "completed": Text(de="Completed", en="Completed"),
    "speaker": Text(de="Speaker", en="Speaker"),
    "slides": Text(de="Folien", en="Slides"),
    "python": Text(de="Python", en="Python"),
    "csharp": Text(de="CSharp", en="CSharp"),
    "java": Text(de="Java", en="Java"),
    "cpp": Text(de="Cpp", en="Cpp"),
    "typescript": Text(de="TypeScript", en="TypeScript"),
}


def as_dir_name(name, lang):
    return TEXT_MAPPINGS[name][lang]


_PARENS_TO_REPLACE = "{}[]"
_REPLACEMENT_PARENS = "()" * (len(_PARENS_TO_REPLACE) // 2)
_CHARS_TO_REPLACE = r"/\$#%&<>*=^€|"
_REPLACEMENT_CHARS = "_" * len(_CHARS_TO_REPLACE)
_CHARS_TO_DELETE = r""";!?"'`.:"""
_FILE_STRING_TRANSLATION_TABLE = str.maketrans(
    _PARENS_TO_REPLACE + _CHARS_TO_REPLACE,
    _REPLACEMENT_PARENS + _REPLACEMENT_CHARS,
    _CHARS_TO_DELETE,
)
_STREAM_REPLACEMENT_CHARS = ";!?'`: -\t\n\"" + _PARENS_TO_REPLACE + _CHARS_TO_REPLACE
_STREAM_STRING_TRANSLATION_TABLE = str.maketrans(
    _STREAM_REPLACEMENT_CHARS, "_" * len(_STREAM_REPLACEMENT_CHARS)
)


def sanitize_file_name(text: str):
    text = text.replace("C#", "CSharp")
    sanitized_text = text.strip().translate(_FILE_STRING_TRANSLATION_TABLE)
    return sanitized_text


def sanitize_path(path) -> Path:
    """Sanitize all components of a path (directories and filename).

    This function sanitizes each component (directory and filename) of a path
    to ensure it only contains filesystem-safe characters. File extensions
    are preserved to maintain file type information.

    Args:
        path: Path object or string to sanitize

    Returns:
        New Path with all components sanitized, preserving the file extension

    Example:
        >>> from pathlib import Path
        >>> sanitize_path(Path("foo/bar: test/file?.txt"))
        Path("foo/bar test/file.txt")
        >>> sanitize_path(Path("section{1}/diagram!.png"))
        Path("section(1)/diagram.png")
    """
    from pathlib import Path

    path_obj = Path(path)

    # Handle empty or current directory
    if not path_obj.parts or path_obj == Path("."):
        return Path(".")

    # Separate the file extension (suffix) from the last component
    parts = list(path_obj.parts)
    last_part = parts[-1]

    # Check if the last part has an extension
    # Use splitext-like logic: find the last dot
    if "." in last_part and not last_part.startswith("."):
        # Find the last dot to separate stem and suffix
        last_dot_index = last_part.rfind(".")
        stem = last_part[:last_dot_index]
        suffix = last_part[last_dot_index:]  # includes the dot

        # Sanitize the stem
        sanitized_stem = sanitize_file_name(stem)
        sanitized_last = sanitized_stem + suffix
    else:
        # No extension or hidden file - just sanitize normally
        sanitized_last = sanitize_file_name(last_part)

    # Sanitize all directory components
    if path_obj.is_absolute():
        # Preserve root for absolute paths
        root = parts[0]
        sanitized_dirs = [sanitize_file_name(part) for part in parts[1:-1]]
        sanitized_parts = [root] + sanitized_dirs + [sanitized_last]
    else:
        # Relative path - sanitize all directory components
        sanitized_dirs = [sanitize_file_name(part) for part in parts[:-1]]
        sanitized_parts = sanitized_dirs + [sanitized_last]

    return Path(*sanitized_parts)


def sanitize_key_name(text: str):
    sanitized_text = text.strip().translate(_STREAM_STRING_TRANSLATION_TABLE).lower()
    return sanitized_text


ANSI_ESCAPE_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def unescape(text_or_dict: str | dict) -> str:
    with io.StringIO() as buf:
        pprint(text_or_dict, stream=buf)
        result = ANSI_ESCAPE_REGEX.sub("", buf.getvalue())
        return result
