import logging
import os
import warnings
from copy import deepcopy
from hashlib import sha3_224
from typing import TYPE_CHECKING

import traitlets.log
import jupytext.config as jupytext_config
from attr import define, field
from clm.core.course import Course
from clm.core.data_sink import DataSink
from clm.core.data_source_location import full_target_location_for_data_source
from clm.core.output_spec import OutputSpec
from jupytext import jupytext
from nbconvert import HTMLExporter
from nbconvert.preprocessors import ExecutePreprocessor
from nbformat import NotebookNode
from nbformat.validator import normalize

if TYPE_CHECKING:
    from clm.data_sources.notebook_data_source import NotebookDataSource

from clm.utils.config import config
from clm.utils.jupyter_utils import (
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
from clm.utils.prog_lang_utils import kernelspec_for, language_info


@define
class CellIdGenerator:
    unique_ids: set[str] = field(factory=set, init=False, repr=False)
    id_uniquifier: int = 1

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


@define
class NotebookDataSink(DataSink["NotebookDataSource"]):
    expanded_notebook: str = field(default="", repr=False)
    unprocessed_notebook: NotebookNode | None = field(default=None, repr=False)
    processed_notebook: NotebookNode | None = field(default=None, repr=False)

    @property
    def jupytext_format(self) -> str:
        if self.data_source.prog_lang not in config["prog_lang"]:
            raise ValueError(
                f"Unknown programming language {self.data_source.prog_lang!r}."
            )
        if "jupytext_format" not in config["prog_lang"][self.data_source.prog_lang]:
            raise ValueError(
                f"Programming language {self.data_source.prog_lang!r} has no "
                f"jupytext_format in config."
            )
        return config["prog_lang"][self.data_source.prog_lang]["jupytext_format"]

    def process_cell(
        self,
        cell: Cell,
        index: int,
        output_spec: OutputSpec,
        id_generator: CellIdGenerator,
    ) -> NotebookNode:
        self.generate_cell_metadata(cell, index, id_generator)
        logging.debug(f"Processing cell {cell}")
        if is_code_cell(cell):
            logging.debug(">> Cell is code cell")
            return self.process_code_cell(cell, output_spec)
        elif is_markdown_cell(cell):
            logging.debug(">> Cell is markdown cell")
            return self.process_markdown_cell(cell, output_spec)
        else:
            logging.warning(f"Keeping unknown cell type {get_cell_type(cell)!r}.")
            return cell

    def generate_cell_metadata(
        self, cell: Cell, index: int, id_generator: CellIdGenerator
    ) -> None:
        id_generator.set_cell_id(cell, index)
        self.process_slide_tag(cell)

    @staticmethod
    def process_slide_tag(cell: Cell):
        slide_tag = get_slide_tag(cell)
        if slide_tag:
            cell["metadata"]["slideshow"] = {"slide_type": slide_tag}

    @staticmethod
    def process_code_cell(cell: Cell, output_spec: OutputSpec):
        assert get_cell_type(cell) == "code"
        if not output_spec.is_cell_contents_included(cell):
            cell.source = ""
            cell.outputs = []
        warn_on_invalid_code_tags(get_tags(cell))
        return cell

    @staticmethod
    def process_markdown_cell(cell, output_spec: OutputSpec):
        assert get_cell_type(cell) == "markdown"
        tags = get_tags(cell)
        warn_on_invalid_markdown_tags(tags)
        NotebookDataSink.process_markdown_cell_contents(cell, output_spec)
        return cell

    answer_text = {"en": "Answer", "de": "Antwort"}

    @staticmethod
    def get_answer_text(output_spec: OutputSpec):
        return NotebookDataSink.answer_text.get(output_spec.lang, "Answer")

    @staticmethod
    def process_markdown_cell_contents(cell: Cell, output_spec: OutputSpec):
        tags = get_tags(cell)
        if "notes" in tags:
            contents = cell.source
            cell.source = "<div style='background:yellow'>\n" + contents + "\n</div>"
        if is_answer_cell(cell):
            prefix = f"*{NotebookDataSink.get_answer_text(output_spec)}:* "
            if output_spec.is_cell_contents_included(cell):
                cell.source = prefix + cell.source
            else:
                cell.source = prefix

    def process_notebook(
        self, doc: "NotebookDataSource", nb_node: NotebookNode, output_spec: OutputSpec
    ):
        self.unprocessed_notebook = nb_node
        out_nb = deepcopy(nb_node)
        cell_id_generator = CellIdGenerator()
        new_cells = [
            self.process_cell(cell, index, output_spec, cell_id_generator)
            for index, cell in enumerate(out_nb.get("cells", []))
            if output_spec.is_cell_included(cell)
        ]
        out_nb.cells = new_cells
        if out_nb.metadata.get("jupytext"):
            del out_nb.metadata["jupytext"]
        else:
            logging.warning("NotebookDataSource has no jupytext metadata?")
        out_nb.metadata["language_info"] = language_info(doc.prog_lang)
        out_nb.metadata["kernelspec"] = kernelspec_for(doc.prog_lang)
        num_changes, normalized_nb = normalize(out_nb)
        if num_changes > 0:
            logging.warning(
                f"NotebookDataSource {doc.source_loc.name} has {num_changes} "
                "changes during normalization!"
            )
        self.processed_notebook = normalized_nb

    def process(
        self, doc: "NotebookDataSource", expanded_nb: str, output_spec: OutputSpec
    ):
        self.expanded_notebook = expanded_nb
        try:
            logging.info(f"Reading notebook as {self.jupytext_format}")
            nb = jupytext.reads(expanded_nb, fmt=self.jupytext_format)
            self.process_notebook(doc, nb, output_spec)
        except Exception as err:
            logging.error(f"Failed to process notebook {doc.source_loc}")
            logging.error(err)

    def write_to_target(self):
        if self.output_spec.notebook_format == "html":
            for _ in range(config.num_retries_for_html):
                try:
                    self._write_using_nbconvert(self.course, self.output_spec)
                    break
                except RuntimeError as err:
                    logging.error(
                        f"Failed to write notebook {self.data_source.source_loc} to HTML."
                    )
                    logging.error(err)
        else:
            self._write_using_jupytext(self.course, self.output_spec)

    def _write_using_nbconvert(self, course: "Course", output_spec: OutputSpec):
        body, target_loc = self._create_html_contents(course, output_spec)
        target_loc.parent.mkdir(exist_ok=True, parents=True)
        with target_loc.open("w") as html_file:
            html_file.write(body)

    def _create_html_contents(self, course, output_spec):
        self._assert_processed_notebook_exists()
        traitlets.log.get_logger().addFilter(DontWarnForMissingAltTags())
        target_loc = full_target_location_for_data_source(
            self.data_source, course, output_spec
        )
        if output_spec.evaluate_for_html:
            if any(
                is_code_cell(cell) for cell in self.processed_notebook.get("cells", [])
            ):
                logging.info(
                    f"Evaluating and writing notebook "
                    f"{self.data_source.source_loc.as_posix()!r} "
                    f"to {target_loc.as_posix()!r}."
                )
                try:
                    # To silence warnings about frozen modules...
                    os.environ["PYDEVD_DISABLE_FILE_VALIDATION"] = "1"
                    with warnings.catch_warnings():
                        warnings.filterwarnings(
                            "ignore",
                            "Proactor event loop does not implement add_reader",
                        )
                        ep = ExecutePreprocessor(timeout=None)
                        ep.preprocess(
                            self.processed_notebook,
                            resources={
                                "metadata": {
                                    "path": self.data_source.source_loc.absolute().parent
                                }
                            },
                        )
                except Exception:
                    print(f"Error while processing {self.data_source.source_loc}!")
                    raise
            else:
                logging.info(
                    f"NotebookDataSource {self.data_source.source_loc} contains no code cells."
                )
        logging.info(
            f"Writing notebook {self.data_source.source_loc.as_posix()!r} "
            f"to {target_loc.as_posix()!r}."
        )
        html_exporter = HTMLExporter(template_name="classic")
        (body, _resources) = html_exporter.from_notebook_node(self.processed_notebook)
        return body, target_loc.with_suffix(".html")

    def _write_using_jupytext(self, course: "Course", output_spec: OutputSpec):
        output, target_loc = self._create_notebook_contents(course, output_spec)
        target_loc.parent.mkdir(exist_ok=True, parents=True)
        with target_loc.open("w", encoding="utf-8") as file:
            file.write(output)

    def _create_notebook_contents(self, course, output_spec):
        self._assert_processed_notebook_exists()
        target_loc = full_target_location_for_data_source(
            self.data_source, course, output_spec
        )
        logging.info(
            f"Writing notebook {self.data_source.source_loc.as_posix()!r} "
            f"to {target_loc.as_posix()!r}."
        )
        config = jupytext_config.JupytextConfiguration(
            notebook_metadata_filter="-all", cell_metadata_filter="-all"
        )
        output = jupytext.writes(
            self.processed_notebook,
            fmt=output_spec.notebook_format,
            config=config,
        )
        if not output.endswith("\n"):
            output += "\n"
        return output, target_loc

    def _assert_processed_notebook_exists(self):
        if self.processed_notebook is None:
            raise RuntimeError(
                f"Trying to copy notebook {self.data_source.source_loc.as_posix()!r} "
                "before it was processed."
            )
