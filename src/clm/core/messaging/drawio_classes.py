from clm.core.messaging.base_classes import ImagePayload


class DrawioPayload(ImagePayload):
    output_file_name: str
