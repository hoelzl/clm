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
