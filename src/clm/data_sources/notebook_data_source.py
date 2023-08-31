import logging
from pathlib import Path

from attr import define, field

from jinja2 import Environment, FileSystemLoader, StrictUndefined, Template

from clm.core.course import Course
from clm.core.data_source import DataSource
from clm.core.data_source_paths import full_target_path_for_data_source
from clm.core.data_sink import DataSink
from clm.core.output_spec import OutputSpec
from clm.data_sinks.notebook_sink import NotebookDataSink
from clm.utils.jupyter_utils import (
    find_notebook_titles,
)
from clm.utils.path_utils import base_path_for_csv_file


@define(init=False)
class NotebookDataSource(DataSource):
    notebook_text_before_expansion: str = field(default="", repr=False)

    def __init__(
        self,
        source_file: Path | str,
        target_dir_fragment: str,
        prog_lang: str,
        file_num: int,
    ):
        super().__init__(
            source_file=source_file,
            target_dir_fragment=target_dir_fragment,
            prog_lang=prog_lang,
            file_num=file_num,
        )
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
        logging.debug(f"NotebookDataSource after expansion:\n{expanded_nb}")
        return expanded_nb

    def _load_jinja_template(self, course, output_spec):
        jinja_env = self._create_jinja_environment(course)
        output_path = full_target_path_for_data_source(
            self, course, output_spec
        ).relative_to(course.target_dir)
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

    def process(self, course: "Course", output_spec: OutputSpec) -> DataSink:
        logging.info(f"Processing notebook {self.source_file}.")
        output = NotebookDataSink(
            self, full_target_path_for_data_source(self, course, output_spec)
        )
        expanded_nb = self.load_and_expand_jinja_template(course, output_spec)
        output.process(self, expanded_nb, output_spec)
        return output

    def get_target_name(self, course: "Course", output_spec: OutputSpec) -> str:
        out_name = self.source_file.name
        if raw_text := self.notebook_text_before_expansion:
            out_names = find_notebook_titles(raw_text, out_name)
            out_name = out_names[output_spec.lang]
        assert out_name

        path = self.source_file.with_name(f"{self.file_num :0>2} {out_name}")
        return path.with_suffix(f".{output_spec.file_suffix}").name
