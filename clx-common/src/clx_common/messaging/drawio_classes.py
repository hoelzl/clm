from clx_common.messaging.base_classes import ImagePayload


class DrawioPayload(ImagePayload):
    output_file_name: str
