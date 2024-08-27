from clx_common.messaging.base_classes import Payload


class DrawioPayload(Payload):
    data: str
    output_file: str
    output_format: str = "png"
