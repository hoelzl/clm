import io
import logging
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
    SPEAKER = "speaker"


class OutputFormat(Enum):
    """Valid output format values."""

    HTML = "html"
    NOTEBOOK = "notebook"
    CODE = "code"


@frozen
class TopicSpec:
    id: str
    skip_html: bool = False
    author: str = ""
    prog_lang: str = ""


@frozen
class SectionSpec:
    name: Text
    topics: list[TopicSpec] = Factory(list)
    enabled: bool = True
    id: str | None = None


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


@frozen
class DirGroupSpec:
    name: Text
    path: str
    subdirs: list[str] | None = None
    include_root_files: bool = False
    recursive: bool = True

    @classmethod
    def from_element(cls, element: ETree.Element):
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
        )


# Valid values for output target configuration
VALID_KINDS: frozenset[str] = frozenset({"code-along", "completed", "speaker", "partial"})
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
    """

    name: str
    path: str
    kinds: list[str] | None = None  # None means "all"
    formats: list[str] | None = None
    languages: list[str] | None = None
    remote_path: str = ""
    jupyterlite: "JupyterLiteConfig | None" = None

    @classmethod
    def from_element(cls, element: ETree.Element) -> "OutputTargetSpec":
        """Parse an <output-target> XML element."""
        name = element.get("name", "default")
        path = element_text(element, "path")
        remote_path = element_text(element, "remote-path") or ""

        # Parse optional filter lists
        kinds = cls._parse_list(element, "kinds", "kind")
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
    image_options: ImageOptionsSpec = field(factory=ImageOptionsSpec)
    jupyterlite: JupyterLiteConfig | None = None
    author: str = "Dr. Matthias Hölzl"
    organization: Text = field(
        factory=lambda: Text(de="Coding-Akademie München", en="Coding-Academy Munich")
    )

    @property
    def topics(self) -> list[TopicSpec]:
        return [topic for section in self.sections for topic in section.topics]

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

            enabled_attr = section_elem.attrib.get("enabled")
            if enabled_attr is None:
                enabled = True
            else:
                normalized = enabled_attr.strip().lower()
                if normalized == "true":
                    enabled = True
                elif normalized == "false":
                    enabled = False
                else:
                    raise CourseSpecError(
                        f"Invalid value for 'enabled' attribute on section "
                        f"'{name.en}': {enabled_attr!r}. "
                        f"Expected 'true' or 'false' (case-insensitive)."
                    )

            section_id = section_elem.attrib.get("id") or None

            if not enabled and not keep_disabled:
                # Skip disabled sections entirely. They may reference topics
                # that do not exist yet, so we do not parse their <topics>.
                continue

            topics_elem = section_elem.find("topics")
            if topics_elem is None:
                if enabled:
                    logger.warning(f"Malformed section: {name.en} has no topics")
                    continue
                topics = []
            else:
                topics = [
                    TopicSpec(
                        id=(topic_elem.text or "").strip(),
                        skip_html=bool(topic_elem.attrib.get("html")),
                        author=topic_elem.attrib.get("author", ""),
                        prog_lang=topic_elem.attrib.get("prog-lang", ""),
                    )
                    for topic_elem in topics_elem.findall("topic")
                ]
            sections.append(SectionSpec(name=name, topics=topics, enabled=enabled, id=section_id))
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
        for section_elem in root.findall("sections/section"):
            enabled_attr = section_elem.attrib.get("enabled")
            if (
                enabled_attr is not None
                and enabled_attr.strip().lower() == "false"
                and not keep_disabled
            ):
                continue
            for dg in section_elem.iterfind("topics/topic/dir-group"):
                dir_groups.append(DirGroupSpec.from_element(dg))
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
