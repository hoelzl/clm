import hashlib
from typing import Literal

from clx.infrastructure.messaging.base_classes import Payload, ProcessingError, Result


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
