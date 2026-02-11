import io
import logging
from enum import Enum
from pathlib import Path
from xml.etree import ElementTree as ETree

from attr import Factory, field, frozen

from clm.core.utils.text_utils import Text

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


@frozen
class SectionSpec:
    name: Text
    topics: list[TopicSpec] = Factory(list)


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
        <include-speaker>true</include-speaker>
    </github>

    Attributes:
        project_slug: Base name for repositories (e.g., "machine-learning-azav")
        repository_base: Base URL for repositories (e.g., "https://github.com/Org")
        include_speaker: Whether to create repos for speaker targets (default: False)
    """

    project_slug: str | None = None
    repository_base: str | None = None
    include_speaker: bool = False

    @classmethod
    def from_element(cls, element: ETree.Element | None) -> "GitHubSpec":
        """Parse a <github> XML element."""
        if element is None:
            return cls()

        project_slug = element_text(element, "project-slug") or None
        repository_base = element_text(element, "repository-base") or None

        include_speaker_elem = element.find("include-speaker")
        include_speaker = (
            include_speaker_elem is not None and (include_speaker_elem.text or "").lower() == "true"
        )

        return cls(
            project_slug=project_slug,
            repository_base=repository_base,
            include_speaker=include_speaker,
        )

    @property
    def is_configured(self) -> bool:
        """Check if git configuration is properly set up."""
        return bool(self.project_slug and self.repository_base)

    def derive_remote_url(
        self,
        target_name: str,
        language: str,
        is_first_target: bool = False,
    ) -> str | None:
        """Derive the remote URL for a target+language combination.

        URL pattern: {repository-base}/{project-slug}-{lang}[-{target-suffix}]

        For implicit targets (public/speaker):
        - public: {slug}-{lang}
        - speaker: {slug}-{lang}-speaker (only if include_speaker=True)

        For explicit targets:
        - First target (usually code-along): {slug}-{lang} (no suffix)
        - Other targets: {slug}-{lang}-{target-name}
        - speaker target: {slug}-{lang}-speaker

        Returns None if git config is not properly configured or if speaker
        is requested but include_speaker is False.
        """
        if not self.is_configured:
            return None

        # Determine suffix based on target name
        if target_name in ("public", "default") or is_first_target:
            suffix = ""
        elif target_name == "speaker":
            if not self.include_speaker:
                return None
            suffix = "-speaker"
        else:
            suffix = f"-{target_name}"

        return f"{self.repository_base}/{self.project_slug}-{language}{suffix}"


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
VALID_KINDS: frozenset[str] = frozenset({"code-along", "completed", "speaker"})
VALID_FORMATS: frozenset[str] = frozenset({"html", "notebook", "code"})
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
    """

    name: str
    path: str
    kinds: list[str] | None = None  # None means "all"
    formats: list[str] | None = None
    languages: list[str] | None = None

    @classmethod
    def from_element(cls, element: ETree.Element) -> "OutputTargetSpec":
        """Parse an <output-target> XML element."""
        name = element.get("name", "default")
        path = element_text(element, "path")

        # Parse optional filter lists
        kinds = cls._parse_list(element, "kinds", "kind")
        formats = cls._parse_list(element, "formats", "format")
        languages = cls._parse_list(element, "languages", "language")

        return cls(
            name=name,
            path=path,
            kinds=kinds,
            formats=formats,
            languages=languages,
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
class CourseSpec:
    name: Text
    prog_lang: str
    description: Text
    certificate: Text
    sections: list[SectionSpec]
    github: GitHubSpec = field(factory=GitHubSpec)
    dictionaries: list[DirGroupSpec] = field(factory=list)
    output_targets: list[OutputTargetSpec] = field(factory=list)

    @property
    def topics(self) -> list[TopicSpec]:
        return [topic for section in self.sections for topic in section.topics]

    @staticmethod
    def parse_sections(root: ETree.Element) -> list[SectionSpec]:
        sections = []
        for i, section_elem in enumerate(root.findall("sections/section"), start=1):
            name = parse_multilang(root, f"sections/section[{i}]/name")
            topics_elem = section_elem.find("topics")
            if topics_elem is None:
                logger.warning(f"Malformed section: {name.en} has no topics")
                continue
            topics = [
                TopicSpec(
                    id=(topic_elem.text or "").strip(),
                    skip_html=bool(topic_elem.attrib.get("html")),
                )
                for topic_elem in topics_elem.findall("topic")
            ]
            sections.append(SectionSpec(name=name, topics=topics))
        return sections

    @staticmethod
    def parse_dir_groups(root: ETree.Element) -> list[DirGroupSpec]:
        dir_groups = []
        for dir_group in root.iter("dir-group"):
            dir_groups.append(DirGroupSpec.from_element(dir_group))
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

        return errors

    @classmethod
    def from_file(cls, xml_file: Path | io.IOBase) -> "CourseSpec":
        """Parse a course specification from an XML file.

        Args:
            xml_file: Path to the XML file or file-like object

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

        return cls(
            name=parse_multilang(root, "name"),
            prog_lang=prog_lang,
            description=parse_multilang(root, "description"),
            certificate=parse_multilang(root, "certificate"),
            github=GitHubSpec.from_element(root.find("github")),
            sections=cls.parse_sections(root),
            dictionaries=cls.parse_dir_groups(root),
            output_targets=cls.parse_output_targets(root),
        )


def parse_multilang(root: ETree.Element, tag: str) -> Text:
    element = root.find(tag)
    if element is None:
        return Text(de="", en="")
    return Text(**{child.tag: (child.text or "") for child in element})
