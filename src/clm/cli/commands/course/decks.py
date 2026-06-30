"""``clm course decks`` and ``clm slides referenced-by``.

Spec → deck resolution (gap #1). ``spec decks`` answers "which decks does this
spec build?"; ``referenced-by`` is the reverse lookup. Both delegate to
:mod:`clm.core.spec_decks`, which mirrors the build's resolution semantics so the
shipping set is correct (a ``<topic>`` resolves to a directory and CLM builds
**every** ``slides_*.<ext>`` in it — filename-stem heuristics silently miss decks).
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import CourseSpec, CourseSpecError
from clm.core.spec_decks import (
    SpecDeckResolution,
    resolve_spec_decks,
)
from clm.core.topic_resolver import build_topic_map
from clm.slides.pairing import split_lang_tag


def _slides_dir(data_dir: Path | None, spec_file: Path) -> Path:
    """The ``slides/`` directory for *spec_file* (``--data-dir`` overrides)."""
    course_root, _ = resolve_course_paths(spec_file, data_dir)
    return course_root / "slides"


def _deck_matches_lang(deck: Path, lang: str) -> bool:
    """Whether *deck* serves *lang* (``de`` / ``en`` / ``both``).

    Bilingual decks (no ``.de``/``.en`` tag) serve both languages; a split half
    serves only its own language.
    """
    if lang == "both":
        return True
    tag = split_lang_tag(deck)
    return tag is None or tag == lang


def _rel(path: Path, base: Path) -> str:
    """Display *path* relative to *base* when possible, else absolute."""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


@click.command("decks")
@click.argument(
    "spec_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=False,
)
@click.option(
    "--all-specs",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Resolve the union 'shipping set' across every *.xml spec in this "
    "directory, annotating each deck with the spec(s) that reference it.",
)
@click.option(
    "--lang",
    type=click.Choice(["de", "en", "both"]),
    default="both",
    show_default=True,
    help="Keep only decks serving this language (bilingual decks serve both).",
)
@click.option(
    "--data-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Course data directory (contains slides/). Default: inferred from the "
    "spec file (its grandparent).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output as JSON. Adds a per-topic ``topics`` array — each entry "
    "carries its ``section``, ``resolved_module`` and ``slide_files`` (the "
    "source ``.py`` decks), plus first-occurrence-shadowed duplicates and "
    "unresolved topics. This is the section -> source-file mapping in one call.",
)
def spec_decks_cmd(
    spec_file: Path | None,
    all_specs: Path | None,
    lang: str,
    data_dir: Path | None,
    as_json: bool,
) -> None:
    """List the deck files a course spec pulls in.

    Resolution mirrors the build exactly: a ``<topic>`` resolves to a topic
    *directory* and **every** ``slides_*.<ext>`` in it is a deck (module-bound
    references pick their module; unbound duplicates are first-occurrence-wins).
    This is the reliable way to compute a spec's "shipping set" — do not guess
    from deck filenames.

    Plain output is one deck path per line. ``--json`` additionally emits a
    per-topic ``topics`` array keyed by ``section`` with each topic's
    ``slide_files`` — i.e. the **section -> source-``.py``-deck mapping**, with
    no need to parse the spec XML by hand. (``clm export outline --format json``
    gives the same mapping grouped by section and annotated with deck titles.)

    \b
    Examples:
        clm course decks course-specs/python.xml
        clm course decks course-specs/python.xml --lang de --json
        clm course decks --all-specs course-specs/
    """
    if all_specs is not None and spec_file is not None:
        raise click.UsageError("Pass either SPEC_FILE or --all-specs, not both.")
    if all_specs is None and spec_file is None:
        raise click.UsageError("Provide a SPEC_FILE or --all-specs DIR.")

    if all_specs is not None:
        _run_all_specs(all_specs, lang, data_dir, as_json)
        return

    assert spec_file is not None
    slides_dir = _slides_dir(data_dir, spec_file)
    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        raise click.ClickException(f"Failed to parse course spec: {e}") from None

    resolution = resolve_spec_decks(spec, slides_dir)
    decks = [d for d in resolution.deck_files if _deck_matches_lang(d, lang)]

    if as_json:
        click.echo(json.dumps(_resolution_to_dict(resolution, decks, lang), indent=2))
        return

    base = slides_dir.parent
    for deck in decks:
        click.echo(_rel(deck, base))
    if resolution.unresolved:
        click.echo(
            f"\nWARNING: {len(resolution.unresolved)} topic reference(s) "
            "resolved to no directory on disk:",
            err=True,
        )
        for topic in resolution.unresolved:
            where = topic.requested_module or "any module"
            click.echo(f"  {topic.topic_id} ({where}) — section {topic.section!r}", err=True)


def _run_all_specs(
    specs_dir: Path,
    lang: str,
    data_dir: Path | None,
    as_json: bool,
) -> None:
    """Resolve the union shipping set across every spec in *specs_dir*."""
    spec_files = sorted(specs_dir.glob("*.xml"))
    if not spec_files:
        raise click.ClickException(f"No *.xml specs found in {specs_dir}")

    # All specs in one directory share a course root, so scan the filesystem once.
    slides_dir = _slides_dir(data_dir, spec_files[0])
    topic_map = build_topic_map(slides_dir)
    base = slides_dir.parent

    deck_to_specs: dict[Path, set[str]] = {}
    parse_errors: list[tuple[Path, str]] = []
    for spec_file in spec_files:
        try:
            spec = CourseSpec.from_file(spec_file)
        except CourseSpecError as e:
            parse_errors.append((spec_file, str(e)))
            continue
        resolution = resolve_spec_decks(spec, slides_dir, topic_map=topic_map)
        for deck in resolution.deck_files:
            if _deck_matches_lang(deck, lang):
                deck_to_specs.setdefault(deck, set()).add(spec_file.name)

    ordered = sorted(deck_to_specs, key=lambda p: str(p))

    if as_json:
        payload = {
            "specs_dir": str(specs_dir),
            "slides_dir": str(slides_dir),
            "lang": lang,
            "deck_count": len(ordered),
            "decks": [
                {"path": str(deck), "specs": sorted(deck_to_specs[deck])} for deck in ordered
            ],
            "parse_errors": [{"spec": str(p), "error": e} for p, e in parse_errors],
        }
        click.echo(json.dumps(payload, indent=2))
        return

    for deck in ordered:
        specs = ", ".join(sorted(deck_to_specs[deck]))
        click.echo(f"{_rel(deck, base)}\t{specs}")
    if parse_errors:
        click.echo(f"\nWARNING: {len(parse_errors)} spec(s) failed to parse:", err=True)
        for spec_file, err in parse_errors:
            click.echo(f"  {spec_file.name}: {err}", err=True)


def _resolution_to_dict(resolution: SpecDeckResolution, decks: list[Path], lang: str) -> dict:
    return {
        "spec": str(resolution.spec_path),
        "slides_dir": str(resolution.slides_dir),
        "lang": lang,
        "deck_count": len(decks),
        "decks": [str(d) for d in decks],
        "topics": [
            {
                "topic_id": t.topic_id,
                "section": t.section,
                "requested_module": t.requested_module,
                "resolved_module": t.resolved_module,
                "path": str(t.path) if t.path else None,
                "found": t.found,
                "slide_files": [str(d) for d in t.slide_files],
                "shadowed": [str(m.path) for m in t.shadowed],
            }
            for t in resolution.topics
        ],
        "unresolved": [t.topic_id for t in resolution.unresolved],
    }
