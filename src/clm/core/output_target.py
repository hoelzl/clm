"""Runtime representation of output targets for multi-directory output.

This module provides the OutputTarget class, which is the runtime representation
of an output target with resolved paths and filtering capabilities.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define, field

from clm.core.course_spec import (
    VALID_KINDS,
    VALID_LANGUAGES,
    JupyterLiteConfig,
    OutputTargetSpec,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Default values when a target does not explicitly list kinds/formats/languages.
#
# DEFAULT_FORMATS is deliberately a literal set rather than derived from
# course_spec.VALID_FORMATS so that adding a new opt-in format (e.g.,
# "jupyterlite") to VALID_FORMATS does not silently expand what every
# existing course builds. The opt-in contract: a format only runs when a
# target lists it explicitly.
ALL_KINDS: frozenset[str] = frozenset(VALID_KINDS)
DEFAULT_FORMATS: frozenset[str] = frozenset({"html", "notebook", "code"})
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
        is_explicit: True if this target was explicitly specified in the spec file.
            When True, output paths start directly with language directory (e.g., De/En).
            When False (default target), paths include public/speaker subdirectories.
    """

    name: str
    output_root: Path
    kinds: frozenset[str] = field(factory=lambda: ALL_KINDS)
    formats: frozenset[str] = field(factory=lambda: DEFAULT_FORMATS)
    languages: frozenset[str] = field(factory=lambda: ALL_LANGUAGES)
    is_explicit: bool = False
    jupyterlite: JupyterLiteConfig | None = None
    course_jupyterlite: JupyterLiteConfig | None = None

    @classmethod
    def from_spec(
        cls,
        spec: OutputTargetSpec,
        course_root: Path,
        course_jupyterlite: JupyterLiteConfig | None = None,
    ) -> "OutputTarget":
        """Create OutputTarget from spec with resolved paths.

        Args:
            spec: The parsed target specification
            course_root: Course root directory for resolving relative paths
            course_jupyterlite: Course-level ``<jupyterlite>`` config, used as
                the fallback when this target does not declare its own.

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
        formats = frozenset(spec.formats) if spec.formats else DEFAULT_FORMATS
        languages = frozenset(spec.languages) if spec.languages else ALL_LANGUAGES

        return cls(
            name=spec.name,
            output_root=output_root.resolve(),
            kinds=kinds,
            formats=formats,
            languages=languages,
            is_explicit=True,  # Targets from spec are explicitly defined
            jupyterlite=spec.jupyterlite,
            course_jupyterlite=course_jupyterlite,
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
            formats=DEFAULT_FORMATS,
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

    def effective_jupyterlite_config(self) -> JupyterLiteConfig | None:
        """Return the effective JupyterLite config for this target.

        Target-level ``<jupyterlite>`` (when present) replaces the course-level
        block wholesale; field-wise merging is deliberately not supported so
        that reasoning about which wheel list is active stays trivial. Returns
        ``None`` when neither level declares a config — in that case, any
        target requesting the ``jupyterlite`` format must fail validation.
        """
        return self.jupyterlite or self.course_jupyterlite

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
            is_explicit=self.is_explicit,
            jupyterlite=self.jupyterlite,
            course_jupyterlite=self.course_jupyterlite,
        )

    def __repr__(self) -> str:
        return (
            f"OutputTarget(name={self.name!r}, "
            f"output_root={self.output_root}, "
            f"kinds={sorted(self.kinds)}, "
            f"formats={sorted(self.formats)}, "
            f"languages={sorted(self.languages)})"
        )
