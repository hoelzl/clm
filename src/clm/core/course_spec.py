import io
import logging
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from xml.etree import ElementTree as ETree

from attr import Factory, field, frozen

from clm.core.utils.text_utils import Text, sanitize_file_name

logger = logging.getLogger(__name__)


class CourseSpecError(Exception):
    """Raised when a course specification file cannot be parsed or is invalid.

    This exception provides user-friendly error messages with context about
    what went wrong and how to fix it.
    """

    pass


class OutputKind(Enum):
    """Valid output kind values."""

    CODE_ALONG = "code-along"
    COMPLETED = "completed"
    TRAINER = "trainer"
    RECORDING = "recording"
    SPEAKER = "speaker"  # Deprecated alias for RECORDING.


class OutputFormat(Enum):
    """Valid output format values."""

    HTML = "html"
    NOTEBOOK = "notebook"
    CODE = "code"


VALID_HTTP_REPLAY_MODES: frozenset[str] = frozenset(
    {"replay", "once", "new-episodes", "refresh", "disabled"}
)


@frozen
class IncludeSpec:
    """A `<include>` element on a `<topic>` or `<section>`.

    Pulls a file or directory from elsewhere in the course root into a
    topic's effective file map under a relative target path. See
    ``docs/claude/design/shared-source-includes-and-output-dedup.md``.

    ``source`` is normalized to forward-slash form during parse so that
    the same XML produces the same key on Windows and POSIX. It is a
    course-root-relative POSIX-style path; ``..`` segments are rejected.
    ``as_path`` is the target relative path inside the topic directory;
    it defaults to the basename of ``source`` and must be a single
    relative path with no ``..`` segments.
    """

    source: str
    as_path: str
    optional: bool = False

    @property
    def key(self) -> str:
        """Per-topic dedup key (the target location).

        Section-level defaults are overridden by topic-level includes
        sharing the same ``key``.
        """
        return self.as_path


# Glob metacharacters rejected in ``as`` paths. These are valid filename
# characters on POSIX but they turn the path into a glob pattern when
# pasted into a ``.gitignore`` (see ``clm sync-includes --print-gitignore``),
# and they're vanishingly rare in real filenames — reject them at parse
# time rather than let them surface as confusing gitignore behavior later.
_GLOB_METACHARS = frozenset("*?[]")


def _normalize_include_path(
    value: str,
    *,
    attr_name: str,
    element_label: str,
    reject_globs: bool = False,
) -> str:
    """Validate and normalize an include path attribute.

    Rejects empty strings, absolute paths, and any path that contains a
    ``..`` segment after splitting on either separator. When
    ``reject_globs`` is True, also rejects glob metacharacters
    (``*``, ``?``, ``[``, ``]``); used for the ``as`` attribute because
    it surfaces in generated gitignore patterns. Returns the
    forward-slash-normalized form so includes from Windows-authored
    specs collide correctly with POSIX-authored ones in the dedup
    bookkeeping.
    """
    cleaned = value.strip()
    if not cleaned:
        raise CourseSpecError(
            f"{element_label}: '{attr_name}' attribute is empty. "
            f"Provide a course-root-relative path."
        )
    # Normalize separators *before* constructing a Path: on POSIX,
    # ``Path("a\\b")`` is one component and ``as_posix()`` won't split it,
    # so a Windows-authored spec containing backslashes would survive
    # untouched. Replace explicitly so the normalization is identical on
    # every platform.
    cleaned = cleaned.replace("\\", "/")
    candidate = Path(cleaned)
    if candidate.is_absolute() or cleaned.startswith("/"):
        raise CourseSpecError(
            f"{element_label}: '{attr_name}={cleaned!r}' is absolute. "
            f"Include paths must be relative to the course root."
        )
    parts = candidate.parts
    if any(part == ".." for part in parts):
        raise CourseSpecError(
            f"{element_label}: '{attr_name}={cleaned!r}' contains a '..' "
            f"segment. Include paths must stay inside the course root; "
            f"reorganize the source so no '..' is needed."
        )
    if reject_globs:
        offending = sorted(_GLOB_METACHARS.intersection(cleaned))
        if offending:
            raise CourseSpecError(
                f"{element_label}: '{attr_name}={cleaned!r}' contains glob "
                f"metacharacter(s) {''.join(offending)!r}. The 'as' attribute "
                f"is used as a literal filesystem path and in generated "
                f".gitignore patterns; pick a name without these characters."
            )
    return candidate.as_posix()


def _parse_includes(parent: ETree.Element, *, element_label: str) -> list[IncludeSpec]:
    """Parse ``<include>`` children of a section or topic element.

    Each ``<include>`` requires a ``source`` attribute; ``as`` defaults
    to the basename of ``source`` when omitted. ``optional`` accepts the
    same boolean values as other CLM XML attributes. Two includes on the
    same parent that resolve to the same ``as`` value are an
    :class:`CourseSpecError`.
    """
    includes: list[IncludeSpec] = []
    seen_keys: dict[str, str] = {}
    for inc in parent.findall("include"):
        raw_source = inc.attrib.get("source")
        if raw_source is None:
            raise CourseSpecError(f"{element_label}: <include> requires a 'source' attribute.")
        source = _normalize_include_path(
            raw_source, attr_name="source", element_label=element_label
        )

        raw_as = inc.attrib.get("as")
        if raw_as is None or raw_as.strip() == "":
            as_path = Path(source).name
        else:
            as_path = _normalize_include_path(
                raw_as,
                attr_name="as",
                element_label=element_label,
                reject_globs=True,
            )

        optional = _parse_bool_attr(inc.attrib.get("optional"), attr_name="optional")

        if as_path in seen_keys:
            raise CourseSpecError(
                f"{element_label}: two <include> elements target the same "
                f"location 'as={as_path}'. Sources were "
                f"{seen_keys[as_path]!r} and {source!r}. Pick one source per "
                f"target, or differentiate them with explicit 'as' values."
            )
        seen_keys[as_path] = source
        includes.append(IncludeSpec(source=source, as_path=as_path, optional=optional))
    return includes


@frozen
class TopicSpec:
    id: str
    skip_html: bool = False
    # When True, the notebook is converted to all configured output
    # formats (HTML, .ipynb, code) without spawning a kernel — cells are
    # rendered with empty outputs. Opt out of evaluation with
    # ``evaluate="no"`` on the topic.
    skip_evaluation: bool = False
    skip_errors: bool = False
    # When set, this topic opts in to (True) or out of (False) HTTP replay
    # (cassette-backed request recording/playback via ``vcrpy``). ``None``
    # means "inherit from the section default" — see
    # :meth:`SectionSpec.http_replay_for`. The global record mode is chosen
    # at build time by ``--http-replay`` or ``CLM_HTTP_REPLAY_MODE``.
    http_replay: bool | None = None
    author: str = ""
    prog_lang: str = ""
    # Optional module binding. When set, resolution is restricted to the
    # named module directory (e.g., ``"module_545_ml_azav_cohort_2026_04"``).
    # ``None`` means "use the section default if any, otherwise unbound".
    # Empty string is treated as None for tolerance with XML defaults.
    module: str | None = None
    # Topic-scoped ``<include>`` elements. Section-level defaults are
    # merged in by :meth:`SectionSpec.includes_for`; the topic's own
    # entries override section defaults sharing the same ``as_path``.
    includes: list[IncludeSpec] = Factory(list)
    # Presentation-only export-visibility flag (the build/export split,
    # mirroring how ``optional``/``enabled`` are presentation-only). When
    # False (``export="false"``) the topic still BUILDS — it flattens into the
    # section's flat topic list like any topic — but is omitted from
    # ``clm export schedule`` / ``clm export outline`` listings. It is the
    # complement of an ``<activity>`` (export-only, never built). Default True.
    export: bool = True


# Weekday tokens for ``<subsection weekday="...">`` (issue #261). A closed,
# language-neutral enum so the validator can check ordering/uniqueness; the
# tokens are localized only at render time (see
# ``clm.cli.commands.schedule.WEEKDAY_LABELS``). ``sat``/``sun`` are valid
# (some industry courses run on Saturday); AZAV uses Mon–Fri only. The tuple
# defines the canonical week order used for the out-of-order check.
WEEKDAY_ORDER: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
VALID_WEEKDAYS: frozenset[str] = frozenset(WEEKDAY_ORDER)

# The workdays a Mon–Fri course is expected to fill. Used by the opt-in
# ``clm validate --check-workdays`` coverage check, which warns when a section
# that uses the day-of-week subsection layer leaves one of these uncovered.
REQUIRED_WORKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri")


@frozen
class ActivitySpec:
    """A non-deck schedule entry inside a ``<subsection>``.

    Represents scheduled time that has **no slide deck on disk** and is never
    built — e.g. a project-work day, an exam, or a holiday — yet should still
    appear in ``clm export schedule`` / ``clm export outline`` so a
    certification listing has no empty days. It is the complement of an
    ``export="false"`` topic (built but unlisted).

    An ``<activity>`` is never handed to the topic resolver, so it cannot fail
    resolution and produces no build output. Authored as::

        <subsection weekday="thu">
            <activity kind="project">
                <de>Projektarbeit: RAG-App (kein Video)</de>
                <en>Project work: RAG app (no video)</en>
            </activity>
        </subsection>

    Attributes:
        text: The bilingual label shown in the Video column of the schedule
            (and as a bullet in the outline).
        kind: Optional free-form classifier (``"project"``, ``"exam"``,
            ``"holiday"``, …) for styling/filtering. Never affects resolution.
        id: Optional, purely informational identifier.
    """

    text: Text
    kind: str = ""
    id: str | None = None


@frozen
class SubsectionSpec:
    """An optional ``<subsection>`` grouping inside a ``<section>``'s ``<topics>``.

    Groups one or more ``<topic>`` elements under an optional weekday and/or
    label. It expresses day-of-week scheduling for certification listings
    (issue #261): ``<section>`` = week, ``<subsection>`` = day.

    The layer is **purely additive**: :meth:`CourseSpec.parse_sections`
    flattens the topics of *enabled* subsections into the parent
    :attr:`SectionSpec.topics` list (in document order) so the build path
    never sees the wrapper. A spec with subsections therefore builds
    byte-identically to the same spec with the ``<subsection>`` wrappers
    removed. The grouping is retained here only for outline/schedule output
    and validation.

    Attributes:
        topics: The contained topics, in document order. For an *enabled*
            subsection these are the very same :class:`TopicSpec` objects
            that also appear in the parent :attr:`SectionSpec.topics` flat
            list. A *disabled* subsection's topics are held only here — they
            are deliberately kept out of the flat build list (see ``enabled``).
        weekdays: Zero or more language-neutral weekday tokens from
            :data:`VALID_WEEKDAYS` (``"mon"``..``"sun"``), in declared order.
            A single ``<subsection weekday="mon,tue,wed">`` spans several
            days; an empty tuple is a generic thematic group that carries
            only a ``<name>``. The :attr:`weekday` property returns the first
            token (or ``None``) for callers that only handle a single day.
        name: Optional bilingual label override. When ``None``, consumers
            derive the displayed label from ``weekdays``.
        enabled: Mirrors ``<section enabled=...>``. A disabled subsection is
            dropped entirely from the build — its topics never enter the flat
            :attr:`SectionSpec.topics` list (the build path), regardless of
            ``keep_disabled``. With ``keep_disabled=True`` the wrapper is still
            retained in :attr:`SectionSpec.subsections` so tooling
            (``clm outline --include-disabled``) can surface it.
        optional: An optional module that is excluded from ``clm schedule`` /
            ``clm outline`` listings unless ``--include-optional`` is passed.
            Presentation-only: it never affects the build (the topics still
            flatten into :attr:`SectionSpec.topics` like any enabled
            subsection). An ``optional`` *and* disabled subsection is dropped
            entirely (``enabled`` wins) — it is never listed, flag or not.
        activities: Zero or more :class:`ActivitySpec` entries (``<activity>``
            children) — non-deck schedule rows (project work, exams, …) that
            appear in ``clm export`` output but are never resolved or built.
            Held only here; they never enter the flat build list.
    """

    topics: list[TopicSpec] = Factory(list)
    weekdays: tuple[str, ...] = ()
    name: Text | None = None
    enabled: bool = True
    optional: bool = False
    activities: list[ActivitySpec] = Factory(list)

    @property
    def weekday(self) -> str | None:
        """The first weekday token, or ``None`` — convenience for single-day callers."""
        return self.weekdays[0] if self.weekdays else None


@frozen
class SectionSpec:
    name: Text
    topics: list[TopicSpec] = Factory(list)
    enabled: bool = True
    id: str | None = None
    # Optional module binding applied as a default to all child topics that
    # do not themselves carry an explicit ``module`` attribute.
    module: str | None = None
    # Section-level ``<include>`` defaults. Each direct ``<topic>`` child
    # inherits these unless the topic declares its own ``<include>`` with
    # the same ``as_path``.
    includes: list[IncludeSpec] = Factory(list)
    # Section-level ``http-replay`` default applied to all child topics
    # that do not themselves carry an explicit ``http-replay`` attribute.
    # Per-topic ``http-replay="yes"``/``"no"`` overrides this default.
    http_replay: bool = False
    # Optional ``<subsection>`` groupings (issue #261). The contained topics
    # are *also* present in ``topics`` (flattened); this list retains the
    # day-of-week / thematic grouping for ``clm outline`` and
    # ``clm schedule``. Empty for sections that use only bare ``<topic>``s.
    # Honors ``keep_disabled`` the same way ``CourseSpec.sections`` does:
    # disabled subsections appear here only when parsed with
    # ``keep_disabled=True``.
    subsections: list[SubsectionSpec] = Factory(list)
    # An optional module (whole week). Excluded from ``clm schedule`` /
    # ``clm outline`` listings unless ``--include-optional`` is passed.
    # Presentation-only, exactly like :attr:`SubsectionSpec.optional`: it
    # never gates the build. An ``optional`` *and* disabled section is still
    # dropped at parse time (``enabled`` wins) and is never listed.
    optional: bool = False

    def module_for(self, topic_spec: TopicSpec) -> str | None:
        """Effective module binding for *topic_spec* under this section.

        Per-topic ``module=`` overrides the section default. Returns
        ``None`` when neither side specifies a module (unbound — resolution
        falls back to first-occurrence-wins across modules).
        """
        return topic_spec.module or self.module

    def includes_for(self, topic_spec: TopicSpec) -> list[IncludeSpec]:
        """Effective include list for *topic_spec* under this section.

        Section-level defaults are merged with topic-level entries. The
        target path (``as_path``) is the dedup key: when both layers
        contribute an include with the same key, the topic's wins.
        Order is preserved (section defaults first, then any topic
        entries that did not collide).
        """
        merged: dict[str, IncludeSpec] = {}
        for inc in self.includes:
            merged[inc.key] = inc
        for inc in topic_spec.includes:
            merged[inc.key] = inc
        return list(merged.values())

    def http_replay_for(self, topic_spec: TopicSpec) -> bool:
        """Effective ``http-replay`` flag for *topic_spec* under this section.

        Per-topic ``http-replay`` overrides the section default. ``None``
        on the topic means "not set" → inherit the section value.
        """
        return self.http_replay if topic_spec.http_replay is None else topic_spec.http_replay


@frozen
class TopicBinding:
    """A single (section, topic, effective module) triple from a course spec.

    Yielded by :meth:`CourseSpec.iter_topic_bindings` so callers do not need
    to repeat the per-topic / section-default override logic. The
    ``effective_module`` field is what every spec-aware resolver/validator
    should pass to topic resolution.
    """

    section: SectionSpec
    topic_spec: TopicSpec
    effective_module: str | None

    @property
    def topic_id(self) -> str:
        return self.topic_spec.id


@frozen
class SectionSelection:
    """Result of resolving ``--only-sections`` selector tokens.

    Attributes:
        resolved_indices: 0-based indices into the disabled-inclusive
            section list. Indices are stable under ``enabled`` toggles so
            toggling a section's ``enabled`` flag does not renumber the
            selectors that follow it. Excludes disabled sections —
            selected disabled sections go into :attr:`skipped_disabled`
            and do not appear here.
        skipped_disabled: Human-readable labels for disabled sections
            that were selected but intentionally dropped from the build
            with a warning. Format: the section's ``id`` when present,
            otherwise the English or German name.
    """

    resolved_indices: list[int]
    skipped_disabled: list[str] = Factory(list)


def find_subdirs(element: ETree.Element) -> list[str]:
    subdirs = element.find("subdirs")
    if subdirs is None:
        return []
    return [subdir_elem.text or "" for subdir_elem in subdirs]


def element_text(element: ETree.Element, tag: str) -> str:
    child = element.find(tag)
    if child is not None:
        return child.text or ""
    return ""


def _parse_bool_attr(value: str | None, *, attr_name: str) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in ("true", "yes", "1"):
        return True
    if normalized in ("false", "no", "0", ""):
        return False
    raise CourseSpecError(
        f"Invalid value for {attr_name!r} attribute: {value!r}. "
        f"Expected 'true'/'yes'/'1' or 'false'/'no'/'0' (case-insensitive)."
    )


def _parse_optional_bool_attr(value: str | None, *, attr_name: str) -> bool | None:
    """Like :func:`_parse_bool_attr` but distinguishes "absent" from "false".

    Returns ``None`` when the attribute is missing (so callers can fall
    back to an inherited default), and ``True``/``False`` for explicit
    truthy/falsy values. Empty string is treated as ``False`` for
    symmetry with :func:`_parse_bool_attr`.
    """
    if value is None:
        return None
    return _parse_bool_attr(value, attr_name=attr_name)


def _parse_disable_attr(value: str | None, *, attr_name: str) -> bool:
    """Parse a "default-on" boolean attribute (e.g. ``evaluate``).

    Returns True when the user explicitly disables the behaviour
    (``"false"``/``"no"``/``"0"``) and False when the attribute is absent
    or explicitly enabled. Mirrors :func:`_parse_bool_attr` but inverts
    the polarity so the resulting boolean reads as a *skip* flag.
    """
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in ("true", "yes", "1", ""):
        return False
    if normalized in ("false", "no", "0"):
        return True
    raise CourseSpecError(
        f"Invalid value for {attr_name!r} attribute: {value!r}. "
        f"Expected 'true'/'yes'/'1' or 'false'/'no'/'0' (case-insensitive)."
    )


def _parse_enabled_attr(value: str | None, *, label: str) -> bool:
    """Parse an ``enabled`` attribute (default-on, strict true/false).

    Shared by ``<section>`` and ``<subsection>`` parsing. Returns True when
    the attribute is absent or ``"true"`` and False for ``"false"``
    (case-insensitive). Any other value is a hard :class:`CourseSpecError`.
    ``label`` identifies the element in the error message (e.g.
    ``"section 'Week 1'"``).
    """
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise CourseSpecError(
        f"Invalid value for 'enabled' attribute on {label}: {value!r}. "
        f"Expected 'true' or 'false' (case-insensitive)."
    )


def _parse_export_attr(value: str | None) -> bool:
    """Parse a default-on ``export`` attribute on ``<topic>``.

    Absent or truthy → True (the topic is listed in ``clm export`` output);
    ``"false"``/``"no"``/``"0"`` → False (built, but hidden from exports).
    Reuses :func:`_parse_bool_attr`'s truthy/falsy vocabulary; an unknown
    value is a hard :class:`CourseSpecError`.
    """
    if value is None:
        return True
    return _parse_bool_attr(value, attr_name="export")


def _parse_weekdays_attr(value: str | None, *, section_label: str) -> tuple[str, ...]:
    """Parse a ``<subsection>`` ``weekday`` attribute into ordered tokens.

    Accepts a single token (``"mon"``) or a comma-separated list
    (``"mon,tue,wed"``), so one ``<subsection>`` can span several days
    (issue #261 follow-up). Each token is normalized to lowercase and
    validated against the closed :data:`VALID_WEEKDAYS` enum; an unknown
    token is a hard :class:`CourseSpecError`. Returns an empty tuple when the
    attribute is absent or blank (a thematic group carrying only a
    ``<name>``). Duplicate tokens collapse, preserving first-occurrence
    order.
    """
    if value is None:
        return ()
    result: list[str] = []
    for raw in value.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token not in VALID_WEEKDAYS:
            raise CourseSpecError(
                f"Invalid weekday {raw.strip()!r} on a <subsection> in "
                f"{section_label}. Expected one of {list(WEEKDAY_ORDER)} "
                f"(language-neutral, lowercase), optionally comma-separated."
            )
        if token not in result:
            result.append(token)
    return tuple(result)


@frozen
class GitHubSpec:
    """Git repository configuration for course output directories.

    Supports the new structure:
    <github>
        <project-slug>machine-learning-azav</project-slug>
        <repository-base>https://github.com/Coding-Academy-Munich</repository-base>
        <remote-path>Coding-Academy-Munich</remote-path>
        <remote-template>git@github.com-cam:Coding-Academy-Munich/{repo}.git</remote-template>
        <include-speaker>true</include-speaker>
    </github>

    Attributes:
        project_slug: Base name for repositories (e.g., "machine-learning-azav")
        repository_base: Base URL for repositories (e.g., "https://github.com/Org")
        remote_path: Optional path between repository_base and repo name (e.g.,
            "Coding-Academy-Munich" or "azav-editors"). When set, the default URL
            pattern becomes "{repository_base}/{remote_path}/{repo}". Supports
            nested paths (e.g., "azav-students/2026-q2").
        remote_template: Optional URL template with placeholders. If empty, uses
            "{repository_base}/{repo}" (or "{repository_base}/{remote_path}/{repo}"
            when remote_path is set). Available placeholders: {repository_base},
            {remote_path}, {repo}, {slug}, {lang}, {suffix}.
        include_speaker: Whether to create repos for speaker targets (default: False)
    """

    project_slug: str | None = None
    repository_base: str | None = None
    remote_path: str = ""
    remote_template: str = ""
    include_speaker: bool = False

    @classmethod
    def from_element(cls, element: ETree.Element | None) -> "GitHubSpec":
        """Parse a <github> XML element."""
        if element is None:
            return cls()

        project_slug = element_text(element, "project-slug") or None
        repository_base = element_text(element, "repository-base") or None
        remote_path = element_text(element, "remote-path") or ""
        remote_template = element_text(element, "remote-template") or ""

        include_speaker_elem = element.find("include-speaker")
        include_speaker = (
            include_speaker_elem is not None and (include_speaker_elem.text or "").lower() == "true"
        )

        return cls(
            project_slug=project_slug,
            repository_base=repository_base,
            remote_path=remote_path,
            remote_template=remote_template,
            include_speaker=include_speaker,
        )

    @property
    def is_configured(self) -> bool:
        """Check if git configuration is properly set up."""
        return bool(self.project_slug and self.repository_base)

    def derive_dir_name(self, lang: str) -> str | None:
        """Derive the output directory name for a given language.

        Returns:
            Directory name like "ml-course-de", or None if project_slug is not set.
        """
        if not self.project_slug:
            return None
        return f"{self.project_slug}-{lang}"

    def derive_remote_url(
        self,
        target_name: str,
        language: str,
        is_first_target: bool = False,
        project_slug: str | None = None,
        remote_template: str = "",
        remote_path: str = "",
    ) -> str | None:
        """Derive the remote URL for a target+language combination.

        Default URL pattern: ``{repository_base}/{repo}`` (without remote_path)
        or ``{repository_base}/{remote_path}/{repo}`` (with remote_path).

        The pattern can be overridden via ``remote_template`` (or the instance's
        ``self.remote_template``). The template is formatted with the following
        placeholders:

        - ``{repository_base}``: The repository base URL from the course spec
        - ``{remote_path}``: Path between base and repo (e.g., GitLab group)
        - ``{repo}``: Full derived repo name (slug + lang + suffix)
        - ``{slug}``: Project slug only
        - ``{lang}``: Language code
        - ``{suffix}``: Target suffix including leading dash (e.g., "-completed")

        For implicit targets (public/speaker):
        - public: {slug}-{lang}
        - speaker: {slug}-{lang}-speaker (only if include_speaker=True)

        For explicit targets:
        - First target (usually code-along): {slug}-{lang} (no suffix)
        - Target with its own remote_path: {slug}-{lang} (no suffix, path disambiguates)
        - Other targets: {slug}-{lang}-{target-name}
        - speaker target: {slug}-{lang}-speaker

        Args:
            target_name: Name of the output target
            language: Language code (e.g., "de", "en")
            is_first_target: Whether this is the first explicit target
            project_slug: Optional slug from CourseSpec (overrides self.project_slug)
            remote_template: Optional URL template (overrides self.remote_template)
            remote_path: Per-target remote path override. When non-empty and
                different from self.remote_path, the target suffix is suppressed.

        Returns None if git config is not properly configured or if speaker
        is requested but include_speaker is False.
        """
        slug = project_slug or self.project_slug
        if not (slug and self.repository_base):
            return None

        # A target with its own remote_path (different from course-level) gets no suffix
        has_own_remote_path = bool(remote_path) and remote_path != self.remote_path

        # Determine suffix based on target name
        if target_name in ("public", "default") or is_first_target or has_own_remote_path:
            suffix = ""
        elif target_name == "speaker":
            if not self.include_speaker:
                return None
            suffix = "-speaker"
        else:
            suffix = f"-{target_name}"

        # Resolve effective remote_path (parameter > instance)
        effective_remote_path = remote_path or self.remote_path

        repo = f"{slug}-{language}{suffix}"
        template = remote_template or self.remote_template
        if not template:
            if effective_remote_path:
                template = "{repository_base}/{remote_path}/{repo}"
            else:
                template = "{repository_base}/{repo}"
        return template.format(
            repository_base=self.repository_base,
            remote_path=effective_remote_path,
            repo=repo,
            slug=slug,
            lang=language,
            suffix=suffix,
        )

    def derive_channel_remote_url(
        self,
        channel_name: str,
        *,
        project_slug: str | None = None,
        remote_template: str = "",
        remote_path: str = "",
        stream: str = "",
        language: str = "",
    ) -> str | None:
        """Derive the remote URL for a release channel (issues #208, #291, #293).

        The repo name is ``{slug}-{channel_name}`` for the single unnamed
        ``<release-channels>`` block (the cohort name, e.g. ``cohort-jan``,
        disambiguates it); a named stream appends its name —
        ``{slug}-{channel_name}-{stream}`` (e.g. ``ml-2026-04-materials``) —
        so the two streams of one cohort land in distinct repos, and a
        language-scoped channel appends its ``lang`` as the final segment
        (``…-materials-de``), matching the per-language repo convention.
        Reusing :meth:`derive_remote_url` with an empty language would emit a
        stray ``--`` in the repo name; this method avoids that while sharing
        the same base/remote-path/template handling.

        The ``{suffix}`` template placeholder is bound to the empty string for
        channels; ``{stream}`` carries the stream name and ``{lang}`` the
        channel language (each empty when unset). Returns ``None`` when git
        config is not set.
        """
        slug = project_slug or self.project_slug
        if not (slug and self.repository_base):
            return None

        effective_remote_path = remote_path or self.remote_path
        repo = "-".join(part for part in (slug, channel_name, stream, language) if part)
        template = remote_template or self.remote_template
        if not template:
            if effective_remote_path:
                template = "{repository_base}/{remote_path}/{repo}"
            else:
                template = "{repository_base}/{repo}"
        return template.format(
            repository_base=self.repository_base,
            remote_path=effective_remote_path,
            repo=repo,
            slug=slug,
            lang=language,
            suffix="",
            stream=stream,
        )


@frozen
class DirGroupSpec:
    name: Text
    path: str
    subdirs: list[str] | None = None
    include_root_files: bool = False
    recursive: bool = True
    # Ownership for a *topic-scoped* dir-group (a ``<dir-group>`` nested inside a
    # ``<topic>``). ``None`` for a global ``<dir-groups>`` entry. Recorded so a
    # per-topic release can ship a topic's dir-group together with that topic
    # (issue #208); the output placement itself is unchanged.
    section_id: str | None = None
    topic_id: str | None = None

    @classmethod
    def from_element(
        cls,
        element: ETree.Element,
        *,
        section_id: str | None = None,
        topic_id: str | None = None,
    ):
        subdirs = find_subdirs(element)
        name = Text.from_string(element_text(element, "name"))
        include_root_files = element.get("include-root-files", "").lower() == "true"
        recursive = element.get("recursive", "").lower() != "false"
        return cls(
            name=name,
            path=element_text(element, "path"),
            subdirs=subdirs,
            include_root_files=include_root_files,
            recursive=recursive,
            section_id=section_id,
            topic_id=topic_id,
        )


# Valid values for output target configuration. ``speaker`` is the deprecated
# input alias for ``recording`` — accepted through CLM 1.6 with a
# DeprecationWarning logged at parse time (see :func:`_normalize_output_kind`).
# Removal target: CLM 1.8 (originally planned for 1.6, then 1.7 — slipped to align
# with the Phase 0 CLI-alias removal so users see a single deprecation cliff).
VALID_KINDS: frozenset[str] = frozenset(
    {"code-along", "completed", "trainer", "recording", "speaker", "partial"}
)


def _normalize_output_kind(kind: str) -> str:
    """Map deprecated kind aliases onto their replacements.

    ``speaker`` is the historical name for the private output containing both
    speaker notes and voiceover cells. It is preserved as an input alias for
    backwards compatibility but is logged as deprecated and remapped to
    ``recording``. New consumers should never see ``speaker`` once a spec has
    been normalized through this function. Scheduled for removal in CLM 1.8
    (originally planned for 1.6, then 1.7).
    """
    if kind == "speaker":
        logger.warning(
            "Output kind 'speaker' is deprecated; use 'recording' (notes + "
            "voiceover) or 'trainer' (notes only) instead. 'speaker' is "
            "currently treated as 'recording'."
        )
        return "recording"
    return kind


VALID_FORMATS: frozenset[str] = frozenset({"html", "notebook", "code", "jupyterlite"})
VALID_LANGUAGES: frozenset[str] = frozenset({"de", "en"})


@frozen
class OutputTargetSpec:
    """Specification for a single output target from the course spec file.

    Attributes:
        name: Unique identifier for this target
        path: Output directory path (absolute or relative to course root)
        kinds: List of output kinds to generate (None = all)
        formats: List of output formats to generate (None = all)
        languages: List of languages to generate (None = all)
        remote_path: Optional remote path override for this target (e.g., GitLab
            group). When set, overrides the course-level <remote-path> from
            <github> for remote URL construction.
        distribute_attr: Raw ``distribute`` XML attribute (issue #292).
            ``"false"`` marks the target as a private build input (e.g. a
            release-stream source) that ``clm git`` must not turn into a
            distributed repo; ``""`` means unset — see
            :meth:`CourseSpec.is_distributed_target` for the effective default,
            which auto-excludes release ``source-target``\\ s.
    """

    name: str
    path: str
    kinds: list[str] | None = None  # None means "all"
    formats: list[str] | None = None
    languages: list[str] | None = None
    remote_path: str = ""
    jupyterlite: "JupyterLiteConfig | None" = None
    distribute_attr: str = ""

    @property
    def distribute(self) -> bool | None:
        """The parsed ``distribute`` attribute; ``None`` when unset."""
        if not self.distribute_attr:
            return None
        return self.distribute_attr.lower() == "true"

    @classmethod
    def from_element(cls, element: ETree.Element) -> "OutputTargetSpec":
        """Parse an <output-target> XML element."""
        name = element.get("name", "default")
        path = element_text(element, "path")
        remote_path = element_text(element, "remote-path") or ""
        distribute_attr = element.get("distribute", "")

        # Parse optional filter lists. Normalize deprecated kind aliases here
        # so downstream consumers (output_targets, execution dependencies,
        # path utils) only ever see the canonical kind set.
        raw_kinds = cls._parse_list(element, "kinds", "kind")
        kinds = [_normalize_output_kind(k) for k in raw_kinds] if raw_kinds is not None else None
        formats = cls._parse_list(element, "formats", "format")
        languages = cls._parse_list(element, "languages", "language")

        jupyterlite = JupyterLiteConfig.from_element(element.find("jupyterlite"))

        return cls(
            name=name,
            path=path,
            kinds=kinds,
            formats=formats,
            languages=languages,
            remote_path=remote_path,
            jupyterlite=jupyterlite,
            distribute_attr=distribute_attr,
        )

    @staticmethod
    def _parse_list(
        element: ETree.Element,
        container_tag: str,
        item_tag: str,
    ) -> list[str] | None:
        """Parse a list of values from nested XML elements."""
        container = element.find(container_tag)
        if container is None:
            return None
        return [(item.text or "").strip() for item in container.findall(item_tag) if item.text]

    def validate(self) -> list[str]:
        """Validate the target specification.

        Returns:
            List of validation error messages (empty if valid)
        """
        errors: list[str] = []

        if not self.name:
            errors.append("Output target must have a name attribute")

        if not self.path:
            errors.append(f"Output target '{self.name}' must have a <path> element")

        # Validate kinds
        if self.kinds:
            for kind in self.kinds:
                if kind not in VALID_KINDS:
                    errors.append(
                        f"Invalid kind '{kind}' in target '{self.name}'. "
                        f"Valid values: {sorted(VALID_KINDS)}"
                    )

        # Validate formats
        if self.formats:
            for fmt in self.formats:
                if fmt not in VALID_FORMATS:
                    errors.append(
                        f"Invalid format '{fmt}' in target '{self.name}'. "
                        f"Valid values: {sorted(VALID_FORMATS)}"
                    )

        # Validate languages
        if self.languages:
            for lang in self.languages:
                if lang not in VALID_LANGUAGES:
                    errors.append(
                        f"Invalid language '{lang}' in target '{self.name}'. "
                        f"Valid values: {sorted(VALID_LANGUAGES)}"
                    )

        # Validate the distribute attribute (issue #292). A typo like
        # distribute="flase" must not silently flip distribution behavior.
        if self.distribute_attr and self.distribute_attr.lower() not in ("true", "false"):
            errors.append(
                f"Invalid distribute value {self.distribute_attr!r} in target "
                f"'{self.name}'. Valid values: true, false"
            )

        return errors


@frozen
class ImageOptionsSpec:
    """Image processing options for a course.

    Supports the structure:
    <image-options>
        <format>svg</format>     <!-- "png" (default) or "svg" -->
        <inline>true</inline>    <!-- true or false (default) -->
    </image-options>
    """

    format: str = "png"
    inline: bool = False

    @classmethod
    def from_element(cls, element: ETree.Element | None) -> "ImageOptionsSpec":
        """Parse an <image-options> XML element."""
        if element is None:
            return cls()

        fmt = element_text(element, "format") or "png"
        inline_text = element_text(element, "inline")
        inline = inline_text.lower() == "true" if inline_text else False

        return cls(format=fmt, inline=inline)


VALID_JUPYTERLITE_KERNELS: frozenset[str] = frozenset({"xeus-python", "pyodide"})
VALID_JUPYTERLITE_APP_ARCHIVES: frozenset[str] = frozenset({"offline", "cdn"})
VALID_JUPYTERLITE_LAUNCHERS: frozenset[str] = frozenset({"python", "miniserve", "none"})


@frozen
class BrandingConfig:
    """Optional branding overrides for a JupyterLite site.

    Maps to JupyterLab's ``overrides.json`` mechanism. All fields are
    optional; omitting the entire ``<branding>`` block produces an
    unmodified default JupyterLab theme.

    Supports the structure::

        <branding>
            <theme>dark</theme>
            <logo>assets/logo.svg</logo>
            <site-name>My Course</site-name>
        </branding>
    """

    theme: str = ""
    logo: str = ""
    site_name: str = ""

    @classmethod
    def from_element(cls, element: ETree.Element | None) -> "BrandingConfig | None":
        if element is None:
            return None
        theme = element_text(element, "theme").strip().lower()
        if theme and theme not in ("light", "dark"):
            raise CourseSpecError(
                f"<branding>: invalid <theme> {theme!r}. Valid values: 'light' or 'dark'."
            )
        logo = element_text(element, "logo").strip()
        site_name = element_text(element, "site-name").strip()
        return cls(theme=theme, logo=logo, site_name=site_name)


@frozen
class JupyterLiteConfig:
    """Configuration for a JupyterLite output site.

    May appear at course level (child of ``<course>``) as a default for every
    target that opts in, or at target level (child of ``<output-target>``) to
    override the course-level block wholesale for that one target. See the
    ``jupyterlite`` info topic for authoring guidance.

    Supports the structure::

        <jupyterlite>
            <kernel>xeus-python</kernel>
            <wheels>
                <wheel>wheels/rich-13.7.1-py3-none-any.whl</wheel>
            </wheels>
            <environment>jupyterlite/environment.yml</environment>
            <launcher>python</launcher>
            <app-archive>offline</app-archive>
            <branding>
                <theme>dark</theme>
                <logo>assets/logo.svg</logo>
                <site-name>My Course</site-name>
            </branding>
        </jupyterlite>

    Attributes:
        kernel: In-browser kernel. ``"xeus-python"`` or ``"pyodide"``.
        wheels: Wheel paths relative to course root, pre-staged into site.
        environment: Path to ``environment.yml`` (xeus-python only).
        launcher: ``"python"`` emits ``launch.py``; ``"miniserve"`` bundles
            prebuilt miniserve binaries per OS; ``"none"`` skips launcher
            emission entirely. Backward compat: ``"true"`` maps to
            ``"python"``, ``"false"`` maps to ``"none"``.
        app_archive: ``"offline"`` or ``"cdn"``.
        branding: Optional UI customization (theme, logo, site name).
    """

    kernel: str
    wheels: list[str] = Factory(list)
    environment: str = ""
    launcher: str = "python"
    app_archive: str = "offline"
    branding: BrandingConfig | None = None

    @classmethod
    def from_element(cls, element: ETree.Element | None) -> "JupyterLiteConfig | None":
        """Parse a ``<jupyterlite>`` XML element.

        Returns ``None`` when ``element`` is ``None`` so callers can treat
        absence and presence uniformly.
        """
        if element is None:
            return None

        kernel = element_text(element, "kernel").strip()
        if not kernel:
            raise CourseSpecError(
                "<jupyterlite> requires a <kernel> child element. "
                "Valid values: 'xeus-python' or 'pyodide'. "
                "Run 'clm info jupyterlite' for details."
            )
        if kernel not in VALID_JUPYTERLITE_KERNELS:
            raise CourseSpecError(
                f"<jupyterlite>: invalid kernel {kernel!r}. "
                f"Valid values: {sorted(VALID_JUPYTERLITE_KERNELS)}."
            )

        wheels_elem = element.find("wheels")
        wheels: list[str] = []
        if wheels_elem is not None:
            wheels = [(w.text or "").strip() for w in wheels_elem.findall("wheel") if w.text]

        environment = element_text(element, "environment").strip()

        launcher = _parse_launcher(element_text(element, "launcher").strip().lower())

        app_archive = element_text(element, "app-archive").strip() or "offline"
        if app_archive not in VALID_JUPYTERLITE_APP_ARCHIVES:
            raise CourseSpecError(
                f"<jupyterlite>: invalid <app-archive> {app_archive!r}. "
                f"Valid values: {sorted(VALID_JUPYTERLITE_APP_ARCHIVES)}."
            )

        branding = BrandingConfig.from_element(element.find("branding"))

        return cls(
            kernel=kernel,
            wheels=wheels,
            environment=environment,
            launcher=launcher,
            app_archive=app_archive,
            branding=branding,
        )


def _parse_launcher(text: str) -> str:
    """Normalize a ``<launcher>`` value to one of ``VALID_JUPYTERLITE_LAUNCHERS``.

    Backward compat: ``"true"``/``""`` → ``"python"``, ``"false"`` → ``"none"``.
    """
    if not text or text == "true":
        return "python"
    if text == "false":
        return "none"
    if text not in VALID_JUPYTERLITE_LAUNCHERS:
        raise CourseSpecError(
            f"<jupyterlite>: invalid <launcher> {text!r}. "
            f"Valid values: {sorted(VALID_JUPYTERLITE_LAUNCHERS)}, "
            "or 'true'/'false' for backward compatibility."
        )
    return text


# GitLab group-share access levels accepted by <share-with access="...">
# (issue #294). Mapped to GitLab's numeric levels by the provisioning code.
VALID_SHARE_ACCESS: frozenset[str] = frozenset({"guest", "reporter", "developer", "maintainer"})


@frozen
class ShareWithSpec:
    """One ``<share-with>`` declaration: share the channel repo into a group.

    ``group`` is the full GitLab group path (e.g.
    ``students/azav-ml/ml-2026-04``); ``access`` the access level granted
    (default ``reporter``). Applied by ``clm release provision`` (issue #294).
    """

    group: str
    access: str = "reporter"

    @classmethod
    def from_element(cls, element: ETree.Element) -> "ShareWithSpec":
        return cls(
            group=(element.text or "").strip(),
            access=element.get("access", "reporter"),
        )


@frozen
class ReleaseChannelSpec:
    """One cohort's solution-release channel (issue #208).

    Each channel is its own git repository (``path``) fed by a volatile
    ``ledger`` (in the course source repo). ``remote_path`` overrides the
    parent ``<release-channels>`` default for remote-URL derivation, mirroring
    :class:`OutputTargetSpec.remote_path`.

    ``lang`` (issue #293) scopes the channel to a single language: ``clm
    release sync`` then promotes only that language's files, re-rooted at the
    language directory, and the derived repo name gains a ``-{lang}`` suffix —
    matching the established per-language distribution convention (e.g.
    ``…/machine-learning-azav-de``). When unset, the channel receives **every**
    built language root (the pre-#293 behavior).

    ``share_with`` (issue #294) lists the GitLab groups the channel repo is
    shared into by ``clm release provision`` — block-level ``<share-with>``
    entries (e.g. a trainers group) are inherited by every channel and come
    first; a channel-level entry for the same group overrides the inherited
    access level.

    ``evergreen`` lists glob patterns (matched against destination-relative
    POSIX paths) of skeleton files that are **never frozen**: ``clm release
    sync`` re-promotes a matching file whenever the built content differs from
    the cohort's copy (e.g. a NEWS file). Block-level ``<evergreen>`` entries
    are inherited by every channel; channel-level entries are additive.
    """

    name: str
    path: str
    ledger: str
    remote_path: str = ""
    lang: str = ""
    share_with: tuple[ShareWithSpec, ...] = ()
    evergreen: tuple[str, ...] = ()

    @classmethod
    def from_element(
        cls,
        element: ETree.Element,
        *,
        default_remote_path: str = "",
        default_shares: "tuple[ShareWithSpec, ...]" = (),
        default_evergreen: "tuple[str, ...]" = (),
    ) -> "ReleaseChannelSpec":
        own_shares = tuple(ShareWithSpec.from_element(sw) for sw in element.findall("share-with"))
        # Inherited entries first; a channel-level entry for the same group
        # replaces the inherited one (its access wins).
        own_groups = {s.group for s in own_shares}
        shares = tuple(s for s in default_shares if s.group not in own_groups) + own_shares
        own_evergreen = tuple((eg.text or "").strip() for eg in element.findall("evergreen"))
        # Inherited patterns first, channel additions after; duplicates dropped
        # (patterns have no identity key, so union — not override — semantics).
        evergreen = tuple(dict.fromkeys(default_evergreen + own_evergreen))
        return cls(
            name=element.get("name", ""),
            path=element.get("path", ""),
            ledger=element.get("ledger", ""),
            remote_path=(element_text(element, "remote-path") or default_remote_path),
            lang=element.get("lang", ""),
            share_with=shares,
            evergreen=evergreen,
        )


@frozen
class ReleaseChannelsSpec:
    """One ``<release-channels>`` block: a release *stream* and its channels.

    ``source_target`` names the ``<output-target>`` that is the frozen source
    of this stream (typically a ``completed``-kind target for solutions, or a
    ``code-along``/``partial`` target for pre-session materials). ``remote_path``
    is the default remote path for channels that do not override it.

    A course may declare **several** blocks — one per release stream (issue
    #291), e.g. ``materials`` fed by a ``shared`` target and ``solutions`` fed
    by a ``completed`` target. With more than one block each needs a unique
    ``name`` attribute (the stream name); channels are then addressed as
    ``<stream>/<channel>`` (or by bare channel name when unambiguous). A single
    unnamed block keeps the original issue-#208 behavior.
    """

    source_target: str
    channels: list[ReleaseChannelSpec] = field(factory=list)
    remote_path: str = ""
    # Stream name. Empty for the single-block (issue #208) layout; required
    # and unique when several <release-channels> blocks are declared.
    name: str = ""
    # Block-level evergreen patterns, inherited by every channel.
    evergreen: tuple[str, ...] = ()

    @classmethod
    def from_element(cls, element: ETree.Element) -> "ReleaseChannelsSpec":
        remote_path = element_text(element, "remote-path") or ""
        # Block-level <share-with> entries are inherited by every channel
        # (issue #294) — e.g. one trainers group shared across all cohorts.
        default_shares = tuple(
            ShareWithSpec.from_element(sw) for sw in element.findall("share-with")
        )
        # Block-level <evergreen> patterns likewise apply to every channel.
        default_evergreen = tuple((eg.text or "").strip() for eg in element.findall("evergreen"))
        channels = [
            ReleaseChannelSpec.from_element(
                ch,
                default_remote_path=remote_path,
                default_shares=default_shares,
                default_evergreen=default_evergreen,
            )
            for ch in element.findall("channel")
        ]
        return cls(
            source_target=element.get("source-target", ""),
            channels=channels,
            remote_path=remote_path,
            name=element.get("name", ""),
            evergreen=default_evergreen,
        )

    def channel(self, name: str) -> "ReleaseChannelSpec | None":
        for channel in self.channels:
            if channel.name == name:
                return channel
        return None


@frozen
class TaskSpec:
    """One named ``<task>`` from the spec's ``<tasks>`` block (``clm run``).

    Each ``<step>`` child holds one clm command line **without** the leading
    ``clm`` (e.g. ``export calendar {spec} --channel jan``). Steps run in
    declaration order; resolution rules (placeholders, tokenization) live in
    :mod:`clm.core.tasks`.
    """

    name: str
    description: str = ""
    steps: tuple[str, ...] = ()

    @classmethod
    def from_element(cls, element: ETree.Element) -> "TaskSpec":
        return cls(
            name=element.get("name", ""),
            description=element.get("description", ""),
            steps=tuple((step.text or "").strip() for step in element.findall("step")),
        )


def release_channel_ref(block: ReleaseChannelsSpec, channel: ReleaseChannelSpec) -> str:
    """The canonical CLI address of *channel*: ``stream/channel`` or bare name.

    A channel in a named stream is addressed as ``<stream>/<channel>`` (e.g.
    ``materials/2026-04``); a channel in the single unnamed block keeps its
    bare name, so issue-#208 era commands are unchanged.
    """
    return f"{block.name}/{channel.name}" if block.name else channel.name


def _validate_evergreen_patterns(patterns: tuple[str, ...], owner_label: str) -> list[str]:
    """Validation errors for ``<evergreen>`` patterns declared on *owner_label*.

    Patterns are matched against destination-relative POSIX paths by the
    release sync, so an empty pattern (matches nothing) or a backslash
    separator (never matches a POSIX path) is a spec mistake, not a no-op.
    """
    errors: list[str] = []
    for pattern in patterns:
        if not pattern:
            errors.append(f"{owner_label}: <evergreen> needs a glob pattern as text content.")
        elif "\\" in pattern:
            errors.append(
                f"{owner_label}: evergreen pattern {pattern!r} contains a backslash; "
                f"patterns match POSIX paths — use '/' as the separator."
            )
    return errors


@frozen
class CourseSpec:
    name: Text
    prog_lang: str
    description: Text
    certificate: Text
    sections: list[SectionSpec]
    project_slug: str | None = None
    github: GitHubSpec = field(factory=GitHubSpec)
    dictionaries: list[DirGroupSpec] = field(factory=list)
    output_targets: list[OutputTargetSpec] = field(factory=list)
    # One entry per <release-channels> block (a release *stream*, issue #291).
    # Empty list = the solution-release feature is dormant.
    release_channel_blocks: list[ReleaseChannelsSpec] = field(factory=list)
    # Named clm-command sequences for `clm run` (empty = feature dormant).
    tasks: list[TaskSpec] = field(factory=list)
    image_options: ImageOptionsSpec = field(factory=ImageOptionsSpec)
    jupyterlite: JupyterLiteConfig | None = None
    author: str = "Dr. Matthias Hölzl"
    organization: Text = field(
        factory=lambda: Text(de="Coding-Akademie München", en="Coding-Academy Munich")
    )

    @property
    def topics(self) -> list[TopicSpec]:
        return [topic for section in self.sections for topic in section.topics]

    def iter_topic_bindings(self):
        """Iterate every topic in declared order with its effective module.

        Replaces the doubly-nested ``for section in spec.sections: for
        topic_spec in section.topics:`` pattern. Each yielded
        :class:`TopicBinding` carries the section, topic spec, and the
        effective module computed via :meth:`SectionSpec.module_for` (per-
        topic override beats section default; ``None`` means unbound).

        Use this in every consumer that walks the spec to reach the
        filesystem — ``slides normalize``, ``validate``, the spec
        validator, and ``Course._build_topics`` all share this contract so
        module-bound cohort archives resolve uniformly.
        """
        for section in self.sections:
            for topic_spec in section.topics:
                yield TopicBinding(
                    section=section,
                    topic_spec=topic_spec,
                    effective_module=section.module_for(topic_spec),
                )

    def topic_bindings(self) -> set[tuple[str, str | None]]:
        """Module-aware companion to :func:`get_course_topic_ids`.

        Returns the set of ``(topic_id, effective_module)`` pairs the spec
        actually references. Use this when scoping resolution or search to
        a course spec — a bare topic-ID set incorrectly conflates the
        live-module copy of ``intro`` with a frozen-cohort copy of the
        same ID, even when the spec deliberately binds each section to a
        different module.
        """
        return {(b.topic_id, b.effective_module) for b in self.iter_topic_bindings()}

    @property
    def output_dir_name(self) -> Text:
        """Derive directory names for output from the project slug.

        Uses the project slug with a language suffix (e.g., "ml-course-de").
        Falls back to sanitized course name with language suffix if no slug is configured.
        """
        if self.project_slug:
            return Text(
                de=f"{self.project_slug}-de",
                en=f"{self.project_slug}-en",
            )
        # Fallback: sanitized name + language suffix
        return Text(
            de=f"{sanitize_file_name(self.name.de)}-de",
            en=f"{sanitize_file_name(self.name.en)}-en",
        )

    @staticmethod
    def _parse_topic_element(topic_elem: ETree.Element, *, section_label: str) -> TopicSpec:
        """Parse a single ``<topic>`` element into a :class:`TopicSpec`.

        Shared by both the bare ``<topic>`` path and the ``<subsection>``
        path so the topic grammar (id forms, ``<include>`` children,
        attributes) is identical wherever a ``<topic>`` appears.
        """
        topic_id_attr = (topic_elem.attrib.get("id") or "").strip()
        topic_id_text = (topic_elem.text or "").strip()
        has_child_elements = len(topic_elem) > 0

        if topic_id_attr and topic_id_text:
            raise CourseSpecError(
                f"Topic ID specified twice in {section_label}: "
                f"both as id={topic_id_attr!r} attribute and as "
                f"text content {topic_id_text!r}. Use only one "
                f"form per topic. Prefer the id= attribute when "
                f"the topic carries <include> or other child "
                f"elements."
            )

        topic_id = topic_id_attr or topic_id_text

        if has_child_elements and not topic_id:
            raise CourseSpecError(
                f"<topic> in {section_label} has child elements "
                f"but no ID. When a <topic> contains <include> "
                f"or other child elements, its ID must be set "
                f'via the id= attribute (e.g. <topic id="my_topic">). '
                f"The text-content form (<topic>my_topic</topic>) "
                f"is unsafe with children: XML parsers assign "
                f"text appearing after a child element to that "
                f"child's tail rather than to the topic, which "
                f"silently empties the topic ID."
            )

        topic_label = (
            f"topic '{topic_id}' in {section_label}" if topic_id else f"<topic> in {section_label}"
        )
        topic_includes = _parse_includes(topic_elem, element_label=topic_label)
        return TopicSpec(
            id=topic_id,
            skip_html=bool(topic_elem.attrib.get("html")),
            skip_evaluation=_parse_disable_attr(
                topic_elem.attrib.get("evaluate"), attr_name="evaluate"
            ),
            skip_errors=_parse_bool_attr(
                topic_elem.attrib.get("skip-errors"),
                attr_name="skip-errors",
            ),
            http_replay=_parse_optional_bool_attr(
                topic_elem.attrib.get("http-replay"),
                attr_name="http-replay",
            ),
            author=topic_elem.attrib.get("author", ""),
            prog_lang=topic_elem.attrib.get("prog-lang", ""),
            module=topic_elem.attrib.get("module") or None,
            includes=topic_includes,
            export=_parse_export_attr(topic_elem.attrib.get("export")),
        )

    @staticmethod
    def _parse_activity_element(act_elem: ETree.Element, *, section_label: str) -> ActivitySpec:
        """Parse an ``<activity>`` child of a ``<subsection>``.

        An ``<activity>`` carries bilingual ``<de>``/``<en>`` label text and an
        optional ``kind`` / ``id`` attribute. It is never resolved or built —
        it only contributes a non-deck row to ``clm export`` output. Missing
        label text is a hard error (an empty schedule row helps nobody).
        """
        de = act_elem.findtext("de") or ""
        en = act_elem.findtext("en") or ""
        if not (de.strip() or en.strip()):
            raise CourseSpecError(
                f"<activity> in {section_label} has no label text. Provide "
                f"<de> and <en> children with the text to show in the "
                f"schedule (e.g. <activity><de>Projektarbeit</de>"
                f"<en>Project work</en></activity>)."
            )
        kind = (act_elem.attrib.get("kind") or "").strip()
        act_id = (act_elem.attrib.get("id") or "").strip() or None
        return ActivitySpec(text=Text(de=de, en=en), kind=kind, id=act_id)

    @staticmethod
    def _parse_subsection_element(
        sub_elem: ETree.Element, *, section_label: str, keep_disabled: bool
    ) -> SubsectionSpec | None:
        """Parse a ``<subsection>`` child of ``<topics>`` (issue #261).

        Returns ``None`` when the subsection is disabled and
        ``keep_disabled`` is False, so the caller drops it entirely
        (topics and all) — mirroring how a disabled ``<section>`` is
        dropped. ``weekday`` accepts a single token or a comma-separated
        list, each validated against the closed :data:`VALID_WEEKDAYS`
        enum; an unknown token is a hard error.
        """
        enabled = _parse_enabled_attr(
            sub_elem.attrib.get("enabled"), label=f"subsection in {section_label}"
        )
        if not enabled and not keep_disabled:
            return None

        weekdays = _parse_weekdays_attr(sub_elem.attrib.get("weekday"), section_label=section_label)
        optional = _parse_bool_attr(sub_elem.attrib.get("optional"), attr_name="optional")

        name_elem = sub_elem.find("name")
        name: Text | None = None
        if name_elem is not None:
            de = name_elem.findtext("de") or ""
            en = name_elem.findtext("en") or ""
            # An all-empty <name> reads as "absent" so the weekday fallback
            # still fires; otherwise an empty override would blank the label.
            name = Text(de=de, en=en) if (de or en) else None

        topics = [
            CourseSpec._parse_topic_element(topic_elem, section_label=section_label)
            for topic_elem in sub_elem.findall("topic")
        ]
        activities = [
            CourseSpec._parse_activity_element(act_elem, section_label=section_label)
            for act_elem in sub_elem.findall("activity")
        ]
        return SubsectionSpec(
            topics=topics,
            weekdays=weekdays,
            name=name,
            enabled=enabled,
            optional=optional,
            activities=activities,
        )

    @staticmethod
    def _iter_topic_elements(
        section_elem: ETree.Element, *, keep_disabled: bool
    ) -> "list[ETree.Element]":
        """Yield every ``<topic>`` element of a section in document order.

        Descends into ``<subsection>`` wrappers (issue #261), skipping
        disabled subsections unless ``keep_disabled`` is set — the same
        drop-entirely rule applied to disabled sections. Used by
        :meth:`parse_dir_groups` so topic-scoped ``<dir-group>`` elements
        nested inside subsections are still collected.
        """
        topics_elem = section_elem.find("topics")
        if topics_elem is None:
            return []
        result: list[ETree.Element] = []
        for child in topics_elem:
            if child.tag == "topic":
                result.append(child)
            elif child.tag == "subsection":
                # Loose, non-raising check (mirrors the section-level skip in
                # parse_dir_groups): malformed ``enabled`` values are validated
                # authoritatively by parse_sections, which runs first on the
                # from_file path. Re-raising here would only hurt standalone
                # callers with a less section-qualified message.
                disabled = (child.attrib.get("enabled") or "").strip().lower() == "false"
                if disabled and not keep_disabled:
                    continue
                result.extend(child.findall("topic"))
        return result

    @staticmethod
    def parse_sections(root: ETree.Element, *, keep_disabled: bool = False) -> list[SectionSpec]:
        """Parse <section> elements from a course spec root.

        By default, sections with ``enabled="false"`` are dropped entirely.
        Pass ``keep_disabled=True`` to retain them (for tooling that needs to
        enumerate the full roadmap such as ``--include-disabled``).

        Disabled sections are allowed to have missing or empty ``<topics>``
        elements and may reference non-existent topic directories — they are
        never built, so their contents are not validated here.
        """
        sections = []
        for i, section_elem in enumerate(root.findall("sections/section"), start=1):
            name = parse_multilang(root, f"sections/section[{i}]/name")

            enabled = _parse_enabled_attr(
                section_elem.attrib.get("enabled"), label=f"section '{name.en}'"
            )

            section_id = section_elem.attrib.get("id") or None
            section_module = section_elem.attrib.get("module") or None
            section_http_replay = _parse_bool_attr(
                section_elem.attrib.get("http-replay"), attr_name="http-replay"
            )
            section_optional = _parse_bool_attr(
                section_elem.attrib.get("optional"), attr_name="optional"
            )

            if not enabled and not keep_disabled:
                # Skip disabled sections entirely. They may reference topics
                # that do not exist yet, so we do not parse their <topics>.
                continue

            section_label = (
                f"section '{section_id}'" if section_id else f"section '{name.en or name.de}'"
            )
            section_includes = _parse_includes(section_elem, element_label=section_label)

            topics_elem = section_elem.find("topics")
            topics: list[TopicSpec] = []
            subsections: list[SubsectionSpec] = []
            if topics_elem is None:
                if enabled:
                    logger.warning(f"Malformed section: {name.en} has no topics")
                    continue
            else:
                # Walk the direct children of <topics> in document order so
                # bare <topic>s and <subsection> wrappers can be freely
                # interleaved. Subsection topics are flattened into the same
                # `topics` list (the build path), and the grouping is retained
                # in `subsections` for outline/schedule. Comments and unknown
                # children are ignored (their .tag is not a plain string).
                for child in topics_elem:
                    if child.tag == "topic":
                        topics.append(
                            CourseSpec._parse_topic_element(child, section_label=section_label)
                        )
                    elif child.tag == "subsection":
                        subsection = CourseSpec._parse_subsection_element(
                            child,
                            section_label=section_label,
                            keep_disabled=keep_disabled,
                        )
                        if subsection is None:
                            # Disabled subsection, dropped entirely (topics and
                            # all), exactly like a disabled section.
                            continue
                        subsections.append(subsection)
                        # Only ENABLED subsections contribute to the flat build
                        # list — ALWAYS, independent of keep_disabled. A disabled
                        # subsection is retained in `subsections` (so outline
                        # --include-disabled can still render it) but its topics
                        # must never reach `topics`: that is the list `clm build`
                        # flattens, and there is no per-topic enabled gate
                        # downstream. Disabled *sections* are protected from the
                        # build by SectionSelection.resolved_indices, but a
                        # disabled subsection nested in an enabled, selected
                        # section has no such guard — so the gate must live here
                        # to keep `clm build --only-sections` (which parses with
                        # keep_disabled=True) byte-identical and to keep disabled
                        # decks out of the `clm release` ledger.
                        if subsection.enabled:
                            topics.extend(subsection.topics)
            sections.append(
                SectionSpec(
                    name=name,
                    topics=topics,
                    enabled=enabled,
                    id=section_id,
                    module=section_module,
                    includes=section_includes,
                    http_replay=section_http_replay,
                    subsections=subsections,
                    optional=section_optional,
                )
            )
        return sections

    @staticmethod
    def parse_dir_groups(root: ETree.Element, *, keep_disabled: bool = False) -> list[DirGroupSpec]:
        """Collect ``<dir-group>`` elements from the spec.

        Walks topic-scoped ``<dir-group>`` children of ``<section>/<topics>/<topic>``
        first (preserving document order), then top-level
        ``<dir-groups>/<dir-group>`` children. Topic-scoped dir-groups inside
        disabled sections (``enabled="false"``) are skipped by default so that
        the existing "disabled sections are skipped entirely" contract from
        :meth:`parse_sections` also applies to their dir-groups — otherwise
        topic-scoped dir-groups in disabled sections silently leak into the
        build output.

        Pass ``keep_disabled=True`` to retain topic-scoped dir-groups from
        disabled sections, mirroring :meth:`parse_sections`. The ``enabled``
        attribute is not re-validated here; :meth:`parse_sections` is the
        authoritative validator for malformed values.
        """
        dir_groups: list[DirGroupSpec] = []
        # Topic-scoped dir-groups, respecting section enablement so that
        # disabled sections do not leak their dir-groups into the build output.
        # We walk topic elements explicitly (rather than the flat
        # ``topics/topic/dir-group`` path) so each dir-group can record its
        # owning section/topic id (issue #208). Collection order and contents
        # are unchanged: topics in document order, dir-groups within each topic
        # in document order. Topic-id extraction mirrors :meth:`parse_sections`
        # (attribute ``id`` or element text).
        for section_elem in root.findall("sections/section"):
            enabled_attr = section_elem.attrib.get("enabled")
            if (
                enabled_attr is not None
                and enabled_attr.strip().lower() == "false"
                and not keep_disabled
            ):
                continue
            section_id = section_elem.attrib.get("id") or None
            # Walk topic elements in document order, descending into
            # <subsection> wrappers (issue #261) so dir-groups nested inside a
            # subsection's topics are collected too. Disabled subsections are
            # skipped unless keep_disabled, mirroring the section-level skip.
            for topic_elem in CourseSpec._iter_topic_elements(
                section_elem, keep_disabled=keep_disabled
            ):
                topic_id = (
                    (topic_elem.attrib.get("id") or "").strip()
                    or (topic_elem.text or "").strip()
                    or None
                )
                for dg in topic_elem.findall("dir-group"):
                    dir_groups.append(
                        DirGroupSpec.from_element(dg, section_id=section_id, topic_id=topic_id)
                    )
        # Top-level course-scoped dir-groups come after topic-scoped ones,
        # matching the previous document-order traversal from root.iter().
        top = root.find("dir-groups")
        if top is not None:
            for dg in top.findall("dir-group"):
                dir_groups.append(DirGroupSpec.from_element(dg))
        return dir_groups

    @staticmethod
    def parse_output_targets(root: ETree.Element) -> list[OutputTargetSpec]:
        """Parse <output-targets> element from course spec."""
        targets = []
        output_targets_elem = root.find("output-targets")
        if output_targets_elem is None:
            return []  # No targets defined, use legacy behavior

        for target_elem in output_targets_elem.findall("output-target"):
            target = OutputTargetSpec.from_element(target_elem)
            targets.append(target)

        return targets

    @staticmethod
    def parse_release_channels(root: ETree.Element) -> "list[ReleaseChannelsSpec]":
        """Parse every ``<release-channels>`` element (issues #208, #291).

        Returns an empty list when no element is present, in which case the
        solution-release feature is dormant and all other behavior is
        unchanged. Each block is one release stream; uniqueness/naming rules
        are enforced by :meth:`validate`, not here, so a malformed spec can
        still be loaded for inspection.
        """
        return [ReleaseChannelsSpec.from_element(elem) for elem in root.findall("release-channels")]

    @staticmethod
    def parse_tasks(root: ETree.Element) -> "list[TaskSpec]":
        """Parse the optional ``<tasks>`` element (``clm run``).

        Returns an empty list when no element is present — the task-runner
        feature is then dormant. Naming/step rules are enforced by
        :meth:`validate_tasks`, not here, so a malformed spec can still be
        loaded for inspection.
        """
        container = root.find("tasks")
        if container is None:
            return []
        return [TaskSpec.from_element(elem) for elem in container.findall("task")]

    def task(self, name: str) -> "TaskSpec | None":
        """Look up a ``<task>`` by name (None when absent)."""
        for task in self.tasks:
            if task.name == name:
                return task
        return None

    def iter_release_channels(
        self,
    ) -> "Iterator[tuple[ReleaseChannelsSpec, ReleaseChannelSpec]]":
        """Yield every ``(stream block, channel)`` pair in declaration order."""
        for block in self.release_channel_blocks:
            for channel in block.channels:
                yield block, channel

    def is_distributed_target(self, target: OutputTargetSpec) -> bool:
        """Whether ``clm git`` (without ``--target``) manages a repo for *target*.

        An explicit ``distribute`` attribute wins (issue #292). When unset, a
        target that feeds a release stream (named as some ``<release-channels
        source-target>``) defaults to **not** distributed — it is a private
        build input whose content reaches students only through ``clm release
        sync`` into per-cohort channel repos, so ``clm git init`` must not
        create a student-facing repo for it. Every other target defaults to
        distributed (the pre-#292 behavior).
        """
        if target.distribute is not None:
            return target.distribute
        release_sources = {block.source_target for block in self.release_channel_blocks}
        return target.name not in release_sources

    def release_channel_refs(self) -> list[str]:
        """The canonical CLI addresses of all channels (see :func:`release_channel_ref`)."""
        return [
            release_channel_ref(block, channel) for block, channel in self.iter_release_channels()
        ]

    def resolve_release_channel(self, ref: str) -> "tuple[ReleaseChannelsSpec, ReleaseChannelSpec]":
        """Resolve a CLI channel reference to its ``(stream block, channel)`` pair.

        *ref* is either ``<stream>/<channel>`` (exact) or a bare channel name,
        which must be unique across all streams. Raises :class:`CourseSpecError`
        with the available addresses on an unknown or ambiguous reference —
        callers (``clm release``, ``clm git``, ``clm calendar``) surface the
        message verbatim.
        """
        if not self.release_channel_blocks:
            raise CourseSpecError("The spec declares no <release-channels> block.")
        if "/" in ref:
            stream, _, channel_name = ref.partition("/")
            block = next((b for b in self.release_channel_blocks if b.name == stream), None)
            if block is None:
                streams = ", ".join(b.name or "(unnamed)" for b in self.release_channel_blocks)
                raise CourseSpecError(
                    f"Unknown release stream {stream!r}. Defined streams: {streams}."
                )
            channel = block.channel(channel_name)
            if channel is None:
                available = ", ".join(release_channel_ref(block, c) for c in block.channels)
                raise CourseSpecError(
                    f"Unknown channel {channel_name!r} in stream {stream!r}. "
                    f"Defined channels: {available or '(none defined)'}."
                )
            return block, channel

        matches = [(b, c) for b, c in self.iter_release_channels() if c.name == ref]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            available = ", ".join(self.release_channel_refs()) or "(none defined)"
            raise CourseSpecError(f"Unknown channel {ref!r}. Defined channels: {available}.")
        qualified = ", ".join(release_channel_ref(b, c) for b, c in matches)
        raise CourseSpecError(
            f"Channel name {ref!r} exists in several streams; use a qualified address: {qualified}."
        )

    def resolve_section_selectors(self, tokens: list[str]) -> SectionSelection:
        """Resolve a list of ``--only-sections`` selector tokens.

        This method treats ``self.sections`` as the **disabled-inclusive**
        section list. Callers that want accurate index-based selection must
        parse with ``CourseSpec.from_file(..., keep_disabled=True)`` so the
        indices here match the authoring order.

        Each token is resolved independently.

        A token may carry a prefix (``id:``, ``idx:``, ``name:``), in which
        case only that strategy is tried. Bare tokens try, in order:
        exact ID match → 1-based index → case-insensitive substring match
        on either the German or English name. The first strategy that
        yields ≥1 match wins.

        Args:
            tokens: Selector tokens from the CLI. Leading/trailing
                whitespace is stripped. Empty tokens (or an entirely
                empty list) raise :class:`CourseSpecError`.

        Returns:
            A :class:`SectionSelection` whose ``resolved_indices`` point
            into the disabled-inclusive list and whose
            ``skipped_disabled`` records any disabled sections that
            matched a token.

        Raises:
            CourseSpecError: On empty input, zero matches, ambiguous
                bare substring match, or an entirely-disabled selection
                (no enabled section left to build).
        """
        if not tokens:
            raise CourseSpecError(
                "--only-sections requires at least one selector token; got empty list."
            )

        cleaned = [t.strip() for t in tokens]
        if any(not t for t in cleaned):
            raise CourseSpecError(
                "--only-sections received an empty selector token. "
                "Did you pass `--only-sections ''` or a stray comma?"
            )

        if not self.sections:
            raise CourseSpecError("--only-sections: the course spec has no <section> entries.")

        # Track resolved matches in an ordered-set fashion (insertion order
        # preserved, duplicates dropped).
        resolved_seen: dict[int, None] = {}
        skipped_disabled: list[str] = []
        skipped_disabled_seen: set[int] = set()

        def _section_label(idx: int) -> str:
            section = self.sections[idx]
            if section.id:
                return section.id
            return section.name.en or section.name.de

        def _record(idx: int) -> None:
            section = self.sections[idx]
            if not section.enabled:
                if idx not in skipped_disabled_seen:
                    skipped_disabled.append(_section_label(idx))
                    skipped_disabled_seen.add(idx)
                return
            resolved_seen.setdefault(idx, None)

        for raw_token in cleaned:
            prefix, _, rest = raw_token.partition(":")
            lowered = prefix.strip().lower()
            if lowered in ("id", "idx", "name") and rest != "":
                strategy = lowered
                value = rest.strip()
                if not value:
                    raise CourseSpecError(
                        f"--only-sections: selector {raw_token!r} has an "
                        f"empty value after the '{prefix}:' prefix."
                    )
            else:
                strategy = "bare"
                value = raw_token

            matches = self._resolve_selector_token(
                raw_token=raw_token, strategy=strategy, value=value
            )
            for idx in matches:
                _record(idx)

        if not resolved_seen and skipped_disabled:
            raise CourseSpecError(
                "--only-sections: every selected section is disabled "
                f"({', '.join(skipped_disabled)}). Re-enable at least one "
                "section in the spec, or pick a different selector."
            )

        return SectionSelection(
            resolved_indices=list(resolved_seen.keys()),
            skipped_disabled=skipped_disabled,
        )

    def _resolve_selector_token(self, *, raw_token: str, strategy: str, value: str) -> list[int]:
        """Resolve a single selector token to a list of 0-based section indices.

        ``strategy`` is one of ``"id"``, ``"idx"``, ``"name"``, ``"bare"``.
        Raises :class:`CourseSpecError` on zero matches or on an ambiguous
        bare substring match.
        """
        if strategy == "id":
            matches = self._match_by_id(value)
            if not matches:
                raise CourseSpecError(self._zero_match_message(raw_token, "id"))
            return matches

        if strategy == "idx":
            matches = self._match_by_index(value)
            if not matches:
                raise CourseSpecError(self._zero_match_message(raw_token, "idx"))
            return matches

        if strategy == "name":
            matches = self._match_by_name(value)
            if not matches:
                raise CourseSpecError(self._zero_match_message(raw_token, "name"))
            return matches

        # Bare token: ID → index → substring, stop at first strategy that yields ≥1.
        id_matches = self._match_by_id(value)
        if id_matches:
            return id_matches

        idx_matches = self._match_by_index(value)
        if idx_matches:
            return idx_matches

        name_matches = self._match_by_name(value)
        if len(name_matches) > 1:
            match_labels = ", ".join(self._section_listing_entry(i) for i in name_matches)
            raise CourseSpecError(
                f"--only-sections: token {raw_token!r} is an ambiguous "
                f"substring matching multiple sections:\n  {match_labels}\n"
                f"Disambiguate with an explicit prefix: id:..., idx:..., "
                f"or name:..."
            )
        if name_matches:
            return name_matches

        raise CourseSpecError(self._zero_match_message(raw_token, "bare"))

    def _match_by_id(self, value: str) -> list[int]:
        return [i for i, s in enumerate(self.sections) if s.id is not None and s.id == value]

    def _match_by_index(self, value: str) -> list[int]:
        try:
            one_based = int(value)
        except ValueError:
            return []
        zero_based = one_based - 1
        if 0 <= zero_based < len(self.sections):
            return [zero_based]
        return []

    def _match_by_name(self, value: str) -> list[int]:
        needle = value.casefold()
        matches: list[int] = []
        for i, s in enumerate(self.sections):
            de = (s.name.de or "").casefold()
            en = (s.name.en or "").casefold()
            if needle in de or needle in en:
                matches.append(i)
        return matches

    def _section_listing_entry(self, idx: int) -> str:
        """Human-readable one-line description of a section for error messages."""
        section = self.sections[idx]
        parts = [f"#{idx + 1}"]
        if section.id:
            parts.append(f"id={section.id!r}")
        parts.append(f"de={section.name.de!r}")
        parts.append(f"en={section.name.en!r}")
        if not section.enabled:
            parts.append("(disabled)")
        return " ".join(parts)

    def _zero_match_message(self, raw_token: str, strategy: str) -> str:
        strategy_hint = {
            "id": "(tried: id match only)",
            "idx": "(tried: 1-based index match only)",
            "name": "(tried: case-insensitive substring on de/en name only)",
            "bare": "(tried: id → 1-based index → case-insensitive substring on de/en name)",
        }[strategy]
        listing = "\n  ".join(self._section_listing_entry(i) for i in range(len(self.sections)))
        return (
            f"--only-sections: selector {raw_token!r} did not match any "
            f"section {strategy_hint}.\n"
            f"Available sections:\n  {listing}"
        )

    def validate(self) -> list[str]:
        """Validate the entire course spec.

        Returns:
            List of validation error messages (empty if valid)
        """
        errors: list[str] = []

        # Validate output targets
        target_names: set[str] = set()
        target_paths: set[str] = set()

        for target in self.output_targets:
            # Validate individual target
            errors.extend(target.validate())

            # Check for duplicate names
            if target.name in target_names:
                errors.append(f"Duplicate output target name: '{target.name}'")
            target_names.add(target.name)

            # Check for duplicate paths
            if target.path in target_paths:
                errors.append(
                    f"Duplicate output target path: '{target.path}' "
                    f"(used by target '{target.name}')"
                )
            target_paths.add(target.path)

            # Cross-validate JupyterLite opt-in: a target that lists the
            # jupyterlite format must have an effective <jupyterlite> config
            # available — either target-level or course-level. Neither ⇒
            # the target cannot be built and the user needs to fix the spec.
            if target.formats and "jupyterlite" in target.formats:
                effective = target.jupyterlite or self.jupyterlite
                if effective is None:
                    errors.append(
                        f"Target '{target.name}' requests format 'jupyterlite' "
                        f"but no <jupyterlite> config is defined at course or "
                        f"target level. Add a <jupyterlite> block — see "
                        f"'clm info jupyterlite' for the required fields."
                    )

        errors.extend(self._validate_release_channels(target_names))
        errors.extend(self.validate_tasks())

        return errors

    def validate_tasks(self) -> list[str]:
        """Validate the ``<tasks>`` block structurally (``clm run``).

        Structural rules only — placeholder and command-existence checks need
        step resolution and the CLI command tree, so they live in
        :mod:`clm.core.tasks` / the ``clm run`` command and are surfaced by
        ``clm validate``. Public so the spec validator can reuse the checks
        without running full-spec validation.
        """
        errors: list[str] = []
        task_names: set[str] = set()
        for task in self.tasks:
            label = f"<task name={task.name!r}>" if task.name else "<task>"
            if not task.name:
                errors.append("Every <task> needs a name attribute.")
            elif task.name in task_names:
                errors.append(f"Duplicate task name: {task.name!r}")
            task_names.add(task.name)

            if not task.steps:
                errors.append(f"{label} has no <step> elements.")
            for i, step in enumerate(task.steps, start=1):
                if not step:
                    errors.append(f"{label}: step {i} is empty.")
                elif step.split()[0] == "run":
                    errors.append(
                        f"{label}: step {i} invokes 'clm run' — tasks cannot invoke other tasks."
                    )
        return errors

    def _validate_release_channels(self, target_names: set[str]) -> list[str]:
        """Validate the ``<release-channels>`` blocks (issues #208, #291).

        Stream naming: with several blocks every block needs a unique,
        ``/``-free ``name`` (the stream name used in ``stream/channel``
        addressing). Channels need unique, ``/``-free names within their block,
        and a channel's destination ``path`` and ``ledger`` must be unique
        across *all* streams — two streams writing the same destination would
        fight over one frozen manifest.
        """
        errors: list[str] = []
        blocks = self.release_channel_blocks

        block_names: set[str] = set()
        dest_paths: dict[str, str] = {}
        ledgers: dict[str, str] = {}
        for block in blocks:
            block_label = (
                f"<release-channels name={block.name!r}>" if block.name else "<release-channels>"
            )
            if len(blocks) > 1 and not block.name:
                errors.append(
                    "With several <release-channels> blocks every block needs a "
                    "unique name attribute (the release stream name)."
                )
            if block.name:
                if "/" in block.name:
                    errors.append(
                        f"{block_label}: stream names must not contain '/' "
                        f"(it separates stream and channel in CLI addresses)."
                    )
                if block.name in block_names:
                    errors.append(f"Duplicate release stream name: {block.name!r}")
                block_names.add(block.name)

            if not block.source_target:
                errors.append(f"{block_label} must declare a source-target attribute.")
            elif self.output_targets and block.source_target not in target_names:
                errors.append(
                    f"{block_label}: source-target {block.source_target!r} does not "
                    f"name an <output-target>."
                )

            errors.extend(_validate_evergreen_patterns(block.evergreen, block_label))

            channel_names: set[str] = set()
            for channel in block.channels:
                ref = release_channel_ref(block, channel)
                if not channel.name:
                    errors.append(f"{block_label}: every <channel> needs a name attribute.")
                elif "/" in channel.name:
                    errors.append(
                        f"{block_label}: channel name {channel.name!r} must not contain '/'."
                    )
                elif channel.name in channel_names:
                    errors.append(f"{block_label}: duplicate channel name {channel.name!r}.")
                channel_names.add(channel.name)

                if not channel.path:
                    errors.append(f"Channel '{ref}' needs a path attribute.")
                elif channel.path in dest_paths:
                    errors.append(
                        f"Channels '{dest_paths[channel.path]}' and '{ref}' share the "
                        f"destination path {channel.path!r}; every channel needs its own "
                        f"destination repository."
                    )
                else:
                    dest_paths[channel.path] = ref

                if not channel.ledger:
                    errors.append(f"Channel '{ref}' needs a ledger attribute.")
                elif channel.ledger in ledgers:
                    errors.append(
                        f"Channels '{ledgers[channel.ledger]}' and '{ref}' share the "
                        f"ledger {channel.ledger!r}; every channel needs its own ledger."
                    )
                else:
                    ledgers[channel.ledger] = ref

                if channel.lang and channel.lang not in VALID_LANGUAGES:
                    errors.append(
                        f"Channel '{ref}': invalid lang {channel.lang!r}. "
                        f"Valid values: {sorted(VALID_LANGUAGES)}"
                    )

                for share in channel.share_with:
                    if not share.group:
                        errors.append(f"Channel '{ref}': <share-with> needs a group path.")
                    if share.access not in VALID_SHARE_ACCESS:
                        errors.append(
                            f"Channel '{ref}': invalid share-with access "
                            f"{share.access!r}. Valid values: {sorted(VALID_SHARE_ACCESS)}"
                        )

                # Channel-own patterns only — inherited ones were validated at
                # block level and would otherwise repeat per channel.
                own_evergreen = tuple(p for p in channel.evergreen if p not in set(block.evergreen))
                errors.extend(_validate_evergreen_patterns(own_evergreen, f"Channel '{ref}'"))

        return errors

    @classmethod
    def from_file(cls, xml_file: Path | io.IOBase, *, keep_disabled: bool = False) -> "CourseSpec":
        """Parse a course specification from an XML file.

        Args:
            xml_file: Path to the XML file or file-like object
            keep_disabled: If True, retain sections with ``enabled="false"``
                so tooling like ``--include-disabled`` can enumerate the full
                roadmap. Default: False (disabled sections are dropped).

        Returns:
            Parsed CourseSpec object

        Raises:
            CourseSpecError: If the file cannot be parsed or is invalid
        """
        file_name = str(xml_file) if isinstance(xml_file, Path) else "<file object>"

        try:
            tree = ETree.parse(xml_file)
        except ETree.ParseError as e:
            # Extract line/column info if available
            if hasattr(e, "position") and e.position:
                line, col = e.position
                location = f" at line {line}, column {col}"
            else:
                location = ""

            raise CourseSpecError(
                f"XML parsing error in '{file_name}'{location}:\n"
                f"  {e}\n\n"
                f"Common causes:\n"
                f"  - Unclosed XML tags (missing </tag>)\n"
                f"  - Mismatched tag names\n"
                f"  - Invalid characters (use &amp; for &, &lt; for <)\n"
                f"  - Missing XML declaration or encoding issues\n\n"
                f"Tip: Use an XML validator to check your spec file syntax."
            ) from e
        except FileNotFoundError:
            raise CourseSpecError(
                f"Spec file not found: '{file_name}'\n\n"
                f"Please verify the file path exists and is accessible."
            ) from None
        except PermissionError:
            raise CourseSpecError(
                f"Permission denied reading spec file: '{file_name}'\n\n"
                f"Please check file permissions."
            ) from None
        except Exception as e:
            raise CourseSpecError(
                f"Failed to read spec file '{file_name}': {type(e).__name__}: {e}"
            ) from e

        root = tree.getroot()

        prog_lang_elem = root.find("prog-lang")
        prog_lang = prog_lang_elem.text if prog_lang_elem is not None else ""
        if prog_lang is None:
            prog_lang = ""

        # Parse project slug from both locations
        top_level_slug = element_text(root, "project-slug") or None
        github_spec = GitHubSpec.from_element(root.find("github"))
        github_slug = github_spec.project_slug

        # Resolve effective slug with deprecation/override warnings
        if top_level_slug and github_slug:
            effective_slug = top_level_slug
            logger.warning(
                "project-slug is defined both at top level and inside <github>. "
                "Using top-level value '%s'; the <github> value is ignored.",
                top_level_slug,
            )
        elif top_level_slug:
            effective_slug = top_level_slug
        elif github_slug:
            effective_slug = github_slug
            logger.warning(
                "project-slug inside <github> is deprecated. "
                "Move <project-slug>%s</project-slug> to the top level of <course>.",
                github_slug,
            )
        else:
            effective_slug = None

        # Parse author (simple text element, defaults handled by attrs)
        author = element_text(root, "author") or "Dr. Matthias Hölzl"

        # Parse organization (bilingual element)
        org_elem = root.find("organization")
        if org_elem is not None:
            organization = Text(**{child.tag: (child.text or "") for child in org_elem})
        else:
            organization = Text(de="Coding-Akademie München", en="Coding-Academy Munich")

        return cls(
            name=parse_multilang(root, "name"),
            prog_lang=prog_lang,
            description=parse_multilang(root, "description"),
            certificate=parse_multilang(root, "certificate"),
            sections=cls.parse_sections(root, keep_disabled=keep_disabled),
            project_slug=effective_slug,
            github=github_spec,
            dictionaries=cls.parse_dir_groups(root, keep_disabled=keep_disabled),
            output_targets=cls.parse_output_targets(root),
            release_channel_blocks=cls.parse_release_channels(root),
            tasks=cls.parse_tasks(root),
            image_options=ImageOptionsSpec.from_element(root.find("image-options")),
            jupyterlite=JupyterLiteConfig.from_element(root.find("jupyterlite")),
            author=author,
            organization=organization,
        )


def parse_multilang(root: ETree.Element, tag: str) -> Text:
    element = root.find(tag)
    if element is None:
        return Text(de="", en="")
    return Text(**{child.tag: (child.text or "") for child in element})
