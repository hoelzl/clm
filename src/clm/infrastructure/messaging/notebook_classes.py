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
    # If True, cell execution errors do not abort HTML generation.
    # Cells whose outputs contain an error are cleared, and a
    # ProcessingWarning is emitted so the author sees which cells were
    # affected. Opt-in via the ``skip-errors`` attribute on a topic.
    skip_errors: bool = False
    # HTTP replay mode ("replay"/"once"/"refresh"/"disabled") or None.
    # Only set when the topic opted in via ``http-replay="yes"`` AND a
    # build-level mode was resolved. Consumed by the notebook worker to
    # activate a ``vcrpy`` cassette before kernel execution.
    http_replay_mode: str | None = None
    # Relative path (from kernel cwd) of the cassette file when
    # ``http_replay_mode`` is set. In direct mode the cassette is written
    # to this path inside the temp dir via ``other_files``; in Docker mode
    # it is already present at this path under the source mount.
    http_replay_cassette_name: str | None = None
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
    # Author name for notebook header templates
    author: str = "Dr. Matthias Hölzl"
    # Organization name (already resolved for target language)
    organization: str = ""

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

        When ``http_replay_mode`` is active, the cassette contents are
        folded into the hash so that refreshing the cassette invalidates
        the cached executed notebook for that topic.
        """
        # Use prog_lang:language:data (without kind or format)
        # Format is excluded because we only cache HTML execution results
        hash_data = f"{self.prog_lang}:{self.language}:{self.data}".encode()
        if (
            self.http_replay_mode
            and self.http_replay_mode != "disabled"
            and self.http_replay_cassette_name
        ):
            # Cassette bytes are already present in ``other_files``
            # (base64-encoded) when the topic opted in; an empty default
            # keeps the hash stable if the cassette is missing at build
            # time (e.g. first ``once`` run before recording).
            cassette_bytes = self.other_files.get(self.http_replay_cassette_name, b"")
            hash_data += b":cassette:" + cassette_bytes
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
