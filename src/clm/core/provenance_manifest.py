"""Build provenance manifest (issue #208, step 1).

A ``.clm-manifest.json`` written into an output root maps every generated
output file to its origin::

    output_path -> {section_id, topic_id, kind, format, language, content_hash}

plus build-level ``source_commit`` / ``source_dirty`` / ``built_at``.

Why this exists: the owning **topic of an output file is not recoverable from
the output path** (topics within a section share one ``sanitize(section.name)``
folder; assets carry no topic marker). The manifest is therefore the join key a
per-topic release engine needs, and it answers "which source commit produced
this file" for free.

The manifest is a **private, build-internal** artifact. It is read by the
release engine (a later step) but is never itself shipped to students; the
release sync must skip ``.clm-*`` sidecars when promoting files.

Scope of this first increment: notebook-derived outputs (notebooks, code, and
rendered HTML). Topic-specific assets and dir-group ownership are added in a
follow-up commit on the same issue; see ``enumerate_expected_outputs``.

The enumeration deliberately re-uses the *same* path computation the build
uses (:func:`output_specs` + ``CourseFile.output_dir`` + ``file_name``) and
then filters to files that actually exist on disk. Over-enumerating (e.g. both
languages for a split ``.de.py`` / ``.en.py`` source) is harmless: paths that
were not written simply fail the existence check and are dropped.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clm.core.course_files.data_file import DataFile
from clm.core.course_files.duplicated_image_file import DuplicatedImageFile
from clm.core.course_files.notebook_file import NotebookFile
from clm.infrastructure.utils.path_utils import (
    ext_for,
    is_ignored_file_for_output,
    output_specs,
)

if TYPE_CHECKING:
    from clm.core.course import Course
    from clm.core.output_target import OutputTarget

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = ".clm-manifest.json"
MANIFEST_VERSION = 1

_HASH_CHUNK = 1 << 16


def enumerate_expected_outputs(
    course: Course, target: OutputTarget
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield ``(absolute_output_path, record)`` for every output *target*
    should produce, before any existence check.

    ``record`` carries ``section_id``/``topic_id``/``kind``/``format``/
    ``language`` but not yet ``path`` or ``content_hash`` (added by
    :func:`build_provenance_manifest`).

    Covered: notebook-derived outputs (notebook/code/HTML), copied topic data
    assets (``DataFile``), duplicated images (``DuplicatedImageFile``), and
    dir-group output files with their recorded section/topic ownership.

    Not yet covered (issue #208 follow-up): ``SharedImageFile`` (the course-
    level ``image_mode="shared"`` layout). Source diagrams (PlantUML/DrawIo)
    emit only a source-tree intermediate image; their *output* copy is a
    separate image ``CourseFile`` that is already covered above.
    """
    for file in course.files:
        topic = file.topic
        section = topic.section
        if isinstance(file, NotebookFile):
            for lang, fmt, kind, output_dir in output_specs(
                course, target.output_root, file.skip_html, target=target
            ):
                try:
                    out_path = file.output_dir(output_dir, lang) / file.file_name(
                        lang, ext_for(fmt, file.prog_lang)
                    )
                except (KeyError, ValueError) as e:
                    # e.g. a split-language source has no title for the other
                    # language; that combination is simply not produced.
                    logger.debug(
                        "provenance: skip %s (%s/%s/%s): %s", file.path, lang, fmt, kind, e
                    )
                    continue
                yield (
                    out_path,
                    {
                        "section_id": section.id,
                        "topic_id": topic.id,
                        "kind": kind,
                        "format": fmt,
                        "language": lang,
                    },
                )
        elif isinstance(file, (DataFile, DuplicatedImageFile)):
            # Topic data assets and duplicated images accompany every output
            # variant: the build copies them under each (language, format, kind)
            # output dir at ``output_dir/relative_path``, so we enumerate the
            # same way. DataFiles excluded from output (e.g. HTTP-replay
            # cassettes) are skipped, mirroring ``get_processing_operation``.
            if isinstance(file, DataFile) and is_ignored_file_for_output(file.path):
                continue
            asset_format = "image" if isinstance(file, DuplicatedImageFile) else "data"
            for lang, _fmt, _kind, output_dir in output_specs(
                course, target.output_root, target=target
            ):
                try:
                    out_path = file.output_dir(output_dir, lang) / file.relative_path
                except (KeyError, ValueError) as e:
                    logger.debug("provenance: skip asset %s (%s): %s", file.path, lang, e)
                    continue
                yield (
                    out_path,
                    {
                        "section_id": section.id,
                        "topic_id": topic.id,
                        "kind": None,
                        "format": asset_format,
                        "language": lang,
                    },
                )
        # SharedImageFile (image_mode="shared") and the source-tree-only
        # PlantUML/DrawIo diagrams are intentionally not enumerated here; see
        # the function docstring.

    yield from _enumerate_dir_group_outputs(course, target)


def _enumerate_dir_group_outputs(
    course: Course, target: OutputTarget
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield existing dir-group output files with (section, topic) ownership.

    Dir-groups copy whole directories, so — unlike the per-file enumeration —
    we walk each plausible output directory (every language × public/speaker)
    and record the files actually found on disk; placements the build did not
    produce simply have no directory. Ownership comes from the recorded
    ``DirGroup.spec`` (``None``/``None`` for a global ``<dir-groups>`` entry).
    """
    for dir_group in course.dir_groups:
        spec = dir_group.spec
        section_id = spec.section_id if spec is not None else None
        topic_id = spec.topic_id if spec is not None else None
        for lang in target.languages:
            for is_speaker in (False, True):
                for out_dir in dir_group.output_dirs(
                    is_speaker, lang, target.output_root, skip_toplevel=target.is_explicit
                ):
                    if not out_dir.is_dir():
                        continue
                    for path in sorted(out_dir.rglob("*")):
                        if path.is_file():
                            yield (
                                path,
                                {
                                    "section_id": section_id,
                                    "topic_id": topic_id,
                                    "kind": None,
                                    "format": "dir-group",
                                    "language": lang,
                                },
                            )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def build_provenance_manifest(
    course: Course,
    target: OutputTarget,
    *,
    source_commit: str | None,
    source_dirty: bool | None,
    built_at: str,
    spec_name: str | None = None,
) -> dict[str, Any]:
    """Build the manifest dict for a single output *target*.

    Records only output files that actually exist on disk, hashing each.
    Entries are sorted by path so the manifest is deterministic and diffs
    cleanly across builds.
    """
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for out_path, record in enumerate_expected_outputs(course, target):
        try:
            rel = out_path.relative_to(target.output_root).as_posix()
        except ValueError:
            continue
        if rel in seen or not out_path.is_file():
            continue
        seen.add(rel)
        files.append(
            {
                "path": rel,
                "section_id": record["section_id"],
                "topic_id": record["topic_id"],
                "kind": record["kind"],
                "format": record["format"],
                "language": record["language"],
                "content_hash": _hash_file(out_path),
            }
        )
    files.sort(key=lambda r: r["path"])
    return {
        "version": MANIFEST_VERSION,
        "spec": spec_name,
        "target": target.name,
        "source_commit": source_commit,
        "source_dirty": source_dirty,
        "built_at": built_at,
        "files": files,
    }


def write_provenance_manifests(
    course: Course,
    *,
    source_commit: str | None,
    source_dirty: bool | None,
    built_at: str,
    spec_name: str | None = None,
) -> list[Path]:
    """Write one ``.clm-manifest.json`` per built output target.

    Targets whose ``output_root`` does not exist (e.g. a target that produced
    nothing) are skipped. Returns the list of manifest paths written.
    """
    written: list[Path] = []
    for target in course.output_targets:
        out_root = target.output_root
        if not out_root.exists():
            continue
        manifest = build_provenance_manifest(
            course,
            target,
            source_commit=source_commit,
            source_dirty=source_dirty,
            built_at=built_at,
            spec_name=spec_name,
        )
        manifest_path = out_root / MANIFEST_FILENAME
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written.append(manifest_path)
        logger.info(
            "Wrote provenance manifest %s (%d files)", manifest_path, len(manifest["files"])
        )
    return written


def load_manifest(path: Path) -> dict[str, Any]:
    """Load a ``.clm-manifest.json`` written by :func:`write_provenance_manifests`."""
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def manifest_files_by_topic(
    manifest: dict[str, Any],
) -> dict[str | None, list[dict[str, Any]]]:
    """Group a manifest's ``files`` by ``topic_id``.

    The key ``None`` collects skeleton/global files (e.g. global ``<dir-groups>``)
    that are not owned by any topic.
    """
    by_topic: dict[str | None, list[dict[str, Any]]] = {}
    for entry in manifest.get("files", []):
        by_topic.setdefault(entry.get("topic_id"), []).append(entry)
    return by_topic
