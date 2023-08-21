import logging
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha3_224
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, Template
from jupytext import jupytext
from nbconvert import HTMLExporter
from nbconvert.preprocessors import ExecutePreprocessor
from nbformat import NotebookNode
from nbformat.validator import normalize

from clm.core.course import Course
from clm.core.document import Document
from clm.core.output_spec import OutputSpec
from clm.utils.jupyter_utils import (
    Cell,
    find_notebook_titles,
    get_cell_type,
    get_slide_tag,
    get_tags,
    is_answer_cell,
    is_code_cell,
    is_markdown_cell,
    warn_on_invalid_code_tags,
    warn_on_invalid_markdown_tags,
)
from clm.utils.path_utils import base_path_for_csv_file
from clm.utils.prog_lang_utils import kernelspec_for, language_info


@dataclass()
class CellIdGenerator:
    unique_ids: set[str] = field(default_factory=set, init=False, repr=False)
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


@dataclass
class NotebookOutput:
    doc: "Notebook | None" = field(default=None, repr=False)
    path: Path = field(default_factory=Path, repr=False)
    expanded_notebook: str = field(default="", repr=False)
    unprocessed_notebook: NotebookNode | None = field(default=None, repr=False)
    processed_notebook: NotebookNode | None = field(default=None, repr=False)

    @property
    def jupytext_fmt(self):
        if self.doc.prog_lang == "python":
            return "py:percent"
        elif self.doc.prog_lang == "cpp":
            return "cpp:percent"
        elif self.doc.prog_lang == "rust":
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
        NotebookOutput.process_markdown_cell_contents(cell, output_spec)
        return cell

    answer_text = {"en": "Answer", "de": "Antwort"}

    @staticmethod
    def get_answer_text(output_spec: OutputSpec):
        return NotebookOutput.answer_text.get(output_spec.lang, "Answer")

    @staticmethod
    def process_markdown_cell_contents(cell: Cell, output_spec: OutputSpec):
        tags = get_tags(cell)
        if "notes" in tags:
            contents = cell.source
            cell.source = "<div style='background:yellow'>\n" + contents + "\n</div>"
        if is_answer_cell(cell):
            prefix = f"*{NotebookOutput.get_answer_text(output_spec)}:* "
            if output_spec.is_cell_contents_included(cell):
                cell.source = prefix + cell.source
            else:
                cell.source = prefix

    def process_notebook(
        self, doc: "Notebook", nb_node: NotebookNode, output_spec: OutputSpec
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
            logging.warning("Notebook has no jupytext metadata?")
        out_nb.metadata["language_info"] = language_info(doc.prog_lang)
        out_nb.metadata["kernelspec"] = kernelspec_for(doc.prog_lang)
        num_changes, normalized_nb = normalize(out_nb)
        if num_changes > 0:
            logging.warning(
                f"Notebook {doc.source_file.name} has {num_changes} "
                "changes during normalization!"
            )
        self.processed_notebook = normalized_nb

    def process(self, doc: "Notebook", expanded_nb: str, output_spec: OutputSpec):
        self.expanded_notebook = expanded_nb
        try:
            logging.info(f"Reading notebook as {self.jupytext_fmt}")
            nb = jupytext.reads(expanded_nb, fmt=self.jupytext_fmt)
            self.process_notebook(doc, nb, output_spec)
        except Exception as err:
            logging.error(f"Failed to process notebook {doc.source_file}")
            logging.error(err)


@dataclass
class Notebook(Document):
    notebook_text_before_expansion: str = field(default="", repr=False)
    output: NotebookOutput = field(default_factory=NotebookOutput, repr=False)

    def __post_init__(self):
        try:
            with open(self.source_file, encoding="utf-8") as file:
                self.notebook_text_before_expansion = file.read()
        except FileNotFoundError:
            source_file = self.source_file.relative_to(
                base_path_for_csv_file(self.source_file)
            )
            logging.error(f"Cannot create notebook: no file '{source_file}'.")

    def load_and_expand_jinja_template(
        self, course: "Course", output_spec: OutputSpec
    ) -> str:
        nb_template, jinja_env = self._load_jinja_template(course, output_spec)
        # HACK: We need to set the notebook text since it is used by get_target name.
        # Remove this order dependency in the future!
        name = self.get_target_name(course, output_spec)
        expanded_nb = nb_template.render(name=name)
        logging.debug(f"Notebook after expansion:\n{expanded_nb}")
        return expanded_nb

    def _load_jinja_template(self, course, output_spec):
        jinja_env = self._create_jinja_environment(course)
        output_path = self.get_full_target_path(course, output_spec).relative_to(
            course.target_dir
        )
        nb_template: Template = jinja_env.from_string(
            self.notebook_text_before_expansion,
            globals=self._create_jinja_globals(
                self.source_file.relative_to(course.source_dir),
                output_path,
                output_spec,
            ),
        )
        return nb_template, jinja_env

    def _create_jinja_environment(self, course: "Course"):
        template_path = course.template_dir
        self._assert_template_dir_exists(template_path)
        jinja_env = Environment(
            loader=FileSystemLoader([self.source_file.parent, template_path]),
            autoescape=False,
            undefined=StrictUndefined,
            line_statement_prefix="// j2" if self.prog_lang == "cpp" else "# j2",
            keep_trailing_newline=True,
        )
        return jinja_env

    @staticmethod
    def _create_jinja_globals(source_file, output_path, output_spec):
        return {
            "source_name": source_file.as_posix(),
            "name": output_path.as_posix(),
            "is_notebook": output_spec.file_suffix == "ipynb",
            "lang": output_spec.lang,
        }

    @staticmethod
    def _assert_template_dir_exists(template_path):
        if not template_path.exists():
            raise ValueError(f"Template directory {template_path} does not exist.")

    def process(self, course: "Course", output_spec: OutputSpec):
        logging.info(f"Processing notebook {self.source_file}.")
        self.output.doc = self
        self.output.path = self.get_full_target_path(course, output_spec)
        expanded_nb = self.load_and_expand_jinja_template(course, output_spec)
        self.output.process(self, expanded_nb, output_spec)

    def get_target_name(self, course: "Course", output_spec: OutputSpec) -> str:
        out_name = self.source_file.name
        if raw_text := self.notebook_text_before_expansion:
            out_names = find_notebook_titles(raw_text, out_name)
            out_name = out_names[output_spec.lang]
        assert out_name

        path = self.source_file.with_name(f"{self.file_num :0>2} {out_name}")
        return path.with_suffix(f".{output_spec.file_suffix}").name

    def write_to_target(self, course: "Course", output_spec: OutputSpec):
        if output_spec.notebook_format == "html":
            self._write_using_nbconvert(course, output_spec)
        else:
            self._write_using_jupytext(course, output_spec)

    def _write_using_nbconvert(self, course: "Course", output_spec: OutputSpec):
        self._assert_processed_notebook_exists()
        target_path = self.get_full_target_path(course, output_spec)
        if output_spec.evaluate_for_html:
            if any(
                is_code_cell(cell)
                for cell in self.output.processed_notebook.get("cells", [])
            ):
                logging.info(
                    f"Evaluating and writing notebook {self.source_file.as_posix()!r} "
                    f"to {target_path.as_posix()!r}."
                )
                try:
                    ep = ExecutePreprocessor(timeout=None)
                    ep.preprocess(
                        self.output.processed_notebook,
                        {"metadata": {"path": self.source_file.parent}},
                    )
                except Exception:
                    print(f"Error while processing {self.source_file}!")
                    raise
            else:
                logging.info(f"Notebook {self.source_file} contains no code cells.")
        logging.info(
            f"Writing notebook {self.source_file.as_posix()!r} "
            f"to {target_path.as_posix()!r}."
        )
        target_path.parent.mkdir(exist_ok=True, parents=True)
        html_exporter = HTMLExporter(template_name="classic")
        (body, _resources) = html_exporter.from_notebook_node(
            self.output.processed_notebook
        )
        with open(target_path.with_suffix(".html"), "w") as html_file:
            html_file.write(body)

    def _write_using_jupytext(self, course: "Course", output_spec: OutputSpec):
        self._assert_processed_notebook_exists()
        target_path = self.get_full_target_path(course, output_spec)
        logging.info(
            f"Writing notebook {self.source_file.as_posix()!r} "
            f"to {target_path.as_posix()!r}."
        )
        target_path.parent.mkdir(exist_ok=True, parents=True)
        jupytext.write(
            self.output.processed_notebook,
            target_path,
            fmt=output_spec.notebook_format,
        )

    def _assert_processed_notebook_exists(self):
        if self.output.processed_notebook is None:
            raise RuntimeError(
                f"Trying to copy notebook {self.source_file.as_posix()!r} "
                "before it was processed."
            )
