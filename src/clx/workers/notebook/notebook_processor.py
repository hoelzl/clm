import asyncio
import copy
import logging
import os
import re
import warnings
from base64 import b64decode
from hashlib import sha3_224
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, cast

import jupytext.config as jupytext_config  # type: ignore[import-untyped]
import traitlets.log
from jinja2 import Environment, PackageLoader, StrictUndefined
from jupytext import jupytext
from nbconvert import HTMLExporter
from nbconvert.preprocessors import ExecutePreprocessor
from nbformat import NotebookNode
from nbformat.validator import normalize

from clx.infrastructure.messaging.notebook_classes import NotebookPayload

from .output_spec import OutputSpec

if TYPE_CHECKING:
    from clx.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache
from clx.infrastructure.messaging.base_classes import ProcessingWarning

from .utils.jupyter_utils import (
    Cell,
    get_cell_type,
    get_conflicting_slide_tags,
    get_invalid_code_tags,
    get_invalid_markdown_tags,
    get_slide_tag,
    get_tags,
    is_answer_cell,
    is_code_cell,
    is_markdown_cell,
)
from .utils.prog_lang_utils import (
    jinja_prefix_for,
    jupytext_format_for,
    kernelspec_for,
    language_info,
)


def string_to_list(string: str) -> list[str]:
    return [s.strip() for s in string.split(",")]


# Configuration
JINJA_LINE_STATEMENT_PREFIX = os.environ.get("JINJA_LINE_STATEMENT_PREFIX", "# j2")
JINJA_TEMPLATES_PREFIX = os.environ.get("JINJA_TEMPLATES_PATH", "templates")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()
LOG_CELL_PROCESSING = os.environ.get("LOG_CELL_PROCESSING", "False") == "True"
NUM_RETRIES_FOR_HTML = 6

# Regex pattern to match img and video tags with src="img/..." paths
# Captures: prefix (before img/), filename (after img/), suffix (rest of tag)
MEDIA_SRC_PATTERN = re.compile(r'(<(?:img|video)\s+[^>]*src=["\'])img/([^"\']+)(["\'][^>]*>)')

# Logging setup
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - notebook-processor - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class CellIdGenerator:
    def __init__(self):
        self.unique_ids: set[str] = set()
        self.id_uniquifier: int = 1

    def set_cell_id(self, cell: Cell, index: int) -> None:
        cell_hash = sha3_224()
        cell_source: str = cell["source"]
        hash_text = cell_source
        while True:
            cell_hash.update(hash_text.encode("utf-8"))
            cell_id = cell_hash.hexdigest()[:16]
            if cell_id in self.unique_ids:
                hash_text = f"{index}:{cell_source}"
                index += 1
            else:
                self.unique_ids.add(cell_id)
                cell.id = cell_id
                break


class DontWarnForMissingAltTags(logging.Filter):
    def filter(self, record):
        return "Alternative text is missing" not in record.getMessage()


class NotebookProcessor:
    def __init__(
        self,
        output_spec: OutputSpec,
        cache: "ExecutedNotebookCache | None" = None,
    ):
        self.output_spec = output_spec
        self.id_generator = CellIdGenerator()
        self.cache = cache
        self._warnings: list[ProcessingWarning] = []

    def add_warning(
        self,
        category: str,
        message: str,
        file_path: str = "",
        severity: str = "medium",
        details: dict | None = None,
    ) -> None:
        """Add a processing warning to be reported to the user.

        Args:
            category: Category of the warning (e.g., "invalid_tags", "multiple_slide_tags")
            message: Human-readable warning message
            file_path: Path to the file being processed
            severity: Warning severity ("high", "medium", or "low")
            details: Optional dict with additional context
        """
        self._warnings.append(
            ProcessingWarning(
                category=category,
                message=message,
                file_path=file_path,
                severity=severity,  # type: ignore[arg-type]
                details=details or {},
            )
        )

    def get_warnings(self) -> list[ProcessingWarning]:
        """Return all collected warnings."""
        return self._warnings.copy()

    def clear_warnings(self) -> None:
        """Clear all collected warnings."""
        self._warnings.clear()

    async def process_notebook(
        self, payload: NotebookPayload, source_dir: Path | None = None
    ) -> str:
        """Process a notebook and return the result.

        Args:
            payload: Notebook payload with data and metadata
            source_dir: Optional path to source directory where supporting files
                are located (Docker mode with source mount). When set, files are
                read directly from this directory instead of from other_files.

        Returns:
            The processed notebook as a string (HTML, notebook, or code)
        """
        cid = payload.correlation_id
        logger.info(
            f"{cid}:Processing notebook '{payload.input_file_name}' "
            f"({payload.language}, {payload.kind}, {payload.format})"
        )

        # Check if we can reuse a cached executed notebook (Completed HTML)
        if (
            self.output_spec.can_reuse_execution
            and self.cache is not None
            and not payload.fallback_execute
        ):
            cached_result = await self._try_reuse_cached_execution(payload)
            if cached_result is not None:
                return cached_result
            # Cache miss - log warning and fall through to normal processing
            # This can happen when Speaker HTML was served from database cache
            # (not executed), so the execution cache was never populated
            logger.warning(
                f"{cid}:Execution cache miss for '{payload.input_file_name}'. "
                f"Falling back to direct execution."
            )

        # Normal processing path
        expanded_nb = await self.load_and_expand_jinja_template(
            payload.data, payload.input_file_name, cid
        )
        processed_nb = await self.process_notebook_for_spec(expanded_nb, payload)
        result = await self.create_contents(processed_nb, payload, source_dir=source_dir)
        if result:
            logger.debug(f"{cid}:Processed notebook. Result: {result[:100]}...")
        else:
            logger.error(f"{cid}:Could not process notebook: No contents.")
        return result

    async def _try_reuse_cached_execution(self, payload: NotebookPayload) -> str | None:
        """Try to reuse a cached executed notebook for Completed HTML.

        For Completed HTML, we can reuse the Speaker HTML's executed notebook
        by filtering out the "notes" cells (which are markdown, not code).

        Returns:
            The HTML result if cache hit, None if cache miss.
        """
        cid = payload.correlation_id
        cache_hash = payload.execution_cache_hash()

        logger.debug(f"{cid}:Trying to reuse cached execution for '{payload.input_file_name}'")

        assert self.cache is not None  # Checked by caller
        cached_nb = self.cache.get(
            input_file=payload.input_file,
            content_hash=cache_hash,
            language=payload.language,
            prog_lang=payload.prog_lang,
        )

        if cached_nb is None:
            logger.debug(f"{cid}:Cache miss for '{payload.input_file_name}'")
            return None

        logger.info(f"{cid}:Cache hit - reusing executed notebook for '{payload.input_file_name}'")

        # Filter out notes cells from the cached notebook
        # The cached notebook is from Speaker, which includes notes cells
        # For Completed, we need to remove notes cells
        filtered_nb = self._filter_notes_cells_from_cached(cached_nb)

        # Export to HTML (no execution needed)
        traitlets_logger = traitlets.log.get_logger()
        if hasattr(traitlets_logger, "addFilter"):
            traitlets_logger.addFilter(DontWarnForMissingAltTags())
        html_exporter = HTMLExporter(template_name="classic")
        (body, _resources) = html_exporter.from_notebook_node(filtered_nb)

        logger.debug(f"{cid}:Successfully reused cached execution for '{payload.input_file_name}'")
        return body

    def _filter_notes_cells_from_cached(self, nb: NotebookNode) -> NotebookNode:
        """Filter out notes cells from a cached executed notebook.

        This is used when reusing Speaker's executed notebook for Completed HTML.
        Notes cells are markdown cells that should not appear in Completed output.
        """
        # Make a deep copy to avoid modifying the cached notebook
        filtered_nb = copy.deepcopy(nb)
        filtered_nb.cells = [
            cell for cell in filtered_nb.get("cells", []) if "notes" not in get_tags(cell)
        ]
        return filtered_nb

    async def load_and_expand_jinja_template(
        self, notebook_text: str, notebook_file: str, cid
    ) -> str:
        logger.debug(f"{cid}:Loading and expanding Jinja template")
        jinja_env = self._create_jinja_environment(cid)
        nb_template = jinja_env.from_string(
            notebook_text,
            globals=self._create_jinja_globals(self.output_spec),
        )
        logger.debug(f"{cid}:Jinja template created for {notebook_file}")
        expanded_nb = await nb_template.render_async()
        logger.debug(f"{cid}:Jinja template expanded for {notebook_file}")
        return cast(str, expanded_nb)

    def _create_jinja_environment(self, cid):
        templates_path = f"{JINJA_TEMPLATES_PREFIX}_{self.output_spec.prog_lang}"
        logger.debug(f"{cid}:Creating Jinja environment with templates from {templates_path}")
        try:
            jinja_env = Environment(
                loader=PackageLoader("clx.workers.notebook", templates_path),
                autoescape=False,
                undefined=StrictUndefined,
                line_statement_prefix=jinja_prefix_for(self.output_spec.prog_lang),
                keep_trailing_newline=True,
                enable_async=True,
            )
            logger.debug("Jinja environment created")
            return jinja_env
        except Exception as e:
            logger.error(
                f"Failed to create Jinja environment for "
                f"'{self.output_spec.prog_lang}' with template dir "
                f"'{templates_path}': {e}"
            )
            raise

    @staticmethod
    def _create_jinja_globals(output_spec):
        return {
            "is_notebook": output_spec.format == "notebook",
            "is_html": output_spec.format == "html",
            "lang": output_spec.language,
        }

    async def process_notebook_for_spec(
        self, expanded_nb: str, payload: NotebookPayload
    ) -> NotebookNode:
        jupytext_format = jupytext_format_for(self.output_spec.prog_lang)
        logger.debug(
            f"{payload.correlation_id}:Processing notebook for in format "
            f"'{self.output_spec.format}' with Jupytext format "
            f"'{jupytext_format}'"
        )
        loop = asyncio.get_running_loop()
        nb = await loop.run_in_executor(None, jupytext.reads, expanded_nb, jupytext_format)
        # nb = jupytext.reads(expanded_nb, fmt=jupytext_format)
        processed_nb = await self._process_notebook_node(nb, payload)
        return processed_nb

    async def _process_notebook_node(
        self, nb: NotebookNode, payload: NotebookPayload
    ) -> NotebookNode:
        new_cells = [
            await self._process_cell(cell, index, payload)
            for index, cell in enumerate(nb.get("cells", []))
            if self.output_spec.is_cell_included(cell)
        ]
        nb.cells = new_cells
        nb.metadata["language_info"] = language_info(payload.prog_lang)
        nb.metadata["kernelspec"] = kernelspec_for(payload.prog_lang)
        _, normalized_nb = normalize(nb)
        return cast(NotebookNode, normalized_nb)

    async def _process_cell(self, cell: Cell, index: int, payload: NotebookPayload) -> Cell:
        cid = payload.correlation_id
        self._generate_cell_metadata(cell, index, payload.input_file)
        await asyncio.sleep(0)
        if LOG_CELL_PROCESSING:
            logger.debug(f"{cid}:Processing cell {cell} of {payload.input_file_name}")
        if is_code_cell(cell):
            return self._process_code_cell(cell, index, payload.input_file)
        elif is_markdown_cell(cell):
            return self._process_markdown_cell(
                cell, index, payload.input_file, payload.img_path_prefix
            )
        else:
            logger.warning(f"{cid}:Keeping unknown cell type {get_cell_type(cell)!r}.")
            return cell

    def _generate_cell_metadata(self, cell: Cell, index: int, file_path: str = "") -> None:
        self.id_generator.set_cell_id(cell, index)
        self._process_slide_tag(cell, index, file_path)

    def _process_slide_tag(self, cell: Cell, index: int = 0, file_path: str = "") -> None:
        """Process slide tag for a cell and collect warnings for conflicts."""
        tags = get_tags(cell)

        # Check for conflicting slide tags
        conflicting_tags = get_conflicting_slide_tags(tags)
        if conflicting_tags:
            self.add_warning(
                category="multiple_slide_tags",
                message=f"Cell #{index} has multiple slide tags: {conflicting_tags}. One will be chosen arbitrarily.",
                file_path=file_path,
                severity="medium",
                details={"cell_index": index, "conflicting_tags": conflicting_tags},
            )

        slide_tag = get_slide_tag(cell)
        if slide_tag:
            cell["metadata"]["slideshow"] = {"slide_type": slide_tag}

    def _process_code_cell(self, cell: Cell, index: int = 0, file_path: str = "") -> Cell:
        if not self.output_spec.is_cell_contents_included(cell):
            cell["source"] = ""
            cell["outputs"] = []

        # Check for invalid tags and collect warnings
        tags = get_tags(cell)
        invalid_tags = get_invalid_code_tags(tags)
        for tag in invalid_tags:
            self.add_warning(
                category="invalid_tag",
                message=f"Unknown tag '{tag}' for code cell #{index}",
                file_path=file_path,
                severity="low",
                details={"cell_index": index, "tag": tag, "cell_type": "code"},
            )

        return cell

    def _process_markdown_cell(
        self, cell: Cell, index: int = 0, file_path: str = "", img_path_prefix: str = "img/"
    ) -> Cell:
        tags = get_tags(cell)

        # Check for invalid tags and collect warnings
        invalid_tags = get_invalid_markdown_tags(tags)
        for tag in invalid_tags:
            self.add_warning(
                category="invalid_tag",
                message=f"Unknown tag '{tag}' for markdown cell #{index}",
                file_path=file_path,
                severity="low",
                details={"cell_index": index, "tag": tag, "cell_type": "markdown"},
            )

        self._process_markdown_cell_contents(cell, img_path_prefix)
        return cell

    def _process_markdown_cell_contents(self, cell: Cell, img_path_prefix: str = "img/"):
        tags = get_tags(cell)
        if "notes" in tags:
            contents = cell["source"]
            cell["source"] = "<div style='background:yellow'>\n" + contents + "\n</div>"
        if is_answer_cell(cell):
            answer_text = "Answer" if self.output_spec.language == "en" else "Antwort"
            prefix = f"*{answer_text}:* "
            if self.output_spec.is_cell_contents_included(cell):
                cell["source"] = prefix + cell["source"]
            else:
                cell["source"] = prefix

        # Rewrite image paths from img/filename to the shared img/ folder location
        cell["source"] = self._rewrite_image_paths(cell["source"], img_path_prefix)

    @staticmethod
    def _rewrite_image_paths(content: str, img_path_prefix: str) -> str:
        """Rewrite image/video paths from img/filename to use the shared img/ folder.

        Transforms paths like:
            <img src="img/diagram.png">
            <video src="img/demo.mp4">
        to:
            <img src="../../../../img/diagram.png">
            <video src="../../../../img/demo.mp4">

        where the prefix depends on how deep the output file is relative to the
        course directory.

        Args:
            content: Markdown cell content potentially containing img/video tags
            img_path_prefix: Relative path prefix to the shared img/ folder

        Returns:
            Content with rewritten image/video paths
        """
        # If img_path_prefix is already "img/", no rewriting needed
        if img_path_prefix == "img/":
            return content

        # Replace img/filename with {img_path_prefix}filename
        def replace_media_src(match):
            prefix = match.group(1)  # e.g., '<img src="' or '<video src="'
            filename = match.group(2)  # e.g., 'diagram.png' or 'demo.mp4'
            suffix = match.group(3)  # e.g., '">'
            return f"{prefix}{img_path_prefix}{filename}{suffix}"

        return MEDIA_SRC_PATTERN.sub(replace_media_src, content)

    async def create_contents(
        self,
        processed_nb: NotebookNode,
        payload: NotebookPayload,
        source_dir: Path | None = None,
    ) -> str:
        try:
            if self.output_spec.format == "html":
                result = await self._create_using_nbconvert(
                    processed_nb, payload, source_dir=source_dir
                )
            else:
                result = await self._create_using_jupytext(processed_nb)
            return result
        except RuntimeError as e:
            logging.error(
                f"Failed to convert notebook '{payload.input_file_name}' to HTML: {e}",
            )
            logging.debug(f"Error traceback for '{payload.input_file_name}'", exc_info=True)
            raise

    async def _cleanup_kernel_resources(self, ep: ExecutePreprocessor, cid: str) -> None:
        """Cleanup kernel resources to prevent ZMQ connection leaks.

        This method ensures proper cleanup of:
        - Kernel client channels (ZMQ sockets)
        - Kernel process (via shutdown_kernel)
        - ZMQ context (via cleanup_resources)

        This prevents "Connection reset by peer [10054]" errors on Windows
        that occur when ZMQ sockets are left in an invalid state after
        kernel crashes or connection resets.

        Args:
            ep: The ExecutePreprocessor instance to clean up
            cid: Correlation ID for logging
        """
        try:
            loop = asyncio.get_running_loop()

            # Stop kernel client channels first (ZMQ sockets)
            if hasattr(ep, "kc") and ep.kc is not None:
                try:
                    await loop.run_in_executor(None, ep.kc.stop_channels)
                    logger.debug(f"{cid}: Stopped kernel client channels")
                except Exception as e:
                    logger.debug(f"{cid}: Error stopping channels: {e}")

            # Shutdown kernel and cleanup ZMQ resources
            if hasattr(ep, "km") and ep.km is not None:
                km = ep.km  # Capture for type narrowing
                try:
                    if km.has_kernel:
                        await loop.run_in_executor(None, lambda: km.shutdown_kernel(now=True))
                        logger.debug(f"{cid}: Shutdown kernel")
                except Exception as e:
                    logger.debug(f"{cid}: Error shutting down kernel: {e}")

                # Cleanup ZMQ resources - this destroys the context
                try:
                    await loop.run_in_executor(None, km.cleanup_resources)
                    logger.debug(f"{cid}: Cleaned up kernel resources")
                except Exception as e:
                    logger.debug(f"{cid}: Error cleaning up resources: {e}")

        except Exception as e:
            logger.warning(f"{cid}: Unexpected error during kernel cleanup: {e}")

    async def _execute_notebook_with_path(
        self,
        cid: str,
        path: Path,
        processed_nb: NotebookNode,
        payload: NotebookPayload,
        loop: asyncio.AbstractEventLoop,
        source_dir: Path | None,
    ) -> None:
        """Execute notebook with supporting files at the given path.

        This handles the retry loop for notebook execution with kernel cleanup.

        Args:
            cid: Correlation ID for logging
            path: Directory containing supporting files (temp dir or source mount)
            processed_nb: The processed notebook to execute
            payload: Notebook payload
            loop: Event loop for running executor
            source_dir: Source directory if using source mount (for logging)
        """
        last_error: Exception | None = None
        for attempt in range(1, NUM_RETRIES_FOR_HTML + 1):
            # Create FRESH ExecutePreprocessor for each attempt
            # This ensures no stale ZMQ state from previous failures
            ep = ExecutePreprocessor(timeout=None, startup_timeout=300)
            try:

                def run_preprocess(
                    ep: ExecutePreprocessor = ep,
                ) -> tuple[NotebookNode, dict]:
                    return ep.preprocess(
                        processed_nb,
                        resources={"metadata": {"path": path}},
                    )

                await loop.run_in_executor(None, run_preprocess)
                last_error = None
                break  # Success - exit retry loop
            except RuntimeError as e:
                last_error = e
                if not logger.isEnabledFor(logging.DEBUG):
                    logger.info(f"{cid}: Kernel died (attempt {attempt}/{NUM_RETRIES_FOR_HTML})")
                logger.debug(f"{cid}: Kernel died (attempt {attempt}): {e}")
            finally:
                # ALWAYS cleanup kernel resources to prevent ZMQ leaks
                await self._cleanup_kernel_resources(ep, cid)

            # Exponential backoff before next retry
            if attempt < NUM_RETRIES_FOR_HTML:
                await asyncio.sleep(1.0 * attempt)

        if last_error is not None:
            # Enhance the error message with more context
            enhanced_error = self._enhance_notebook_error(last_error, processed_nb, payload)
            raise enhanced_error from last_error

    async def _create_using_nbconvert(
        self, processed_nb, payload: NotebookPayload, source_dir: Path | None = None
    ) -> str:
        cid = payload.correlation_id
        traitlets_logger = traitlets.log.get_logger()
        if hasattr(traitlets_logger, "addFilter"):
            traitlets_logger.addFilter(DontWarnForMissingAltTags())
        if self.output_spec.evaluate_for_html:
            if any(is_code_cell(cell) for cell in processed_nb.get("cells", [])):
                logger.debug(f"Evaluating and writing notebook '{payload.input_file_name}'")
                try:
                    # To silence warnings about frozen modules...
                    os.environ["PYDEVD_DISABLE_FILE_VALIDATION"] = "1"
                    with warnings.catch_warnings():
                        warnings.filterwarnings(
                            "ignore",
                            "Proactor event loop does not implement add_reader",
                        )
                        ExecutePreprocessor.log_level = logging.DEBUG  # type: ignore[attr-defined]
                        loop = asyncio.get_running_loop()

                        # Determine execution path: use source_dir if available (Docker mode
                        # with source mount), otherwise create temp directory for other_files
                        if source_dir is not None:
                            # Docker mode with source mount: files already available
                            path = source_dir
                            logger.debug(f"{cid}:Using source mount for execution: {source_dir}")
                            await self._execute_notebook_with_path(
                                cid, path, processed_nb, payload, loop, source_dir
                            )
                        else:
                            # Standard mode: write other_files to temp directory
                            with TemporaryDirectory() as temp_dir:
                                path = Path(temp_dir)
                                await self.write_other_files(cid, path, payload)
                                await self._execute_notebook_with_path(
                                    cid, path, processed_nb, payload, loop, None
                                )

                except Exception as e:
                    file_name = payload.input_file_name
                    logger.error(
                        f"Notebook Processor (nbconvert): "
                        f"Error while processing notebook '{file_name}': {e}",
                    )
                    logger.debug(f"{cid}:Error traceback for {file_name}:", exc_info=e)
                    raise

                # Cache the executed notebook for later reuse by Completed HTML
                if self.output_spec.should_cache_execution and self.cache is not None:
                    self._cache_executed_notebook(processed_nb, payload)
            else:
                logger.debug(f"Notebook {payload.input_file_name} contains no code cells.")
                # Still cache the notebook for Completed HTML even without code cells
                # The "executed" notebook is just the processed notebook in this case
                if self.output_spec.should_cache_execution and self.cache is not None:
                    self._cache_executed_notebook(processed_nb, payload)
        html_exporter = HTMLExporter(template_name="classic")
        (body, _resources) = html_exporter.from_notebook_node(processed_nb)
        return body

    def _cache_executed_notebook(self, executed_nb: NotebookNode, payload: NotebookPayload) -> None:
        """Cache the executed notebook for reuse by Completed HTML.

        Speaker HTML caches its executed notebook so that Completed HTML can
        reuse it by simply filtering out the "notes" cells.
        """
        cid = payload.correlation_id
        cache_hash = payload.execution_cache_hash()

        logger.info(
            f"{cid}:Caching executed notebook for '{payload.input_file_name}' "
            f"(language={payload.language}, prog_lang={payload.prog_lang})"
        )

        assert self.cache is not None  # Checked by caller
        self.cache.store(
            input_file=payload.input_file,
            content_hash=cache_hash,
            language=payload.language,
            prog_lang=payload.prog_lang,
            executed_notebook=executed_nb,
        )

        logger.debug(f"{cid}:Successfully cached executed notebook")

    def _enhance_notebook_error(
        self,
        error: Exception,
        notebook: NotebookNode,
        payload: NotebookPayload,
    ) -> RuntimeError:
        """Enhance a notebook execution error with more context.

        Extracts the root cause, cell information, and code snippet from the
        error to create a more informative error message.

        Args:
            error: The original exception
            notebook: The notebook being processed
            payload: The notebook payload

        Returns:
            A new RuntimeError with enhanced context
        """
        import traceback as tb_module

        # Get the original traceback string
        tb_str = "".join(tb_module.format_exception(type(error), error, error.__traceback__))

        # Extract the root cause (the innermost exception)
        root_cause: BaseException = error
        while root_cause.__cause__ is not None:
            root_cause = root_cause.__cause__

        # Try to extract cell number from error message or traceback
        cell_number = None
        cell_match = re.search(r"[Cc]ell\s*#?(\d+)", str(error) + tb_str)
        if cell_match:
            cell_number = int(cell_match.group(1))

        # Try to find the Python error class and message
        error_class = type(root_cause).__name__
        error_message = str(root_cause)

        # Build the enhanced error message
        parts = [f"Notebook execution failed: {payload.input_file_name}"]

        if cell_number is not None:
            parts.append(f"  Cell: #{cell_number}")
            # Try to get the cell content for context
            cells = notebook.get("cells", [])
            if 0 <= cell_number < len(cells):
                cell = cells[cell_number]
                cell_source = cell.get("source", "")
                # Get first few lines of the cell
                source_lines = cell_source.split("\n")[:5]
                if source_lines:
                    snippet = "\n    ".join(source_lines)
                    if len(source_lines) < len(cell_source.split("\n")):
                        snippet += "\n    ..."
                    parts.append(f"  Cell content:\n    {snippet}")

        parts.append(f"  Error: {error_class}: {error_message}")

        # Include line number within cell if found
        line_match = re.search(r"line\s+(\d+)", str(error) + tb_str, re.IGNORECASE)
        if line_match:
            parts.append(f"  Line: {line_match.group(1)}")

        enhanced_message = "\n".join(parts)
        return RuntimeError(enhanced_message)

    async def write_other_files(
        self, cid: str, path: Path, payload: NotebookPayload, source_dir: Path | None = None
    ):
        """Write supporting files to the execution directory.

        In Docker mode with source mount (source_dir is set), files are already
        available at the source directory and don't need to be written.
        In other modes, files are decoded from base64 and written to temp directory.

        Args:
            cid: Correlation ID for logging
            path: Target directory to write files to (temp directory)
            payload: Notebook payload containing other_files
            source_dir: Optional source directory (Docker mode with source mount)
        """
        if source_dir is not None:
            # Docker mode with source mount: files are already available
            # No need to write anything
            logger.debug(f"{cid}:Source mount mode - files available at {source_dir}")
            return

        # Standard mode: decode and write files from payload
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.write_other_files_sync, cid, path, payload)

    @staticmethod
    def write_other_files_sync(cid: str, path: Path, payload: NotebookPayload):
        for extra_file, encoded_contents in payload.other_files.items():
            contents = b64decode(encoded_contents)
            logger.debug(f"{cid}:Writing extra file {extra_file}")
            file_path = path / extra_file
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(contents)
        if hasattr(os, "sync"):
            os.sync()

    async def _create_using_jupytext(self, processed_nb) -> str:
        config = jupytext_config.JupytextConfiguration(
            notebook_metadata_filter="-all", cell_metadata_filter="-all"
        )
        output = cast(
            str,
            jupytext.writes(
                processed_nb,
                fmt=self.output_spec.jupytext_format,
                config=config,
            ),
        )
        if not output.endswith("\n"):
            output += "\n"
        return output
