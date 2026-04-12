"""HTTP client for the Auphonic Complex JSON API.

A thin, typed wrapper around `<https://auphonic.com/help/api/>`_ that
exposes only the subset of endpoints :class:`AuphonicBackend` needs to
drive a production from creation to download. The client is deliberately
stateless: callers supply the API key and base URL once, then call
methods that return Pydantic domain objects.

Design notes:

* ``httpx`` is imported **lazily inside methods** so that environments
  without the ``[recordings]`` extra can still import
  ``clm.recordings.workflow.backends`` without ``ModuleNotFoundError``.
  (``httpx`` is actually already a core CLM dependency, but we keep the
  pattern to match the rest of the package and stay robust to future
  dependency trimming.)
* Uploads are **streamed** via a chunked reader so multi-gigabyte lecture
  recordings don't have to fit in memory. Progress is reported via an
  optional ``on_progress`` callback so the dashboard can show an upload
  bar.
* Redirects are followed on the download endpoint because Auphonic
  serves output files from a signed-URL CDN.
* Errors raise :class:`AuphonicHTTPError` with the offending URL and the
  server response so the caller can surface actionable messages on the
  dashboard.

See ``docs/claude/design/recordings-backend-architecture.md`` §3 (API
background) and §6.8 (backend usage).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:  # pragma: no cover — only for type checking
    import httpx

#: Default base URL for the Auphonic API.
DEFAULT_BASE_URL = "https://auphonic.com"

#: Default streaming upload chunk size (8 MiB).
DEFAULT_UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024

#: Default HTTP timeout for non-upload requests, in seconds.
DEFAULT_REQUEST_TIMEOUT = 30.0

#: Upload requests get a generous timeout since multi-GB uploads take
#: many minutes even on a fast connection.
DEFAULT_UPLOAD_TIMEOUT = 60 * 60.0  # 1 hour

#: Download timeout per chunk (the server may pause-and-resume the CDN).
DEFAULT_DOWNLOAD_TIMEOUT = 60 * 60.0


ProgressCallback = Callable[[float], None]
"""Signature ``on_progress(fraction) -> None`` with ``fraction`` in [0, 1]."""


# ----------------------------------------------------------------------
# Domain models
# ----------------------------------------------------------------------


class AuphonicStatus:
    """Production status codes, as returned by the Auphonic API.

    These match the values documented at
    ``/api/info/production_status.json``. Kept as a plain class rather
    than an :class:`~enum.Enum` because Auphonic may return
    undocumented codes in transitional states and we don't want Pydantic
    to reject them on parse.
    """

    FILE_UPLOAD = 0
    WAITING = 1
    ERROR = 2
    DONE = 3
    INCOMPLETE_FORM = 4
    PRODUCTION_NOT_STARTED = 5
    PRODUCTION_OUTDATED = 6
    INCOMPLETE = 7
    AUDIO_PROCESSING = 9
    AUDIO_ENCODING = 10
    SPEECH_RECOGNITION = 12
    OUTGOING_FILE_TRANSFER = 13

    #: Terminal error state.
    TERMINAL_ERROR = ERROR
    #: Terminal success state.
    TERMINAL_DONE = DONE


class AuphonicOutputFile(BaseModel):
    """One entry from a production's ``output_files`` array.

    The Auphonic API returns a variety of fields per output; we surface
    only the ones the backend uses (download URL + format + optional
    ending). Unknown fields are ignored via ``extra="ignore"`` so the
    client is robust to Auphonic adding fields in the future.

    Note on null handling: Auphonic frequently returns ``null`` for
    string fields that are "not applicable yet" — e.g. ``download_url``
    is ``null`` until a production has finished rendering its outputs.
    The ``_none_to_empty`` validator coerces those to empty strings so
    downstream truthy checks (``if not out.download_url``) keep working.
    """

    model_config = {"extra": "ignore"}

    format: str = ""
    """High-level format: ``"video"``, ``"cut-list"``, ``"mp3"``, …"""

    ending: str = ""
    """File extension/type hint (e.g. ``"mp4"``, ``"DaVinciResolve.edl"``)."""

    download_url: str = ""
    """Absolute URL for downloading this output; requires bearer auth.
    Empty string until the production has finished rendering this output."""

    filename: str = ""
    """Filename Auphonic suggests for this output."""

    size: int | None = None
    """File size in bytes, if reported by the API."""

    @field_validator("format", "ending", "download_url", "filename", mode="before")
    @classmethod
    def _none_to_empty(cls, value: Any) -> Any:
        """Coerce ``None`` to ``""`` so unset Auphonic fields validate."""
        return "" if value is None else value


class AuphonicProduction(BaseModel):
    """Subset of a production object returned by the Auphonic API.

    We only parse what :class:`AuphonicBackend` cares about. Additional
    fields present in the API response (e.g. ``chapter_positions``) are
    ignored so backend code never has to track the full upstream schema.

    Note on null handling: Auphonic returns ``null`` for several string
    fields when they are "not applicable yet" — e.g. ``error_status`` is
    ``null`` on a freshly created production that has never errored. The
    ``_none_to_empty`` validator coerces those to empty strings so the
    model validates successfully on every production lifecycle state.

    Note on ``used_credits``: the current Auphonic API returns a nested
    dict ``{"recurring": …, "onetime": …, "combined": …}`` (credit-source
    breakdown). Older API versions returned a plain float. The field
    accepts both shapes; use :attr:`used_credits_combined` for a single
    number regardless of response version.
    """

    model_config = {"extra": "ignore"}

    uuid: str
    status: int = AuphonicStatus.INCOMPLETE_FORM
    status_string: str = ""
    error_message: str = ""
    error_status: str = ""
    output_files: list[AuphonicOutputFile] = Field(default_factory=list)
    length: float | None = None
    used_credits: dict[str, Any] | float | None = None
    warning_message: str = ""

    @field_validator(
        "status_string",
        "error_message",
        "error_status",
        "warning_message",
        mode="before",
    )
    @classmethod
    def _none_to_empty(cls, value: Any) -> Any:
        """Coerce ``None`` to ``""`` so unset Auphonic fields validate."""
        return "" if value is None else value

    @property
    def used_credits_combined(self) -> float | None:
        """Return the total credits used for this production, if known.

        Handles both response shapes: a plain float (older API) and a
        ``{"recurring": …, "onetime": …, "combined": …}`` dict (current
        API). Returns ``None`` when the field is absent.
        """
        credits = self.used_credits
        if credits is None:
            return None
        if isinstance(credits, dict):
            value = credits.get("combined")
            return float(value) if value is not None else None
        return float(credits)


class AuphonicPreset(BaseModel):
    """Subset of a preset object returned by ``/api/presets.json``."""

    model_config = {"extra": "ignore"}

    uuid: str
    preset_name: str = ""
    short_name: str = ""


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------


class AuphonicError(Exception):
    """Base class for all Auphonic client errors."""


class AuphonicHTTPError(AuphonicError):
    """Raised when the Auphonic API returns a non-success status code.

    Includes the offending URL, the status code, and the response body
    (truncated) so callers can present actionable error messages in the
    dashboard without needing to re-inspect the low-level HTTP exchange.
    """

    def __init__(self, method: str, url: str, status_code: int, body: str) -> None:
        truncated = body if len(body) <= 500 else body[:497] + "…"
        super().__init__(f"Auphonic {method} {url} returned {status_code}: {truncated}")
        self.method = method
        self.url = url
        self.status_code = status_code
        self.body = body


# ----------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------


class AuphonicClient:
    """httpx-based wrapper around the Auphonic Complex JSON API.

    All methods are synchronous; the :class:`AuphonicBackend` runs on
    the :class:`JobManager`'s poller thread, so async doesn't buy us
    anything and keeps the test shape simple.

    Args:
        api_key: Auphonic API key (``Authorization: Bearer …``).
        base_url: API base URL. Defaults to ``https://auphonic.com``;
            override for staging or tests.
        timeout: Default HTTP timeout for non-upload requests.
        upload_timeout: Timeout for the upload endpoint (generous by
            default; multi-GB uploads take many minutes).
        download_timeout: Timeout for downloads.
        chunk_size: Streaming chunk size for upload/download.
        transport: Optional ``httpx`` transport override for testing
            (e.g. a ``respx`` mock transport). When provided, the client
            uses it instead of creating a fresh ``httpx.Client`` per call.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        upload_timeout: float = DEFAULT_UPLOAD_TIMEOUT,
        download_timeout: float = DEFAULT_DOWNLOAD_TIMEOUT,
        chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._upload_timeout = upload_timeout
        self._download_timeout = download_timeout
        self._chunk_size = chunk_size
        self._transport = transport

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _client(self, *, timeout: float, follow_redirects: bool = False):
        """Construct a fresh ``httpx.Client`` for a single request.

        Lazy-imports ``httpx`` so modules importing the package without
        the ``[recordings]`` extra still succeed. Uses the caller-supplied
        transport if set (respx tests).
        """
        import httpx

        return httpx.Client(
            timeout=timeout,
            transport=self._transport,
            follow_redirects=follow_redirects,
        )

    @staticmethod
    def _raise_for_status(method: str, response: httpx.Response) -> None:
        if response.status_code >= 400:
            try:
                body = response.text
            except Exception:  # pragma: no cover — defensive
                body = "<unreadable response body>"
            raise AuphonicHTTPError(
                method=method,
                url=str(response.request.url) if response.request else "",
                status_code=response.status_code,
                body=body,
            )

    @staticmethod
    def _unwrap(payload: Any) -> dict[str, Any]:
        """Auphonic wraps responses in ``{"status_code": 200, "data": {...}}``.

        Returns the inner ``data`` dict if present, otherwise the payload
        itself (which covers endpoints that do not wrap).
        """
        if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
            return payload["data"]
        if isinstance(payload, dict):
            return payload
        raise AuphonicError(f"Unexpected Auphonic response shape: {type(payload).__name__}")

    # ------------------------------------------------------------------
    # Productions
    # ------------------------------------------------------------------

    def create_production(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        preset: str | None = None,
        algorithms: dict[str, Any] | None = None,
        output_files: list[dict[str, Any]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AuphonicProduction:
        """Create a new production (step 1 of 3).

        One of *preset* or *algorithms* should be supplied. If both are
        given, *preset* wins and *algorithms* is ignored by Auphonic.

        Args:
            metadata: ``{"title": …, "artist": …, …}``.
            preset: Preset UUID or preset name (``short_name``).
            algorithms: Inline algorithm configuration (see design doc §3.5).
            output_files: List of output file descriptors
                (``[{"format": "video", …}, {"format": "cut-list", …}]``).
            extra: Any additional fields to merge into the request body.
        """
        body: dict[str, Any] = {}
        if metadata:
            body["metadata"] = metadata
        if preset:
            body["preset"] = preset
        if algorithms:
            body["algorithms"] = algorithms
        if output_files:
            body["output_files"] = output_files
        if extra:
            body.update(extra)

        url = f"{self._base_url}/api/productions.json"
        with self._client(timeout=self._timeout) as client:
            response = client.post(url, headers=self._headers(), json=body)
            self._raise_for_status("POST", response)
            payload = response.json()
        return AuphonicProduction.model_validate(self._unwrap(payload))

    def get_production(self, uuid: str) -> AuphonicProduction:
        """Fetch the current state of *uuid*."""
        url = f"{self._base_url}/api/production/{uuid}.json"
        with self._client(timeout=self._timeout) as client:
            response = client.get(url, headers=self._headers())
            self._raise_for_status("GET", response)
            payload = response.json()
        return AuphonicProduction.model_validate(self._unwrap(payload))

    def upload_input(
        self,
        uuid: str,
        file_path: Path,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> AuphonicProduction:
        """Upload *file_path* as the production's input (step 2 of 3).

        Streams the file in ``self._chunk_size`` chunks. If *on_progress*
        is supplied, it is called with a fraction in ``[0.0, 1.0]`` after
        each chunk is sent. The last call is always ``1.0`` on success.

        Args:
            uuid: Production UUID returned by :meth:`create_production`.
            file_path: Local file to upload.
            on_progress: Optional progress callback.
        """

        url = f"{self._base_url}/api/production/{uuid}/upload.json"
        total_size = file_path.stat().st_size
        filename = file_path.name

        def body_generator():
            """Yield multipart body bytes while updating progress."""
            boundary = b"----clmAuphonicUpload"
            head = (
                b"--" + boundary + b"\r\n"
                b'Content-Disposition: form-data; name="input_file"; '
                b'filename="' + filename.encode("utf-8") + b'"\r\n'
                b"Content-Type: application/octet-stream\r\n\r\n"
            )
            tail = b"\r\n--" + boundary + b"--\r\n"

            sent = 0
            yield head
            with file_path.open("rb") as fh:
                while True:
                    chunk = fh.read(self._chunk_size)
                    if not chunk:
                        break
                    sent += len(chunk)
                    yield chunk
                    if on_progress is not None and total_size > 0:
                        on_progress(min(sent / total_size, 1.0))
            yield tail

        # Compute content-length for the multipart payload so the server
        # doesn't have to buffer it. The body has a head + file + tail.
        boundary_marker = b"----clmAuphonicUpload"
        head_len = (
            len(b"--" + boundary_marker + b"\r\n")
            + len(b'Content-Disposition: form-data; name="input_file"; filename="')
            + len(filename.encode("utf-8"))
            + len(b'"\r\n')
            + len(b"Content-Type: application/octet-stream\r\n\r\n")
        )
        tail_len = len(b"\r\n--" + boundary_marker + b"--\r\n")
        content_length = head_len + total_size + tail_len

        headers = {
            **self._headers(),
            "Content-Type": f"multipart/form-data; boundary={boundary_marker.decode()}",
            "Content-Length": str(content_length),
        }

        with self._client(timeout=self._upload_timeout) as client:
            response = client.post(
                url,
                headers=headers,
                content=body_generator(),
            )
            self._raise_for_status("POST", response)
            payload = response.json()

        # Guarantee a final 1.0 tick in case the file was 0 bytes.
        if on_progress is not None:
            on_progress(1.0)

        return AuphonicProduction.model_validate(self._unwrap(payload))

    def start_production(self, uuid: str) -> AuphonicProduction:
        """Kick off processing for the given production (step 3 of 3)."""
        url = f"{self._base_url}/api/production/{uuid}/start.json"
        with self._client(timeout=self._timeout) as client:
            response = client.post(url, headers=self._headers())
            self._raise_for_status("POST", response)
            payload = response.json()
        return AuphonicProduction.model_validate(self._unwrap(payload))

    def delete_production(self, uuid: str) -> None:
        """Delete the given production (best-effort cancel)."""
        url = f"{self._base_url}/api/production/{uuid}.json"
        with self._client(timeout=self._timeout) as client:
            response = client.delete(url, headers=self._headers())
            # 204 No Content and 200 OK are both success.
            if response.status_code not in (200, 202, 204):
                self._raise_for_status("DELETE", response)

    def download(
        self,
        url: str,
        dest_path: Path,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        """Download *url* to *dest_path*, following redirects.

        Auphonic download URLs redirect to a signed CDN URL; we stream
        the response to disk. Progress is reported by byte counts so
        callers can render a download bar.
        """

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        with self._client(timeout=self._download_timeout, follow_redirects=True) as client:
            with client.stream("GET", url, headers=self._headers()) as response:
                self._raise_for_status("GET", response)
                total_header = response.headers.get("content-length")
                total = int(total_header) if total_header and total_header.isdigit() else 0

                received = 0
                with dest_path.open("wb") as out:
                    for chunk in response.iter_bytes(chunk_size=self._chunk_size):
                        if not chunk:
                            continue
                        out.write(chunk)
                        received += len(chunk)
                        if on_progress is not None and total > 0:
                            on_progress(min(received / total, 1.0))

        if on_progress is not None:
            on_progress(1.0)

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------

    def list_presets(self) -> list[AuphonicPreset]:
        """Return all presets owned by the authenticated user."""
        url = f"{self._base_url}/api/presets.json"
        with self._client(timeout=self._timeout) as client:
            response = client.get(url, headers=self._headers())
            self._raise_for_status("GET", response)
            payload = response.json()

        # Auphonic wraps this endpoint the same way (``data``) but with
        # a list instead of a dict. Unwrap accordingly.
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            raise AuphonicError(
                f"Unexpected Auphonic /api/presets.json payload: {type(data).__name__}"
            )
        return [AuphonicPreset.model_validate(entry) for entry in data]

    def create_preset(self, *, preset_data: dict[str, Any]) -> AuphonicPreset:
        """Create a new preset with the given configuration."""
        url = f"{self._base_url}/api/presets.json"
        with self._client(timeout=self._timeout) as client:
            response = client.post(url, headers=self._headers(), json=preset_data)
            self._raise_for_status("POST", response)
            payload = response.json()
        return AuphonicPreset.model_validate(self._unwrap(payload))

    def update_preset(self, uuid: str, *, preset_data: dict[str, Any]) -> AuphonicPreset:
        """Replace an existing preset with the given configuration."""
        url = f"{self._base_url}/api/preset/{uuid}.json"
        with self._client(timeout=self._timeout) as client:
            response = client.post(url, headers=self._headers(), json=preset_data)
            self._raise_for_status("POST", response)
            payload = response.json()
        return AuphonicPreset.model_validate(self._unwrap(payload))


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_DOWNLOAD_TIMEOUT",
    "DEFAULT_REQUEST_TIMEOUT",
    "DEFAULT_UPLOAD_CHUNK_SIZE",
    "DEFAULT_UPLOAD_TIMEOUT",
    "AuphonicClient",
    "AuphonicError",
    "AuphonicHTTPError",
    "AuphonicOutputFile",
    "AuphonicPreset",
    "AuphonicProduction",
    "AuphonicStatus",
    "ProgressCallback",
]
