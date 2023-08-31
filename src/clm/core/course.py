from attr import field, frozen
from pathlib import Path

from clm.core.course_spec import CourseSpec
from clm.core.data_source import DataSource
from clm.core.notifier import Notifier
from clm.core.output_spec import OutputSpec
from clm.utils.executor import genjobs


@frozen
class Course:
    """A course comprises all data that should be processed or referenced."""

    source_dir: Path
    target_dir: Path = field(validator=lambda _self, _attr, val: val.is_absolute())
    template_dir: Path = field()
    prog_lang: str = "python"
    data_sources: list[DataSource] = field(factory=list)

    @template_dir.default
    def _template_dir_default(self):
        return self.source_dir / "templates"

    @staticmethod
    def from_spec(course_spec: CourseSpec):
        source_dir = Path(course_spec.base_dir)
        target_dir = Path(course_spec.target_dir)
        template_dir = Path(course_spec.template_dir)
        prog_lang = course_spec.prog_lang
        data_sources = course_spec.data_sources
        return Course(
            source_dir=source_dir,
            target_dir=target_dir,
            template_dir=template_dir,
            prog_lang=prog_lang,
            data_sources=data_sources,
        )

    def _process_data_source(self, src: DataSource, output_spec: OutputSpec, notifier: Notifier):
        try:
            output = src.process(self, output_spec)
            notifier.processed_data_source()
            output.write_to_target(self, output_spec)
            notifier.wrote_to_target()
        except Exception as err:
            print(f"ERROR: {err}")

    @genjobs
    def process_for_output_spec(self, output_spec: OutputSpec, notifier: Notifier):
        for doc in self.data_sources:
            yield self._process_data_source, doc, output_spec, notifier
