from clx_common.base_classes import Payload


class DrawioPayload(Payload):
    data: str
    output_format: str = "png"
