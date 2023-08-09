# %%
from dataclasses import dataclass, field
from pathlib import Path

from clm.core.course_spec import CourseSpec
from clm.core.document import Document
from clm.core.output_spec import OutputSpec
from clm.utils.executor import genjobs


# %%
@dataclass
class Course:
    """A course comprises all data that should be processed or referenced."""

    source_dir: Path
    target_dir: Path
    template_dir: Path = None
    prog_lang: str = "python"
    documents: list[Document] = field(default_factory=list)

    # noinspection PyTypeChecker
    def __post_init__(self):
        if self.template_dir is None:
            self.template_dir = self.source_dir / "templates"
        if not self.target_dir.is_absolute():
            raise ValueError(
                "Target directory for a course must be absolute."
            )  # TODO: should we force other paths to be absolute as well?

    @staticmethod
    def from_spec(course_spec: CourseSpec):
        source_dir = Path(course_spec.base_dir)
        target_dir = Path(course_spec.target_dir)
        template_dir = Path(course_spec.template_dir)
        prog_lang = course_spec.prog_lang
        documents = course_spec.documents
        return Course(
            source_dir=source_dir,
            target_dir=target_dir,
            template_dir=template_dir,
            prog_lang=prog_lang,
            documents=documents,
        )

    def _process_doc(self, doc: Document, output_spec: OutputSpec):
        try:
            doc.process(self, output_spec)
            print("p", end="", flush=True)
        except Exception as err:
            print(f"ERROR: {err}")
        doc.copy_to_target(self, output_spec)
        print("c", end="", flush=True)

    @genjobs
    def process_for_output_spec(self, output_spec: OutputSpec):
        for doc in self.documents:
            yield (self._process_doc, doc, output_spec)
