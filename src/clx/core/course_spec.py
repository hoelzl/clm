import io
import logging
from enum import Enum
from pathlib import Path
from xml.etree import ElementTree as ETree

from attr import Factory, field, frozen

from clx.core.utils.text_utils import Text

logger = logging.getLogger(__name__)


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
class DirGroupSpec:
    name: Text
    path: str
    subdirs: list[str] | None = None

    @classmethod
    def from_element(cls, element: ETree.Element):
        subdirs = find_subdirs(element)
        name = Text.from_string(element_text(element, "name"))
        return cls(
            name=name,
            path=element_text(element, "path"),
            subdirs=subdirs,
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
    github_repo: Text
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
        tree = ETree.parse(xml_file)
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
            github_repo=parse_multilang(root, "github"),
            sections=cls.parse_sections(root),
            dictionaries=cls.parse_dir_groups(root),
            output_targets=cls.parse_output_targets(root),
        )


def parse_multilang(root: ETree.Element, tag: str) -> Text:
    element = root.find(tag)
    if element is None:
        return Text(de="", en="")
    return Text(**{child.tag: (child.text or "") for child in element})
