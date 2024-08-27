from clx_common.messaging.base_classes import Payload


class PlantUmlPayload(Payload):
    data: str
    output_format: str = "png"
