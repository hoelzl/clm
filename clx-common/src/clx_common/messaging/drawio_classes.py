from clx_common.messaging.base_classes import Payload


class DrawioPayload(Payload):
    output_format: str = "png"
    output_file_name: str
