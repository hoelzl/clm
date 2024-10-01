import io
import logging
import re
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
    "code": Text(de="Python", en="Python"),
    "html": Text(de="Html", en="Html"),
    "notebook": Text(de="Notebooks", en="Notebooks"),
    "code-along": Text(de="Code-Along", en="Code-Along"),
    "completed": Text(de="Completed", en="Completed"),
    "speaker": Text(de="Speaker", en="Speaker"),
    "slides": Text(de="Folien", en="Slides"),
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


def sanitize_key_name(text: str):
    sanitized_text = text.strip().translate(_STREAM_STRING_TRANSLATION_TABLE).lower()
    return sanitized_text


ANSI_ESCAPE_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def unescape(text_or_dict: str | dict) -> str:
    with io.StringIO() as buf:
        pprint(text_or_dict, stream=buf)
        result = ANSI_ESCAPE_REGEX.sub("", buf.getvalue())
        return result
