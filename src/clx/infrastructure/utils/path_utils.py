import logging
import re
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import field, frozen

from clx.core.utils.text_utils import Text, as_dir_name, sanitize_file_name

if TYPE_CHECKING:
    from clx.core.course import Course

logger = logging.getLogger(__name__)

SLIDES_PREFIX = "slides_"
TOPIC_PREFIX = "topic_"

SKIP_DIRS_FOR_COURSE = frozenset(
    (
        "__pycache__",
        ".git",
        ".ipynb_checkpoints",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".venv",
        ".vs",
        ".vscode",
        ".idea",
        "build",
        "dist",
        ".cargo",
        ".idea",
        ".vscode",
        "target",
        "out",
        "CMakeFiles",
        "bin",
        "obj",
        "localdata"
    )
)

SKIP_DIRS_FOR_OUTPUT = SKIP_DIRS_FOR_COURSE | frozenset({"pu", "drawio"})

SKIP_DIRS_PATTERNS = ["*.egg-info*", "*cmake-build*"]

SKIP_FILE_SUFFIXES = [".keras", ".bkp"]

PLANTUML_EXTENSIONS = frozenset({".pu", ".puml", ".plantuml"})

IMG_FILE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".svg"})

IMG_DATA_FOLDERS = frozenset({"imgdata"})

IMG_SOURCE_FILE_EXTENSIONS = frozenset({".pu", ".drawio", ".psd", ".xfc"})

SUPPORTED_PROG_LANG_EXTENSIONS = frozenset(
    (
        ".c",
        ".cpp",
        ".cs",
        ".java",
        ".md",
        ".py",
        ".rs",
        ".rust",
        ".ts",
    )
)

EXTENSION_TO_PROG_LANG = {
    ".c": "c",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".java": "java",
    ".md": "rust",
    ".py": "python",
    ".rs": "rust",
    ".rust": "rust",
    ".ts": "typescript",
}

PROG_LANG_TO_EXTENSION = {
    "c": ".c",
    "cpp": ".cpp",
    "csharp": ".cs",
    "java": ".java",
    "python": ".py",
    "rust": ".rs",
    "typescript": ".ts",
}

IGNORE_PATH_REGEX = re.compile(r"(.*\.egg-info.*|.*cmake-build-.*|.*\.bkp|.*\.bak)")

def is_image_file(input_path: Path) -> bool:
    is_image_data = IMG_DATA_FOLDERS.intersection(input_path.absolute().parts) != set()
    return input_path.suffix in IMG_FILE_EXTENSIONS and not is_image_data


def is_image_source_file(input_path: Path) -> bool:
    return input_path.suffix in IMG_SOURCE_FILE_EXTENSIONS


def is_slides_file(input_path: Path) -> bool:
    return (
        input_path.name.startswith(SLIDES_PREFIX)
        or input_path.name.startswith(TOPIC_PREFIX)
    ) and input_path.suffix in SUPPORTED_PROG_LANG_EXTENSIONS


def is_ignored_dir_for_course(dir_path: Path) -> bool:
    for part in dir_path.parts:
        if part in SKIP_DIRS_FOR_COURSE:
            return True
        if re.match(IGNORE_PATH_REGEX, part):
            return True
    return False


def is_ignored_dir_for_output(dir_path: Path) -> bool:
    for part in dir_path.parts:
        if part in SKIP_DIRS_FOR_OUTPUT:
            return True
        if re.match(IGNORE_PATH_REGEX, part):
            return True
    return False


def is_ignored_file_for_course(file_path: Path) -> bool:
    return (file_path.is_dir()
            or is_ignored_dir_for_course(file_path.parent)
            or file_path.suffix in SKIP_FILE_SUFFIXES)


def simplify_ordered_name(name: str, prefix: str | None = None) -> str:
    name = name.rsplit(".", maxsplit=1)[0]
    parts = name.split("_")
    if prefix:
        assert parts[0] == prefix
    return "_".join(parts[2:])


class Lang(StrEnum):
    DE = "de"
    EN = "en"


class Format(StrEnum):
    HTML = "html"
    NOTEBOOK = "notebook"
    CODE = "code"


class Kind(StrEnum):
    CODE_ALONG = "code-along"
    COMPLETED = "completed"
    SPEAKER = "speaker"


def ext_for(format_: str | Format, prog_lang: str) -> str:
    match str(format_):
        case "html":
            return ".html"
        case "notebook":
            return ".ipynb"
        case "code":
            return prog_lang_to_extension(prog_lang)
        case _:
            raise ValueError(f"Unknown format: {format_}")


@frozen
class OutputSpec:
    course: "Course"
    language: str = field(converter=str)
    format: str = field(converter=str)
    kind: str = field(converter=str)
    root_dir: Path
    output_dir: Path = field(init=False)

    def __attrs_post_init__(self):
        if self.format == "code":
            format_ = as_dir_name(self.course.prog_lang, self.language)
        else:
            format_ = as_dir_name(self.format, self.language)
        kind = as_dir_name(self.kind, self.language)
        output_path = output_path_for(
            self.root_dir, self.kind == "speaker", self.language, self.course.name
        )

        object.__setattr__(
            self,
            "output_dir",
            output_path / f"{as_dir_name('slides', self.language)}/{format_}/{kind}",
        )

    def __iter__(self):
        return iter((self.language, self.format, self.kind, self.output_dir))


def output_specs(course: "Course", root_dir: Path, skip_html=False) -> OutputSpec:
    format_dirs = [Format.NOTEBOOK] if skip_html else [Format.HTML, Format.NOTEBOOK]
    for lang_dir in [Lang.DE, Lang.EN]:
        for format_dir in format_dirs:
            for kind_dir in [Kind.CODE_ALONG, Kind.COMPLETED]:
                yield OutputSpec(
                    course=course,
                    language=lang_dir,
                    format=format_dir,
                    kind=kind_dir,
                    root_dir=root_dir,
                )
    for lang_dir in [Lang.DE, Lang.EN]:
        yield OutputSpec(
            course=course,
            language=lang_dir,
            format=Format.CODE,
            kind=Kind.COMPLETED,
            root_dir=root_dir,
        )
    for lang_dir in [Lang.DE, Lang.EN]:
        for format_dir in format_dirs:
            for kind_dir in [Kind.SPEAKER]:
                yield OutputSpec(
                    course=course,
                    language=lang_dir,
                    format=format_dir,
                    kind=kind_dir,
                    root_dir=root_dir,
                )


def path_to_prog_lang(path: Path) -> str:
    return extension_to_prog_lang(path.suffix)


def extension_to_prog_lang(ext: str) -> str:
    return EXTENSION_TO_PROG_LANG[ext]


def prog_lang_to_extension(prog_lang: str) -> str:
    return PROG_LANG_TO_EXTENSION[prog_lang]


def output_path_for(root_dir: Path, is_speaker: bool, lang: str, name: Text):
    toplevel_dir = "speaker" if is_speaker else "public"
    return (
        root_dir
        / toplevel_dir
        / as_dir_name(lang, lang)
        / sanitize_file_name(name[lang])
    )


def is_in_dir(member_path: Path, dir_path: Path, check_is_file: bool = True) -> bool:
    if dir_path.resolve() == member_path.resolve():
        return True
    if dir_path.resolve() in member_path.resolve().parents:
        if check_is_file:
            return member_path.is_file()
        return True
    return False
