import logging

from attrs import frozen

from clx.operations.convert_file import ConvertFileOperation

logger = logging.getLogger(__name__)


@frozen
class ConvertPlantUmlFileOperation(ConvertFileOperation):
    async def exec(self, *_args, **_kwargs) -> None:
        logger.info(
            f"Converting PlantUML file {self.input_file.relative_path} "
            f"to {self.output_file}"
        )
        # TODO: Do something here...
        self.input_file.generated_outputs.add(self.output_file)
