import hashlib
from typing import Literal

from clm.infrastructure.messaging.base_classes import Payload, ProcessingError, Result


def notebook_metadata(kind, prog_lang, language, output_format) -> str:
    return f"{kind}:{prog_lang}:{language}:{output_format}"


def notebook_metadata_tags(kind, prog_lang, language, output_format) -> tuple[str, str, str, str]:
    return kind, prog_lang, language, output_format


class NotebookPayload(Payload):
    # Base Payload fields are inherited: input_file, input_file_name, output_file, data, correlation_id
    kind: str
    prog_lang: str
    language: str
    format: str
    template_dir: str = ""
    other_files: dict[str, bytes] = {}
    fallback_execute: bool = False
    # Relative path from output file to shared img/ folder (e.g., "../../../../img/")
    img_path_prefix: str = "img/"
    # Path to topic directory relative to data_dir (for Docker mode with source mount).
    # When set, workers can read supporting files directly from /source/{source_topic_dir}/
    # instead of from the other_files payload field.
    source_topic_dir: str = ""
    # Image stems (without extension) that have SVG equivalents from DrawIO/PlantUML
    # Used for selective .png -> .svg URL rewriting when image_format is "svg"
    svg_available_stems: list[str] = []
    # Whether to inline images as data URLs in notebook markdown cells
    inline_images: bool = False

    # The backend relies on having a data property
    @property
    def notebook_text(self) -> str:
        return self.data

    def content_hash(self) -> str:
        hash_data = f"{self.output_metadata()}:{self.data}".encode()
        return hashlib.sha256(hash_data).hexdigest()

    def execution_cache_hash(self) -> str:
        """Compute a kind-agnostic hash for execution caching.

        This hash excludes 'kind' (speaker/completed/code_along) because
        Speaker and Completed HTML share the same executed notebook.
        Completed HTML is just Speaker HTML with "notes" cells filtered out.
        """
        # Use prog_lang:language:data (without kind or format)
        # Format is excluded because we only cache HTML execution results
        hash_data = f"{self.prog_lang}:{self.language}:{self.data}".encode()
        return hashlib.sha256(hash_data).hexdigest()

    def output_metadata(self) -> str:
        return notebook_metadata(self.kind, self.prog_lang, self.language, self.format)


class NotebookResult(Result):
    result_type: Literal["result"] = "result"
    result: str
    output_metadata_tags: tuple[str, str, str, str]

    def result_bytes(self) -> bytes:
        return self.result.encode("utf-8")

    def output_metadata(self) -> str:
        return ":".join(self.output_metadata_tags)


NotebookResultOrError = NotebookResult | ProcessingError
