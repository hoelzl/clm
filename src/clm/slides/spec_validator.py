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
from clm.core.topic_resolver import build_topic_map


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

    Returns:
        A :class:`SpecValidationResult` with all findings.

    Raises:
        CourseSpecError: If the spec file cannot be parsed.
    """
    spec = CourseSpec.from_file(course_spec_path)
    topic_map = build_topic_map(slides_dir)
    all_topic_ids = list(topic_map.keys())

    findings: list[SpecFinding] = []
    topics_total = sum(len(s.topics) for s in spec.sections)

    # Track seen topic IDs for duplicate detection: topic_id -> list of section names
    seen_topics: dict[str, list[str]] = {}

    for section in spec.sections:
        section_name = section.name.en or section.name.de

        # Empty section check
        if not section.topics:
            findings.append(
                SpecFinding(
                    severity="warning",
                    type="empty_section",
                    section=section_name,
                    message=f"Section '{section_name}' contains no topics",
                )
            )
            continue

        for topic_spec in section.topics:
            tid = topic_spec.id

            # Track for duplicate detection
            seen_topics.setdefault(tid, []).append(section_name)

            # Resolution check
            matches = topic_map.get(tid, [])

            if not matches:
                # Unresolved — try to suggest near matches
                suggestion = ""
                close = difflib.get_close_matches(tid, all_topic_ids, n=1, cutoff=0.6)
                if close:
                    near = topic_map[close[0]]
                    suggestion = f"Did you mean '{close[0]}'? Found: {near[0].path}"

                findings.append(
                    SpecFinding(
                        severity="error",
                        type="unresolved_topic",
                        topic_id=tid,
                        section=section_name,
                        message=f"Topic '{tid}' does not match any topic directory or file",
                        suggestion=suggestion,
                    )
                )

            elif len(matches) > 1:
                # Ambiguous — same ID in multiple modules
                match_paths = [str(m.path) for m in matches]
                findings.append(
                    SpecFinding(
                        severity="error",
                        type="ambiguous_topic",
                        topic_id=tid,
                        section=section_name,
                        message=(f"Topic '{tid}' matches multiple directories across modules"),
                        matches=match_paths,
                        suggestion=(
                            "Qualify the topic ID to make it unique, "
                            "or move one variant to a different name"
                        ),
                    )
                )

    # Duplicate topic references (same topic in multiple sections)
    for tid, section_names in seen_topics.items():
        if len(section_names) > 1:
            findings.append(
                SpecFinding(
                    severity="warning",
                    type="duplicate_topic",
                    topic_id=tid,
                    sections=section_names,
                    message=f"Topic '{tid}' is referenced in multiple sections",
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
