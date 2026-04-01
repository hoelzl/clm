"""Tests for the OBS WebSocket client wrapper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.recordings.workflow.obs import ObsClient, RecordingEvent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_obsws():
    """Patch obsws_python with mock ReqClient and EventClient."""
    with (
        patch("obsws_python.ReqClient") as MockReq,
        patch("obsws_python.EventClient") as MockEvt,
    ):
        req_instance = MagicMock()
        evt_instance = MagicMock()
        MockReq.return_value = req_instance
        MockEvt.return_value = evt_instance
        yield {
            "ReqClient": MockReq,
            "EventClient": MockEvt,
            "req": req_instance,
            "evt": evt_instance,
        }


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestObsClientConnection:
    def test_connect_creates_both_clients(self, mock_obsws):
        client = ObsClient(host="myhost", port=9999, password="secret")
        client.connect()

        mock_obsws["ReqClient"].assert_called_once_with(host="myhost", port=9999, password="secret")
        mock_obsws["EventClient"].assert_called_once_with(
            host="myhost", port=9999, password="secret"
        )
        assert client.connected is True

    def test_connect_registers_event_handler(self, mock_obsws):
        client = ObsClient()
        client.connect()

        mock_obsws["evt"].callback.register.assert_called_once()
        handler = mock_obsws["evt"].callback.register.call_args[0][0]
        assert handler.__name__ == "on_record_state_changed"

    def test_connected_false_before_connect(self):
        client = ObsClient()
        assert client.connected is False

    def test_disconnect_cleans_up(self, mock_obsws):
        client = ObsClient()
        client.connect()
        client.disconnect()

        mock_obsws["req"].disconnect.assert_called_once()
        mock_obsws["evt"].disconnect.assert_called_once()
        assert client.connected is False

    def test_disconnect_when_not_connected(self):
        client = ObsClient()
        client.disconnect()  # Should not raise

    def test_connect_req_failure_raises(self):
        with patch("obsws_python.ReqClient", side_effect=Exception("refused")):
            client = ObsClient()
            with pytest.raises(ConnectionError, match="Cannot connect to OBS"):
                client.connect()

    def test_connect_evt_failure_disconnects_req(self):
        req_mock = MagicMock()
        with (
            patch("obsws_python.ReqClient", return_value=req_mock),
            patch("obsws_python.EventClient", side_effect=Exception("evt fail")),
        ):
            client = ObsClient()
            with pytest.raises(ConnectionError, match="event client"):
                client.connect()
            req_mock.disconnect.assert_called_once()
            assert client.connected is False

    def test_context_manager(self, mock_obsws):
        with ObsClient() as client:
            assert client.connected is True
        assert client.connected is False


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


class TestObsClientQueries:
    def test_get_record_status(self, mock_obsws):
        client = ObsClient()
        client.connect()

        mock_obsws["req"].get_record_status.return_value = MagicMock(
            output_active=True,
            output_state="OBS_WEBSOCKET_OUTPUT_STARTED",
            output_path=None,
        )

        status = client.get_record_status()
        assert status.output_active is True
        assert status.output_state == "OBS_WEBSOCKET_OUTPUT_STARTED"

    def test_get_record_status_not_connected(self):
        client = ObsClient()
        with pytest.raises(ConnectionError, match="Not connected"):
            client.get_record_status()

    def test_get_record_directory(self, mock_obsws):
        client = ObsClient()
        client.connect()

        mock_obsws["req"].get_record_directory.return_value = MagicMock(
            record_directory="C:/Videos"
        )

        result = client.get_record_directory()
        assert result == Path("C:/Videos")

    def test_get_record_directory_not_connected(self):
        client = ObsClient()
        with pytest.raises(ConnectionError, match="Not connected"):
            client.get_record_directory()


# ---------------------------------------------------------------------------
# Event dispatching
# ---------------------------------------------------------------------------


class TestObsClientEvents:
    def test_callback_receives_recording_event(self, mock_obsws):
        client = ObsClient()
        callback = MagicMock()
        client.on_record_state_changed(callback)
        client.connect()

        # Get the handler that was registered with obsws-python
        handler = mock_obsws["evt"].callback.register.call_args[0][0]

        # Simulate an OBS event
        fake_data = MagicMock(
            output_active=False,
            output_state="OBS_WEBSOCKET_OUTPUT_STOPPED",
            output_path="/tmp/recording.mkv",
        )
        handler(fake_data)

        callback.assert_called_once()
        event = callback.call_args[0][0]
        assert isinstance(event, RecordingEvent)
        assert event.output_active is False
        assert event.output_state == "OBS_WEBSOCKET_OUTPUT_STOPPED"
        assert event.output_path == "/tmp/recording.mkv"

    def test_callback_handles_missing_output_path(self, mock_obsws):
        client = ObsClient()
        callback = MagicMock()
        client.on_record_state_changed(callback)
        client.connect()

        handler = mock_obsws["evt"].callback.register.call_args[0][0]

        # Event data without output_path attribute
        fake_data = MagicMock(spec=["output_active"])
        fake_data.output_active = True
        handler(fake_data)

        event = callback.call_args[0][0]
        assert event.output_path is None
        assert event.output_state == "unknown"

    def test_multiple_callbacks(self, mock_obsws):
        client = ObsClient()
        cb1 = MagicMock()
        cb2 = MagicMock()
        client.on_record_state_changed(cb1)
        client.on_record_state_changed(cb2)
        client.connect()

        handler = mock_obsws["evt"].callback.register.call_args[0][0]
        fake_data = MagicMock(output_active=True, output_state="started")
        handler(fake_data)

        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_callback_exception_does_not_stop_others(self, mock_obsws):
        client = ObsClient()
        cb1 = MagicMock(side_effect=ValueError("boom"))
        cb2 = MagicMock()
        client.on_record_state_changed(cb1)
        client.on_record_state_changed(cb2)
        client.connect()

        handler = mock_obsws["evt"].callback.register.call_args[0][0]
        fake_data = MagicMock(output_active=True, output_state="started")
        handler(fake_data)

        # cb2 should still be called despite cb1 raising
        cb2.assert_called_once()
