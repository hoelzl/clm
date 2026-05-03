"""Course authoring rules lookup.

Reads per-course ``.authoring.md`` files from the ``course-specs/``
directory and returns merged rules (common + course-specific) for a
given course spec or slide file path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from clm.core.course_spec import CourseSpec, CourseSpecError
from clm.core.topic_resolver import build_topic_map

logger = logging.getLogger(__name__)


@dataclass
class AuthoringRulesResult:
    """Result of looking up authoring rules."""

    common_rules: str | None = None
    course_rules: list[CourseRulesEntry] = field(default_factory=list)
    merged: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class CourseRulesEntry:
    """Authoring rules for a single course."""

    course_spec: str
    rules: str


def get_authoring_rules(
    data_dir: Path,
    *,
    course_spec: str | None = None,
    slide_path: str | None = None,
) -> AuthoringRulesResult:
    """Return merged authoring rules for a course or slide file.

    At least one of *course_spec* or *slide_path* must be provided.

    Args:
        data_dir: Root data directory (contains ``course-specs/``,
            ``slides/``).
        course_spec: Course spec path or slug (e.g.,
            ``"machine-learning-azav"`` or
            ``"course-specs/ml-azav.xml"``).
        slide_path: Path to a slide file.  Resolved to the course(s)
            that reference the containing topic.

    Returns:
        An :class:`AuthoringRulesResult` with merged markdown text.
    """
    if not course_spec and not slide_path:
        result = AuthoringRulesResult()
        result.notes.append("At least one of course_spec or slide_path must be provided.")
        return result

    specs_dir = data_dir / "course-specs"

    # Load common rules
    common_rules = _read_common_rules(specs_dir)

    # Determine which course(s) to load rules for
    if course_spec:
        course_names = _resolve_course_spec_names(course_spec, specs_dir)
    else:
        assert slide_path is not None
        course_names = _resolve_slide_to_courses(slide_path, data_dir)

    result = AuthoringRulesResult(common_rules=common_rules)

    if not course_names:
        result.notes.append("No matching course spec found.")

    # Load course-specific rules
    for name in course_names:
        authoring_path = specs_dir / f"{name}.authoring.md"
        if authoring_path.is_file():
            rules_text = authoring_path.read_text(encoding="utf-8")
            result.course_rules.append(CourseRulesEntry(course_spec=name, rules=rules_text))
        else:
            result.notes.append(
                f"No authoring rules file found for course '{name}' "
                f"(expected {authoring_path.name})."
            )

    # Build merged text
    result.merged = _build_merged_text(common_rules, result.course_rules)
    return result


def _read_common_rules(specs_dir: Path) -> str | None:
    """Read ``_common.authoring.md`` if it exists."""
    common_path = specs_dir / "_common.authoring.md"
    if common_path.is_file():
        return common_path.read_text(encoding="utf-8")
    return None


def _resolve_course_spec_names(course_spec: str, specs_dir: Path) -> list[str]:
    """Resolve a course_spec argument to a list of course base names.

    Accepts:
    - A slug like ``"machine-learning-azav"`` → looks for
      ``machine-learning-azav.xml`` in specs_dir
    - A path (absolute or relative) to an XML file → extracts the stem
    """
    spec_path = Path(course_spec)

    # If it's an absolute path or has an XML suffix, use it directly
    if spec_path.is_absolute() or spec_path.suffix == ".xml":
        if spec_path.is_absolute() and spec_path.is_file():
            return [spec_path.stem]
        # Try relative to specs_dir
        candidate = specs_dir / spec_path.name
        if candidate.is_file():
            return [candidate.stem]
        # Try the stem as-is (the slug might just not have an XML file)
        return [spec_path.stem]

    # Bare slug: check if corresponding XML exists
    xml_path = specs_dir / f"{course_spec}.xml"
    if xml_path.is_file():
        return [course_spec]

    # Still return the slug so we can report "no authoring rules found"
    return [course_spec]


def _resolve_slide_to_courses(slide_path: str, data_dir: Path) -> list[str]:
    """Resolve a slide file path to the course(s) that reference its topic.

    Walks up from the slide file to find the topic directory, extracts the
    topic ID *and module*, then scans all course spec XML files to find
    which ones bind that topic to that module (or reference it without a
    module binding).
    """
    slides_dir = data_dir / "slides"
    specs_dir = data_dir / "course-specs"

    slide = Path(slide_path)
    if not slide.is_absolute():
        slide = data_dir / slide

    located = _locate_topic_for_path(slide, slides_dir)
    if not located:
        return []
    topic_id, module = located

    return _find_courses_with_topic(topic_id, module, specs_dir)


def _locate_topic_for_path(slide: Path, slides_dir: Path) -> tuple[str, str] | None:
    """Find the (topic_id, module) for the given slide file.

    Walks up from the slide path to find a topic directory; the parent of
    that directory is the module directory under ``slides/``. Returning
    the module is what lets the caller distinguish a slide in the live
    module from the same-named slide in a frozen-cohort archive.
    """
    from clm.infrastructure.utils.path_utils import simplify_ordered_name

    topic_map = build_topic_map(slides_dir)

    # Typical structure: slides/module_XXX/topic_YYY/slides_foo.py
    candidate = slide.parent
    resolved_slide = slide.resolve()

    # Walk up at most 3 levels (file → topic → module → slides)
    for _ in range(3):
        name = simplify_ordered_name(candidate.name)
        if name and name in topic_map:
            for match in topic_map[name]:
                if resolved_slide == match.path.resolve() or _is_under(resolved_slide, match.path):
                    return name, match.module
        candidate = candidate.parent
        if candidate == slides_dir or candidate == slides_dir.parent:
            break

    return None


def _is_under(child: Path, parent: Path) -> bool:
    """Check if *child* is under *parent* directory."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _find_courses_with_topic(topic_id: str, module: str, specs_dir: Path) -> list[str]:
    """Scan course specs for ones that reference *topic_id* in *module*.

    A spec is considered to reference the slide if it has either a
    binding ``(topic_id, module)`` (explicit cohort binding) or a binding
    ``(topic_id, None)`` (unbound — first-occurrence-wins resolution may
    land on this slide). Specs that bind ``topic_id`` to a different
    module are correctly excluded.
    """
    if not specs_dir.is_dir():
        return []

    courses: list[str] = []
    for xml_file in sorted(specs_dir.glob("*.xml")):
        try:
            spec = CourseSpec.from_file(xml_file)
        except CourseSpecError:
            logger.debug("Skipping unparseable spec: %s", xml_file)
            continue

        bindings = spec.topic_bindings()
        if (topic_id, module) in bindings or (topic_id, None) in bindings:
            courses.append(xml_file.stem)

    return courses


def _build_merged_text(
    common_rules: str | None,
    course_entries: list[CourseRulesEntry],
) -> str:
    """Build the merged markdown output."""
    parts: list[str] = []

    if common_rules:
        parts.append("# Common Authoring Rules\n")
        parts.append(common_rules.rstrip())

    for entry in course_entries:
        parts.append(f"\n\n# Course: {entry.course_spec}\n")
        parts.append(entry.rules.rstrip())

    if not parts:
        return "No authoring rules found."

    return "\n".join(parts) + "\n"
