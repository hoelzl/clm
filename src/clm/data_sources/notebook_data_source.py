import logging
import re

from attr import field, define
from jinja2 import Environment, StrictUndefined, Template

from clm.core.course import Course
from clm.core.data_sink import DataSink
from clm.core.data_source import DataSource, DATA_SOURCE_TYPES
from clm.core.data_source_location import full_target_location_for_data_source
from clm.core.dependency import find_dependencies
from clm.core.output_spec import OutputSpec
from clm.data_sinks.notebook_sink import NotebookDataSink
from clm.utils.jinja_utils import get_jinja_loader
from clm.utils.jupyter_utils import (
    find_notebook_titles,
)
from clm.utils.location import Location


MARKDOWN_IMG_REGEX = re.compile(r"!\[(?P<alt_text>.*?)]\((?P<image_path>.*?)\)")
HTML_IMG_REGEX = re.compile(r'<img\s+src="(?P<image_path>.*?)"')


@define(init=False)
class NotebookDataSource(DataSource):
    notebook_text_before_expansion: str = field(default="", repr=False)

    def __init__(
        self,
        source_loc: Location,
        target_dir_fragment: str,
        prog_lang: str,
        file_num: int,
    ):
        super().__init__(
            source_loc=source_loc,
            target_dir_fragment=target_dir_fragment,
            prog_lang=prog_lang,
            file_num=file_num,
        )
        try:
            with self.source_loc.open(encoding="utf-8") as file:
                self.notebook_text_before_expansion = file.read()
        except FileNotFoundError:
            logging.error(f"Cannot create notebook: no file '{source_loc}'.")
            raise

    @property
    def dependencies(self) -> list[tuple[Location, Location], ...]:
        result = []
        self._append_img_dependencies(MARKDOWN_IMG_REGEX, result)
        self._append_img_dependencies(HTML_IMG_REGEX, result)
        return result

    def _append_img_dependencies(self, matcher, result):
        loc = self.source_loc
        for match in matcher.finditer(self.notebook_text_before_expansion):
            image_path = match.group("image_path")
            result.append((loc.parent / image_path, loc))

    def process(self, course: "Course", output_spec: OutputSpec) -> DataSink:
        logging.info(f"Processing notebook {self.source_loc}.")
        output = NotebookDataSink(
            course=course,
            output_spec=output_spec,
            data_source=self,
        )
        expanded_nb = self.load_and_expand_jinja_template(course, output_spec)
        output.process(self, expanded_nb, output_spec)
        return output

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
        output_location = full_target_location_for_data_source(
            self, course, output_spec
        )
        nb_template: Template = jinja_env.from_string(
            self.notebook_text_before_expansion,
            globals=self._create_jinja_globals(
                self.source_loc.relative_path,
                output_location,
                output_spec,
            ),
        )
        return nb_template, jinja_env

    def _create_jinja_environment(self, course: "Course"):
        template_path = course.template_loc
        self._assert_template_dir_exists(template_path)
        loader = get_jinja_loader([self.source_loc.parent, template_path])
        jinja_env = Environment(
            loader=loader,
            autoescape=False,
            undefined=StrictUndefined,
            # FIXME: This should be configured by the language config
            line_statement_prefix="// j2" if self.prog_lang == "cpp" else "# j2",
            keep_trailing_newline=True,
        )
        return jinja_env

    @staticmethod
    def _create_jinja_globals(source_file, output_loc, output_spec):
        return {
            "source_name": source_file.as_posix(),
            "name": output_loc.relative_path.as_posix(),
            "is_notebook": output_spec.file_suffix == "ipynb",
            "lang": output_spec.lang,
        }

    @staticmethod
    def _assert_template_dir_exists(template_loc: Location):
        if not template_loc.exists():
            raise ValueError(f"Template directory {template_loc} does not exist.")

    def get_target_name(self, course: "Course", output_spec: OutputSpec) -> str:
        out_name = self.source_loc.name
        if raw_text := self.notebook_text_before_expansion:
            out_names = find_notebook_titles(raw_text, out_name)
            out_name = out_names[output_spec.lang]
        assert out_name

        path = self.source_loc.with_name(f"{self.file_num :0>2} {out_name}")
        return path.with_suffix(f".{output_spec.file_suffix}").name


DATA_SOURCE_TYPES["Notebook"] = NotebookDataSource
