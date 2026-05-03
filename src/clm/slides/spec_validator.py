"""Course spec validation.

Checks a course specification XML file for consistency against the
filesystem (unresolved topics, ambiguous topics, duplicate references,
missing dir-group paths) and returns structured findings.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from clm.core.course_spec import CourseSpec
from clm.core.topic_resolver import build_topic_map, matches_for_binding


@dataclass
class SpecFinding:
    """A single validation finding."""

    severity: str  # "error", "warning", "info"
    type: str  # "unresolved_topic", "ambiguous_topic", etc.
    message: str
    topic_id: str | None = None
    section: str | None = None
    suggestion: str = ""
    matches: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)


@dataclass
class SpecValidationResult:
    """Result of validating a course spec."""

    course_spec: str
    topics_total: int
    findings: list[SpecFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[SpecFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[SpecFinding]:
        return [f for f in self.findings if f.severity == "warning"]


def validate_spec(
    course_spec_path: Path,
    slides_dir: Path,
    *,
    include_disabled: bool = False,
) -> SpecValidationResult:
    """Validate a course spec XML file for consistency.

    Checks:
    - Unresolved topics (with near-match suggestions)
    - Ambiguous topics (same ID in multiple modules)
    - Duplicate topic references within the spec
    - Missing dir-group paths
    - Empty sections

    Args:
        course_spec_path: Path to the course spec XML file.
        slides_dir: Path to the ``slides/`` directory.
        include_disabled: If True, also validate sections marked
            ``enabled="false"``. Each finding from a disabled section has
            ``(disabled)`` appended to its ``message`` so callers can tell
            which findings come from deferred roadmap content. Default:
            False (disabled sections are dropped at parse time and
            therefore invisible to validation, which is the desired
            behavior for the fast build path).

    Returns:
        A :class:`SpecValidationResult` with all findings.

    Raises:
        CourseSpecError: If the spec file cannot be parsed.
    """
    spec = CourseSpec.from_file(course_spec_path, keep_disabled=include_disabled)
    topic_map = build_topic_map(slides_dir)
    all_topic_ids = list(topic_map.keys())

    # Set of module directory names (the immediate children of slides/).
    # Used to validate ``module=`` attributes on sections and topics.
    available_modules: set[str] = set()
    if slides_dir.is_dir():
        available_modules = {p.name for p in slides_dir.iterdir() if p.is_dir()}

    findings: list[SpecFinding] = []
    topics_total = sum(len(s.topics) for s in spec.sections)

    # Track seen topic IDs for duplicate detection: topic_id -> list of section names
    seen_topics: dict[str, list[str]] = {}
    # Track which (topic_id, module) pairs have been bound so the
    # cross-section "duplicate reference" warning can ignore deliberate
    # cohort duplication (same topic ID in two different modules).
    seen_bound: dict[tuple[str, str | None], list[str]] = {}

    def _suffix(section_is_disabled: bool, msg: str) -> str:
        return f"{msg} (disabled)" if section_is_disabled else msg

    for section in spec.sections:
        section_name = section.name.en or section.name.de
        section_disabled = not section.enabled

        # Validate section-level module binding (if any).
        if section.module and section.module not in available_modules:
            findings.append(
                SpecFinding(
                    severity="error",
                    type="unknown_module",
                    section=section_name,
                    message=_suffix(
                        section_disabled,
                        f"Section '{section_name}' references unknown module '{section.module}'",
                    ),
                    suggestion=(
                        "Check the module attribute on this section. The value "
                        "must be the literal module directory name under slides/ "
                        "(e.g., 'module_545_ml_azav_cohort_2026_04')."
                    ),
                )
            )

        # Empty section check
        if not section.topics:
            findings.append(
                SpecFinding(
                    severity="warning",
                    type="empty_section",
                    section=section_name,
                    message=_suffix(
                        section_disabled,
                        f"Section '{section_name}' contains no topics",
                    ),
                )
            )
            continue

        for topic_spec in section.topics:
            tid = topic_spec.id
            # Effective module: per-topic override beats section default.
            effective_module = section.module_for(topic_spec)

            # Track for duplicate detection. Key by (id, module) so the
            # same topic ID resolved in two different modules is treated
            # as two distinct bindings, not a duplicate reference.
            seen_topics.setdefault(tid, []).append(section_name)
            seen_bound.setdefault((tid, effective_module), []).append(section_name)

            # Validate per-topic module if it differs from the section default.
            if (
                topic_spec.module
                and topic_spec.module != section.module
                and topic_spec.module not in available_modules
            ):
                findings.append(
                    SpecFinding(
                        severity="error",
                        type="unknown_module",
                        topic_id=tid,
                        section=section_name,
                        message=_suffix(
                            section_disabled,
                            f"Topic '{tid}' references unknown module '{topic_spec.module}'",
                        ),
                    )
                )
                continue

            # Resolution check, honoring the effective module if set.
            matches = matches_for_binding(topic_map, tid, effective_module)

            if not matches:
                # Unresolved — try to suggest near matches
                suggestion = ""
                close = difflib.get_close_matches(tid, all_topic_ids, n=1, cutoff=0.6)
                if close:
                    near = topic_map[close[0]]
                    suggestion = f"Did you mean '{close[0]}'? Found: {near[0].path}"

                msg = (
                    f"Topic '{tid}' not found in module '{effective_module}'"
                    if effective_module
                    else f"Topic '{tid}' does not match any topic directory or file"
                )
                findings.append(
                    SpecFinding(
                        severity="error",
                        type="unresolved_topic",
                        topic_id=tid,
                        section=section_name,
                        message=_suffix(section_disabled, msg),
                        suggestion=suggestion,
                    )
                )

            elif len(matches) > 1:
                # Ambiguous — same ID in multiple modules. Only fires for
                # unbound references; module-bound references will have
                # filtered down to one match (or zero, handled above).
                match_paths = [str(m.path) for m in matches]
                findings.append(
                    SpecFinding(
                        severity="error",
                        type="ambiguous_topic",
                        topic_id=tid,
                        section=section_name,
                        message=_suffix(
                            section_disabled,
                            f"Topic '{tid}' matches multiple directories across modules",
                        ),
                        matches=match_paths,
                        suggestion=(
                            "Bind the section or topic to a specific module with "
                            'module="...", or move one variant to a different name'
                        ),
                    )
                )

    # Duplicate topic references (same topic resolved to the same target
    # in multiple sections). Module-bound references that share an ID but
    # point at different modules are intentionally NOT flagged here —
    # cohort archives commonly do exactly that.
    for (tid, mod), section_names in seen_bound.items():
        if len(section_names) > 1:
            qualifier = f" (module: {mod})" if mod else ""
            findings.append(
                SpecFinding(
                    severity="warning",
                    type="duplicate_topic",
                    topic_id=tid,
                    sections=section_names,
                    message=(f"Topic '{tid}'{qualifier} is referenced in multiple sections"),
                )
            )

    # Dir-group path checks
    course_root = slides_dir.parent
    for dg in spec.dictionaries:
        dg_path = course_root / dg.path
        if not dg_path.exists():
            findings.append(
                SpecFinding(
                    severity="warning",
                    type="missing_dir_group",
                    message=f"Dir-group path does not exist: {dg.path}",
                )
            )

    return SpecValidationResult(
        course_spec=str(course_spec_path),
        topics_total=topics_total,
        findings=findings,
    )
