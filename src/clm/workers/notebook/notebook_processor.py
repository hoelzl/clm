import asyncio
import copy
import logging
import os
import re
import warnings
from base64 import b64decode
from dataclasses import dataclass
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

from clm.infrastructure.messaging.notebook_classes import NotebookPayload

from .output_spec import OutputSpec

if TYPE_CHECKING:
    from clm.infrastructure.database.executed_notebook_cache import ExecutedNotebookCache
from clm.infrastructure.messaging.base_classes import ProcessingWarning

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


@dataclass
class CellContext:
    """Context for the currently executing cell.

    This is used to track which cell is being executed so that
    error messages can include accurate cell information even
    when the error occurs before the notebook outputs are populated.
    """

    cell_index: int
    cell_source: str
    cell_type: str = "code"


class TrackingExecutePreprocessor(ExecutePreprocessor):
    """ExecutePreprocessor that tracks the currently executing cell.

    This subclass updates the NotebookProcessor's _current_cell attribute
    before each cell is executed, enabling accurate error reporting even
    when errors occur before cell outputs are populated.
    """

    def __init__(
        self,
        processor: "NotebookProcessor",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.processor = processor

    def preprocess_cell(self, cell, resources, cell_index):
        """Execute a cell, tracking it for error reporting.

        Args:
            cell: The notebook cell to execute
            resources: Resources dict passed through preprocessing
            cell_index: Index of the cell in the notebook

        Returns:
            Tuple of (processed cell, resources)
        """
        # Set the current cell context before execution
        self.processor._current_cell = CellContext(
            cell_index=cell_index,
            cell_source=cell.get("source", ""),
            cell_type=cell.get("cell_type", "code"),
        )
        # Execute the cell - on success, clear context; on error, preserve it
        result = super().preprocess_cell(cell, resources, cell_index)
        # Only clear on success - preserve context for error reporting
        self.processor._current_cell = None
        return result


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
        # Track the currently executing cell for accurate error reporting
        self._current_cell: CellContext | None = None

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
                loader=PackageLoader("clm.workers.notebook", templates_path),
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
                cell, index, payload.input_file, payload.img_path_prefix, payload
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
        self,
        cell: Cell,
        index: int = 0,
        file_path: str = "",
        img_path_prefix: str = "img/",
        payload: NotebookPayload | None = None,
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

        self._process_markdown_cell_contents(cell, img_path_prefix, payload)
        return cell

    def _process_markdown_cell_contents(
        self,
        cell: Cell,
        img_path_prefix: str = "img/",
        payload: NotebookPayload | None = None,
    ):
        tags = get_tags(cell)
        if "notes" in tags:
            contents = cell["source"]
            cell["source"] = (
                "<div style='background: yellow; color: black;'>\n" + contents + "\n</div>"
            )
        if is_answer_cell(cell):
            answer_text = "Answer" if self.output_spec.language == "en" else "Antwort"
            prefix = f"*{answer_text}:* "
            if self.output_spec.is_cell_contents_included(cell):
                cell["source"] = prefix + cell["source"]
            else:
                cell["source"] = prefix

        # Rewrite .png -> .svg for images that have SVG equivalents
        if payload and payload.svg_available_stems:
            cell["source"] = self._rewrite_png_to_svg(
                cell["source"], set(payload.svg_available_stems)
            )

        # Rewrite image paths from img/filename to the shared img/ folder location
        cell["source"] = self._rewrite_image_paths(cell["source"], img_path_prefix)

        # Inject data URLs for images (if enabled and cell doesn't opt out)
        if payload and payload.inline_images and "nodataurl" not in tags:
            cell["source"] = self._inject_data_urls(cell["source"], payload)

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

    @staticmethod
    def _rewrite_png_to_svg(content: str, svg_stems: set[str]) -> str:
        """Rewrite .png references to .svg for images that have SVG equivalents.

        Only rewrites image URLs whose stem (filename without extension) is in
        the svg_stems set. This ensures raw .png files that are not generated
        from DrawIO/PlantUML sources are left unchanged.

        Args:
            content: Markdown cell content
            svg_stems: Set of image stems that have SVG versions available

        Returns:
            Content with .png -> .svg rewrites where applicable
        """

        def replace_if_svg(match):
            prefix = match.group(1)
            filename = match.group(2)  # e.g., 'diagram.png'
            suffix = match.group(3)
            stem = Path(filename).stem
            if stem in svg_stems and filename.endswith(".png"):
                filename = stem + ".svg"
            return f"{prefix}{filename}{suffix}"

        return MEDIA_SRC_PATTERN.sub(replace_if_svg, content)

    # Regex to match <img> tags with src attribute (for data URL injection)
    _IMG_SRC_PATTERN = re.compile(r'<img\s+[^>]*src="(?P<image_url>[^"]+)"')

    # MIME type mapping for image inlining
    _EXTENSION_TO_MIME_TYPE = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
    }

    def _inject_data_urls(self, content: str, payload: NotebookPayload) -> str:
        """Replace image src attributes with base64 data URLs.

        Reads images from the filesystem (source topic directory) with fallback
        to the other_files payload data. Based on Stefan Behnel's implementation.

        Args:
            content: Markdown cell content with <img> tags
            payload: Notebook payload with source directory and other_files

        Returns:
            Content with images embedded as data URLs
        """
        import base64

        source_dir = Path(payload.source_topic_dir) if payload.source_topic_dir else None

        def replace_with_data_url(match: re.Match) -> str:
            match_tag: str = match.group()
            image_url: str = match.group("image_url")

            # Skip data URLs and HTTP(S) URLs
            if image_url.startswith(("data:", "http:", "https:")):
                return match_tag

            # Try reading from filesystem first
            image_data: bytes | None = None
            if source_dir:
                image_path = source_dir / image_url
                if image_path.is_file():
                    try:
                        image_data = image_path.read_bytes()
                    except OSError:
                        pass

            # Fall back to other_files payload
            if image_data is None and image_url in payload.other_files:
                raw = payload.other_files[image_url]
                if isinstance(raw, bytes):
                    image_data = raw
                else:
                    image_data = b64decode(raw)

            if image_data is None:
                return match_tag  # Image not available, keep original

            extension = Path(image_url).suffix.lower()
            mime_type = self._EXTENSION_TO_MIME_TYPE.get(extension)
            if mime_type is None:
                return match_tag  # Unknown format, keep original

            encoded = base64.b64encode(image_data).decode()
            data_url = f"data:{mime_type};base64,{encoded}"
            result: str = match_tag.replace(image_url, data_url)
            return result

        return self._IMG_SRC_PATTERN.sub(replace_with_data_url, content)

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
            # Create FRESH TrackingExecutePreprocessor for each attempt
            # This ensures no stale ZMQ state from previous failures
            # TrackingExecutePreprocessor updates _current_cell for error reporting
            ep = TrackingExecutePreprocessor(self, timeout=None, startup_timeout=300)
            try:

                def run_preprocess(
                    ep: TrackingExecutePreprocessor = ep,
                ) -> tuple[NotebookNode, dict]:
                    return ep.preprocess(
                        processed_nb,
                        resources={"metadata": {"path": path}},
                    )

                await loop.run_in_executor(None, run_preprocess)
                last_error = None
                break  # Success - exit retry loop
            except Exception as e:
                # Catch all execution errors including:
                # - RuntimeError (kernel died)
                # - CellExecutionError (cell failed to execute)
                # - DeadKernelError (kernel crashed)
                # - Other nbclient exceptions
                last_error = e
                error_type = type(e).__name__
                if not logger.isEnabledFor(logging.DEBUG):
                    logger.info(
                        f"{cid}: Execution failed ({error_type}, attempt {attempt}/{NUM_RETRIES_FOR_HTML})"
                    )
                logger.debug(f"{cid}: Execution failed ({error_type}, attempt {attempt}): {e}")
            finally:
                # ALWAYS cleanup kernel resources to prevent ZMQ leaks
                await self._cleanup_kernel_resources(ep, cid)

            # Exponential backoff before next retry
            if attempt < NUM_RETRIES_FOR_HTML:
                await asyncio.sleep(1.0 * attempt)

        if last_error is not None:
            # Enhance the error message with more context
            # _current_cell may contain context from the failed cell
            enhanced_error = self._enhance_notebook_error(last_error, processed_nb, payload)
            # Clear cell context after using it for error enhancement
            self._current_cell = None
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
        error to create a more informative error message. For C++ notebooks,
        also tries to extract compiler error details.

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
        error_str = str(error)

        # Extract the root cause (the innermost exception)
        root_cause: BaseException = error
        while root_cause.__cause__ is not None:
            root_cause = root_cause.__cause__

        # Try to extract cell number from error message or traceback
        cell_number: int | None = None
        cell_match = re.search(r"[Cc]ell\s*#?(\d+)", error_str + tb_str)
        if cell_match:
            cell_number = int(cell_match.group(1))

        # Try to find the error class and message.
        # Walk the exception chain looking for CellExecutionError (or similar)
        # which carries ename/evalue with the actual Python error details.
        # This avoids displaying the verbose CellExecutionError.__str__() output.
        exc_to_check: BaseException | None = error
        while exc_to_check is not None:
            if hasattr(exc_to_check, "ename") and hasattr(exc_to_check, "evalue"):
                error_class = exc_to_check.ename
                error_message = exc_to_check.evalue
                break
            exc_to_check = exc_to_check.__cause__
        else:
            error_class = type(root_cause).__name__
            error_message = str(root_cause)

        # For C++ notebooks, try to extract compiler error from error output
        # xeus-cling format: "input_line_X:Y:Z: error: message"
        cpp_error_info: dict[str, str] = {}
        cpp_error_match = re.search(
            r"input_line_\d+:(\d+):(\d+):\s*error:\s*(.+?)(?:\n|$)",
            error_str + tb_str,
        )
        if cpp_error_match:
            cpp_error_info["line"] = cpp_error_match.group(1)
            cpp_error_info["column"] = cpp_error_match.group(2)
            cpp_error_info["message"] = cpp_error_match.group(3).strip()
            error_class = "CompilationError"
            error_message = cpp_error_info["message"]

        # Also check for generic clang-style errors
        if not cpp_error_info:
            clang_error = re.search(
                r":\s*(\d+):\s*(\d+):\s*error:\s*(.+?)(?:\n|$)",
                error_str + tb_str,
            )
            if clang_error:
                cpp_error_info["line"] = clang_error.group(1)
                cpp_error_info["column"] = clang_error.group(2)
                cpp_error_info["message"] = clang_error.group(3).strip()
                error_class = "CompilationError"
                error_message = cpp_error_info["message"]

        # Try to find the failing cell - prioritize tracked cell context if available
        cells = notebook.get("cells", [])
        failing_cell = None
        cell_source: str | None = None

        # Priority 1: Use tracked cell context (most reliable)
        if self._current_cell is not None:
            cell_number = self._current_cell.cell_index
            cell_source = self._current_cell.cell_source
            if 0 <= cell_number < len(cells):
                failing_cell = cells[cell_number]
        # Priority 2: Use cell number from error message
        elif cell_number is not None and 0 <= cell_number < len(cells):
            failing_cell = cells[cell_number]
        else:
            # Priority 3: Try multiple strategies to find the failing cell
            failing_cell, cell_number = self._find_failing_cell(cells, error_str + tb_str)

        # Build the enhanced error message
        parts = [f"Notebook execution failed: {payload.input_file_name}"]

        if cell_number is not None:
            parts.append(f"  Cell: #{cell_number}")

        # Get cell source - prefer tracked context, fall back to notebook cell
        if cell_source is None and failing_cell is not None:
            cell_source = failing_cell.get("source", "")

        if cell_source:
            # Get first few lines of the cell
            source_lines = cell_source.split("\n")[:8]
            if source_lines:
                snippet = "\n    ".join(source_lines)
                if len(source_lines) < len(cell_source.split("\n")):
                    snippet += "\n    ..."
                parts.append(f"  Cell content:\n    {snippet}")

        parts.append(f"  Error: {error_class}: {error_message}")

        # Include line/column number if found (especially useful for C++)
        if cpp_error_info:
            parts.append(f"  Line: {cpp_error_info['line']}, Column: {cpp_error_info['column']}")
        else:
            line_match = re.search(r"line\s+(\d+)", error_str + tb_str, re.IGNORECASE)
            if line_match:
                parts.append(f"  Line: {line_match.group(1)}")

        enhanced_message = "\n".join(parts)
        return RuntimeError(enhanced_message)

    def _find_failing_cell(self, cells: list, error_text: str) -> tuple[dict | None, int | None]:
        """Find the cell that caused an execution error.

        Uses multiple strategies:
        1. Look for cells with error output type
        2. Look for cells with stderr containing error patterns
        3. Find the cell with the highest execution_count (most recently executed)
        4. Return first code cell as fallback

        Args:
            cells: List of notebook cells
            error_text: Combined error message and traceback for pattern matching

        Returns:
            Tuple of (failing_cell, cell_index) or (None, None) if not found
        """
        # Strategy 1: Look for cells with error output type
        for idx, cell in enumerate(cells):
            if cell.get("cell_type") != "code":
                continue
            outputs = cell.get("outputs", [])
            for output in outputs:
                if output.get("output_type") == "error":
                    return cell, idx

        # Strategy 2: Look for cells with stderr containing error patterns
        # C++ compilation errors often appear in stderr stream
        error_patterns = ["error:", "Error:", "ERROR:", "undefined", "undeclared"]
        for idx, cell in enumerate(cells):
            if cell.get("cell_type") != "code":
                continue
            outputs = cell.get("outputs", [])
            for output in outputs:
                if output.get("output_type") == "stream" and output.get("name") == "stderr":
                    text = output.get("text", "")
                    if isinstance(text, list):
                        text = "".join(text)
                    if any(pattern in text for pattern in error_patterns):
                        return cell, idx

        # Strategy 3: Find the cell with the highest execution_count
        # This is likely the most recently executed cell where the error occurred
        max_exec_count = -1
        max_exec_idx = -1
        for idx, cell in enumerate(cells):
            if cell.get("cell_type") != "code":
                continue
            exec_count = cell.get("execution_count")
            if exec_count is not None and exec_count > max_exec_count:
                max_exec_count = exec_count
                max_exec_idx = idx

        if max_exec_idx >= 0:
            return cells[max_exec_idx], max_exec_idx

        # Strategy 4: Return first code cell as fallback
        for idx, cell in enumerate(cells):
            if cell.get("cell_type") == "code":
                return cell, idx

        return None, None

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
