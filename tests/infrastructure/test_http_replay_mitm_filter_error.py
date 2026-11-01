"""A request filter that raises refuses the flow in every mode (issue #1067).

Before #1067 ``ClmReplayAddon._filter_request`` returned ``None`` both for an
``ignore_hosts`` match *and* when the filter raised, and ``request()`` read
``None`` as "pass through": the flow went upstream untouched in every mode,
strict ``replay`` included. A buggy or crashing filter thus became a live,
possibly billed call -- exactly what strict replay is supposed to rule out.

Now a filter failure is distinct from an ignored host. The addon answers the
flow itself with the non-retryable ``clm_replay_miss`` 404 naming the filter
error, in every mode:

* strict modes (``replay``/``once`` with a cassette) must never reach the
  network;
* recording modes (``new-episodes``/``refresh``/``once`` without a cassette)
  cannot record a request they could not scrub, and forwarding it unrecorded
  would leave a cassette that silently misses on the next replay -- so they
  fail loudly too.

``ignore_hosts`` hosts keep passing through untouched.

"No upstream request" is asserted in-process: mitmproxy forwards a flow
upstream exactly when the ``request`` hook leaves ``flow.response`` unset, so a
set ``flow.response`` is the proof that nothing was sent.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

pytest.importorskip("mitmproxy")
pytest.importorskip("yaml")

from mitmproxy import http  # noqa: E402
from mitmproxy.test import tflow  # noqa: E402

from clm.infrastructure.http_replay_mitm import cassette_format as cf  # noqa: E402
from clm.infrastructure.http_replay_mitm.addon import (  # noqa: E402
    MODE_NEW_EPISODES,
    MODE_ONCE,
    MODE_REFRESH,
    MODE_REPLAY,
    ClmReplayAddon,
)

_URL = "https://api.openai.com/v1/chat/completions"
_IGNORED_URL = "https://api.smith.langchain.com/runs/batch"
_BODY = b'{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}'
_BOOM = "filter exploded on purpose"


def _raising_filter(_request: object) -> object:
    raise RuntimeError(_BOOM)


def _addon(mode: str, tmp_path: Path, request_filter=None) -> ClmReplayAddon:
    """An addon configured the way ``running()`` would configure it."""
    addon = ClmReplayAddon()
    addon._mode = mode
    addon._default_cassette = tmp_path / "mitm" / "transport.http-cassette.yaml"
    addon._build_id = "testbuild"
    addon._request_filter = request_filter or cf.build_request_filter(
        ignore_hosts=("api.smith.langchain.com",)
    )
    addon._response_filter = cf.build_response_filter()
    return addon


def _flow(tag: str | None, url: str = _URL) -> http.HTTPFlow:
    flow = tflow.tflow()
    flow.request = http.Request.make(
        "POST", url, _BODY, {"Content-Type": "application/json", "Authorization": "Bearer sk-x"}
    )
    if tag is not None:
        flow.request.headers["X-CLM-Cassette"] = tag
    return flow


def _canonical(tmp_path: Path, *, recorded: bool) -> Path:
    """A topic cassette path; with ``recorded`` it holds one matching interaction."""
    canonical = tmp_path / "topic" / ".clm" / "cassettes" / "slides.http-cassette.yaml"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    if recorded:
        recorder = _addon(MODE_NEW_EPISODES, tmp_path)
        flow = _flow(str(canonical))
        recorder.request(flow)
        assert flow.response is None
        flow.response = http.Response.make(
            200, b'{"choices": []}', {"Content-Type": "application/json"}
        )
        recorder.response(flow)
        staging = next(canonical.parent.glob(f"{canonical.name}.staging-mitm-*"))
        cf.write_cassette(canonical, cf.load_interactions(staging))
        staging.unlink()
    return canonical


def _assert_filter_refusal(flow: http.HTTPFlow) -> dict:
    assert flow.response is not None, "a request whose filter raised must not be forwarded"
    assert flow.response.status_code == 404, "refusal must be non-retryable (no 5xx)"
    payload = json.loads(flow.response.content)
    assert payload["clm_replay_miss"] is True
    assert payload["error"]["type"] == "clm_replay_miss"
    assert payload["error"]["code"] == 404  # int: openrouter SDK parses it strictly (#909)
    assert payload["filter_error"] is True
    message = payload["error"]["message"]
    assert "RuntimeError" in message
    assert _BOOM in message
    assert payload["url"] == _URL
    return payload


@pytest.mark.parametrize(
    ("mode", "recorded"),
    [
        (MODE_REPLAY, True),
        (MODE_REPLAY, False),
        (MODE_ONCE, True),
    ],
    ids=["replay-hit", "replay-empty", "once-present"],
)
def test_strict_replay_refuses_tagged_flow_when_filter_raises(
    tmp_path: Path, mode: str, recorded: bool
) -> None:
    """Regression test for #1067: strict replay must not forward a request
    whose filter raised -- not even when the cassette holds a match."""
    canonical = _canonical(tmp_path, recorded=recorded)
    flow = _flow(str(canonical))
    _addon(mode, tmp_path, _raising_filter).request(flow)
    _assert_filter_refusal(flow)


@pytest.mark.parametrize("mode", [MODE_REPLAY, MODE_ONCE, MODE_NEW_EPISODES, MODE_REFRESH])
def test_untagged_flow_with_raising_filter_is_refused(tmp_path: Path, mode: str) -> None:
    """The filter runs before routing, so an untagged flow is refused with the
    filter error (the real cause), in recording modes as well."""
    flow = _flow(None)
    _addon(mode, tmp_path, _raising_filter).request(flow)
    _assert_filter_refusal(flow)


@pytest.mark.parametrize("mode", [MODE_NEW_EPISODES, MODE_REFRESH, MODE_ONCE])
def test_recording_modes_refuse_and_record_nothing_when_filter_raises(
    tmp_path: Path, mode: str
) -> None:
    """Recording modes fail closed too: an unscrubbable request is neither
    recorded (it may carry secrets) nor forwarded unrecorded (the cassette
    would then silently miss on the next strict replay)."""
    canonical = _canonical(tmp_path, recorded=False)  # ``once`` without a cassette records
    addon = _addon(mode, tmp_path, _raising_filter)
    flow = _flow(str(canonical))
    addon.request(flow)
    _assert_filter_refusal(flow)

    addon.response(flow)  # the synthetic refusal must not be persisted
    assert not list(canonical.parent.glob(f"{canonical.name}.staging-mitm-*"))


def test_missing_request_filter_refuses_instead_of_forwarding(tmp_path: Path) -> None:
    """``running()`` always builds the filter; if it somehow has not, the addon
    cannot scrub or match, so it must refuse rather than pass through."""
    canonical = _canonical(tmp_path, recorded=True)
    addon = _addon(MODE_REPLAY, tmp_path)
    addon._request_filter = None
    flow = _flow(str(canonical))
    addon.request(flow)
    assert flow.response is not None
    payload = json.loads(flow.response.content)
    assert payload["clm_replay_miss"] is True
    assert payload["filter_error"] is True


def test_filter_failure_is_logged_at_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    canonical = _canonical(tmp_path, recorded=True)
    with caplog.at_level(logging.WARNING, logger="clm.http_replay_mitm.addon"):
        _addon(MODE_REPLAY, tmp_path, _raising_filter).request(_flow(str(canonical)))
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a filter failure must be logged loudly"
    assert _BOOM in errors[0].getMessage()
    assert _URL in errors[0].getMessage()


@pytest.mark.parametrize("mode", [MODE_REPLAY, MODE_ONCE, MODE_NEW_EPISODES, MODE_REFRESH])
@pytest.mark.parametrize("tagged", [True, False], ids=["tagged", "untagged"])
def test_ignored_host_still_passes_through(tmp_path: Path, mode: str, tagged: bool) -> None:
    """ignore_hosts (LangSmith telemetry) keeps its pass-through in every mode."""
    canonical = _canonical(tmp_path, recorded=False)
    addon = _addon(mode, tmp_path)
    flow = _flow(str(canonical) if tagged else None, url=_IGNORED_URL)
    addon.request(flow)
    assert flow.response is None, "an ignored host is forwarded untouched"

    flow.response = http.Response.make(200, b"{}", {"Content-Type": "application/json"})
    addon.response(flow)
    assert not list(canonical.parent.glob(f"{canonical.name}.staging-mitm-*"))


def test_filter_raising_at_response_time_records_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The second call site: response() re-filters before recording. If the
    filter raises there, the interaction is dropped (never recorded unscrubbed)
    and the drop is logged at ERROR, since the cassette is now short one entry."""
    canonical = _canonical(tmp_path, recorded=False)
    addon = _addon(MODE_NEW_EPISODES, tmp_path)
    flow = _flow(str(canonical))
    addon.request(flow)
    assert flow.response is None

    addon._request_filter = _raising_filter
    flow.response = http.Response.make(
        200, b'{"choices": []}', {"Content-Type": "application/json"}
    )
    with caplog.at_level(logging.WARNING, logger="clm.http_replay_mitm.addon"):
        addon.response(flow)
    assert not list(canonical.parent.glob(f"{canonical.name}.staging-mitm-*"))
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors and _BOOM in errors[0].getMessage()


@pytest.mark.parametrize("mode", [MODE_REPLAY, MODE_NEW_EPISODES])
def test_ignored_host_passes_through_even_when_the_scrubber_raises(
    tmp_path: Path, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whether a host is ignored must not depend on the scrubber working: the
    ignore-host check runs before the filters, so a scrubber crash on LangSmith
    telemetry still passes through instead of being refused -- while the same
    crash on a non-ignored host is refused."""

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(_BOOM)

    monkeypatch.setattr(cf.vf, "replace_headers", _boom)
    addon = _addon(mode, tmp_path)  # builds the real filter around the broken scrubber

    ignored = _flow(None, url=_IGNORED_URL)
    addon.request(ignored)
    assert ignored.response is None, "an ignored host is forwarded untouched"

    refused = _flow(None)
    addon.request(refused)
    _assert_filter_refusal(refused)


def test_refusal_redacts_the_unscrubbed_query_string(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The scrubber did not run, so a ``?api_key=`` secret must not be echoed
    into the 404 body (which lands in the notebook's cell output), the log or
    the trace."""
    events: list[tuple[str, dict]] = []

    class _Trace:
        def emit(self, event: str, data: dict | None = None) -> None:
            events.append((event, data or {}))

        def close(self) -> None:
            pass

    addon = _addon(MODE_REPLAY, tmp_path, _raising_filter)
    addon._trace = _Trace()
    flow = _flow(None, url=_URL + "?api_key=SECRET-123&model=x")
    with caplog.at_level(logging.WARNING, logger="clm.http_replay_mitm.addon"):
        addon.request(flow)

    assert flow.response is not None
    assert b"SECRET-123" not in flow.response.content
    payload = json.loads(flow.response.content)
    assert payload["url"] == _URL + "?<redacted>"
    assert payload["filter_error"] is True
    assert all("SECRET-123" not in r.getMessage() for r in caplog.records)
    [(name, data)] = events
    assert name == "proxy.request"
    assert data["action"] == "miss"
    assert data["filter_error"] is True
    assert "SECRET-123" not in data["path"]
