"""Generate CMake projects for the C++ code export (issue #333, phase 2; #928, phase 3).

After a build, every ``format="code"`` output directory of a C++ course
contains one compilable file set per deck: ``<deck>.cpp`` (#333 phase 1),
plus — since #928 phase 3 — a deck header and one ``<deck>_workshop_N.cpp``
per workshop range, with any deck-local headers copied alongside by the
regular data-file copy. This module adds generated ``CMakeLists.txt`` files
so students can open the directory as a CMake project (VS Code, CLion,
Visual Studio) and build any deck or workshop with real compiler
diagnostics, independent of the Jupyter kernels: the kind root is one
project that ``add_subdirectory``s every module, and each module directory
is a project of its own — one executable target per deck and per workshop
— that can be opened standalone.

Deck-local and deck headers need no CMake configuration: they sit next to
the translation units, and ``#include "..."`` searches the including file's
directory first.

Layout produced (one per language × kind, language is already a separate
course tree)::

    <Course>-<lang>/<Slides>/<Cpp>/<Kind>/CMakeLists.txt
                                          <01 Module>/CMakeLists.txt
                                          <01 Module>/<01 Deck>.cpp
                                          <01 Module>/<01 Deck>.hpp
                                          <01 Module>/<01 Deck>_workshop_1.cpp
"""

from __future__ import annotations

import importlib.resources
import logging
import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from clm.core.course_files.notebook_file import NotebookFile
from clm.core.cpp_export_files import companion_output_files, workshop_ordinal
from clm.core.deck_markers import has_no_compile_marker
from clm.core.utils.path_utils import ext_for, output_specs
from clm.core.utils.prog_lang_utils import comment_token_for_path, effective_standard
from clm.core.utils.text_utils import sanitize_file_name

if TYPE_CHECKING:
    from clm.core.course import Course

logger = logging.getLogger(__name__)

CMAKELISTS_FILENAME = "CMakeLists.txt"

SUPPORT_INCLUDE_DIRNAME = "include"
"""Subdirectory of a kind root receiving vendored support headers."""

# Vendored headers (clm/data/cpp_export/include/) copied into a kind root
# when its exported code — or a deck-local header next to it — references
# them. The xcpp display shim depends on the vendored nlohmann header (it
# provides xeus's ``nl`` namespace alias), so referencing it pulls in both.
_SUPPORT_HEADER_TOKENS: dict[str, tuple[str, ...]] = {
    "nlohmann/json.hpp": ("nlohmann/json.hpp",),
    "xcpp/xdisplay.hpp": ("xcpp/xdisplay.hpp", "nlohmann/json.hpp"),
    # The emitter's labeled-display helper (#928): every deck with a bare
    # display expression includes it.
    "clm/display.hpp": ("clm/display.hpp",),
}


def cmake_identifier(text: str) -> str:
    """Fold *text* to a safe CMake identifier.

    ASCII-folds (``Einführung`` → ``einfuhrung``), lowercases, and collapses
    every other character run to ``_``. Returns ``deck`` for input that
    folds to nothing.
    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    ident = re.sub(r"[^A-Za-z0-9]+", "_", folded).strip("_").lower()
    return ident or "deck"


def deck_target_name(rel_path: str) -> str:
    """Derive a CMake target name from a deck's path relative to the project.

    ``"01 Einführung/03 Entwicklungsumgebungen.cpp"`` →
    ``"s01_03_entwicklungsumgebungen"``: section number (when present) keeps
    the targets sorted and grouped like the course material.
    """
    path = PurePosixPath(rel_path)
    name = cmake_identifier(path.stem)
    section = path.parent.name
    if section:
        section_number = re.match(r"(\d+)", section)
        prefix = (
            f"s{int(section_number.group(1)):02d}" if section_number else cmake_identifier(section)
        )
        name = f"{prefix}_{name}"
    if not re.match(r"[A-Za-z_]", name):
        name = f"deck_{name}"
    return name


@dataclass(frozen=True)
class _Target:
    name: str
    rel_path: str
    """Source path relative to the directory whose ``CMakeLists.txt`` lists it."""
    excluded: bool


#: Variable the root project sets so a module project knows the toolchain is
#: configured; a module opened standalone configures it itself.
_CONFIGURED_VAR = "CLM_CODE_EXPORT_CONFIGURED"


DEFAULT_CXX_STANDARD = effective_standard("cpp")
"""``CMAKE_CXX_STANDARD`` of a course that selects no standard (``"20"``)."""


def _toolchain_lines(
    include_dir: str | None, indent: str = "", cxx_standard: str = DEFAULT_CXX_STANDARD
) -> list[str]:
    lines = [
        f"{indent}set({_CONFIGURED_VAR} ON)",
        f"{indent}set(CMAKE_CXX_STANDARD {cxx_standard})",
        f"{indent}set(CMAKE_CXX_STANDARD_REQUIRED ON)",
        f"{indent}if(MSVC)",
        f"{indent}    # Slide sources are UTF-8; MSVC assumes the local codepage without this.",
        f"{indent}    add_compile_options(/utf-8)",
        f"{indent}endif()",
    ]
    if include_dir is not None:
        lines += [
            f"{indent}# Vendored support headers (clm/display.hpp, nlohmann/json, the xcpp shim).",
            f'{indent}include_directories("${{CMAKE_CURRENT_SOURCE_DIR}}/{include_dir}")',
        ]
    return lines


def _target_lines(targets: Sequence[_Target]) -> list[str]:
    lines: list[str] = []
    for target in targets:
        if target.excluded:
            lines.append(f'add_executable({target.name} EXCLUDE_FROM_ALL "{target.rel_path}")')
        else:
            lines.append(f'add_executable({target.name} "{target.rel_path}")')
    return lines


def _assign_targets(
    deck_rel_paths: Sequence[str],
    excluded: frozenset[str] | set[str],
    workshops: Mapping[str, Sequence[str]],
    reserved: Collection[str] = (),
) -> list[_Target]:
    """One target per deck and per workshop file, unique names project-wide.

    CMake target names are global, so they are assigned over the whole
    kind root before the targets are split into module projects.
    Duplicate deck names get a numeric suffix; a workshop target is named
    after its deck's (disambiguated) target plus ``_workshop_N``. Names in
    *reserved* — targets of modules this call does not regenerate (#1138)
    — are never reused.
    """
    targets: list[_Target] = []
    used: dict[str, int] = {}
    for rel in sorted(deck_rel_paths):
        base = deck_target_name(rel)
        count = used.get(base, 0)
        name = f"{base}_{count + 1}" if count else base
        while name in reserved:
            count += 1
            name = f"{base}_{count + 1}"
        used[base] = count + 1
        is_excluded = rel in excluded
        targets.append(_Target(name, rel, is_excluded))
        stem = PurePosixPath(rel).stem
        for ws_rel in sorted(
            workshops.get(rel, ()), key=lambda p: workshop_ordinal(PurePosixPath(p).name, stem) or 0
        ):
            ordinal = workshop_ordinal(PurePosixPath(ws_rel).name, stem)
            if ordinal is None:
                continue
            targets.append(_Target(f"{name}_workshop_{ordinal}", ws_rel, is_excluded))
    return targets


def generate_cmake_files(
    project_name: str,
    deck_rel_paths: Sequence[str],
    excluded: frozenset[str] | set[str] = frozenset(),
    *,
    workshops: Mapping[str, Sequence[str]] | None = None,
    with_include_dir: bool = False,
    extra_modules: Collection[str] = (),
    reserved_targets: Collection[str] = (),
    cxx_standard: str = DEFAULT_CXX_STANDARD,
) -> dict[str, str]:
    """Generate the CMake files of one code-output directory.

    Returns ``{relative path: text}``: ``CMakeLists.txt`` at the kind root —
    the toolchain settings plus ``add_subdirectory`` for every module
    directory — and ``<module>/CMakeLists.txt`` per module with one
    executable target per deck and per workshop file (``workshops`` maps a
    deck's relative path to its workshop files' relative paths). A module
    project configures the toolchain itself when opened standalone.
    Deterministic order; duplicate target names disambiguated with a
    numeric suffix. Decks in *excluded* (marked ``clm: no-compile`` in the
    course source) become ``EXCLUDE_FROM_ALL`` targets, workshops included:
    still buildable explicitly, but skipped by "build all" — and thereby by
    the CI compile check.

    *extra_modules* and *reserved_targets* serve a section-only build
    (#1138): module directories whose decks are not passed in but which the
    kind root must still ``add_subdirectory``, and the target names their
    existing project files already use.

    *cxx_standard* is the ``CMAKE_CXX_STANDARD`` of every project file
    written — the same standard the course's notebooks are executed at
    (issue #1228).
    """
    targets = _assign_targets(deck_rel_paths, excluded, workshops or {}, reserved_targets)
    include_dir = SUPPORT_INCLUDE_DIRNAME if with_include_dir else None

    root_targets: list[_Target] = []
    modules: dict[str, list[_Target]] = {}
    for target in targets:
        parts = PurePosixPath(target.rel_path).parts
        if len(parts) < 2:
            root_targets.append(target)
            continue
        modules.setdefault(parts[0], []).append(
            _Target(target.name, PurePosixPath(*parts[1:]).as_posix(), target.excluded)
        )

    project_ident = cmake_identifier(project_name)
    root_lines = [
        "# Generated by CLM — one CMake project per module, one executable target",
        "# per deck and per workshop. Open this directory as a CMake project",
        "# (VS Code, CLion, Visual Studio), or a single module directory on its own.",
        "",
        "cmake_minimum_required(VERSION 3.21)",
        f"project({project_ident} LANGUAGES CXX)",
        "",
        *_toolchain_lines(include_dir, cxx_standard=cxx_standard),
        "",
    ]
    root_lines += [f'add_subdirectory("{module}")' for module in sorted({*modules, *extra_modules})]
    root_lines += _target_lines(root_targets)
    files = {CMAKELISTS_FILENAME: "\n".join(root_lines).rstrip("\n") + "\n"}

    for module, module_targets in sorted(modules.items()):
        module_lines = [
            "# Generated by CLM — the decks of one module, one executable target per",
            "# deck and per workshop. Open this directory on its own, or the parent",
            "# directory for the whole course.",
            "",
            "cmake_minimum_required(VERSION 3.21)",
            f"project({project_ident}_{cmake_identifier(module)} LANGUAGES CXX)",
            "",
            f"if(NOT {_CONFIGURED_VAR})",
            "    # Opened standalone: the parent project did not configure the toolchain.",
            *_toolchain_lines(
                f"../{include_dir}" if include_dir else None,
                indent="    ",
                cxx_standard=cxx_standard,
            ),
            "endif()",
            "",
            *_target_lines(module_targets),
        ]
        files[f"{module}/{CMAKELISTS_FILENAME}"] = "\n".join(module_lines) + "\n"
    return files


def generate_cmakelists(
    project_name: str,
    deck_rel_paths: Sequence[str],
    excluded: frozenset[str] | set[str] = frozenset(),
    *,
    workshops: Mapping[str, Sequence[str]] | None = None,
    with_include_dir: bool = False,
    cxx_standard: str = DEFAULT_CXX_STANDARD,
) -> str:
    """The kind-root ``CMakeLists.txt`` text of :func:`generate_cmake_files`."""
    return generate_cmake_files(
        project_name,
        deck_rel_paths,
        excluded,
        workshops=workshops,
        with_include_dir=with_include_dir,
        cxx_standard=cxx_standard,
    )[CMAKELISTS_FILENAME]


@dataclass
class CodeProject:
    """The decks found in one code-output directory."""

    project_name: str
    lang: str = ""
    """Output language of the directory (``de`` / ``en``)."""
    decks: set[str] = field(default_factory=set)
    excluded: set[str] = field(default_factory=set)
    """Decks marked ``clm: no-compile`` in the course source."""
    workshops: dict[str, list[str]] = field(default_factory=dict)
    """Per deck, the ``<deck>_workshop_N.cpp`` files found next to it (#928)."""


def _deck_has_no_compile_marker(file: NotebookFile) -> bool:
    try:
        text = Path(file.path).read_text(encoding="utf-8")
    except OSError:
        return False
    return has_no_compile_marker(text, comment_token_for_path(Path(file.path)))


def collect_cpp_code_outputs(course: Course) -> dict[Path, CodeProject]:
    """Find every C++ code-output directory with the decks it contains.

    Keys are the kind-root directories (``.../<Cpp>/<Kind>``); only code
    outputs that exist on disk are recorded, enumerating the same way the
    provenance manifest does (course files × output specs).
    """
    groups: dict[Path, CodeProject] = {}
    for target in course.output_targets:
        if not target.output_root.exists():
            continue
        for file in course.files:
            if not isinstance(file, NotebookFile):
                continue
            ext = ext_for("code", file.prog_lang)
            if ext != ".cpp":
                continue
            no_compile = _deck_has_no_compile_marker(file)
            for lang, fmt, kind, output_dir in output_specs(
                course, target.output_root, file.skip_html, target=target
            ):
                if fmt != "code":
                    continue
                try:
                    out_path = file.output_dir(output_dir, lang) / file.file_name(lang, ext)
                except (KeyError, ValueError):
                    # e.g. a split-language source with no title for this
                    # language; that combination is simply not produced.
                    continue
                if not out_path.is_file():
                    continue
                group = groups.setdefault(
                    output_dir,
                    CodeProject(project_name=f"{course.output_dir_name[lang]} {kind}", lang=lang),
                )
                rel = out_path.relative_to(output_dir).as_posix()
                group.decks.add(rel)
                if no_compile:
                    group.excluded.add(rel)
                workshop_files = [
                    companion.relative_to(output_dir).as_posix()
                    for companion in companion_output_files(out_path)
                    if companion.suffix == ".cpp"
                ]
                if workshop_files:
                    group.workshops[rel] = workshop_files
    return groups


def needed_support_headers(kind_root: Path) -> set[str]:
    """Which vendored support headers the code under *kind_root* references.

    Scans every C++ source/header in the tree — deck-local headers included,
    since e.g. ``point.hpp`` pulls in nlohmann — but skips the vendored
    ``include/`` directory itself so a regeneration doesn't self-trigger.
    """
    needed: set[str] = set()
    for path in kind_root.rglob("*"):
        if not path.is_file() or path.suffix not in {".cpp", ".h", ".hpp"}:
            continue
        if path.relative_to(kind_root).parts[0] == SUPPORT_INCLUDE_DIRNAME:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for token, headers in _SUPPORT_HEADER_TOKENS.items():
            if token in text:
                needed.update(headers)
    return needed


def _copy_support_headers(kind_root: Path, headers: set[str]) -> None:
    data_root = importlib.resources.files("clm") / "data" / "cpp_export" / "include"
    for header in sorted(headers):
        target = kind_root / SUPPORT_INCLUDE_DIRNAME / PurePosixPath(header)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data_root.joinpath(header).read_bytes())


_ADD_EXECUTABLE_RE = re.compile(r"^\s*add_executable\(\s*([^\s)]+)", re.MULTILINE)


def _section_dir_names(course: Course, lang: str) -> set[str]:
    """Output directory names of every section a full build would produce."""
    names: set[str] = set()
    for section in course.spec.sections:
        if not section.enabled:
            continue
        try:
            names.add(sanitize_file_name(section.name[lang]))
        except (KeyError, ValueError):
            continue
    return names


def _unselected_modules(
    kind_root: Path, selected: set[str], section_dirs: set[str]
) -> dict[str, set[str]]:
    """Module directories under *kind_root* that a section-only build keeps.

    Every directory of an unselected spec section (*section_dirs* minus
    *selected*) holding a ``CMakeLists.txt`` from an earlier build, mapped
    to the target names that file defines — so the kind-root project keeps
    adding it and no regenerated target reuses one of its names (#1138).
    Directories no spec section owns (renamed or removed sections) are
    left out, as a full build's sweep would remove them.
    """
    modules: dict[str, set[str]] = {}
    for name in sorted(section_dirs - selected):
        module_dir = kind_root / name
        cmakelists = module_dir / CMAKELISTS_FILENAME
        if not cmakelists.is_file():
            if module_dir.is_dir() and any(module_dir.glob("*.cpp")):
                logger.warning(
                    "%s has C++ code output but no %s, so the course CMake project "
                    "does not include it; run a full build to regenerate it.",
                    module_dir,
                    CMAKELISTS_FILENAME,
                )
            continue
        try:
            text = cmakelists.read_text(encoding="utf-8")
        except OSError:
            text = ""
        modules[name] = set(_ADD_EXECUTABLE_RE.findall(text))
    return modules


def _existing_code_projects(course: Course) -> dict[Path, CodeProject]:
    """Code-output kind roots that already hold a ``CMakeLists.txt``.

    A section-only build must rewrite these even when none of the selected
    sections left a deck there: the section cleanup has emptied the
    selected module directories the old kind-root file still adds (#1138).
    """
    roots: dict[Path, CodeProject] = {}
    for target in course.output_targets:
        if not target.output_root.exists():
            continue
        for lang, fmt, kind, output_dir in output_specs(
            course, target.output_root, False, target=target
        ):
            if fmt == "code" and (output_dir / CMAKELISTS_FILENAME).is_file():
                roots.setdefault(
                    output_dir,
                    CodeProject(project_name=f"{course.output_dir_name[lang]} {kind}", lang=lang),
                )
    return roots


def write_cmake_projects(course: Course) -> list[Path]:
    """Write the CMake project files of every built C++ code-output directory.

    No-op for non-C++ courses. Only directories that actually contain code
    outputs get project files: a ``CMakeLists.txt`` at the kind root plus one
    per module directory (see :func:`generate_cmake_files`). Vendored support
    headers (the display helper, nlohmann/json, the xcpp display shim) are
    copied into ``include/`` where the exported code references them.
    Returns the list of ``CMakeLists.txt`` paths written.

    For a course built with a section selection (``--only-sections``,
    #1138) only the selected sections' module files are regenerated. The
    kind-root file is rewritten too — also where no selected section left a
    deck — still adding every unselected section's module directory that
    holds a ``CMakeLists.txt`` from an earlier build; those module files
    are left as they are.

    ``CMAKE_CXX_STANDARD`` is the course's C++ standard (the spec's
    ``<prog-lang standard="…">``, default 20) — the one setting that also
    selects the kernel the notebooks run in, so a deck cannot be C++23 in
    the notebook and C++20 in its exported project (issue #1228).
    """
    if course.prog_lang != "cpp":
        return []
    cxx_standard = effective_standard("cpp", course.standard_for("cpp"))
    partial = course.section_selection is not None
    projects = collect_cpp_code_outputs(course)
    if partial:
        projects = {**_existing_code_projects(course), **projects}
    written: list[Path] = []
    for kind_root, project in sorted(projects.items()):
        support = needed_support_headers(kind_root)
        if support:
            _copy_support_headers(kind_root, support)
        kept: dict[str, set[str]] = {}
        if partial:
            selected = {PurePosixPath(deck).parts[0] for deck in project.decks}
            kept = _unselected_modules(
                kind_root, selected, _section_dir_names(course, project.lang)
            )
        files = generate_cmake_files(
            project.project_name,
            sorted(project.decks),
            project.excluded,
            workshops=project.workshops,
            with_include_dir=bool(support),
            extra_modules=kept,
            reserved_targets=set().union(*kept.values()),
            cxx_standard=cxx_standard,
        )
        for rel, content in files.items():
            path = kind_root / PurePosixPath(rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            # LF on every host: the export lands in git-tracked course repos.
            path.write_text(content, encoding="utf-8", newline="\n")
            written.append(path)
        logger.info(
            "Wrote %d CMake file(s) under %s (%d deck targets, %d workshop targets, "
            "%d support headers)",
            len(files),
            kind_root,
            len(project.decks),
            sum(len(ws) for ws in project.workshops.values()),
            len(support),
        )
    return written
