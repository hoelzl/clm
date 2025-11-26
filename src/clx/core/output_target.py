"""Runtime representation of output targets for multi-directory output.

This module provides the OutputTarget class, which is the runtime representation
of an output target with resolved paths and filtering capabilities.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define, field

from clx.core.course_spec import (
    VALID_FORMATS,
    VALID_KINDS,
    VALID_LANGUAGES,
    OutputTargetSpec,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Default values for "all" semantics
ALL_KINDS: frozenset[str] = frozenset(VALID_KINDS)
ALL_FORMATS: frozenset[str] = frozenset(VALID_FORMATS)
ALL_LANGUAGES: frozenset[str] = frozenset(VALID_LANGUAGES)


@define
class OutputTarget:
    """Runtime representation of an output target.

    Resolves paths and provides filtering for output generation.
    Each target specifies which kinds, formats, and languages to generate
    and where to write the output.

    Attributes:
        name: Unique identifier for this target
        output_root: Resolved absolute path to output directory
        kinds: Set of output kinds to generate
        formats: Set of output formats to generate
        languages: Set of languages to generate
    """

    name: str
    output_root: Path
    kinds: frozenset[str] = field(factory=lambda: ALL_KINDS)
    formats: frozenset[str] = field(factory=lambda: ALL_FORMATS)
    languages: frozenset[str] = field(factory=lambda: ALL_LANGUAGES)

    @classmethod
    def from_spec(
        cls,
        spec: OutputTargetSpec,
        course_root: Path,
    ) -> "OutputTarget":
        """Create OutputTarget from spec with resolved paths.

        Args:
            spec: The parsed target specification
            course_root: Course root directory for resolving relative paths

        Returns:
            OutputTarget with resolved absolute paths
        """
        # Resolve path (relative to course root or absolute)
        path = Path(spec.path)
        if not path.is_absolute():
            output_root = course_root / path
        else:
            output_root = path

        # Convert None to "all" semantics
        kinds = frozenset(spec.kinds) if spec.kinds else ALL_KINDS
        formats = frozenset(spec.formats) if spec.formats else ALL_FORMATS
        languages = frozenset(spec.languages) if spec.languages else ALL_LANGUAGES

        return cls(
            name=spec.name,
            output_root=output_root.resolve(),
            kinds=kinds,
            formats=formats,
            languages=languages,
        )

    @classmethod
    def default_target(cls, output_root: Path) -> "OutputTarget":
        """Create a default target that generates all outputs.

        Used when no output-targets are specified in the course spec
        or when --output-dir CLI flag is used.

        Args:
            output_root: Root directory for output

        Returns:
            OutputTarget configured to generate all kinds/formats/languages
        """
        return cls(
            name="default",
            output_root=output_root.resolve(),
            kinds=ALL_KINDS,
            formats=ALL_FORMATS,
            languages=ALL_LANGUAGES,
        )

    def includes_kind(self, kind: str) -> bool:
        """Check if this target includes the given kind.

        Args:
            kind: Output kind (e.g., "code-along", "completed", "speaker")

        Returns:
            True if this target should generate this kind
        """
        return kind in self.kinds

    def includes_format(self, fmt: str) -> bool:
        """Check if this target includes the given format.

        Args:
            fmt: Output format (e.g., "html", "notebook", "code")

        Returns:
            True if this target should generate this format
        """
        return fmt in self.formats

    def includes_language(self, lang: str) -> bool:
        """Check if this target includes the given language.

        Args:
            lang: Language code (e.g., "de", "en")

        Returns:
            True if this target should generate this language
        """
        return lang in self.languages

    def should_generate(self, lang: str, fmt: str, kind: str) -> bool:
        """Check if this output combination should be generated for this target.

        All format/kind combinations are valid - there are no special restrictions
        on which formats can be generated for which kinds. This gives users full
        control via their output target configuration.

        Args:
            lang: Language code (e.g., "de", "en")
            fmt: Format (e.g., "html", "notebook", "code")
            kind: Kind (e.g., "code-along", "completed", "speaker")

        Returns:
            True if this combination should be generated for this target
        """
        return (
            self.includes_language(lang) and self.includes_format(fmt) and self.includes_kind(kind)
        )

    def with_cli_filters(
        self,
        languages: list[str] | None,
        kinds: list[str] | None,
    ) -> "OutputTarget":
        """Apply CLI-level filters to create a new filtered target.

        CLI filters are intersected with target filters, narrowing the output.

        Args:
            languages: CLI language filter (None = no additional filter)
            kinds: CLI kinds filter (None = no additional filter)

        Returns:
            New OutputTarget with filters applied
        """
        new_languages = self.languages & frozenset(languages) if languages else self.languages
        new_kinds = self.kinds & frozenset(kinds) if kinds else self.kinds

        return OutputTarget(
            name=self.name,
            output_root=self.output_root,
            kinds=new_kinds,
            formats=self.formats,
            languages=new_languages,
        )

    def __repr__(self) -> str:
        return (
            f"OutputTarget(name={self.name!r}, "
            f"output_root={self.output_root}, "
            f"kinds={sorted(self.kinds)}, "
            f"formats={sorted(self.formats)}, "
            f"languages={sorted(self.languages)})"
        )
