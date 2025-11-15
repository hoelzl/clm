from clx.infrastructure.messaging.base_classes import ImagePayload


class PlantUmlPayload(ImagePayload):
    output_file_name: str
