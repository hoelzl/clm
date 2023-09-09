from typing import Callable

import jinja2
from attr import field, frozen

from clm.core.course_spec import CourseSpec
from clm.core.data_source import DataSource
from clm.core.notifier import Notifier
from clm.core.output_spec import OutputSpec
from clm.utils.executor import genjobs
from clm.utils.location import Location, FileSystemLocation


@frozen
class Course:
    """A course comprises all data that should be processed or referenced."""

    source_loc: Location
    target_loc: Location
    template_loc: Location = field()
    lang: str = "en"
    prog_lang: str = "python"
    data_sources: list[DataSource] = field(factory=list)

    # noinspection PyUnresolvedReferences
    @template_loc.default
    def _template_dir_default(self):
        return self.source_loc / "templates"

    @staticmethod
    def from_spec(
        course_spec: CourseSpec,
    ):
        source_loc = course_spec.source_loc
        target_loc = course_spec.target_loc
        template_loc = course_spec.template_loc
        lang = course_spec.lang
        prog_lang = course_spec.prog_lang
        data_sources = course_spec.data_sources
        return Course(
            source_loc=course_spec.source_loc,
            target_loc=course_spec.target_loc,
            template_loc=course_spec.template_loc,
            lang=course_spec.lang,
            prog_lang=course_spec.prog_lang,
            data_sources=course_spec.data_sources,
        )

    def _process_one_data_source(
        self, src: DataSource, output_spec: OutputSpec, notifier: Notifier
    ):
        try:
            output = src.process(self, output_spec)
            notifier.processed_data_source()
            output.write_to_target(self, output_spec)
            notifier.wrote_to_target()
        except jinja2.TemplateNotFound as err:
            print(f"ERROR: no such template: {err} ({err.message})")
        except Exception as err:
            print(f"ERROR: {err}")

    @genjobs
    def process_for_output_spec(self, output_spec: OutputSpec, notifier: Notifier):
        for doc in self.data_sources:
            yield self._process_one_data_source, doc, output_spec, notifier
