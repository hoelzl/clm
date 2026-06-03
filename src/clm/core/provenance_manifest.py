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

from clm.core.course_files.notebook_file import NotebookFile
from clm.infrastructure.utils.path_utils import ext_for, output_specs

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

    Only notebook-derived outputs are enumerated for now. Topic-specific
    assets and dir-group outputs are a follow-up (issue #208, step 1).
    """
    for file in course.files:
        if not isinstance(file, NotebookFile):
            # TODO(#208 step 1): topic assets (images/data) and topic-scoped
            # dir-groups, keyed by their owning (section_id, topic_id).
            continue
        topic = file.topic
        section = topic.section
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
                logger.debug("provenance: skip %s (%s/%s/%s): %s", file.path, lang, fmt, kind, e)
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
