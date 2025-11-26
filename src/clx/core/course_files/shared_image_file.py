"""SharedImageFile - Image file that copies once to shared course-level img/ folder.

This module provides the SharedImageFile class which handles image files that are
referenced by notebooks. Instead of copying images to each output variant folder,
SharedImageFile copies each image once to a shared img/ folder at the course level,
significantly reducing storage duplication.

Output structure:
    output/public/De/Mein-Kurs/img/image.png  (German public)
    output/public/En/My-Course/img/image.png  (English public)
    output/speaker/De/Mein-Kurs/img/image.png (German speaker)
    output/speaker/En/My-Course/img/image.png (English speaker)
"""

from pathlib import Path

from attrs import define, field

from clx.core.course_file import CourseFile
from clx.core.utils.execution_utils import (
    COPY_GENERATED_IMAGES_STAGE,
    FIRST_EXECUTION_STAGE,
)
from clx.infrastructure.operation import Concurrently, NoOperation, Operation


@define
class SharedImageFile(CourseFile):
    """Image file that is copied once to course-level shared img/ folder.

    Unlike DataFile which copies to each output variant folder (HTML/Notebooks/Code ×
    Code-Along/Completed/Speaker × De/En), SharedImageFile copies to a single shared
    img/ folder per language and audience (public/speaker).

    This reduces duplication from up to 18 copies per image to just 4 copies
    (2 languages × 2 audiences).

    For generated images (e.g., PNGs from DrawIO/PlantUML conversions), the execution
    stage is set to COPY_GENERATED_IMAGES_STAGE to ensure the copy runs after the
    conversion completes. For pre-existing images, FIRST_EXECUTION_STAGE is used.
    """

    # Track whether source file existed when this object was created
    # This determines which execution stage to use
    _source_exists_at_load: bool = field(default=True, init=False)

    @classmethod
    def _from_path(cls, course, file: Path, topic) -> "SharedImageFile":
        """Create SharedImageFile, recording whether source exists at load time."""
        instance = cls(course=course, path=file, topic=topic)
        # Check if source file exists - if not, it's a generated file
        object.__setattr__(instance, "_source_exists_at_load", file.exists())
        return instance

    @property
    def execution_stage(self) -> int:
        """Determine execution stage based on whether source exists.

        Pre-existing images run in FIRST_EXECUTION_STAGE so they're available early.
        Generated images (from DrawIO/PlantUML) run in COPY_GENERATED_IMAGES_STAGE
        which is after conversions complete.
        """
        if self._source_exists_at_load:
            return FIRST_EXECUTION_STAGE
        else:
            return COPY_GENERATED_IMAGES_STAGE

    async def get_processing_operation(
        self, target_dir: Path, stage: int | None = None
    ) -> Operation:
        """Create copy operations for shared img/ folders.

        Creates one copy operation for each language (de, en) and audience
        (public, speaker) combination.

        Args:
            target_dir: Root output directory
            stage: If specified, only return operations for this stage

        Returns:
            Concurrently operation containing copy operations, or NoOperation
            if this isn't the right stage.
        """
        from clx.core.operations.copy_file import CopyFileOperation
        from clx.infrastructure.utils.path_utils import output_path_for

        # Only run in our designated stage
        if stage is not None and stage != self.execution_stage:
            return NoOperation()

        # Get languages from course configuration, default to both
        languages = self.course.output_languages or ["de", "en"]

        # Determine audiences based on course output configuration
        # If output_kinds is set to only "speaker", only generate speaker outputs
        output_kinds = self.course.output_kinds
        if output_kinds and output_kinds == ["speaker"]:
            is_speaker_options = [True]
        else:
            # Generate both public and speaker outputs
            is_speaker_options = [False, True]

        ops = []
        for lang in languages:
            for is_speaker in is_speaker_options:
                # Get the course directory for this language/audience
                course_dir = output_path_for(target_dir, is_speaker, lang, self.course.name)
                # Output path is course_dir/img/filename
                output_path = course_dir / "img" / self.path.name

                ops.append(
                    CopyFileOperation(
                        input_file=self,
                        output_file=output_path,
                    )
                )

        return Concurrently(ops)
