"""Tests for :class:`AuphonicClient`.

Uses ``respx`` — an ``httpx`` mock transport — to assert the exact shape
of requests the client sends and drive canned responses without hitting
the real Auphonic API.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from clm.recordings.workflow.backends.auphonic_client import (
    AuphonicClient,
    AuphonicError,
    AuphonicHTTPError,
    AuphonicPreset,
    AuphonicStatus,
)

BASE_URL = "https://auphonic.test"


@pytest.fixture()
def client() -> AuphonicClient:
    """AuphonicClient pointed at a test base URL with a dummy key."""
    return AuphonicClient(api_key="token-abc", base_url=BASE_URL, chunk_size=4096)


# ---------------------------------------------------------------------
# create_production
# ---------------------------------------------------------------------


class TestCreateProduction:
    def test_happy_path_wrapped_payload(self, client: AuphonicClient) -> None:
        with respx.mock(base_url=BASE_URL) as mock:
            route = mock.post("/api/productions.json").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status_code": 200,
                        "data": {
                            "uuid": "prod-1",
                            "status": AuphonicStatus.INCOMPLETE_FORM,
                        },
                    },
                )
            )

            production = client.create_production(
                metadata={"title": "My Lecture"},
                algorithms={"denoise": True, "leveler": True},
                output_files=[{"format": "video"}],
            )

            assert production.uuid == "prod-1"
            assert production.status == AuphonicStatus.INCOMPLETE_FORM
            assert route.called
            sent = route.calls[0].request
            assert sent.headers["authorization"] == "Bearer token-abc"
            body = sent.content.decode()
            # httpx serializes JSON in compact form (no whitespace).
            assert '"title":"My Lecture"' in body
            assert '"denoise":true' in body

    def test_preset_reference_is_sent(self, client: AuphonicClient) -> None:
        with respx.mock(base_url=BASE_URL) as mock:
            route = mock.post("/api/productions.json").mock(
                return_value=httpx.Response(200, json={"data": {"uuid": "p"}})
            )
            client.create_production(preset="CLM Lecture Recording")

            # Assert inside the with block — respx clears recorded calls
            # on context exit, and httpx serializes JSON compactly.
            request_body = route.calls[0].request.content.decode()
            assert '"preset":"CLM Lecture Recording"' in request_body
            assert '"algorithms"' not in request_body

    def test_unwrapped_payload_is_accepted(self, client: AuphonicClient) -> None:
        """Auphonic sometimes returns the production dict at the top level."""
        with respx.mock(base_url=BASE_URL) as mock:
            mock.post("/api/productions.json").mock(
                return_value=httpx.Response(200, json={"uuid": "top-level"})
            )
            production = client.create_production()
        assert production.uuid == "top-level"

    def test_raises_on_http_error(self, client: AuphonicClient) -> None:
        with respx.mock(base_url=BASE_URL) as mock:
            mock.post("/api/productions.json").mock(
                return_value=httpx.Response(400, text="Bad metadata")
            )
            with pytest.raises(AuphonicHTTPError) as info:
                client.create_production()
        assert info.value.status_code == 400
        assert "Bad metadata" in info.value.body

    def test_null_string_fields_are_coerced_to_empty(self, client: AuphonicClient) -> None:
        """Real Auphonic returns ``null`` for not-yet-applicable strings.

        Regression test: ``error_status`` and every output's
        ``download_url`` are ``null`` on a freshly created production
        (no error yet, outputs not rendered yet). Pydantic rejected this
        before we added the ``_none_to_empty`` validator, breaking the
        happy path of ``clm recordings submit``.
        """
        with respx.mock(base_url=BASE_URL) as mock:
            mock.post("/api/productions.json").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status_code": 200,
                        "error_code": None,
                        "error_message": "",
                        "form_errors": {},
                        "data": {
                            "uuid": "7Sg2vazmofYAmN2pHWah5S",
                            "status": AuphonicStatus.INCOMPLETE_FORM,
                            "status_string": "Incomplete Form",
                            "error_status": None,
                            "warning_message": None,
                            "output_files": [
                                {
                                    "format": "video",
                                    "ending": "mp4",
                                    "download_url": None,
                                    "filename": None,
                                },
                                {
                                    "format": "cut-list",
                                    "ending": "DaVinciResolve.edl",
                                    "download_url": None,
                                },
                            ],
                        },
                    },
                )
            )
            production = client.create_production(
                metadata={"title": "Smoke Test"},
                preset="CLM Lecture Recording",
                output_files=[
                    {"format": "video"},
                    {"format": "cut-list"},
                ],
            )
        assert production.uuid == "7Sg2vazmofYAmN2pHWah5S"
        assert production.error_status == ""
        assert production.warning_message == ""
        assert len(production.output_files) == 2
        assert production.output_files[0].download_url == ""
        assert production.output_files[0].filename == ""
        assert production.output_files[1].download_url == ""

    def test_realistic_done_production_response(self, client: AuphonicClient) -> None:
        """Validate against a full real-API DONE response.

        Captured from a real production on 2026-04-12 during the
        Auphonic smoke test. Exercises fields that the hand-crafted
        happy-path fixtures missed: dict-shaped ``used_credits``, extra
        ignored fields (``algorithms``, ``statistics``, ``chapters``,
        ``speech_recognition``, ``metadata``, …), and output-file
        metadata (``checksum``, ``size_string``, ``mono_mixdown``, …).

        If Auphonic changes its response shape again, this test will
        catch it without a round-trip through the real API.
        """
        realistic_payload = {
            "status_code": 200,
            "error_code": None,
            "error_message": "",
            "form_errors": {},
            "data": {
                "uuid": "ShfFXnXeAiHoB8U4uFJQS3",
                "status": AuphonicStatus.DONE,
                "status_string": "Done",
                "error_message": "",
                "error_status": None,
                "warning_message": "",
                "warning_status": None,
                "length": 20.009319727891157,
                "length_timestring": "00:00:20.009",
                "used_credits": {
                    "recurring": 0.0,
                    "onetime": 0.05,
                    "combined": 0.05,
                },
                "bitrate": 191.987,
                "channels": 2,
                "samplerate": 48000,
                "format": "aac",
                "has_video": True,
                "input_file": "smoke--RAW.mp4",
                "output_basename": "smoke--RAW",
                "preset": "vS7YnceijKxUURDL6uZX3H",
                "change_allowed": True,
                "start_allowed": False,
                "in_review": False,
                "is_multitrack": False,
                "review_before_publishing": False,
                "cut_start": 0.0,
                "cut_end": 0.0,
                "creation_time": "2026-04-12T01:41:00.795Z",
                "change_time": "2026-04-12T01:41:14.024Z",
                "image": None,
                "service": None,
                "shownotes": None,
                "thumbnail": None,
                "webhook": None,
                "chapters": [],
                "cuts": [],
                "multi_input_files": [],
                "outgoing_services": [],
                "edit_page": "https://auphonic.com/engine/upload/edit/ShfFXnXeAiHoB8U4uFJQS3",
                "status_page": "https://auphonic.com/engine/status/ShfFXnXeAiHoB8U4uFJQS3",
                "waveform_image": "https://auphonic.com/api/download/audio-result/ShfFXnXeAiHoB8U4uFJQS3/waveform.png",
                "algorithms": {
                    "filtering": True,
                    "filtermethod": "autoeq",
                    "leveler": True,
                    "levelermode": "default",
                    "compressor_speech": "auto",
                },
                "metadata": {
                    "album": "",
                    "subtitle": "",
                    "license": "",
                    "summary": "",
                    "artist": "Matthias Hölzl",
                    "track": "",
                    "title": "smoke",
                },
                "speech_recognition": {
                    "uuid": "atUCUJm5EYLRUUKSv8LMHm",
                    "keywords": [""],
                    "type": "whisper",
                    "language": "auto",
                    "shownotes": False,
                },
                "statistics": {
                    "levels": {
                        "input": {
                            "signal_level": [-32.35, "dB"],
                            "noise_level": [-73.14, "dB"],
                            "snr": [40.79, "dB"],
                        }
                    }
                },
                "output_files": [
                    {
                        "format": "video",
                        "ending": "mp4",
                        "download_url": "https://auphonic.com/api/download/audio-result/ShfFXnXeAiHoB8U4uFJQS3/smoke--RAW.mp4",
                        "filename": "smoke--RAW.mp4",
                        "checksum": "96673128a7092c25039dc5bf5c63a312",
                        "size": 1335528,
                        "size_string": "1.3 MB",
                        "bitrate": 191,
                        "mono_mixdown": False,
                        "split_on_chapters": False,
                        "suffix": "",
                        "outgoing_services": [],
                    }
                ],
            },
        }
        with respx.mock(base_url=BASE_URL) as mock:
            mock.get("/api/production/ShfFXnXeAiHoB8U4uFJQS3.json").mock(
                return_value=httpx.Response(200, json=realistic_payload)
            )
            production = client.get_production("ShfFXnXeAiHoB8U4uFJQS3")

        assert production.uuid == "ShfFXnXeAiHoB8U4uFJQS3"
        assert production.status == AuphonicStatus.DONE
        assert production.status_string == "Done"
        assert production.error_status == ""  # coerced from None
        assert production.warning_message == ""
        assert production.length == pytest.approx(20.009319727891157)
        # used_credits preserves the dict shape
        assert isinstance(production.used_credits, dict)
        assert production.used_credits["combined"] == 0.05
        # …and the convenience accessor returns the combined total
        assert production.used_credits_combined == pytest.approx(0.05)
        # Output file parsing still works with extra fields present
        assert len(production.output_files) == 1
        output = production.output_files[0]
        assert output.format == "video"
        assert output.ending == "mp4"
        assert output.download_url.endswith("/smoke--RAW.mp4")
        assert output.filename == "smoke--RAW.mp4"
        assert output.size == 1335528

    def test_used_credits_combined_accepts_plain_float(self, client: AuphonicClient) -> None:
        """Legacy API shape: ``used_credits`` as a plain float.

        Keeps backward compatibility with older Auphonic responses
        (and any staging deploys that still return the simpler shape).
        """
        with respx.mock(base_url=BASE_URL) as mock:
            mock.get("/api/production/legacy.json").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": {
                            "uuid": "legacy",
                            "status": AuphonicStatus.DONE,
                            "used_credits": 0.12,
                        }
                    },
                )
            )
            production = client.get_production("legacy")
        assert production.used_credits == 0.12
        assert production.used_credits_combined == pytest.approx(0.12)


# ---------------------------------------------------------------------
# get_production / start_production / delete_production
# ---------------------------------------------------------------------


class TestGetProduction:
    def test_returns_status(self, client: AuphonicClient) -> None:
        with respx.mock(base_url=BASE_URL) as mock:
            mock.get("/api/production/prod-1.json").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": {
                            "uuid": "prod-1",
                            "status": AuphonicStatus.DONE,
                            "status_string": "Done",
                            "output_files": [
                                {
                                    "format": "video",
                                    "ending": "mp4",
                                    "download_url": "https://cdn.auphonic.test/out.mp4",
                                }
                            ],
                        }
                    },
                )
            )
            production = client.get_production("prod-1")
        assert production.status == AuphonicStatus.DONE
        assert production.output_files[0].download_url.endswith("out.mp4")


class TestStartProduction:
    def test_posts_start_endpoint(self, client: AuphonicClient) -> None:
        with respx.mock(base_url=BASE_URL) as mock:
            route = mock.post("/api/production/prod-1/start.json").mock(
                return_value=httpx.Response(200, json={"data": {"uuid": "prod-1", "status": 9}})
            )
            production = client.start_production("prod-1")
        assert route.called
        assert production.status == 9


class TestDeleteProduction:
    def test_accepts_204(self, client: AuphonicClient) -> None:
        with respx.mock(base_url=BASE_URL) as mock:
            route = mock.delete("/api/production/p.json").mock(return_value=httpx.Response(204))
            client.delete_production("p")
        assert route.called

    def test_raises_on_server_error(self, client: AuphonicClient) -> None:
        with respx.mock(base_url=BASE_URL) as mock:
            mock.delete("/api/production/p.json").mock(
                return_value=httpx.Response(500, text="oops")
            )
            with pytest.raises(AuphonicHTTPError):
                client.delete_production("p")


# ---------------------------------------------------------------------
# upload_input
# ---------------------------------------------------------------------


class TestUpload:
    def test_streams_file_and_reports_progress(
        self, client: AuphonicClient, tmp_path: Path
    ) -> None:
        payload = b"x" * (4096 * 5)  # 5 chunks
        source = tmp_path / "raw.mp4"
        source.write_bytes(payload)

        progress_values: list[float] = []

        with respx.mock(base_url=BASE_URL) as mock:
            route = mock.post("/api/production/prod-1/upload.json").mock(
                return_value=httpx.Response(200, json={"data": {"uuid": "prod-1"}})
            )
            production = client.upload_input(
                "prod-1",
                source,
                on_progress=progress_values.append,
            )

        assert production.uuid == "prod-1"
        assert route.called
        req = route.calls[0].request
        assert req.headers["authorization"] == "Bearer token-abc"
        assert req.headers["content-type"].startswith("multipart/form-data")
        # The multipart body should contain the filename and the raw bytes.
        body = req.content
        assert b'filename="raw.mp4"' in body
        assert payload in body

        # Progress should end at 1.0 and be monotonically increasing.
        assert progress_values[-1] == pytest.approx(1.0)
        assert all(
            progress_values[i] <= progress_values[i + 1] for i in range(len(progress_values) - 1)
        )

    def test_zero_byte_file_still_reports_final_progress(
        self, client: AuphonicClient, tmp_path: Path
    ) -> None:
        empty = tmp_path / "empty.mp4"
        empty.write_bytes(b"")
        progress: list[float] = []

        with respx.mock(base_url=BASE_URL) as mock:
            mock.post("/api/production/prod-2/upload.json").mock(
                return_value=httpx.Response(200, json={"data": {"uuid": "prod-2"}})
            )
            client.upload_input("prod-2", empty, on_progress=progress.append)

        # Even zero-length files must end with a 1.0 tick so the UI
        # clears the upload bar.
        assert progress == [1.0]


# ---------------------------------------------------------------------
# download
# ---------------------------------------------------------------------


class TestDownload:
    def test_streams_body_to_disk(self, client: AuphonicClient, tmp_path: Path) -> None:
        dest = tmp_path / "out.mp4"
        blob = b"video-content" * 1000

        with respx.mock(base_url=BASE_URL) as mock:
            mock.get("/downloads/final.mp4").mock(
                return_value=httpx.Response(
                    200,
                    content=blob,
                    headers={"content-length": str(len(blob))},
                )
            )
            progress: list[float] = []
            client.download(
                f"{BASE_URL}/downloads/final.mp4",
                dest,
                on_progress=progress.append,
            )

        assert dest.read_bytes() == blob
        assert progress[-1] == pytest.approx(1.0)

    def test_follows_redirect(self, client: AuphonicClient, tmp_path: Path) -> None:
        dest = tmp_path / "out.mp4"

        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"{BASE_URL}/downloads/first").mock(
                return_value=httpx.Response(
                    302,
                    headers={"location": f"{BASE_URL}/downloads/second"},
                )
            )
            mock.get(f"{BASE_URL}/downloads/second").mock(
                return_value=httpx.Response(200, content=b"final-bytes"),
            )

            client.download(f"{BASE_URL}/downloads/first", dest)

        assert dest.read_bytes() == b"final-bytes"


# ---------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------


class TestPresets:
    def test_list_presets(self, client: AuphonicClient) -> None:
        with respx.mock(base_url=BASE_URL) as mock:
            mock.get("/api/presets.json").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "uuid": "u1",
                                "preset_name": "CLM Lecture Recording",
                                "short_name": "clm-lecture",
                            },
                            {"uuid": "u2", "preset_name": "Other"},
                        ]
                    },
                )
            )
            presets = client.list_presets()
        assert [p.preset_name for p in presets] == [
            "CLM Lecture Recording",
            "Other",
        ]
        assert isinstance(presets[0], AuphonicPreset)

    def test_list_presets_unwrapped_list(self, client: AuphonicClient) -> None:
        """Some Auphonic deployments return a bare list."""
        with respx.mock(base_url=BASE_URL) as mock:
            mock.get("/api/presets.json").mock(
                return_value=httpx.Response(200, json=[{"uuid": "u", "preset_name": "Raw"}])
            )
            presets = client.list_presets()
        assert presets[0].preset_name == "Raw"

    def test_list_presets_rejects_unexpected_shape(self, client: AuphonicClient) -> None:
        with respx.mock(base_url=BASE_URL) as mock:
            mock.get("/api/presets.json").mock(
                return_value=httpx.Response(200, json={"not-a-list": True})
            )
            with pytest.raises(AuphonicError):
                client.list_presets()

    def test_create_preset(self, client: AuphonicClient) -> None:
        with respx.mock(base_url=BASE_URL) as mock:
            route = mock.post("/api/presets.json").mock(
                return_value=httpx.Response(
                    200,
                    json={"data": {"uuid": "new", "preset_name": "CLM"}},
                )
            )
            preset = client.create_preset(preset_data={"preset_name": "CLM"})
        assert preset.uuid == "new"
        assert route.called

    def test_update_preset_posts_to_uuid_endpoint(self, client: AuphonicClient) -> None:
        with respx.mock(base_url=BASE_URL) as mock:
            route = mock.post("/api/preset/abc.json").mock(
                return_value=httpx.Response(
                    200,
                    json={"data": {"uuid": "abc", "preset_name": "CLM"}},
                )
            )
            preset = client.update_preset("abc", preset_data={"preset_name": "CLM"})
        assert route.called
        assert preset.uuid == "abc"
