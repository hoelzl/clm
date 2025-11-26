import logging
import re
import sys
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

# StrEnum is available in Python 3.11+
if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    # Fallback for Python 3.10
    class StrEnum(str, Enum):
        pass


from attrs import field, frozen

from clx.core.utils.text_utils import Text, as_dir_name, sanitize_file_name

if TYPE_CHECKING:
    from clx.core.course import Course
    from clx.core.output_target import OutputTarget

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
        "localdata",
        "chroma_db",
        "chroma_langchain_db",
        "chroma_rag_db",
    )
)

SKIP_DIRS_FOR_OUTPUT = SKIP_DIRS_FOR_COURSE | frozenset({"pu", "drawio"})

SKIP_DIRS_PATTERNS = ["*.egg-info*", "*cmake-build*"]

SKIP_FILE_SUFFIXES = [".keras", ".bkp", ".bin"]

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

IGNORE_PATH_REGEX = re.compile(r"(.*\.egg-info.*|.*cmake-build-.*|.*\.bkp|.*\.bak|chroma_.*db.*)")


def is_image_file(input_path: Path) -> bool:
    is_image_data = IMG_DATA_FOLDERS.intersection(input_path.absolute().parts) != set()
    return input_path.suffix in IMG_FILE_EXTENSIONS and not is_image_data


def is_image_source_file(input_path: Path) -> bool:
    return input_path.suffix in IMG_SOURCE_FILE_EXTENSIONS


def is_slides_file(input_path: Path) -> bool:
    return (
        input_path.name.startswith(SLIDES_PREFIX) or input_path.name.startswith(TOPIC_PREFIX)
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
    return (
        file_path.is_dir()
        or is_ignored_dir_for_course(file_path.parent)
        or file_path.suffix in SKIP_FILE_SUFFIXES
    )


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


def output_specs(
    course: "Course",
    root_dir: Path,
    skip_html: bool = False,
    languages: list[str] | None = None,
    kinds: list[str] | None = None,
    target: "OutputTarget | None" = None,
) -> Iterator["OutputSpec"]:
    """Generate output specifications for course processing.

    When a target is provided, its filters take precedence over languages/kinds.
    Note: Code format is only generated for completed kind (not code-along/speaker).

    Args:
        course: Course object
        root_dir: Root directory for output
        skip_html: If True, skip HTML format generation
        languages: List of languages to generate (default: ["de", "en"])
        kinds: List of output kinds to generate (default: all kinds)
            Valid values: "code-along", "completed", "speaker"
        target: OutputTarget for filtering (if provided, overrides languages/kinds)

    Yields:
        OutputSpec objects for each language/format/kind combination
    """
    # Determine effective filters based on target or explicit parameters
    if target is not None:
        # Use target's filters
        effective_languages = list(target.languages)
        effective_kinds = list(target.kinds)
        effective_formats = list(target.formats)
    else:
        # Use explicit parameters or defaults
        effective_languages = languages if languages else ["de", "en"]
        effective_kinds = kinds if kinds else ["code-along", "completed", "speaker"]
        effective_formats = ["html", "notebook", "code"]

    # Build language list
    lang_dirs: list[Lang] = [Lang(lang) for lang in effective_languages if lang in ("de", "en")]

    # Build format list
    format_dirs: list[Format] = []
    if "html" in effective_formats and not skip_html:
        format_dirs.append(Format.HTML)
    if "notebook" in effective_formats:
        format_dirs.append(Format.NOTEBOOK)
    if "code" in effective_formats:
        format_dirs.append(Format.CODE)

    # Build kind list
    kind_dirs: list[Kind] = []
    if "code-along" in effective_kinds:
        kind_dirs.append(Kind.CODE_ALONG)
    if "completed" in effective_kinds:
        kind_dirs.append(Kind.COMPLETED)
    if "speaker" in effective_kinds:
        kind_dirs.append(Kind.SPEAKER)

    # Generate all format/kind combinations
    # Note: Code format only makes sense for completed kind
    for lang_dir in lang_dirs:
        for format_dir in format_dirs:
            for kind_dir in kind_dirs:
                # Code format only makes sense for completed kind
                if format_dir == Format.CODE and kind_dir != Kind.COMPLETED:
                    continue
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


def output_path_for(root_dir: Path, is_speaker: bool, lang: str, name: Text) -> Path:
    toplevel_dir = "speaker" if is_speaker else "public"
    return root_dir / toplevel_dir / as_dir_name(lang, lang) / sanitize_file_name(name[lang])


def is_in_dir(member_path: Path, dir_path: Path, check_is_file: bool = True) -> bool:
    if dir_path.resolve() == member_path.resolve():
        return True
    if dir_path.resolve() in member_path.resolve().parents:
        if check_is_file:
            return member_path.is_file()
        return True
    return False


def relative_path_to_course_img(output_file: Path, course_dir: Path) -> str:
    """Calculate relative path from output file to course's shared img/ folder.

    This function computes the relative path prefix needed to reference images
    in the shared img/ folder from a specific output file location.

    Args:
        output_file: Full path to the output file (e.g., HTML or notebook)
        course_dir: Path to the course directory containing the img/ folder

    Returns:
        Relative path prefix to prepend to image filenames, e.g., "../../../../img/"

    Example:
        >>> output_file = Path("output/public/De/Kurs/Folien/Html/Code-Along/Section/file.html")
        >>> course_dir = Path("output/public/De/Kurs")
        >>> relative_path_to_course_img(output_file, course_dir)
        '../../../../img/'
    """
    try:
        # Get the relative path from course_dir to output_file's directory
        rel_path = output_file.parent.relative_to(course_dir)
        # Count how many directory levels deep we are
        depth = len(rel_path.parts)
        # Build the relative path back up to the course dir and into img/
        return "../" * depth + "img/"
    except ValueError:
        # output_file is not under course_dir, fall back to absolute-style path
        logger.warning(
            f"Output file {output_file} is not under course dir {course_dir}, "
            f"using default img/ path"
        )
        return "img/"
