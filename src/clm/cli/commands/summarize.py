"""Summarize command for generating LLM-powered course summaries.

This module provides a command to generate summaries of course content
using LLM models, tailored for different audiences (client or trainer).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

from clm.core.course import Course

if TYPE_CHECKING:
    from clm.infrastructure.llm.cache import SummaryCache
from clm.core.course_files.notebook_file import NotebookFile
from clm.core.course_paths import resolve_course_paths
from clm.core.course_spec import CourseSpec, CourseSpecError
from clm.core.utils.text_utils import sanitize_file_name

logger = logging.getLogger(__name__)

# Tags indicating cells that should be excluded from summarization
SKIP_TAGS = {"del", "private", "notes"}

# Patterns indicating a notebook contains a workshop
WORKSHOP_PATTERNS = re.compile(
    r"(?i)\b(workshop|exercise|hands[- ]on|[uü]bung)\b",
)

# Default max content length per notebook (characters, roughly ~3-4 chars/token)
MAX_CONTENT_CHARS = 48_000


def detect_workshop(cells: list[dict]) -> bool:
    """Check if any cell indicates a workshop/exercise."""
    for cell in cells:
        if cell.get("cell_type") != "markdown":
            continue
        source = "".join(cell.get("source", []))
        # Check headings only
        for line in source.splitlines():
            if line.startswith("#") and WORKSHOP_PATTERNS.search(line):
                return True
    # Also check cell tags
    for cell in cells:
        tags = cell.get("metadata", {}).get("tags", [])
        if any("workshop" in t.lower() for t in tags):
            return True
    return False


def _is_cell_included_for_language(cell: dict, language: str) -> bool:
    """Return whether a cell should be included for the given language.

    Cells without a ``lang`` metadata field are included for all languages.
    Cells whose ``lang`` matches *language* are included; others are excluded.
    """
    cell_lang = cell.get("metadata", {}).get("lang", "")
    return not cell_lang or cell_lang == language


def extract_notebook_content(
    notebook_path: Path, audience: str, language: str = "en"
) -> str | None:
    """Extract cell content suitable for LLM summarization.

    Args:
        notebook_path: Path to the notebook file
        audience: "client" or "trainer"
        language: Language code ("en" or "de") — cells tagged for a
            different language are excluded.

    Returns:
        Extracted content string, or None if the file cannot be read
    """
    try:
        text = notebook_path.read_text(encoding="utf-8")
    except OSError:
        logger.warning(f"Cannot read notebook: {notebook_path}")
        return None

    suffix = notebook_path.suffix.lower()

    if suffix == ".ipynb":
        return _extract_from_ipynb(text, audience, language)
    elif suffix == ".py":
        return _extract_from_py(text, audience)
    else:
        return None


def _extract_from_ipynb(text: str, audience: str, language: str = "en") -> str:
    """Extract content from a .ipynb JSON file."""
    try:
        nb = json.loads(text)
    except json.JSONDecodeError:
        return ""

    cells = nb.get("cells", [])
    parts = []

    for cell in cells:
        tags = set(cell.get("metadata", {}).get("tags", []))
        if tags & SKIP_TAGS:
            continue

        if not _is_cell_included_for_language(cell, language):
            continue

        cell_type = cell.get("cell_type", "")
        source = "".join(cell.get("source", []))

        if cell_type == "markdown":
            parts.append(source)
        elif cell_type == "code" and audience == "trainer":
            parts.append(f"```\n{source}\n```")

    content = "\n\n".join(parts)
    return content[:MAX_CONTENT_CHARS]


def _extract_from_py(text: str, audience: str) -> str:
    """Extract content from a jupytext .py file.

    Note: .py files have no per-cell language metadata, so no language
    filtering is applied here.
    """
    parts = []
    in_markdown = False
    current_block: list[str] = []

    for line in text.splitlines():
        if line.startswith("# %%") or line.startswith("# +"):
            # Flush current block
            if current_block:
                block_text = "\n".join(current_block)
                parts.append(block_text)
                current_block = []

            if "[markdown]" in line or "[md]" in line:
                in_markdown = True
            else:
                in_markdown = False
            continue

        if in_markdown:
            # Strip leading "# " from markdown cells
            if line.startswith("# "):
                current_block.append(line[2:])
            elif line == "#":
                current_block.append("")
            else:
                current_block.append(line)
        elif audience == "trainer":
            current_block.append(line)

    if current_block:
        parts.append("\n".join(current_block))

    content = "\n\n".join(parts)
    return content[:MAX_CONTENT_CHARS]


def content_hash(content: str) -> str:
    """SHA-256 hash of content for cache keying."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def get_notebook_cells(notebook_path: Path) -> list[dict]:
    """Read notebook cells for workshop detection."""
    try:
        text = notebook_path.read_text(encoding="utf-8")
        if notebook_path.suffix.lower() == ".ipynb":
            nb = json.loads(text)
            cells: list[dict] = nb.get("cells", [])
            return cells
    except (OSError, json.JSONDecodeError):
        pass
    return []


class SummarizeProgress:
    """Progress reporter for the summarize command, matching clm build style."""

    def __init__(self, console: Console, show_progress: bool = True):
        self.console = console
        self.show_progress = show_progress
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None
        self._cached = 0
        self._generated = 0
        self._errors = 0
        self._total = 0
        self._start_time = 0.0

    def start(self, course_name: str, total: int, model: str, audience: str):
        self._total = total
        self._start_time = time.monotonic()

        self.console.print(f"\n[bold]Summarizing course:[/bold] {course_name}", style="cyan")
        self.console.print(f"  Model: {model}")
        self.console.print(f"  Audience: {audience}")
        self.console.print(f"  Notebooks: {total}")
        self.console.print()

        if self.show_progress and total > 0:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=self.console,
                transient=False,
            )
            self._progress.start()
            self._task_id = self._progress.add_task("Summarizing", total=total)

    def on_cached(self, title: str):
        self._cached += 1
        self._update_bar()

    def on_generated(self, title: str):
        self._generated += 1
        self._update_bar()

    def on_error(self, title: str, message: str):
        self._errors += 1
        self._update_bar()
        # Print error below the progress bar
        if self._progress:
            self._progress.console.print(f"  [red]✗[/red] {title}: {message}")
        else:
            self.console.print(f"  [red]✗[/red] {title}: {message}")

    def _update_bar(self):
        if self._progress and self._task_id is not None:
            done = self._cached + self._generated + self._errors
            desc = "Summarizing"
            if self._cached > 0:
                desc += f" [dim]({self._cached} cached)[/dim]"
            self._progress.update(self._task_id, completed=done, description=desc)

    def finish(self):
        if self._progress:
            self._progress.stop()

        duration = time.monotonic() - self._start_time

        if self._errors > 0:
            symbol, color, status = "✗", "red", "with errors"
        else:
            symbol, color, status = "✓", "green", "successfully"

        self.console.print(
            f"\n[bold {color}]{symbol} Summarization completed {status}[/bold {color}] "
            f"in {duration:.1f}s\n"
        )

        self.console.print("[bold]Summary:[/bold]")
        total_processed = self._cached + self._generated + self._errors
        self.console.print(f"  {total_processed} notebooks processed")
        if self._generated > 0:
            self.console.print(f"  [cyan]{self._generated} generated via LLM[/cyan]")
        if self._cached > 0:
            self.console.print(f"  [dim]{self._cached} from cache[/dim]")
        if self._errors > 0:
            self.console.print(f"  [red]{self._errors} errors[/red]")
        self.console.print()


def _count_notebooks(course: Course) -> int:
    """Count total notebook files in the course."""
    return sum(
        1 for section in course.sections for f in section.files if isinstance(f, NotebookFile)
    )


async def generate_summaries(
    course: Course,
    language: str,
    audience: str,
    granularity: str,
    model: str,
    temperature: float,
    api_base: str | None,
    api_key: str | None,
    max_concurrent: int,
    cache: SummaryCache | None,
    no_cache: bool,
    dry_run: bool,
    progress: SummarizeProgress | None = None,
) -> str:
    """Generate the full summarized outline.

    Returns:
        Markdown string with summaries
    """
    from clm.infrastructure.llm.client import LLMError, summarize_notebook

    course_name = course.name[language]
    lines = [f"# {course_name}", ""]

    if dry_run:
        lines.append("*Dry run — showing what would be summarized:*")
        lines.append("")

    for section_idx, section in enumerate(course.sections):
        section_name = section.name[language]
        if section_idx > 0:
            lines.append("")  # extra blank line between sections
        lines.append(f"## {section_name}")
        lines.append("")

        notebooks = [f for f in section.files if isinstance(f, NotebookFile)]
        if not notebooks:
            continue

        if granularity == "section":
            all_content = []
            all_titles = []
            for nb in notebooks:
                content = extract_notebook_content(nb.path, audience, language)
                if content:
                    all_content.append(f"### {nb.title[language]}\n\n{content}")
                    all_titles.append(nb.title[language])

            combined = "\n\n---\n\n".join(all_content)
            if not combined:
                continue

            if dry_run:
                lines.append(f"Would summarize {len(notebooks)} notebooks (~{len(combined)} chars)")
                lines.append("")
                if progress:
                    for _ in notebooks:
                        progress.on_cached("")  # just advance the bar
                continue

            h = content_hash(combined)
            cached_result = (
                cache.get(h, audience, model, language) if cache and not no_cache else None
            )
            if cached_result:
                lines.append(cached_result)
                if progress:
                    for nb in notebooks:
                        progress.on_cached(nb.title[language])
            else:
                cells = []
                for nb in notebooks:
                    cells.extend(get_notebook_cells(nb.path))
                has_ws = detect_workshop(cells)

                try:
                    summary = await summarize_notebook(
                        content=combined,
                        audience=audience,
                        model=model,
                        notebook_title=", ".join(all_titles),
                        section_name=section_name,
                        course_name=course_name,
                        temperature=temperature,
                        api_base=api_base,
                        api_key=api_key,
                        max_concurrent=max_concurrent,
                        has_workshop=has_ws,
                        language=language,
                    )
                except LLMError as exc:
                    msg = str(exc)
                    summary = f"_Error: {msg}_"
                    if progress:
                        progress.on_error(section_name, msg)
                    else:
                        click.echo(f"  Warning: {msg}", err=True)
                else:
                    if progress:
                        for nb in notebooks:
                            progress.on_generated(nb.title[language])

                lines.append(summary)
                if cache and not summary.startswith("_Error"):
                    cache.put(h, audience, model, summary, language)

            lines.append("")

        else:
            # Per-notebook summaries
            pending_tasks = []
            for nb in notebooks:
                content = extract_notebook_content(nb.path, audience, language)
                if not content:
                    if progress:
                        progress.on_cached(nb.title[language])
                    continue

                title = nb.title[language]

                if dry_run:
                    if audience == "client":
                        lines.append(f"- **{title}**: _would summarize ~{len(content)} chars_")
                    else:
                        lines.append(f"### {title}")
                        lines.append(f"_Would summarize ~{len(content)} chars_")
                        lines.append("")
                    if progress:
                        progress.on_cached(title)
                    continue

                h = content_hash(content)
                cached_result = (
                    cache.get(h, audience, model, language) if cache and not no_cache else None
                )

                if cached_result:
                    if progress:
                        progress.on_cached(title)
                    if audience == "client":
                        lines.append(f"- **{title}**: {cached_result}")
                    else:
                        cells = get_notebook_cells(nb.path)
                        has_ws = detect_workshop(cells)
                        ws_marker = " **[Workshop]**" if has_ws else ""
                        lines.append(f"### {title}{ws_marker}")
                        lines.append(cached_result)
                        lines.append("")
                else:
                    pending_tasks.append((nb, title, content, h))

            if not dry_run and pending_tasks:
                # Run LLM calls concurrently
                async_tasks = []
                for nb, title, content, h in pending_tasks:
                    cells = get_notebook_cells(nb.path)
                    has_ws = detect_workshop(cells)
                    async_tasks.append(
                        (
                            title,
                            h,
                            has_ws,
                            summarize_notebook(
                                content=content,
                                audience=audience,
                                model=model,
                                notebook_title=title,
                                section_name=section_name,
                                course_name=course_name,
                                temperature=temperature,
                                api_base=api_base,
                                api_key=api_key,
                                max_concurrent=max_concurrent,
                                has_workshop=has_ws,
                                language=language,
                            ),
                        )
                    )

                results = await asyncio.gather(*[t[3] for t in async_tasks], return_exceptions=True)

                for (title, h, has_ws, _), result in zip(async_tasks, results, strict=True):
                    if isinstance(result, BaseException):
                        msg = str(result)
                        summary = f"_Error: {msg}_"
                        if progress:
                            progress.on_error(title, msg)
                        else:
                            click.echo(f"  Warning: {msg}", err=True)
                    else:
                        summary = str(result)
                        if cache:
                            cache.put(h, audience, model, summary, language)
                        if progress:
                            progress.on_generated(title)

                    if audience == "client":
                        lines.append(f"- **{title}**: {summary}")
                    else:
                        ws_marker = " **[Workshop]**" if has_ws else ""
                        lines.append(f"### {title}{ws_marker}")
                        lines.append(summary)
                        lines.append("")

            if dry_run:
                lines.append("")

    return "\n".join(lines)


@click.command()
@click.argument(
    "spec-file",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--audience",
    required=True,
    type=click.Choice(["client", "trainer"], case_sensitive=False),
    help="Target audience for summaries.",
)
@click.option(
    "--granularity",
    type=click.Choice(["notebook", "section"], case_sensitive=False),
    default="notebook",
    help="Summary granularity level.",
)
@click.option(
    "-L",
    "--language",
    type=click.Choice(["de", "en"], case_sensitive=False),
    default="en",
    help="Language for the outline structure.",
)
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write output to FILE.",
)
@click.option(
    "-d",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Write to DIR with auto-generated filenames.",
)
@click.option(
    "--model",
    default=None,
    help="Override litellm model identifier (default: from config or anthropic/claude-sonnet-4-6).",
)
@click.option(
    "--api-base",
    default=None,
    help="Custom API base URL (e.g., for OpenRouter).",
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="Skip cache, re-generate all summaries.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be summarized, with content size estimates.",
)
@click.option(
    "--no-progress",
    is_flag=True,
    default=False,
    help="Disable the progress bar.",
)
def summarize(
    spec_file: Path,
    audience: str,
    granularity: str,
    language: str,
    output_file: Path | None,
    output_dir: Path | None,
    model: str | None,
    api_base: str | None,
    no_cache: bool,
    dry_run: bool,
    no_progress: bool,
):
    """Generate LLM-powered summaries of a course.

    Creates a Markdown document with section headings and LLM-generated
    summaries of each notebook's content.

    \b
    Examples:
        clm summarize course.xml --audience client --dry-run
        clm summarize course.xml --audience trainer -o summary.md
        clm summarize course.xml --audience client -d ./docs
        clm summarize course.xml --audience trainer --model openai/gpt-4o
    """
    if output_file and output_dir:
        raise click.UsageError("--output and --output-dir are mutually exclusive.")

    # Load config for LLM settings
    from clm.infrastructure.config import get_config

    config = get_config()
    llm_config = config.llm

    effective_model = model or llm_config.model
    effective_api_base = api_base or llm_config.api_base or None
    effective_api_key = llm_config.api_key or None

    if not dry_run:
        try:
            import litellm  # noqa: F401
        except ImportError:
            raise click.ClickException(
                "litellm is required for summarization. "
                'Install with: pip install "coding-academy-lecture-manager[summarize]"'
            ) from None

    # Load course
    try:
        spec = CourseSpec.from_file(spec_file)
    except CourseSpecError as e:
        raise click.ClickException(f"Failed to parse spec file: {e}") from None

    validation_errors = spec.validate()
    if validation_errors:
        error_msg = "\n".join(f"  - {e}" for e in validation_errors)
        raise click.ClickException(f"Spec validation failed:\n{error_msg}")

    data_dir, _ = resolve_course_paths(spec_file)

    course = Course.from_spec(spec, data_dir, output_root=None)

    # Progress goes to stderr so it doesn't mix with markdown output on stdout
    show_bar = not no_progress and not dry_run
    console = Console(file=sys.stderr, force_terminal=not no_progress)
    progress_reporter: SummarizeProgress | None = None

    if show_bar:
        progress_reporter = SummarizeProgress(console, show_progress=True)
        total = _count_notebooks(course)
        progress_reporter.start(course.name[language], total, effective_model, audience)

    # Set up cache
    cache = None
    if not no_cache and not dry_run:
        from clm.infrastructure.llm.cache import SummaryCache

        cache_path = data_dir / "clm_summaries.db"
        cache = SummaryCache(cache_path)

    try:
        result = asyncio.run(
            generate_summaries(
                course=course,
                language=language,
                audience=audience,
                granularity=granularity,
                model=effective_model,
                temperature=llm_config.temperature,
                api_base=effective_api_base,
                api_key=effective_api_key,
                max_concurrent=llm_config.max_concurrent,
                cache=cache,
                no_cache=no_cache,
                dry_run=dry_run,
                progress=progress_reporter,
            )
        )
    finally:
        if cache:
            cache.close()
        if progress_reporter:
            progress_reporter.finish()

    # Output
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        title = sanitize_file_name(course.name[language])
        filename = f"{title}-{audience}-summary.md"
        file_path = output_dir / filename
        file_path.write_text(result, encoding="utf-8")
        console.print(f"[dim]Written: {file_path}[/dim]")
    elif output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(result, encoding="utf-8")
        console.print(f"[dim]Written: {output_file}[/dim]")
    else:
        click.echo(result)
