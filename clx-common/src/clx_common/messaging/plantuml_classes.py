from clx_common.messaging.base_classes import Payload


class PlantUmlPayload(Payload):
    output_format: str = "png"
