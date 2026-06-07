"""Course spec validation.

Checks a course specification XML file for consistency against the
filesystem (unresolved topics, ambiguous topics, duplicate references,
missing dir-group paths) and returns structured findings.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

from clm.core.course_spec import WEEKDAY_ORDER, CourseSpec, IncludeSpec, SectionSpec
from clm.core.include_ledger import LEDGER_NAME, Ledger
from clm.core.topic_resolver import (
    TopicMatch,
    build_topic_map,
    find_slide_files,
    matches_for_binding,
)


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
    - ``<include>`` source missing / shadowed / inside a topic dir
    - ``<include>`` dependencies (info) and section-level inheritance (info)

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

    # Subsection (day-of-week) checks (issue #261).
    _validate_subsections(
        spec=spec,
        topic_map=topic_map,
        findings=findings,
        suffix=_suffix,
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

    # <include> checks (see docs/claude/design/shared-source-includes-and-output-dedup.md).
    _validate_includes(
        spec=spec,
        topic_map=topic_map,
        course_root=course_root,
        findings=findings,
        suffix=_suffix,
    )

    # Cross-reference checks (Issue #17): scan included slide files for
    # ``clm:`` references and verify each target topic is included.
    _validate_cross_references(
        spec=spec,
        topic_map=topic_map,
        findings=findings,
        suffix=_suffix,
    )

    return SpecValidationResult(
        course_spec=str(course_spec_path),
        topics_total=topics_total,
        findings=findings,
    )


def _validate_subsections(
    *,
    spec: CourseSpec,
    topic_map: dict[str, list[TopicMatch]],
    findings: list[SpecFinding],
    suffix: Callable[[bool, str], str],
) -> None:
    """Emit findings for ``<subsection>`` day-of-week groupings (issue #261).

    Four checks, per the design:

    1. ``duplicate_weekday`` (warning) — the same weekday token appears on
       more than one subsection within a single section.
    2. ``weekday_out_of_order`` (warning) — weekday tokens within a section
       are not in canonical Mon→Sun order.
    3. ``empty_day`` (warning) — a subsection that carries no ``<topic>``,
       or whose resolvable topics produce zero slide decks.
    4. ``unscheduled_topics`` (info) — a section mixes bare ``<topic>``s
       (which appear under no day) with ``<subsection>``s.

    Only enabled subsections are checked for 1–3 (a disabled subsection is
    roadmap content, like a disabled section). All checks are
    informational/advisory: none is an error.
    """
    for section in spec.sections:
        section_name = section.name.en or section.name.de
        section_disabled = not section.enabled

        enabled_subs = [sub for sub in section.subsections if sub.enabled]
        if not enabled_subs:
            # No (enabled) subsections — nothing day-related to check.
            # Check 4 still needs the *structure*; but with no enabled
            # subsections there is no day to schedule against, so there are
            # no "unscheduled" topics to report.
            continue

        # --- Checks 1 & 2: weekday duplicates and ordering ---
        weekdays = [sub.weekday for sub in enabled_subs if sub.weekday is not None]

        seen: set[str] = set()
        reported_dup: set[str] = set()
        for wd in weekdays:
            if wd in seen and wd not in reported_dup:
                reported_dup.add(wd)
                findings.append(
                    SpecFinding(
                        severity="warning",
                        type="duplicate_weekday",
                        section=section_name,
                        message=suffix(
                            section_disabled,
                            f"Section '{section_name}' assigns weekday "
                            f"'{wd}' to more than one subsection.",
                        ),
                        suggestion=(
                            "Merge the subsections that share a weekday, or "
                            "correct the weekday on one of them."
                        ),
                    )
                )
            seen.add(wd)

        prev_index = -1
        reported_ooo: set[str] = set()
        for wd in weekdays:
            index = WEEKDAY_ORDER.index(wd)
            # Report each offending weekday once (mirrors the duplicate check),
            # otherwise a weekday that is both out of order and repeated would
            # emit one identical finding per occurrence.
            if index < prev_index and wd not in reported_ooo:
                reported_ooo.add(wd)
                findings.append(
                    SpecFinding(
                        severity="warning",
                        type="weekday_out_of_order",
                        section=section_name,
                        message=suffix(
                            section_disabled,
                            f"Section '{section_name}': weekday '{wd}' appears "
                            f"out of order (expected Mon→Sun order).",
                        ),
                        suggestion=(
                            "Reorder the <subsection> elements so weekdays run "
                            "Monday through Sunday."
                        ),
                    )
                )
            prev_index = max(prev_index, index)

        # --- Check 3: empty days ---
        for sub in enabled_subs:
            label = sub.weekday or (sub.name.en or sub.name.de if sub.name else "") or "(unnamed)"
            if not sub.topics:
                findings.append(
                    SpecFinding(
                        severity="warning",
                        type="empty_day",
                        section=section_name,
                        message=suffix(
                            section_disabled,
                            f"Section '{section_name}': subsection '{label}' contains no topics.",
                        ),
                    )
                )
                continue
            resolved_any = False
            deck_count = 0
            for topic_spec in sub.topics:
                matches = matches_for_binding(
                    topic_map, topic_spec.id, section.module_for(topic_spec)
                )
                if len(matches) == 1:
                    resolved_any = True
                    deck_count += len(find_slide_files(matches[0].path))
            # Only flag "resolves to zero decks" when at least one topic
            # actually resolved — otherwise the unresolved_topic errors above
            # already explain the emptiness and this would be noise.
            if resolved_any and deck_count == 0:
                findings.append(
                    SpecFinding(
                        severity="warning",
                        type="empty_day",
                        section=section_name,
                        message=suffix(
                            section_disabled,
                            f"Section '{section_name}': subsection '{label}' "
                            f"resolves to zero slide decks.",
                        ),
                    )
                )

        # --- Check 4: bare topics mixed with subsections (info) ---
        # Key by object identity, not topic id: parse_sections appends the very
        # same TopicSpec objects into both section.topics and the subsections,
        # so identity cleanly separates bare topics even when a bare topic
        # happens to share an id with a subsection topic.
        subsection_topic_obj_ids = {id(t) for sub in section.subsections for t in sub.topics}
        bare_ids = [t.id for t in section.topics if id(t) not in subsection_topic_obj_ids]
        if bare_ids:
            listed = ", ".join(bare_ids)
            findings.append(
                SpecFinding(
                    severity="info",
                    type="unscheduled_topics",
                    section=section_name,
                    message=suffix(
                        section_disabled,
                        f"Section '{section_name}' mixes {len(bare_ids)} bare "
                        f"topic(s) with subsections; these appear under no "
                        f"weekday: {listed}.",
                    ),
                    suggestion=(
                        "Move each bare topic into a <subsection> to give it a "
                        "day, or leave it as a deliberately unscheduled topic."
                    ),
                )
            )


@dataclass
class _IncludeDependencies:
    """Parsed ``[project] dependencies`` for an include's pyproject.toml."""

    pyproject_rel: str
    deps: list[str]


def _validate_includes(
    *,
    spec: CourseSpec,
    topic_map: dict[str, list[TopicMatch]],
    course_root: Path,
    findings: list[SpecFinding],
    suffix: Callable[[bool, str], str],
) -> None:
    """Emit findings for every ``<include>`` declared in the spec.

    Categories produced:

    * ``include_source_missing`` — error when ``source`` does not exist
      under the course root and the include is not ``optional``.
    * ``include_shadowed`` — warning when a real file/directory already
      occupies ``topic.path / as_path`` (mirrors the build-time
      ``include_shadowed_by_local`` warning so authors find it without
      doing a full build).
    * ``include_source_is_topic_dir`` — warning when ``source`` resolves
      into ``slides/.../topic_*``. Allowed but fragile.
    * ``include_dependencies`` — info-level: one per unique source,
      listing the include's ``pyproject.toml`` ``[project] dependencies``
      so authors can confirm the worker environment satisfies them.
    * ``include_section_inheritance`` — info-level: per section-level
      include, lists every topic that inherits it (and any topic that
      overrides it with a different source).

    The intra-topic ``include_target_collision`` case (two ``<include>``
    elements on the same parent sharing the same ``as_path``) is enforced
    at parse time by :func:`clm.core.course_spec._parse_includes` and
    surfaces as a :class:`CourseSpecError` before this validator runs.
    """
    # One info per unique include source for the dependencies probe.
    seen_dep_sources: set[str] = set()
    # One warning per unique source for the topic-dir check: it's a
    # property of the source, not the using topic.
    seen_topic_dir_sources: set[str] = set()

    for section in spec.sections:
        section_name = section.name.en or section.name.de
        section_disabled = not section.enabled

        # Section-level inheritance audit, one finding per section-level
        # default. Iterates only ``section.includes`` (not the merged
        # effective list) because inheritance only exists when the
        # default is declared at section level.
        for sec_inc in section.includes:
            _emit_section_inheritance(
                section=section,
                sec_inc=sec_inc,
                section_disabled=section_disabled,
                findings=findings,
                suffix=suffix,
            )

        for topic_spec in section.topics:
            effective_module = section.module_for(topic_spec)
            matches = matches_for_binding(topic_map, topic_spec.id, effective_module)
            # We only have a meaningful topic.path when resolution
            # produced exactly one match — ambiguous/unresolved topics
            # are already flagged above and a shadow check against an
            # unknown directory would be misleading.
            topic_path = matches[0].path if len(matches) == 1 else None

            for inc in section.includes_for(topic_spec):
                source_path = course_root / inc.source
                source_exists = source_path.exists()

                if not source_exists and not inc.optional:
                    findings.append(
                        SpecFinding(
                            severity="error",
                            type="include_source_missing",
                            topic_id=topic_spec.id,
                            section=section_name,
                            message=suffix(
                                section_disabled,
                                f"Topic '{topic_spec.id}': include source "
                                f"'{inc.source}' (as '{inc.as_path}') does "
                                f"not exist under the course root.",
                            ),
                            suggestion=(
                                "Create the file/directory, fix the 'source' "
                                'attribute, or set optional="true" if the '
                                "include is intentionally optional."
                            ),
                        )
                    )

                if topic_path is not None and source_exists:
                    shadow_path = topic_path / Path(inc.as_path)
                    if shadow_path.exists():
                        ledger = Ledger.load(topic_path / LEDGER_NAME)
                        if not ledger.authorizes(
                            as_path=inc.as_path,
                            source_root=source_path,
                            course_root=course_root,
                        ):
                            findings.append(
                                SpecFinding(
                                    severity="warning",
                                    type="include_shadowed",
                                    topic_id=topic_spec.id,
                                    section=section_name,
                                    message=suffix(
                                        section_disabled,
                                        f"Topic '{topic_spec.id}': include target "
                                        f"'{inc.as_path}' is shadowed by a real "
                                        f"file/directory in the topic dir. The "
                                        f"local copy wins at build time; the "
                                        f"include is ignored for shadowed paths.",
                                    ),
                                    suggestion=(
                                        f"Delete '{inc.as_path}' under the topic "
                                        f"directory, or remove the <include> "
                                        f"from this topic."
                                    ),
                                )
                            )

                if inc.source not in seen_topic_dir_sources and _is_inside_topic_dir(inc):
                    seen_topic_dir_sources.add(inc.source)
                    findings.append(
                        SpecFinding(
                            severity="warning",
                            type="include_source_is_topic_dir",
                            message=suffix(
                                section_disabled,
                                f"Include source '{inc.source}' resolves "
                                f"inside a topic directory under 'slides/'. "
                                f"This works but couples two topics' "
                                f"contents — prefer a canonical location "
                                f"outside 'slides/' (e.g. 'examples/...').",
                            ),
                        )
                    )

                if inc.source not in seen_dep_sources:
                    seen_dep_sources.add(inc.source)
                    if source_exists:
                        deps = _find_include_dependencies(source_path, course_root)
                        if deps is not None:
                            joined = ", ".join(deps.deps) if deps.deps else "(none)"
                            findings.append(
                                SpecFinding(
                                    severity="info",
                                    type="include_dependencies",
                                    message=(
                                        f"Include '{inc.source}' declares "
                                        f"dependencies in "
                                        f"'{deps.pyproject_rel}': {joined}"
                                    ),
                                )
                            )


def _validate_cross_references(
    *,
    spec: CourseSpec,
    topic_map: dict[str, list[TopicMatch]],
    findings: list[SpecFinding],
    suffix: Callable[[bool, str], str],
) -> None:
    """Emit findings for ``clm:`` cross-references in included slide files.

    Scans every slide file of every resolved topic in *spec* for the
    ``clm:`` link scheme and checks that each referenced topic id is itself
    included in the spec. Two categories are produced:

    * ``cross_reference_target_missing`` — error when a referenced topic id
      is not part of the course (not in any included section). Because the
      scan runs over ``spec.sections`` (which already reflect any
      ``--section`` selection / disabled-section filtering applied at parse
      time), a target that exists on disk but is not included is correctly
      reported.
    * ``cross_reference_ambiguous`` — warning when a topic-granular
      reference resolves to a directory topic containing several slide
      notebooks and no ``/notebook-stem`` disambiguator was given.

    The build path performs the same check with full course context
    (``clm.core.cross_references.validate_cross_references``); this mirror
    lets authors catch dangling links via ``clm validate`` without a
    full build.
    """
    from clm.core.cross_references import (
        extract_cross_references,
        has_cross_references,
        split_reference,
    )
    from clm.core.topic_resolver import find_slide_files, matches_for_binding

    # The set of topic ids that are actually included by the spec.
    included_topic_ids: set[str] = set()
    for section in spec.sections:
        for topic_spec in section.topics:
            included_topic_ids.add(topic_spec.id)

    seen: set[tuple[str, str]] = set()

    for section in spec.sections:
        section_name = section.name.en or section.name.de
        section_disabled = not section.enabled
        for topic_spec in section.topics:
            effective_module = section.module_for(topic_spec)
            matches = matches_for_binding(topic_map, topic_spec.id, effective_module)
            if len(matches) != 1:
                # Unresolved / ambiguous topics are already flagged; do not
                # double-report by scanning a path we cannot pin down.
                continue
            for slide_file in find_slide_files(matches[0].path):
                try:
                    text = slide_file.read_text(encoding="utf-8")
                except OSError:
                    continue
                if not has_cross_references(text):
                    continue
                for reference in extract_cross_references(text):
                    target_id, notebook_stem = split_reference(reference)
                    key = (str(slide_file), reference)
                    if target_id not in included_topic_ids:
                        if key in seen:
                            continue
                        seen.add(key)
                        findings.append(
                            SpecFinding(
                                severity="error",
                                type="cross_reference_target_missing",
                                topic_id=topic_spec.id,
                                section=section_name,
                                message=suffix(
                                    section_disabled,
                                    f"Topic '{topic_spec.id}' "
                                    f"({slide_file.name}) links to "
                                    f"'{reference}', which is not included in "
                                    f"the course.",
                                ),
                                suggestion=(
                                    "Add the target topic to the spec, fix the "
                                    "'clm:' reference, or remove the link."
                                ),
                            )
                        )
                        continue
                    # Ambiguity: directory topic with several slide notebooks
                    # and no disambiguator.
                    if notebook_stem is None:
                        target_matches = matches_for_binding(topic_map, target_id, None)
                        if len(target_matches) == 1:
                            slide_count = len(find_slide_files(target_matches[0].path))
                            if slide_count > 1:
                                if key in seen:
                                    continue
                                seen.add(key)
                                findings.append(
                                    SpecFinding(
                                        severity="warning",
                                        type="cross_reference_ambiguous",
                                        topic_id=topic_spec.id,
                                        section=section_name,
                                        message=suffix(
                                            section_disabled,
                                            f"Topic '{topic_spec.id}' "
                                            f"({slide_file.name}) links to "
                                            f"'{reference}', a topic with "
                                            f"{slide_count} slide notebooks; the "
                                            f"build resolves this "
                                            f"deterministically to the first.",
                                        ),
                                        suggestion=(
                                            "Add a '/notebook-stem' "
                                            "disambiguator (e.g. "
                                            f"'clm:{target_id}/slides_<stem>') "
                                            "to select a specific deck."
                                        ),
                                    )
                                )


def _emit_section_inheritance(
    *,
    section: SectionSpec,
    sec_inc: IncludeSpec,
    section_disabled: bool,
    findings: list[SpecFinding],
    suffix: Callable[[bool, str], str],
) -> None:
    """Record an info finding describing how a section-level include propagates."""
    section_name = section.name.en or section.name.de
    inheriting: list[str] = []
    overriding: list[str] = []
    for topic_spec in section.topics:
        topic_override = next(
            (t for t in topic_spec.includes if t.key == sec_inc.key),
            None,
        )
        if topic_override is not None and topic_override.source != sec_inc.source:
            overriding.append(f"{topic_spec.id} (source={topic_override.source!r})")
        else:
            # No override, OR override with same source — both inherit
            # the section default's effective behavior.
            inheriting.append(topic_spec.id)

    if not inheriting and not overriding:
        # Section-level include declared but no topics under the section.
        # The empty_section warning already flags the underlying issue;
        # an inheritance audit message here would just be noise.
        return

    base = f"Section '{section_name}' include 'source={sec_inc.source!r} as={sec_inc.as_path!r}'"
    parts: list[str] = []
    if inheriting:
        parts.append(f"inherited by: {', '.join(inheriting)}")
    if overriding:
        parts.append(f"overridden by: {', '.join(overriding)}")
    message = base + "; " + "; ".join(parts)

    findings.append(
        SpecFinding(
            severity="info",
            type="include_section_inheritance",
            section=section_name,
            message=suffix(section_disabled, message),
        )
    )


def _is_inside_topic_dir(inc: IncludeSpec) -> bool:
    """True when ``inc.source`` resolves inside ``slides/.../topic_*``.

    The source is a forward-slash POSIX path (normalized at parse time),
    so a simple parts check suffices — no filesystem access required.
    """
    parts = Path(inc.source).parts
    if len(parts) < 3 or parts[0] != "slides":
        return False
    return any(p.startswith("topic_") for p in parts[1:])


def _find_include_dependencies(source_path: Path, course_root: Path) -> _IncludeDependencies | None:
    """Locate the nearest ``pyproject.toml`` for an include and parse its deps.

    Walks from *source_path* upwards toward (but not including)
    *course_root*, returning the first ``pyproject.toml`` with a
    ``[project]`` table. The course's own root ``pyproject.toml`` is
    intentionally not returned — we want the include's own metadata,
    not the host project's.
    """
    start = source_path if source_path.is_dir() else source_path.parent
    try:
        course_root_resolved = course_root.resolve()
    except OSError:
        course_root_resolved = course_root

    cur = start.resolve() if start.exists() else start
    while True:
        try:
            cur.relative_to(course_root_resolved)
        except ValueError:
            return None
        if cur == course_root_resolved:
            return None
        candidate = cur / "pyproject.toml"
        if candidate.is_file():
            try:
                with candidate.open("rb") as f:
                    data = tomllib.load(f)
            except (OSError, tomllib.TOMLDecodeError):
                return None
            project = data.get("project")
            if not isinstance(project, dict):
                return None
            raw_deps = project.get("dependencies", [])
            if not isinstance(raw_deps, list):
                return None
            deps = [str(d) for d in raw_deps]
            try:
                rel = candidate.relative_to(course_root_resolved).as_posix()
            except ValueError:
                rel = str(candidate)
            return _IncludeDependencies(pyproject_rel=rel, deps=deps)

        parent = cur.parent
        if parent == cur:
            return None
        cur = parent
