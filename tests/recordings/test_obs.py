"""Tests for the OBS WebSocket client wrapper."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clm.recordings.workflow.obs import ObsClient, RecordingEvent

# obsws_python is an optional dependency ([recordings] extra).
# Skip the entire module if it is not installed so that CI environments
# without the extra don't fail on patch targets.
pytest.importorskip("obsws_python", reason="obsws-python not installed")

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

    def test_connect_req_failure_suppresses_obsws_logging(self):
        """obsws_python.baseclient logs a traceback on ConnectionRefusedError.

        Our wrapper should suppress that log so users only see the clean
        warning from the CLM lifespan handler.
        """
        obsws_logger = logging.getLogger("obsws_python.baseclient.ObsClient")

        with patch("obsws_python.ReqClient", side_effect=Exception("refused")):
            client = ObsClient()
            with pytest.raises(ConnectionError):
                client.connect()

        # After connect() returns (even on failure), the logger should be
        # restored — not left permanently disabled.
        assert not obsws_logger.disabled

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
# Recording control
# ---------------------------------------------------------------------------


class TestObsClientRecordingControl:
    def test_start_record_delegates_to_req(self, mock_obsws):
        client = ObsClient()
        client.connect()
        client.start_record()
        mock_obsws["req"].start_record.assert_called_once_with()

    def test_start_record_not_connected(self):
        client = ObsClient()
        with pytest.raises(ConnectionError, match="Not connected"):
            client.start_record()

    def test_start_record_wraps_obsws_error(self, mock_obsws):
        client = ObsClient()
        client.connect()
        mock_obsws["req"].start_record.side_effect = RuntimeError("already recording")
        with pytest.raises(ConnectionError, match="OBS rejected start_record"):
            client.start_record()

    def test_stop_record_delegates_to_req(self, mock_obsws):
        client = ObsClient()
        client.connect()
        client.stop_record()
        mock_obsws["req"].stop_record.assert_called_once_with()

    def test_stop_record_not_connected(self):
        client = ObsClient()
        with pytest.raises(ConnectionError, match="Not connected"):
            client.stop_record()

    def test_stop_record_wraps_obsws_error(self, mock_obsws):
        client = ObsClient()
        client.connect()
        mock_obsws["req"].stop_record.side_effect = RuntimeError("not recording")
        with pytest.raises(ConnectionError, match="OBS rejected stop_record"):
            client.stop_record()

    def test_pause_record_delegates_to_req(self, mock_obsws):
        client = ObsClient()
        client.connect()
        client.pause_record()
        mock_obsws["req"].pause_record.assert_called_once_with()

    def test_pause_record_not_connected(self):
        client = ObsClient()
        with pytest.raises(ConnectionError, match="Not connected"):
            client.pause_record()

    def test_pause_record_wraps_obsws_error(self, mock_obsws):
        client = ObsClient()
        client.connect()
        mock_obsws["req"].pause_record.side_effect = RuntimeError("already paused")
        with pytest.raises(ConnectionError, match="OBS rejected pause_record"):
            client.pause_record()

    def test_resume_record_delegates_to_req(self, mock_obsws):
        client = ObsClient()
        client.connect()
        client.resume_record()
        mock_obsws["req"].resume_record.assert_called_once_with()

    def test_resume_record_not_connected(self):
        client = ObsClient()
        with pytest.raises(ConnectionError, match="Not connected"):
            client.resume_record()

    def test_resume_record_wraps_obsws_error(self, mock_obsws):
        client = ObsClient()
        client.connect()
        mock_obsws["req"].resume_record.side_effect = RuntimeError("not paused")
        with pytest.raises(ConnectionError, match="OBS rejected resume_record"):
            client.resume_record()


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


# ---------------------------------------------------------------------------
# Connection state + watchdog / reconnect
# ---------------------------------------------------------------------------


class TestObsClientConnectionState:
    def test_initial_state_disconnected(self):
        client = ObsClient()
        assert client.connection_state == "disconnected"

    def test_connect_transitions_to_connected(self, mock_obsws):
        client = ObsClient()
        client.connect()
        assert client.connection_state == "connected"

    def test_disconnect_transitions_to_disconnected(self, mock_obsws):
        client = ObsClient()
        client.connect()
        client.disconnect()
        assert client.connection_state == "disconnected"

    def test_state_callback_receives_transitions(self, mock_obsws):
        client = ObsClient()
        transitions: list[str] = []
        client.on_state_change(transitions.append)
        client.connect()
        client.disconnect()
        assert transitions == ["connected", "disconnected"]

    def test_state_callback_fires_only_on_change(self, mock_obsws):
        client = ObsClient()
        transitions: list[str] = []
        client.on_state_change(transitions.append)
        client.connect()
        client._set_state("connected")  # no-op: same state
        assert transitions == ["connected"]


class TestObsClientWatchdog:
    """Watchdog pings OBS periodically and reconnects with backoff on loss."""

    def test_auto_reconnect_off_does_not_start_watchdog(self, mock_obsws):
        client = ObsClient(auto_reconnect=False)
        client.connect()
        assert client._watchdog_thread is None

    def test_auto_reconnect_on_starts_watchdog(self, mock_obsws):
        client = ObsClient(auto_reconnect=True, watchdog_interval=0.05)
        try:
            client.connect()
            assert client._watchdog_thread is not None
            assert client._watchdog_thread.is_alive()
        finally:
            client.disconnect()

    def test_disconnect_stops_watchdog(self, mock_obsws):
        client = ObsClient(auto_reconnect=True, watchdog_interval=0.05)
        client.connect()
        thread = client._watchdog_thread
        client.disconnect()
        assert thread is not None
        thread.join(timeout=1.0)
        assert not thread.is_alive()

    def test_probe_failure_triggers_reconnect_and_state_transitions(self, mock_obsws):
        """Probe raising → state goes reconnecting → connected after success."""
        import time

        req = mock_obsws["req"]
        # First probe raises; subsequent calls succeed.
        call_count = {"n": 0}

        def status_side_effect(*_args, **_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionError("socket dead")
            return MagicMock(output_active=False, output_state="stopped")

        req.get_record_status.side_effect = status_side_effect

        transitions: list[str] = []
        client = ObsClient(
            auto_reconnect=True,
            watchdog_interval=0.02,
            backoff_schedule=(0.01,),
        )
        client.on_state_change(transitions.append)
        client.connect()
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if transitions.count("connected") >= 2:
                    break
                time.sleep(0.02)
        finally:
            client.disconnect()

        # Expected sequence: connected (initial) → reconnecting → connected → disconnected.
        assert "reconnecting" in transitions
        assert transitions.count("connected") >= 2
        assert transitions[-1] == "disconnected"

    def test_backoff_iterator_caps_at_final_value(self):
        client = ObsClient(backoff_schedule=(1.0, 2.0, 4.0))
        it = client._iter_backoff()
        assert next(it) == 1.0
        assert next(it) == 2.0
        # Last value repeats indefinitely
        assert next(it) == 4.0
        assert next(it) == 4.0
        assert next(it) == 4.0

    def test_probe_detects_dead_event_thread(self, mock_obsws):
        """If the EventClient's receive thread dies, probe raises."""
        import threading

        dead_thread = threading.Thread(target=lambda: None)
        dead_thread.start()
        dead_thread.join()
        assert not dead_thread.is_alive()

        mock_obsws["evt"].thread_recv = dead_thread

        client = ObsClient()
        client.connect()
        with pytest.raises(ConnectionError, match="receive thread has died"):
            client._probe()

    def test_reconnect_loop_aborts_when_watchdog_stopped(self, mock_obsws):
        """Stopping the watchdog during reconnect breaks out of the loop."""
        import time

        req = mock_obsws["req"]
        # Initial probe fails → enters reconnect loop.
        req.get_record_status.side_effect = ConnectionError("socket dead")

        client = ObsClient(
            auto_reconnect=True,
            watchdog_interval=0.01,
            backoff_schedule=(0.01,),
        )
        client.connect()  # initial connect succeeds — ReqClient not failing yet

        # Now make every future ReqClient construction fail so the reconnect
        # loop is pinned in ``reconnecting`` until the watchdog is stopped.
        mock_obsws["ReqClient"].side_effect = ConnectionError("still down")

        # Give the watchdog a moment to fail its probe + attempt reconnect.
        time.sleep(0.1)

        client.disconnect()
        thread = client._watchdog_thread
        assert thread is None or not thread.is_alive()
        assert client.connection_state == "disconnected"
