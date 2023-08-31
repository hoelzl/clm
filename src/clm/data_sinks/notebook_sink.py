import logging
import os
import warnings
from copy import deepcopy
from attr import define, field
from hashlib import sha3_224
from pathlib import Path
from typing import TYPE_CHECKING

import traitlets.log
from jupytext import jupytext
from nbconvert import HTMLExporter
from nbconvert.preprocessors import ExecutePreprocessor
from nbformat import NotebookNode
from nbformat.validator import normalize

from clm.core.course import Course
from clm.core.data_source_paths import full_target_path_for_data_source
from clm.core.data_sink import DataSink
from clm.core.output_spec import OutputSpec

if TYPE_CHECKING:
    from clm.data_sources.notebook_data_source import NotebookDataSource

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
class NotebookDataSink(DataSink):
    data_source: "NotebookDataSource | None" = field(default=None, repr=False)
    path: Path = field(factory=Path, repr=False)
    expanded_notebook: str = field(default="", repr=False)
    unprocessed_notebook: NotebookNode | None = field(default=None, repr=False)
    processed_notebook: NotebookNode | None = field(default=None, repr=False)

    @property
    def jupytext_fmt(self):
        if self.data_source.prog_lang == "python":
            return "py:percent"
        elif self.data_source.prog_lang == "cpp":
            return "cpp:percent"
        elif self.data_source.prog_lang == "rust":
            return "md"

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
                f"NotebookDataSource {doc.source_file.name} has {num_changes} "
                "changes during normalization!"
            )
        self.processed_notebook = normalized_nb

    def process(self, doc: "NotebookDataSource", expanded_nb: str, output_spec: OutputSpec):
        self.expanded_notebook = expanded_nb
        try:
            logging.info(f"Reading notebook as {self.jupytext_fmt}")
            nb = jupytext.reads(expanded_nb, fmt=self.jupytext_fmt)
            self.process_notebook(doc, nb, output_spec)
        except Exception as err:
            logging.error(f"Failed to process notebook {doc.source_file}")
            logging.error(err)

    def write_to_target(self, course: "Course", output_spec: OutputSpec):
        if output_spec.notebook_format == "html":
            self._write_using_nbconvert(course, output_spec)
        else:
            self._write_using_jupytext(course, output_spec)

    def _write_using_nbconvert(self, course: "Course", output_spec: OutputSpec):
        self._assert_processed_notebook_exists()
        traitlets.log.get_logger().addFilter(DontWarnForMissingAltTags())
        target_path = full_target_path_for_data_source(self.data_source, course, output_spec)
        if output_spec.evaluate_for_html:
            if any(
                is_code_cell(cell) for cell in self.processed_notebook.get("cells", [])
            ):
                logging.info(
                    f"Evaluating and writing notebook "
                    f"{self.data_source.source_file.as_posix()!r} "
                    f"to {target_path.as_posix()!r}."
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
                                "metadata": {"path": self.data_source.source_file.parent}
                            },
                        )
                except Exception:
                    print(f"Error while processing {self.data_source.source_file}!")
                    raise
            else:
                logging.info(f"NotebookDataSource {self.data_source.source_file} contains no code cells.")
        logging.info(
            f"Writing notebook {self.data_source.source_file.as_posix()!r} "
            f"to {target_path.as_posix()!r}."
        )
        target_path.parent.mkdir(exist_ok=True, parents=True)
        html_exporter = HTMLExporter(template_name="classic")
        (body, _resources) = html_exporter.from_notebook_node(self.processed_notebook)
        with open(target_path.with_suffix(".html"), "w") as html_file:
            html_file.write(body)

    def _write_using_jupytext(self, course: "Course", output_spec: OutputSpec):
        self._assert_processed_notebook_exists()
        target_path = full_target_path_for_data_source(self.data_source, course, output_spec)
        logging.info(
            f"Writing notebook {self.data_source.source_file.as_posix()!r} "
            f"to {target_path.as_posix()!r}."
        )
        target_path.parent.mkdir(exist_ok=True, parents=True)
        jupytext.write(
            self.processed_notebook,
            target_path,
            fmt=output_spec.notebook_format,
        )

    def _assert_processed_notebook_exists(self):
        if self.processed_notebook is None:
            raise RuntimeError(
                f"Trying to copy notebook {self.data_source.source_file.as_posix()!r} "
                "before it was processed."
            )
