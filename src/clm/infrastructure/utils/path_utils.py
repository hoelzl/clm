import errno
import logging
import os
import re
import time
import uuid
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import field, frozen

from clm.core.utils.text_utils import as_dir_name

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.core.output_target import OutputTarget

logger = logging.getLogger(__name__)

SLIDES_PREFIX = "slides_"
TOPIC_PREFIX = "topic_"
PROJECT_PREFIX = "project_"

SKIP_DIRS_FOR_COURSE = frozenset(
    (
        "__pycache__",
        ".cargo",
        ".git",
        ".idea",
        ".idea",
        ".ipynb_checkpoints",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".venv",
        ".vs",
        ".vscode",
        ".vscode",
        "bin",
        "build",
        "chroma_db",
        "chroma_langchain_db",
        "chroma_rag_db",
        ".qdrant",
        "qdrant_data",
        "qdrant_db",
        "CMakeFiles",
        "data-local",
        "dist",
        "img-large",
        "img-local",
        "localdata",
        "obj",
        "out",
        "target",
        # Authoring sidecar: extracted voiceover companions live here when a
        # topic opts into the foldered layout. Fully excluded from the course
        # file map because the build merges their narration host-side at
        # payload time (it locates them by a direct probe — see
        # ``slides.voiceover_tools.resolve_companion`` — not this walk), so they
        # reach neither output nor the worker.
        "voiceover",
    )
)

# Cassettes differ from voiceover companions: the kernel reads them at runtime,
# so ``cassettes/`` (and the legacy ``_cassettes/``) stay in the course file map
# but are suppressed from output here. See ``NotebookFile._resolve_cassette``.
SKIP_DIRS_FOR_OUTPUT = SKIP_DIRS_FOR_COURSE | frozenset({"pu", "drawio", "_cassettes", "cassettes"})

SKIP_DIRS_PATTERNS = ["*.egg-info*", "*cmake-build*"]

SKIP_FILE_SUFFIXES = [".keras", ".bkp", ".bin"]

# Exact file names that are build-internal artifacts and must not enter the
# course file map (so they never reach workers, source mounts, or output).
# ``.clm-include`` is the per-topic ledger written by ``clm course sync-includes``.
SKIP_FILE_NAMES = frozenset({".clm-include"})

# File-name patterns (regex) that are allowed during course scanning (so they
# travel into worker payloads and source mounts) but must NOT be copied to
# public or speaker output. HTTP-replay cassettes are the first case: the
# kernel consumes them at execution time but students never see them.
#
# The ``.staging-`` variant is a per-worker partial cassette: it is a
# build-internal artifact and must be invisible to *every* downstream
# consumer (worker payloads, source mounts, AND public/speaker output).
# Concurrent workers may delete these mid-build during merge, so they
# must never be enumerated by the payload builder either — see
# :func:`compute_other_files` in ``process_notebook.py``.
#
# The ``.staging-<id>.completed`` variant is the per-staging completion
# marker introduced for issue #115: same lifetime and visibility rules
# as the staging file it accompanies — strictly build-internal.
SKIP_OUTPUT_FILE_PATTERNS = [
    re.compile(r".*\.http-cassette\.yaml$"),
    re.compile(r".*\.http-cassette\.yaml\.staging-.*$"),
    re.compile(r".*\.http-cassette\.yaml\.staging-.*\.completed$"),
    # Separated-voiceover companions: ``voiceover_<stem>.py`` and its split
    # ``.de.py`` / ``.en.py`` forms (see ``slides.voiceover_tools.companion_path``).
    # Unlike cassettes these are NOT invisible everywhere — they stay available
    # as *source*: the build merges their narration into the slide notebook at
    # payload time (``ProcessNotebookOperation.payload`` reads the companion
    # directly from source). They must only be kept out of (a) public/speaker
    # OUTPUT and (b) the kernel ``other_files`` payload (the raw author file is
    # never read at runtime). ``is_ignored_file_for_output`` governs exactly
    # those two; source mounts do not consult it, so workers still see it.
    # ``.py`` plus the //-family slide extensions (companion_name preserves the
    # deck's extension), so a ``voiceover_<stem>.cs`` never leaks into output.
    re.compile(r"voiceover_.*\.(py|cs|cpp|cxx|cc|java|ts|rs)$"),
]

# Parallel glob form of ``SKIP_OUTPUT_FILE_PATTERNS`` for consumers that
# take shell-style patterns (e.g. ``shutil.ignore_patterns``). Keep in
# sync with the regex list above.
SKIP_OUTPUT_FILE_GLOBS = [
    "*.http-cassette.yaml",
    "*.http-cassette.yaml.staging-*",
    "*.http-cassette.yaml.staging-*.completed",
    # separated-voiceover companions — output-suppressed (see above). One glob
    # per slide extension (globs have no alternation); keep in sync with the regex.
    "voiceover_*.py",
    "voiceover_*.cs",
    "voiceover_*.cpp",
    "voiceover_*.cxx",
    "voiceover_*.cc",
    "voiceover_*.java",
    "voiceover_*.ts",
    "voiceover_*.rs",
]

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
    # ``.c`` is treated as C++: CLM has no standalone "c" language config
    # (no jinja/jupytext/kernel entry in prog_lang_utils), and the xcpp kernel
    # compiles C as C++. Mapping ``.c`` -> "c" produced a config-less prog_lang
    # that crashed any resolver reaching prog_lang_utils.
    ".c": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".java": "java",
    ".md": "python",
    ".py": "python",
    ".rs": "rust",
    ".rust": "rust",
    ".ts": "typescript",
}

PROG_LANG_TO_EXTENSION = {
    "cpp": ".cpp",
    "csharp": ".cs",
    "java": ".java",
    "python": ".py",
    "rust": ".rs",
    "typescript": ".ts",
}

IGNORE_PATH_REGEX = re.compile(
    r"(.*\.egg-info.*|.*cmake-build-.*|.*\.bkp|.*\.bak|chroma_.*db.*|qdrant_.*)"
)


def is_image_file(input_path: Path) -> bool:
    is_image_data = IMG_DATA_FOLDERS.intersection(input_path.absolute().parts) != set()
    return input_path.suffix in IMG_FILE_EXTENSIONS and not is_image_data


def is_image_source_file(input_path: Path) -> bool:
    return input_path.suffix in IMG_SOURCE_FILE_EXTENSIONS


def is_slides_file(input_path: Path) -> bool:
    return (
        input_path.name.startswith(SLIDES_PREFIX)
        or input_path.name.startswith(TOPIC_PREFIX)
        or input_path.name.startswith(PROJECT_PREFIX)
    ) and input_path.suffix in SUPPORTED_PROG_LANG_EXTENSIONS


SPLIT_LANG_SUFFIXES = ("de", "en")


def split_lang_suffix(input_path: Path) -> str | None:
    """Return ``"de"`` / ``"en"`` if ``input_path`` is a split slide file.

    A split slide file has a stem ending in ``.de`` or ``.en`` *before* its
    program-language extension — e.g. ``slides_foo.de.py`` or
    ``slides_bar.en.cpp``. Only paths that are also recognised by
    :func:`is_slides_file` (right prefix and supported extension) qualify;
    otherwise the function returns ``None``.

    The bilingual companion ``slides_foo.py`` returns ``None`` because the
    stem ``slides_foo`` does not end in a language tag.
    """
    if not is_slides_file(input_path):
        return None
    stem = input_path.name[: -len(input_path.suffix)]
    for lang in SPLIT_LANG_SUFFIXES:
        if stem.endswith(f".{lang}"):
            return lang
    return None


def slide_family_key(input_path: Path) -> str | None:
    """Return the bilingual-companion file name shared by a slide-file family.

    Three paths can belong to the same family — the bilingual ``slides_foo.py``
    and its split companions ``slides_foo.de.py`` / ``slides_foo.en.py``. The
    family key is the *bilingual* file name (``slides_foo.py`` in this
    example), regardless of which path is supplied. Returns ``None`` when
    ``input_path`` is not a slide file at all.

    The bilingual name is the canonical family identifier even when no
    bilingual file exists on disk; the topic-enumeration step uses this
    grouping to detect the four routing cases (bilingual-only, split-pair,
    half-pair, dual-format conflict).
    """
    if not is_slides_file(input_path):
        return None
    ext = input_path.suffix
    stem = input_path.name[: -len(ext)]
    for lang in SPLIT_LANG_SUFFIXES:
        suffix = f".{lang}"
        if stem.endswith(suffix):
            return f"{stem[: -len(suffix)]}{ext}"
    return input_path.name


def atomic_write_all(writes: list[tuple[Path, str]]) -> None:
    """Write several ``(path, text)`` outputs as atomically as the FS allows.

    Every text is first written to a sibling ``*.tmp`` file; only after **all**
    temp writes succeed are they ``os.replace``-d into place back-to-back. A
    failure during the temp phase (the common one — disk full, permission)
    therefore leaves every real target untouched; the replace phase has only a
    tiny residual window, and leftover temps are cleaned up either way.

    Shared by the slide rewriters that emit several coupled files in one op —
    ``split`` / ``unify`` (a deck plus its voiceover companion) and the paired
    ``voiceover extract`` (two slide halves plus two companions). With plain
    per-file writes a mid-operation failure could leave a one-sided companion
    (the very orphaning these seams prevent). Cross-file atomicity is not
    achievable without a journal, but this upgrades direct per-file writes so
    the likely failure is safe.
    """
    temps: list[tuple[Path, Path]] = []
    try:
        for path, text in writes:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8", newline="\n")
            temps.append((tmp, path))
        for tmp, path in temps:
            os.replace(tmp, path)
    finally:
        for tmp, _ in temps:
            if tmp.exists():
                tmp.unlink()


def is_private_dir_name(name: str) -> bool:
    """True for underscore- or dot-prefixed directory names.

    Underscore-prefixed directories under ``slides/`` hold author-parked
    content (retired decks, drafts). They are excluded from module/topic
    *discovery* and from the recursive slide-file walks so an archived copy
    can never shadow a live topic ID (issue #318). This is a discovery rule,
    not a course-file-map rule: the legacy ``_cassettes/`` sidecar inside a
    topic stays in the course file map because the kernel reads it at
    runtime (see ``SKIP_DIRS_FOR_OUTPUT``).

    Dot-prefixed directories (``.ipynb_checkpoints``, ``.git``, ``.vscode``,
    …) are tool sidecars, never course content; Jupyter's checkpoint copies
    of decks otherwise surface as duplicate — possibly stale — findings in
    validation and normalization walks (issue #339).
    """
    return name.startswith(("_", "."))


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
        or file_path.name in SKIP_FILE_NAMES
    )


def is_ignored_file_for_output(file_path: Path) -> bool:
    """Return True if the file must not be copied to public/speaker output.

    Broader than :func:`is_ignored_file_for_course` — rejects everything
    course scanning rejects, plus directories in ``SKIP_DIRS_FOR_OUTPUT``
    (e.g. ``_cassettes``) and file-name patterns in
    ``SKIP_OUTPUT_FILE_PATTERNS`` (e.g. ``*.http-cassette.yaml``). Used
    when deciding whether to emit a ``CopyFileOperation`` for a file that
    is part of a course but should remain invisible to students.
    """
    if is_ignored_file_for_course(file_path):
        return True
    if is_ignored_dir_for_output(file_path.parent):
        return True
    name = file_path.name
    for pattern in SKIP_OUTPUT_FILE_PATTERNS:
        if pattern.match(name):
            return True
    return False


def is_ignored_path_in_output_tree(rel_path: Path) -> bool:
    """Return True if a path *found inside* an output tree cannot be a build output.

    Mirrors the dir-group copy filter (``shutil.ignore_patterns(*SKIP_DIRS_FOR_OUTPUT,
    *SKIP_DIRS_PATTERNS, *SKIP_OUTPUT_FILE_GLOBS)`` in
    ``LocalOpsBackend.copy_dir_group_files``): the build never copies VCS/IDE/cache
    directories or output-suppressed files, so anything found beneath them was put
    there by something else — e.g. the ``.git`` that ``clm git init`` creates inside
    an output target (issue #302) — and must not be recorded as build provenance.

    Unlike :func:`is_ignored_file_for_output` (which classifies *source* files and
    also applies course-scan suffix rules), this checks every segment of the
    output-relative path **including the final name**, so a worktree-style ``.git``
    *file* is rejected too, and deliberately does not reject suffixes like ``.bin``
    that the dir-group copy does ship.
    """
    if is_ignored_dir_for_output(rel_path):
        return True
    name = rel_path.name
    return any(pattern.match(name) for pattern in SKIP_OUTPUT_FILE_PATTERNS)


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
    TRAINER = "trainer"
    RECORDING = "recording"
    SPEAKER = "speaker"  # Deprecated alias for RECORDING; removed in CLM 1.8.
    PARTIAL = "partial"


# Kinds that land under the private (``speaker/``) toplevel directory rather
# than the public one. ``speaker`` is the deprecated input alias for
# ``recording`` — still accepted, still routed to the private toplevel.
PRIVATE_KINDS: frozenset[str] = frozenset({"trainer", "recording", "speaker"})


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
    skip_toplevel: bool = False
    output_dir: Path = field(init=False)

    def __attrs_post_init__(self):
        if self.format == "code":
            format_ = as_dir_name(self.course.prog_lang, self.language)
        else:
            format_ = as_dir_name(self.format, self.language)
        # ``speaker`` is a deprecated alias that resolves to ``recording``: it
        # produces output under ``speaker/.../recording/`` so a course mixing
        # legacy and new spec values doesn't double-emit. Trainer and recording
        # always carry their own kind subdir.
        effective_kind = "recording" if self.kind == "speaker" else self.kind
        kind = as_dir_name(effective_kind, self.language)
        output_path = output_path_for(
            self.root_dir,
            self.kind in PRIVATE_KINDS,
            self.language,
            self.course.output_dir_name[self.language],
            skip_toplevel=self.skip_toplevel,
        )

        slides_dir = as_dir_name("slides", self.language)
        dir_path = output_path / f"{slides_dir}/{format_}/{kind}"
        object.__setattr__(self, "output_dir", dir_path)

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
    All format/kind combinations are valid - code format can be generated for any kind.

    Args:
        course: Course object
        root_dir: Root directory for output
        skip_html: If True, skip HTML format generation
        languages: List of languages to generate (default: ["de", "en"])
        kinds: List of output kinds to generate (default: all kinds)
            Valid values: "code-along", "completed", "trainer", "recording",
            "partial". "speaker" is accepted as a deprecated alias for
            "recording".
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
        effective_kinds = (
            kinds if kinds else ["code-along", "completed", "trainer", "recording", "partial"]
        )
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

    # Build kind list. ``speaker`` is normalized to ``recording`` so a target
    # mixing both (legacy + new spec) doesn't yield duplicate operations.
    kind_dirs: list[Kind] = []
    if "code-along" in effective_kinds:
        kind_dirs.append(Kind.CODE_ALONG)
    if "completed" in effective_kinds:
        kind_dirs.append(Kind.COMPLETED)
    if "trainer" in effective_kinds:
        kind_dirs.append(Kind.TRAINER)
    if "recording" in effective_kinds or (
        "speaker" in effective_kinds and "recording" not in effective_kinds
    ):
        kind_dirs.append(Kind.RECORDING)
    if "partial" in effective_kinds:
        kind_dirs.append(Kind.PARTIAL)

    # Determine if we should skip the toplevel public/speaker directory
    # For explicit targets, paths start directly with the language directory
    skip_toplevel = target.is_explicit if target is not None else False

    # Generate all format/kind combinations
    for lang_dir in lang_dirs:
        for format_dir in format_dirs:
            for kind_dir in kind_dirs:
                yield OutputSpec(
                    course=course,
                    language=lang_dir,
                    format=format_dir,
                    kind=kind_dir,
                    root_dir=root_dir,
                    skip_toplevel=skip_toplevel,
                )


def path_to_prog_lang(path: Path) -> str:
    return extension_to_prog_lang(path.suffix)


def extension_to_prog_lang(ext: str) -> str:
    return EXTENSION_TO_PROG_LANG[ext]


def prog_lang_to_extension(prog_lang: str) -> str:
    return PROG_LANG_TO_EXTENSION[prog_lang]


def output_path_for(
    root_dir: Path,
    is_speaker: bool,
    lang: str,
    dir_name: str,
    skip_toplevel: bool = False,
) -> Path:
    """Construct the output path for a course.

    Args:
        root_dir: Root output directory
        is_speaker: True for private output (``trainer``/``recording``/
            deprecated ``speaker``), False for public output
        lang: Language code (e.g., "de", "en") — kept for API compat but
            no longer used to create a subdirectory
        dir_name: Pre-computed directory name (e.g., "ml-course-de")
        skip_toplevel: If True, skip the "public"/"speaker" directory prefix.
            Used for explicitly specified output targets where the path
            should start directly with the course directory.

    Returns:
        Path to the course output directory
    """
    if skip_toplevel:
        return root_dir / dir_name
    else:
        toplevel_dir = "speaker" if is_speaker else "public"
        return root_dir / toplevel_dir / dir_name


# Errnos that, on Windows, are routinely produced when antivirus, the
# search indexer, or a cloud-sync agent (Defender, OneDrive, Dropbox) is
# briefly holding a handle on a file in the destination directory while
# CLM is rapid-writing many results. EINVAL in particular shows up when
# CreateFileW races with such a handle on an O_TRUNC open.
_TRANSIENT_WRITE_ERRNOS = frozenset({errno.EACCES, errno.EBUSY, errno.EINVAL, errno.EPERM})


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    max_retries: int = 5,
    base_delay: float = 0.05,
) -> None:
    """Write ``data`` to ``path`` atomically and resiliently.

    The bytes are first written to a unique sibling temp file, then
    ``os.replace``-d into place. The destination is therefore never opened
    with ``O_TRUNC`` in place — this avoids most CreateFileW races with
    Windows antivirus / search-indexer / cloud-sync handles, which manifest
    as ``OSError [Errno 22] Invalid argument`` on otherwise-valid paths.

    Transient ``OSError``s during either the temp write or the rename are
    retried with exponential backoff so a short scan window doesn't fail
    the build. Non-transient errors (e.g. ``ENOSPC``) propagate immediately.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    last_err: OSError | None = None
    for attempt in range(max_retries):
        # Fresh temp name per attempt — if a previous attempt left a stale
        # temp behind that itself can't be unlinked, we don't keep retrying
        # against it.
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
            return
        except OSError as exc:
            last_err = exc
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            if exc.errno not in _TRANSIENT_WRITE_ERRNOS:
                raise
            if attempt + 1 < max_retries:
                logger.warning(
                    "Transient OSError writing %s (errno=%s, attempt %d/%d): %s — retrying",
                    path,
                    exc.errno,
                    attempt + 1,
                    max_retries,
                    exc,
                )
                time.sleep(base_delay * (2**attempt))

    assert last_err is not None
    raise last_err


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
