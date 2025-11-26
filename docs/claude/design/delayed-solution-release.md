# Design: Delayed Solution Release (Multiple Output Directories)

**Status**: Draft
**Created**: 2025-11-26
**Author**: Claude (AI Assistant)
**Related Requirements**: [delayed-solution-release.md](../requirements/delayed-solution-release.md)

## Overview

This document describes the architectural design for supporting multiple output directories with selective content generation in CLX. The feature enables "delayed solution release" where instructors can release code-along materials immediately while withholding completed solutions.

**Design Principles**:
1. **Backward Compatible**: Existing course specs work unchanged
2. **Minimal Invasive**: Changes concentrated in spec parsing and output generation
3. **Efficient**: Share execution results across targets; don't re-execute notebooks
4. **Clear Semantics**: Each target is self-contained with predictable output
5. **Uniform Format Handling**: All formats (html, notebook, code) are treated identically with no format-kind dependencies

**Breaking Change**: The `code` format is no longer restricted to the `completed` kind. Previously, code output was only generated for completed notebooks. Now, code output can be generated for any kind (code-along, completed, speaker). This simplifies the implementation and gives users full control via output targets.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         XML Course Spec                                  │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  <output-targets>                                                │   │
│  │    <output-target name="students">...</output-target>            │   │
│  │    <output-target name="solutions">...</output-target>           │   │
│  │  </output-targets>                                               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────┬───────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      CourseSpec (Extended)                               │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  output_targets: list[OutputTargetSpec]                          │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────┬───────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Course                                           │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  output_targets: list[OutputTarget]                              │   │
│  │                                                                   │   │
│  │  For each file, for each target:                                 │   │
│  │    → Generate only the kinds/formats/languages in target config  │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────┬───────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    Output Generation                                     │
│  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐        │
│  │ Target: students │ │ Target: solutions│ │ Target: instructor│       │
│  │ Path: ./students │ │ Path: ./solutions│ │ Path: ./private   │       │
│  │ Kinds: code-along│ │ Kinds: completed │ │ Kinds: speaker    │       │
│  └────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘        │
│           │                    │                    │                   │
│           ▼                    ▼                    ▼                   │
│    ./students/            ./solutions/         ./private/               │
│      De/                    De/                   De/                   │
│      En/                    En/                   En/                   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Component Design

### 1. OutputTargetSpec (New Data Class)

**Purpose**: Parse and store output target configuration from XML

**Location**: `src/clx/core/course_spec.py`

```python
from enum import Enum
from pathlib import Path
from typing import Optional

from attrs import Factory, frozen


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
        item_tag: str
    ) -> list[str] | None:
        """Parse a list of values from nested XML elements."""
        container = element.find(container_tag)
        if container is None:
            return None
        return [
            (item.text or "").strip()
            for item in container.findall(item_tag)
            if item.text
        ]

    def validate(self) -> list[str]:
        """Validate the target specification.

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        if not self.name:
            errors.append("Output target must have a name attribute")

        if not self.path:
            errors.append(f"Output target '{self.name}' must have a <path> element")

        # Validate kinds
        valid_kinds = {"code-along", "completed", "speaker"}
        if self.kinds:
            for kind in self.kinds:
                if kind not in valid_kinds:
                    errors.append(
                        f"Invalid kind '{kind}' in target '{self.name}'. "
                        f"Valid values: {valid_kinds}"
                    )

        # Validate formats
        valid_formats = {"html", "notebook", "code"}
        if self.formats:
            for fmt in self.formats:
                if fmt not in valid_formats:
                    errors.append(
                        f"Invalid format '{fmt}' in target '{self.name}'. "
                        f"Valid values: {valid_formats}"
                    )

        # Validate languages (basic check, could be extended)
        valid_languages = {"de", "en"}
        if self.languages:
            for lang in self.languages:
                if lang not in valid_languages:
                    errors.append(
                        f"Invalid language '{lang}' in target '{self.name}'. "
                        f"Valid values: {valid_languages}"
                    )

        return errors
```

### 2. Extended CourseSpec

**Purpose**: Add output targets parsing to existing CourseSpec

**Location**: `src/clx/core/course_spec.py`

**Changes**:

```python
@frozen
class CourseSpec:
    name: Text
    prog_lang: str
    description: Text
    certificate: Text
    sections: list[SectionSpec]
    github_repo: Text
    dictionaries: list[DirGroupSpec] = field(factory=list)
    output_targets: list[OutputTargetSpec] = field(factory=list)  # NEW

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

    @classmethod
    def from_file(cls, xml_file: Path | io.IOBase) -> "CourseSpec":
        tree = ETree.parse(xml_file)
        root = tree.getroot()

        # ... existing parsing ...

        return cls(
            name=parse_multilang(root, "name"),
            prog_lang=prog_lang,
            description=parse_multilang(root, "description"),
            certificate=parse_multilang(root, "certificate"),
            github_repo=parse_multilang(root, "github"),
            sections=cls.parse_sections(root),
            dictionaries=cls.parse_dir_groups(root),
            output_targets=cls.parse_output_targets(root),  # NEW
        )

    def validate(self) -> list[str]:
        """Validate the entire course spec.

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        # Validate output targets
        target_names = set()
        target_paths = set()

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
```

### 3. OutputTarget (Runtime Class)

**Purpose**: Runtime representation of output target with resolved paths

**Location**: `src/clx/core/output_target.py` (new file)

```python
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import Factory, define, field

from clx.core.course_spec import OutputTargetSpec

if TYPE_CHECKING:
    from clx.core.course import Course

logger = logging.getLogger(__name__)


@define
class OutputTarget:
    """Runtime representation of an output target.

    Resolves paths and provides filtering for output generation.
    """
    name: str
    output_root: Path
    kinds: frozenset[str] = field(factory=frozenset)
    formats: frozenset[str] = field(factory=frozenset)
    languages: frozenset[str] = field(factory=frozenset)

    # All valid values for reference
    ALL_KINDS: frozenset[str] = frozenset({"code-along", "completed", "speaker"})
    ALL_FORMATS: frozenset[str] = frozenset({"html", "notebook", "code"})
    ALL_LANGUAGES: frozenset[str] = frozenset({"de", "en"})

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
        """
        # Resolve path (relative to course root or absolute)
        path = Path(spec.path)
        if not path.is_absolute():
            output_root = course_root / path
        else:
            output_root = path

        # Convert None to "all" semantics
        kinds = frozenset(spec.kinds) if spec.kinds else cls.ALL_KINDS
        formats = frozenset(spec.formats) if spec.formats else cls.ALL_FORMATS
        languages = frozenset(spec.languages) if spec.languages else cls.ALL_LANGUAGES

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

        Used when no output-targets are specified in the course spec.
        """
        return cls(
            name="default",
            output_root=output_root.resolve(),
            kinds=cls.ALL_KINDS,
            formats=cls.ALL_FORMATS,
            languages=cls.ALL_LANGUAGES,
        )

    def includes_kind(self, kind: str) -> bool:
        """Check if this target includes the given kind."""
        return kind in self.kinds

    def includes_format(self, fmt: str) -> bool:
        """Check if this target includes the given format."""
        return fmt in self.formats

    def includes_language(self, lang: str) -> bool:
        """Check if this target includes the given language."""
        return lang in self.languages

    def should_generate(self, lang: str, fmt: str, kind: str) -> bool:
        """Check if this output combination should be generated for this target.

        Args:
            lang: Language code (e.g., "de", "en")
            fmt: Format (e.g., "html", "notebook", "code")
            kind: Kind (e.g., "code-along", "completed", "speaker")

        Returns:
            True if this combination should be generated for this target
        """
        # All format/kind combinations are valid - no special cases
        return (
            self.includes_language(lang)
            and self.includes_format(fmt)
            and self.includes_kind(kind)
        )
```

### 4. Extended Course Class

**Purpose**: Handle multiple output targets during processing

**Location**: `src/clx/core/course.py`

**Changes**:

```python
from clx.core.output_target import OutputTarget


@define
class Course(NotebookMixin):
    spec: CourseSpec
    course_root: Path
    output_root: Path  # Legacy single output (kept for compatibility)
    output_targets: list[OutputTarget] = Factory(list)  # NEW
    # ... existing fields ...

    @classmethod
    def from_spec(
        cls,
        spec: CourseSpec,
        course_root: Path,
        output_root: Path | None,
        output_languages: list[str] | None = None,
        output_kinds: list[str] | None = None,
        fallback_execute: bool = False,
        selected_targets: list[str] | None = None,  # NEW
    ) -> "Course":
        """Create a Course from a CourseSpec.

        Args:
            spec: The parsed course specification
            course_root: Root directory of the course source
            output_root: Override output directory (None = use spec targets)
            output_languages: Filter languages (applies to all targets)
            output_kinds: Filter kinds (applies to all targets)
            fallback_execute: Whether to fall back to execution on cache miss
            selected_targets: List of target names to build (None = all)
        """
        # Determine output targets
        if output_root is not None:
            # CLI override: use single output directory
            targets = [OutputTarget.default_target(output_root)]
            effective_output_root = output_root
        elif spec.output_targets:
            # Use targets from spec file
            targets = [
                OutputTarget.from_spec(t, course_root)
                for t in spec.output_targets
            ]
            # Filter by selected targets if specified
            if selected_targets:
                targets = [t for t in targets if t.name in selected_targets]
                if not targets:
                    raise ValueError(
                        f"No matching targets found. "
                        f"Requested: {selected_targets}, "
                        f"Available: {[t.name for t in spec.output_targets]}"
                    )
            # Use first target's root as the "primary" for legacy compatibility
            effective_output_root = targets[0].output_root if targets else course_root / "output"
        else:
            # No targets in spec, no CLI override: use default
            effective_output_root = course_root / "output"
            targets = [OutputTarget.default_target(effective_output_root)]

        # Apply CLI-level language/kind filters to all targets
        if output_languages or output_kinds:
            targets = [
                cls._apply_cli_filters(t, output_languages, output_kinds)
                for t in targets
            ]

        course = cls(
            spec,
            course_root,
            effective_output_root,
            output_targets=targets,
            output_languages=output_languages,
            output_kinds=output_kinds,
            fallback_execute=fallback_execute,
        )
        course._build_sections()
        course._build_dir_groups()
        course._add_source_output_files()
        return course

    @staticmethod
    def _apply_cli_filters(
        target: OutputTarget,
        languages: list[str] | None,
        kinds: list[str] | None,
    ) -> OutputTarget:
        """Apply CLI-level filters to a target."""
        new_languages = (
            target.languages & frozenset(languages)
            if languages else target.languages
        )
        new_kinds = (
            target.kinds & frozenset(kinds)
            if kinds else target.kinds
        )
        return OutputTarget(
            name=target.name,
            output_root=target.output_root,
            kinds=new_kinds,
            formats=target.formats,
            languages=new_languages,
        )

    async def process_all(self, backend: Backend):
        """Process all files for all output targets."""
        logger.info(f"Processing all files for {self.course_root}")
        logger.info(f"Output targets: {[t.name for t in self.output_targets]}")

        for stage in execution_stages():
            logger.debug(f"Processing stage {stage}")
            for target in self.output_targets:
                logger.debug(f"Processing target '{target.name}' at {target.output_root}")
                num_operations = await self.process_stage_for_target(stage, backend, target)
                logger.debug(f"Processed {num_operations} operations for stage {stage}, target '{target.name}'")

        await self.process_dir_group_for_targets(backend)

    async def process_stage_for_target(
        self,
        stage: int,
        backend: Backend,
        target: OutputTarget,
    ) -> int:
        """Process a single stage for a single target."""
        num_operations = 0
        async with TaskGroup() as tg:
            for file in self.files:
                op = await file.get_processing_operation(
                    target.output_root,
                    stage=stage,
                    target=target,  # Pass target for filtering
                )
                if not isinstance(op, NoOperation):
                    logger.debug(f"Processing file {file.path} for target '{target.name}'")
                    tg.create_task(op.execute(backend))
                    num_operations += 1
        await backend.wait_for_completion()
        return num_operations

    async def process_dir_group_for_targets(self, backend: Backend):
        """Process directory groups for all targets."""
        async with TaskGroup() as tg:
            for dir_group in self.dir_groups:
                for target in self.output_targets:
                    # Check if target includes any non-speaker kind
                    # (dir groups typically go to public outputs)
                    if target.kinds & {"code-along", "completed"}:
                        logger.debug(f"Processing dir group {dir_group.name} for target '{target.name}'")
                        op = await dir_group.get_processing_operation(
                            output_root=target.output_root
                        )
                        tg.create_task(op.execute(backend))
```

### 5. Extended output_specs Function

**Purpose**: Generate OutputSpec objects filtered by target configuration

**Location**: `src/clx/infrastructure/utils/path_utils.py`

**Changes**:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clx.core.output_target import OutputTarget


def output_specs(
    course: "Course",
    root_dir: Path,
    skip_html: bool = False,
    languages: list[str] | None = None,
    kinds: list[str] | None = None,
    target: "OutputTarget | None" = None,  # NEW
) -> Iterator["OutputSpec"]:
    """Generate output specifications for course processing.

    Args:
        course: Course object
        root_dir: Root directory for output
        skip_html: If True, skip HTML format generation
        languages: List of languages to generate (default: ["de", "en"])
        kinds: List of output kinds to generate (default: all kinds)
        target: OutputTarget for filtering (if provided, overrides languages/kinds)

    Yields:
        OutputSpec objects for each language/format/kind combination
    """
    # If target is provided, use its filters
    if target is not None:
        effective_languages = list(target.languages)
        effective_kinds = list(target.kinds)
        effective_formats = list(target.formats)
    else:
        effective_languages = languages if languages else ["de", "en"]
        effective_kinds = kinds if kinds else ["code-along", "completed", "speaker"]
        effective_formats = ["html", "notebook", "code"]

    # Build language list
    lang_dirs: list[Lang] = [Lang(lang) for lang in effective_languages if lang in ("de", "en")]

    # Determine if we should include specific kinds
    include_code_along = "code-along" in effective_kinds
    include_completed = "completed" in effective_kinds
    include_speaker = "speaker" in effective_kinds

    # Determine formats - all formats treated uniformly
    include_html = "html" in effective_formats and not skip_html
    include_notebook = "notebook" in effective_formats
    include_code = "code" in effective_formats

    format_dirs = []
    if include_html:
        format_dirs.append(Format.HTML)
    if include_notebook:
        format_dirs.append(Format.NOTEBOOK)
    if include_code:
        format_dirs.append(Format.CODE)

    # Determine kinds
    kind_dirs = []
    if include_code_along:
        kind_dirs.append(Kind.CODE_ALONG)
    if include_completed:
        kind_dirs.append(Kind.COMPLETED)
    if include_speaker:
        kind_dirs.append(Kind.SPEAKER)

    # Generate all format/kind combinations uniformly
    for lang_dir in lang_dirs:
        for format_dir in format_dirs:
            for kind_dir in kind_dirs:
                yield OutputSpec(
                    course=course,
                    language=lang_dir,
                    format=format_dir,
                    kind=kind_dir,
                    root_dir=root_dir,
                )
```

### 6. Extended NotebookFile

**Purpose**: Pass target filter to output_specs

**Location**: `src/clx/core/course_files/notebook_file.py`

**Changes**:

```python
async def get_processing_operation(
    self,
    target_dir: Path,
    stage: int | None = None,
    target: "OutputTarget | None" = None,  # NEW
) -> Operation:
    """Get the processing operation for this notebook file.

    Args:
        target_dir: Root output directory
        stage: Execution stage filter (None = all stages)
        target: OutputTarget for filtering outputs
    """
    operations = [
        ProcessNotebookOperation(
            input_file=self,
            output_file=(
                self.output_dir(output_dir, lang)
                / self.file_name(lang, ext_for(format_, self.prog_lang))
            ),
            language=lang,
            format=format_,
            kind=mode,
            prog_lang=self.prog_lang,
            fallback_execute=self.course.fallback_execute,
        )
        for lang, format_, mode, output_dir in output_specs(
            self.course,
            target_dir,
            self.skip_html,
            languages=self.course.output_languages,
            kinds=self.course.output_kinds,
            target=target,  # Pass target for filtering
        )
    ]

    # Filter by execution stage if specified
    if stage is not None:
        operations = [
            op for op in operations
            if _get_operation_stage(op.format, op.kind) == stage
        ]

    if not operations:
        return NoOperation()

    return Concurrently(iter(operations))
```

### 7. CLI Updates

**Purpose**: Add target selection and listing commands

**Location**: `src/clx/cli/main.py`

**Changes**:

```python
@click.command()
@click.argument("course_spec", type=click.Path(exists=True))
@click.option("--output-dir", "-o", type=click.Path(), default=None,
              help="Override output directory (ignores spec file targets)")
@click.option("--targets", "-t", type=str, default=None,
              help="Comma-separated list of target names to build")
@click.option("--language", "-l", type=click.Choice(["de", "en"]), default=None,
              help="Filter to specific language")
@click.option("--speaker-only", is_flag=True,
              help="Only generate speaker materials")
# ... other options ...
def build(
    course_spec: str,
    output_dir: str | None,
    targets: str | None,
    language: str | None,
    speaker_only: bool,
    # ... other params ...
):
    """Build course from specification file."""
    spec = CourseSpec.from_file(Path(course_spec))

    # Validate spec
    errors = spec.validate()
    if errors:
        for error in errors:
            click.echo(f"Error: {error}", err=True)
        raise click.Abort()

    # Parse target selection
    selected_targets = None
    if targets:
        selected_targets = [t.strip() for t in targets.split(",")]

    # Determine output kinds
    output_kinds = None
    if speaker_only:
        output_kinds = ["speaker"]

    # Determine output languages
    output_languages = [language] if language else None

    # Create course with target configuration
    course = Course.from_spec(
        spec=spec,
        course_root=Path(course_spec).parent,
        output_root=Path(output_dir) if output_dir else None,
        output_languages=output_languages,
        output_kinds=output_kinds,
        selected_targets=selected_targets,
    )

    # ... rest of build logic ...


@click.command()
@click.argument("course_spec", type=click.Path(exists=True))
def targets(course_spec: str):
    """List output targets defined in course specification."""
    spec = CourseSpec.from_file(Path(course_spec))

    if not spec.output_targets:
        click.echo("No output targets defined in course spec.")
        click.echo("Using default single output directory.")
        return

    click.echo("Output Targets:")
    click.echo()

    for target in spec.output_targets:
        kinds = target.kinds if target.kinds else ["(all)"]
        formats = target.formats if target.formats else ["(all)"]
        languages = target.languages if target.languages else ["(all)"]

        click.echo(f"  {target.name}")
        click.echo(f"    Path:      {target.path}")
        click.echo(f"    Kinds:     {', '.join(kinds)}")
        click.echo(f"    Formats:   {', '.join(formats)}")
        click.echo(f"    Languages: {', '.join(languages)}")
        click.echo()


# Add to CLI group
cli.add_command(targets)
```

---

## Data Flow

### Build with Multiple Targets

```
1. CLI invokes `clx build course.xml`
   │
2. CourseSpec.from_file() parses XML
   │  └── Parses <output-targets> → list[OutputTargetSpec]
   │
3. Course.from_spec() creates Course
   │  ├── If --output-dir: Create single default target
   │  ├── If spec has targets: Create OutputTarget for each
   │  └── Apply CLI filters (--language, --speaker-only)
   │
4. Course.process_all() iterates stages
   │  └── For each stage:
   │      └── For each target:
   │          └── process_stage_for_target()
   │
5. For each file, get_processing_operation(target)
   │  └── output_specs() filters by target.kinds/formats/languages
   │      └── Yields only matching OutputSpec objects
   │
6. Operations execute via backend
   │  └── Each target gets its own output directory
   │
7. Results written to target.output_root
```

### Shared Execution Cache

To avoid re-executing notebooks multiple times:

```
Target A (code-along):     Target B (completed):      Target C (speaker):
  └── kind: code-along       └── kind: completed        └── kind: speaker
                                   │                          │
                                   │     Shares cached        │
                                   │     execution from       │
                                   └──────────────────────────┘
                                              │
                                   ┌──────────▼──────────┐
                                   │   Execution Cache   │
                                   │  (SQLite database)  │
                                   └─────────────────────┘
```

The existing execution caching mechanism continues to work:
- Speaker kind executes notebook and caches result
- Completed kind reuses cached execution from Speaker
- Code-along doesn't need execution (cells are cleared)

### Execution Dependencies (Critical Design Element)

**Problem**: Some output kinds require notebook execution results that may be produced by other kinds. For example, `completed` HTML reuses the execution cache populated by `speaker` HTML. If a user configures a target with only `completed` HTML (no `speaker`), we must still execute the notebook to populate the cache.

**Design Goal**: Make execution dependencies explicit and extensible so that:
1. Future developers understand this behavior
2. The mechanism can be reused for other output formats

#### ExecutionRequirement Abstraction

Introduce an `ExecutionRequirement` enum/class that categorizes outputs by their execution needs:

```python
from enum import Enum, auto


class ExecutionRequirement(Enum):
    """Categorizes outputs by their notebook execution requirements.

    This abstraction makes explicit which outputs need execution and which
    can reuse cached results. It ensures the system correctly handles cases
    where only cache-consumers are requested (e.g., only 'completed' HTML).
    """

    # No execution needed - cells are cleared or content is static
    NONE = auto()

    # Produces execution results and populates the cache
    # Must run before REUSES_CACHE outputs
    POPULATES_CACHE = auto()

    # Consumes cached execution results
    # Requires POPULATES_CACHE to have run (explicitly or implicitly)
    REUSES_CACHE = auto()


# Output classification by execution requirement
EXECUTION_REQUIREMENTS: dict[tuple[str, str], ExecutionRequirement] = {
    # Format, Kind -> ExecutionRequirement

    # Code-along: cells are cleared, no execution needed
    ("html", "code-along"): ExecutionRequirement.NONE,
    ("notebook", "code-along"): ExecutionRequirement.NONE,
    ("code", "code-along"): ExecutionRequirement.NONE,

    # Speaker: executes and caches (for HTML)
    ("html", "speaker"): ExecutionRequirement.POPULATES_CACHE,
    ("notebook", "speaker"): ExecutionRequirement.NONE,  # Just filtered, no execution
    ("code", "speaker"): ExecutionRequirement.NONE,

    # Completed: reuses cache (for HTML), no execution for others
    ("html", "completed"): ExecutionRequirement.REUSES_CACHE,
    ("notebook", "completed"): ExecutionRequirement.NONE,
    ("code", "completed"): ExecutionRequirement.NONE,
}


def get_execution_requirement(format_: str, kind: str) -> ExecutionRequirement:
    """Get the execution requirement for a format/kind combination."""
    return EXECUTION_REQUIREMENTS.get(
        (format_, kind),
        ExecutionRequirement.NONE
    )
```

#### Implicit Execution Provider

When processing targets, the system must ensure execution happens even when only cache-consumers are requested:

```python
class ExecutionDependencyResolver:
    """Ensures execution dependencies are satisfied across targets.

    If any target requests an output that REUSES_CACHE, this resolver
    ensures that a corresponding POPULATES_CACHE operation runs first,
    even if no target explicitly requests it.
    """

    # Maps cache-consuming outputs to their cache-producing counterparts
    CACHE_PROVIDERS: dict[tuple[str, str], tuple[str, str]] = {
        # (consumer_format, consumer_kind) -> (provider_format, provider_kind)
        ("html", "completed"): ("html", "speaker"),
    }

    def resolve_implicit_executions(
        self,
        requested_outputs: set[tuple[str, str, str]],  # (lang, format, kind)
    ) -> set[tuple[str, str, str]]:
        """Determine implicit executions needed to satisfy dependencies.

        Args:
            requested_outputs: Set of (language, format, kind) tuples
                              that were explicitly requested

        Returns:
            Set of additional (language, format, kind) tuples that must
            be executed to populate the cache, but whose outputs should
            not be written to disk.
        """
        implicit_executions = set()

        for lang, fmt, kind in requested_outputs:
            req = get_execution_requirement(fmt, kind)

            if req == ExecutionRequirement.REUSES_CACHE:
                # Check if a cache provider is already requested
                provider = self.CACHE_PROVIDERS.get((fmt, kind))
                if provider:
                    provider_fmt, provider_kind = provider
                    provider_output = (lang, provider_fmt, provider_kind)

                    if provider_output not in requested_outputs:
                        # Need implicit execution
                        implicit_executions.add(provider_output)
                        logger.info(
                            f"Adding implicit execution for {provider_output} "
                            f"to satisfy cache dependency of ({lang}, {fmt}, {kind})"
                        )

        return implicit_executions
```

#### Updated Processing Flow

The `Course.process_all()` method must account for implicit executions:

```python
async def process_all(self, backend: Backend):
    """Process all files for all output targets."""
    logger.info(f"Processing all files for {self.course_root}")

    # Collect all requested outputs across all targets
    all_requested = self._collect_requested_outputs()

    # Resolve implicit execution dependencies
    resolver = ExecutionDependencyResolver()
    implicit_executions = resolver.resolve_implicit_executions(all_requested)

    if implicit_executions:
        logger.info(
            f"Implicit executions required for cache population: "
            f"{implicit_executions}"
        )

    # Process stages with both explicit and implicit executions
    for stage in execution_stages():
        logger.debug(f"Processing stage {stage}")

        # Stage 2 (POPULATES_CACHE) may include implicit executions
        # Stage 3 (REUSES_CACHE) only includes explicit outputs

        for target in self.output_targets:
            await self.process_stage_for_target(
                stage, backend, target,
                implicit_executions=implicit_executions if stage == HTML_SPEAKER_STAGE else set(),
            )

    await self.process_dir_group_for_targets(backend)

def _collect_requested_outputs(self) -> set[tuple[str, str, str]]:
    """Collect all (lang, format, kind) tuples requested by all targets."""
    requested = set()
    for target in self.output_targets:
        for lang in target.languages:
            for fmt in target.formats:
                for kind in target.kinds:
                    if target.should_generate(lang, fmt, kind):
                        requested.add((lang, fmt, kind))
    return requested
```

#### Benefits of This Design

1. **Explicit Dependencies**: The `EXECUTION_REQUIREMENTS` table and `CACHE_PROVIDERS` mapping make dependencies visible and documented.

2. **Future Extensibility**: New formats or kinds can be added by extending the tables. For example, if we add a `"pdf"` format that needs execution:
   ```python
   ("pdf", "completed"): ExecutionRequirement.REUSES_CACHE,
   ```

3. **Testability**: The `ExecutionDependencyResolver` can be unit tested in isolation to verify correct dependency resolution.

4. **Clear Logging**: When implicit executions are added, it's logged clearly so developers can understand what's happening.

5. **Separation of Concerns**: The decision of "what needs to execute" is separate from "what gets written to disk".

#### Alternative Considered: Always Run Speaker Internally

An alternative design would be to always execute notebooks in the speaker stage internally, regardless of whether speaker output is requested. This was rejected because:
- It's less explicit about why execution happens
- It doesn't scale well to other formats that might have similar dependencies
- It makes it harder to optimize (e.g., skip execution entirely if only code-along is requested)

---

## File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `src/clx/core/course_spec.py` | Modify | Add `OutputTargetSpec` class, extend `CourseSpec` |
| `src/clx/core/output_target.py` | New | Add `OutputTarget` runtime class |
| `src/clx/core/execution_dependencies.py` | New | `ExecutionRequirement` enum and `ExecutionDependencyResolver` class |
| `src/clx/core/course.py` | Modify | Add `output_targets` field, multi-target processing with implicit executions |
| `src/clx/core/utils/execution_utils.py` | Modify | Integrate with `ExecutionDependencyResolver` |
| `src/clx/infrastructure/utils/path_utils.py` | Modify | Add `target` parameter to `output_specs()` |
| `src/clx/core/course_files/notebook_file.py` | Modify | Pass target to `get_processing_operation()` |
| `src/clx/core/course_file.py` | Modify | Add target parameter to base class method |
| `src/clx/cli/main.py` | Modify | Add `--targets` flag, `targets` command |
| `tests/core/test_course_spec.py` | New/Modify | Tests for output target parsing |
| `tests/core/test_output_target.py` | New | Tests for OutputTarget class |
| `tests/core/test_execution_dependencies.py` | New | Tests for execution dependency resolution |
| `tests/core/test_course.py` | Modify | Tests for multi-target processing with implicit executions |

---

## Migration Path

### For Existing Users

**No action required**. Existing course specs without `<output-targets>` continue to work exactly as before:

```xml
<!-- This still works -->
<course>
    <name>...</name>
    <prog-lang>python</prog-lang>
    <sections>...</sections>
</course>
```

```bash
# This still works
clx build course.xml --output-dir ./output
```

### For New Multi-Target Usage

1. Add `<output-targets>` section to course spec
2. Remove reliance on `--output-dir` CLI flag
3. Use `clx targets course.xml` to verify configuration

---

## Testing Strategy

### Unit Tests

1. **OutputTargetSpec parsing**
   - Valid XML parsing
   - Default values when elements omitted
   - Validation of invalid kinds/formats/languages

2. **OutputTarget creation**
   - Path resolution (relative and absolute)
   - Filter conversion (None → all)
   - `should_generate()` logic

3. **CourseSpec validation**
   - Duplicate target names
   - Duplicate paths
   - Invalid values

### Integration Tests

1. **Multi-target build**
   - Files generated to correct directories
   - Correct kinds/formats per target
   - Shared execution cache works

2. **CLI integration**
   - `--targets` flag filters correctly
   - `clx targets` command output
   - `--output-dir` override behavior

### End-to-End Tests

1. **Complete delayed release scenario**
   - Build with three targets
   - Verify output directories contain correct content
   - Verify no duplicate execution

---

## Open Design Decisions

### D1: Directory Structure Within Targets

**Current Design**: Each target uses the full standard structure:
```
target-path/De/CourseName/Slides/Html/Completed/
```

**Alternative**: Simplified structure when only one kind:
```
target-path/De/Html/  (no Completed subdirectory if only one kind)
```

**Decision**: Start with full structure for consistency. Add `simplified="true"` option later if needed.

### D2: Progress Reporting

**Current Design**: Per-target progress reporting in logs.

**Future Enhancement**: Rich progress bars showing per-target progress:
```
Building course: Python Programming
  Target 'students' [=====>    ] 45% (120/267 jobs)
  Target 'solutions' [========] 100% (267/267 jobs)
  Target 'instructor' [===     ] 30% (80/267 jobs)
```

**Decision**: Defer rich progress to Phase 2. Basic logging for MVP.

### D3: Parallel Target Processing

**Current Design**: Sequential target processing within each stage.

**Alternative**: Parallel target processing (all targets process stage N concurrently).

**Decision**: Start sequential for simplicity. Parallel processing is an optimization for later.

---

## Appendix: XML Schema

```xml
<!-- Extended course schema with output-targets -->
<!DOCTYPE course [
  <!ELEMENT course (name, prog-lang, description?, certificate?, github?,
                    output-targets?, sections, dir-groups?)>

  <!ELEMENT output-targets (output-target+)>

  <!ELEMENT output-target (path, kinds?, formats?, languages?)>
  <!ATTLIST output-target name CDATA #REQUIRED>

  <!ELEMENT path (#PCDATA)>

  <!ELEMENT kinds (kind+)>
  <!ELEMENT kind (#PCDATA)>
  <!-- Valid values: code-along, completed, speaker -->

  <!ELEMENT formats (format+)>
  <!ELEMENT format (#PCDATA)>
  <!-- Valid values: html, notebook, code -->

  <!ELEMENT languages (language+)>
  <!ELEMENT language (#PCDATA)>
  <!-- Valid values: de, en -->

  <!-- ... existing elements ... -->
]>
```

---

## References

1. Requirements: [delayed-solution-release.md](../requirements/delayed-solution-release.md)
2. Current course spec: `src/clx/core/course_spec.py`
3. Output path utilities: `src/clx/infrastructure/utils/path_utils.py`
4. Course implementation: `src/clx/core/course.py`
