from clx_common.messaging.base_classes import ImagePayload


class PlantUmlPayload(ImagePayload):
    output_file_name: str
