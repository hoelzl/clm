import asyncio
import copy
import logging
import os
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
from .utils.jupyter_utils import (
    Cell,
    get_cell_type,
    get_slide_tag,
    get_tags,
    is_answer_cell,
    is_code_cell,
    is_markdown_cell,
    warn_on_invalid_code_tags,
    warn_on_invalid_markdown_tags,
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

    async def process_notebook(self, payload: NotebookPayload) -> str:
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
            # Cache miss - fail with error
            raise RuntimeError(
                f"Cache miss for Completed HTML notebook '{payload.input_file_name}'. "
                f"Speaker HTML must be processed first. "
                f"Use --fallback-execute to allow direct execution."
            )

        # Normal processing path
        expanded_nb = await self.load_and_expand_jinja_template(
            payload.data, payload.input_file_name, cid
        )
        processed_nb = await self.process_notebook_for_spec(expanded_nb, payload)
        result = await self.create_contents(processed_nb, payload)
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
        content_hash = payload.content_hash()

        logger.debug(f"{cid}:Trying to reuse cached execution for '{payload.input_file_name}'")

        assert self.cache is not None  # Checked by caller
        cached_nb = self.cache.get(
            input_file=payload.input_file,
            content_hash=content_hash,
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
        traitlets.log.get_logger().addFilter(DontWarnForMissingAltTags())
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
        self._generate_cell_metadata(cell, index)
        await asyncio.sleep(0)
        if LOG_CELL_PROCESSING:
            logger.debug(f"{cid}:Processing cell {cell} of {payload.input_file_name}")
        if is_code_cell(cell):
            return self._process_code_cell(cell)
        elif is_markdown_cell(cell):
            return self._process_markdown_cell(cell)
        else:
            logger.warning(f"{cid}:Keeping unknown cell type {get_cell_type(cell)!r}.")
            return cell

    def _generate_cell_metadata(self, cell, index):
        self.id_generator.set_cell_id(cell, index)
        self._process_slide_tag(cell)

    @staticmethod
    def _process_slide_tag(cell):
        slide_tag = get_slide_tag(cell)
        if slide_tag:
            cell["metadata"]["slideshow"] = {"slide_type": slide_tag}

    def _process_code_cell(self, cell: Cell) -> Cell:
        if not self.output_spec.is_cell_contents_included(cell):
            cell["source"] = ""
            cell["outputs"] = []
        warn_on_invalid_code_tags(get_tags(cell))
        return cell

    def _process_markdown_cell(self, cell: Cell) -> Cell:
        tags = get_tags(cell)
        warn_on_invalid_markdown_tags(tags)
        self._process_markdown_cell_contents(cell)
        return cell

    def _process_markdown_cell_contents(self, cell: Cell):
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

    async def create_contents(self, processed_nb: NotebookNode, payload: NotebookPayload) -> str:
        try:
            if self.output_spec.format == "html":
                result = await self._create_using_nbconvert(processed_nb, payload)
            else:
                result = await self._create_using_jupytext(processed_nb)
            return result
        except RuntimeError as e:
            logging.error(
                f"Failed to convert notebook '{payload.input_file_name}' to HTML: {e}",
            )
            logging.debug(f"Error traceback for '{payload.input_file_name}'", exc_info=True)
            raise

    async def _create_using_nbconvert(self, processed_nb, payload: NotebookPayload) -> str:
        cid = payload.correlation_id
        traitlets.log.get_logger().addFilter(DontWarnForMissingAltTags())  # type: ignore[union-attr]
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
                        ep = ExecutePreprocessor(timeout=None, startup_timeout=300)
                        loop = asyncio.get_running_loop()
                        with TemporaryDirectory() as temp_dir:
                            # path = (
                            #     Path("C:/tmp")
                            #     if platform.system() == "Windows"
                            #     else Path("/tmp")
                            # )
                            path = Path(temp_dir)
                            await self.write_other_files(cid, path, payload)
                            for i in range(1, NUM_RETRIES_FOR_HTML + 1):
                                try:
                                    await loop.run_in_executor(
                                        None,
                                        lambda: ep.preprocess(
                                            processed_nb,
                                            resources={"metadata": {"path": path}},
                                        ),
                                    )
                                except RuntimeError as e:
                                    if not logger.isEnabledFor(logging.DEBUG):
                                        logger.info(f"{cid}: Kernel died: Trying restart {i}")
                                    logger.debug(f"{cid}: Kernel died: Trying restart {i}: {e}")
                                    await asyncio.sleep(1.0 * i)
                                    continue
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
        html_exporter = HTMLExporter(template_name="classic")
        (body, _resources) = html_exporter.from_notebook_node(processed_nb)
        return body

    def _cache_executed_notebook(self, executed_nb: NotebookNode, payload: NotebookPayload) -> None:
        """Cache the executed notebook for reuse by Completed HTML.

        Speaker HTML caches its executed notebook so that Completed HTML can
        reuse it by simply filtering out the "notes" cells.
        """
        cid = payload.correlation_id
        content_hash = payload.content_hash()

        logger.info(
            f"{cid}:Caching executed notebook for '{payload.input_file_name}' "
            f"(language={payload.language}, prog_lang={payload.prog_lang})"
        )

        assert self.cache is not None  # Checked by caller
        self.cache.store(
            input_file=payload.input_file,
            content_hash=content_hash,
            language=payload.language,
            prog_lang=payload.prog_lang,
            executed_notebook=executed_nb,
        )

        logger.debug(f"{cid}:Successfully cached executed notebook")

    async def write_other_files(self, cid: str, path: Path, payload: NotebookPayload):
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
