import logging

from attrs import frozen

from clm.core.messaging.correlation_ids import new_correlation_id, note_correlation_id_dependency
from clm.core.messaging.plantuml_classes import PlantUmlPayload
from clm.core.operations.convert_source_output_file import ConvertSourceOutputFileOperation

logger = logging.getLogger(__name__)


@frozen
class ConvertPlantUmlFileOperation(ConvertSourceOutputFileOperation):
    def object_type(self) -> str:
        return "PlantUML file"

    @property
    def service_name(self) -> str:
        return "plantuml-converter"

    async def payload(self) -> PlantUmlPayload:
        data = self.input_file.source_path.read_text(encoding="utf-8")
        correlation_id = await new_correlation_id()
        from clm.infrastructure.workers.image_identity import (
            effective_worker_image_identity,
        )

        payload = PlantUmlPayload(
            data=data,
            correlation_id=correlation_id,
            worker_image_identity=effective_worker_image_identity("plantuml"),
            input_file=str(self.input_file.path),
            input_file_name=self.input_file.path.name,
            output_file=str(self.output_file),
            output_file_name=self.output_file.name,
        )
        await note_correlation_id_dependency(correlation_id, payload)
        return payload
