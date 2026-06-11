import hashlib
from collections.abc import Mapping
from typing import Any, Literal

from clm.infrastructure.messaging.base_classes import Payload, ProcessingError, Result

# Version tag folded into every notebook cache hash. Bump whenever the
# *composition* of ``content_hash()`` / ``execution_cache_hash()`` changes so
# that stale cache entries produced under the old key schema can never be
# replayed as hits under the new one. Bumping invalidates all existing
# notebook caches (one full re-execution on the next build) — deliberate and
# visible, instead of an accidental partial overlap between key schemas.
CACHE_HASH_SCHEMA_VERSION = 2


def notebook_metadata(kind, prog_lang, language, output_format) -> str:
    return f"{kind}:{prog_lang}:{language}:{output_format}"


def notebook_metadata_tags(kind, prog_lang, language, output_format) -> tuple[str, str, str, str]:
    return kind, prog_lang, language, output_format


def notebook_metadata_tags_from_payload(
    payload_data: Mapping[str, Any],
) -> tuple[str, str, str, str]:
    """Extract the ``(kind, prog_lang, language, format)`` tuple from a
    serialized notebook payload dict, using one canonical set of fallbacks.

    Several host- and worker-side call sites previously duplicated this
    extraction with *divergent* defaults (``kind`` fell back to ``"participant"``
    in the result/metadata path but ``"completed"`` in the worker), an
    avoidable source of drift. The fallbacks are unreachable for real jobs —
    the host always sets these required fields — but keep result and cache
    bookkeeping robust against a malformed payload.
    """
    return notebook_metadata_tags(
        payload_data.get("kind", "participant"),
        payload_data.get("prog_lang", "python"),
        payload_data.get("language", "en"),
        payload_data.get("format", "notebook"),
    )


class NotebookPayload(Payload):
    # Base Payload fields are inherited: input_file, input_file_name, output_file, data, correlation_id
    kind: str
    prog_lang: str
    language: str
    format: str
    # Digest of the bundled Jinja template directory for this payload's
    # prog_lang plus the CLM version, computed HOST-side at payload
    # construction (``compute_template_fingerprint``). Templates are
    # resolved worker-side from the installed clm package, so without this
    # field a ``macros.j2`` change (shipped with a new clm version) would
    # invalidate nothing and every deck would replay stale title slides
    # from a warm cache (issue #321 class 4). Covers HOST-side changes
    # only; worker-image divergence is covered by ``worker_image_identity``
    # below. Folded into both cache hashes.
    template_fingerprint: str = ""
    # Identity of the execution environment this job is configured to run
    # in: "direct", or "docker:<image reference>", computed HOST-side
    # (``compute_worker_image_identity``). A Docker image carries its own
    # clm version, templates, and kernel, so a cache populated under one
    # image must not be replayed under another (issue #321 class 5) — and
    # the template fingerprint above cannot see a worker-image change when
    # the host is unchanged. Image reference, not content digest: mutable
    # tags (":latest") weaken this; pin versioned tags/digests for exact
    # invalidation. Folded into both cache hashes.
    worker_image_identity: str = ""
    other_files: dict[str, bytes] = {}
    fallback_execute: bool = False
    # If True, the notebook is rendered to all configured output formats
    # without spawning a kernel. Cells appear with empty outputs. Opt in
    # via the ``evaluate="no"`` attribute on a topic. Mutually
    # independent of ``skip_errors`` and ``skip_html``.
    skip_evaluation: bool = False
    # If True, cell execution errors do not abort HTML generation.
    # Cells whose outputs contain an error are cleared, and a
    # ProcessingWarning is emitted so the author sees which cells were
    # affected. Opt-in via the ``skip-errors`` attribute on a topic.
    skip_errors: bool = False
    # HTTP replay mode ("replay"/"once"/"new-episodes"/"refresh"/"disabled") or None.
    # Only set when the topic opted in via ``http-replay="yes"`` AND a
    # build-level mode was resolved. Consumed by the notebook worker to
    # activate a ``vcrpy`` cassette before kernel execution.
    http_replay_mode: str | None = None
    # Relative path (from kernel cwd) of the cassette file when
    # ``http_replay_mode`` is set. In direct mode the cassette is written
    # to this path inside the temp dir via ``other_files``; in Docker mode
    # it is already present at this path under the source mount.
    http_replay_cassette_name: str | None = None
    # Absolute path of the per-invocation HTTP-replay trace directory
    # when the forensic trace harness is active (``CLM_HTTP_REPLAY_TRACE=1``
    # on the host). Empty string means tracing is off — the bootstrap
    # template installs neither the socket audit hook nor the vcr
    # wrappers. Design: ``docs/claude/design/http-replay-trace.md``.
    http_replay_trace_dir: str = ""
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
    # Cross-references (Issue #17): maps a raw ``clm:`` reference string
    # (the part after ``clm:``, exactly as authored) to its already-resolved
    # relative href for THIS (language, kind, format) artifact. An empty
    # string value means "drop the link, keep the text" (the ``code`` format
    # rule and the warn-and-drop missing-target policy). A reference absent
    # from this map is left verbatim. Empty when the notebook has no
    # cross-references. Computed in ProcessNotebookOperation.payload() where
    # the full Course is in scope; the worker only does a mechanical string
    # rewrite via ``rewrite_cross_references`` and needs no knowledge of
    # other notebooks' output names.
    cross_references: dict[str, str] = {}

    # The backend relies on having a data property
    @property
    def notebook_text(self) -> str:
        return self.data

    def _dependency_digest(self) -> str:
        """Digest of every dependency this payload carries besides ``data``.

        Folds in ``other_files`` (the complete byte content of every
        non-image topic sibling — C++ headers a deck ``#include``s, files
        pulled in via Jinja ``{% include %}``, runtime data files), the
        template fingerprint, the worker image identity, and the
        ``skip_evaluation`` / ``skip_errors`` execution flags. Editing any
        topic sibling re-executes the deck:
        that over-invalidates slightly (a sibling the deck never reads also
        triggers re-execution), but over-invalidation is safe and cheap
        relative to silently shipping stale teaching material (issue #321).

        The HTTP-replay cassette entry (``other_files[http_replay_cassette_name]``)
        is intentionally EXCLUDED. Folding cassette bytes into the key was
        tried earlier and produced an unfixable cache-miss loop:
        ``compute_other_files`` reads the cassette at payload construction
        (before the kernel runs), while record-capable modes
        (``once``/``new-episodes``/``refresh``) write the cassette after the
        kernel runs. So the next build's lookup hash uses the post-execution
        cassette and never matches the prior build's stored hash. The same
        issue surfaces the first time a cassette is created (missing →
        populated) and whenever ``.gitattributes`` normalizes CRLF↔LF
        between builds. Users who want to invalidate cached execution after
        a manual cassette edit should use ``--ignore-cache``. Do NOT "fix"
        this exclusion when extending the digest.
        """
        hasher = hashlib.sha256()
        hasher.update(
            f"{self.template_fingerprint}:{self.worker_image_identity}:"
            f"{self.skip_evaluation}:{self.skip_errors}".encode()
        )
        cassette_key = self.http_replay_cassette_name
        for name in sorted(self.other_files):
            if cassette_key is not None and name == cassette_key:
                continue
            content = self.other_files[name]
            # Length-prefix name and content so (name, content) boundaries
            # are unambiguous regardless of the bytes they contain.
            hasher.update(f"\n{len(name)}:{name}:{len(content)}:".encode())
            hasher.update(content)
        return hasher.hexdigest()

    def content_hash(self) -> str:
        hash_data = (
            f"{CACHE_HASH_SCHEMA_VERSION}:{self.output_metadata()}:"
            f"{self._dependency_digest()}:{self.data}"
        ).encode()
        return hashlib.sha256(hash_data).hexdigest()

    def execution_cache_hash(self) -> str:
        """Compute a kind-agnostic hash for execution caching.

        This hash excludes 'kind' (speaker/completed/code_along) because
        Speaker and Completed HTML share the same executed notebook.
        Completed HTML is just Speaker HTML with "notes" cells filtered out.
        Format is excluded because we only cache HTML execution results.

        Besides the notebook text itself, the key covers the full dependency
        set via :meth:`_dependency_digest` — sibling files, template
        fingerprint, and execution flags — so editing a ``#include``d header
        or upgrading clm's bundled templates re-executes the deck instead of
        silently replaying stale outputs (issue #321). The cassette entry is
        deliberately excluded from that digest; see ``_dependency_digest``
        for the rationale.
        """
        hash_data = (
            f"{CACHE_HASH_SCHEMA_VERSION}:{self.prog_lang}:{self.language}:"
            f"{self._dependency_digest()}:{self.data}"
        ).encode()
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
