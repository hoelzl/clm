"""Untagged flows are never replayed from the catch-all (issue #1055).

An untagged flow is a request from a kernel HTTP client the tag bootstrap
does not patch (before #1055: anything on ``httpx2`` — openai >= 3,
anthropic, mcp). It cannot be matched against the topic's committed
cassette, so the addon used to route it to the build's *catch-all*
cassette: a machine-local scratch file under the jobs-DB ``mitm/`` dir
that persists across builds and is never merged or committed. The addon
*served* catch-all hits — in strict ``replay`` too — so a response recorded
by an earlier local ``new-episodes`` build (a live
``429 credit_balance_exhausted`` included) was replayed as if it came from
the topic cassette. Builds then depended on per-machine scratch state and
looked like they were calling the real API. Now the catch-all is
record-only, and strict modes refuse untagged flows outright.

These tests drive the addon in-process (no ``mitmdump`` subprocess) with
mitmproxy's own test flows, so they run in the fast suite.
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
    MODE_REPLAY,
    UNTAGGED_FLOW_SENTINEL,
    ClmReplayAddon,
)

_URL = "https://api.openai.com/v1/chat/completions"
_BODY = b'{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}'


def _addon(mode: str, catch_all: Path) -> ClmReplayAddon:
    """An addon configured the way ``running()`` would configure it."""
    addon = ClmReplayAddon()
    addon._mode = mode
    addon._default_cassette = catch_all
    addon._build_id = "testbuild"
    addon._request_filter = cf.build_request_filter(ignore_hosts=("api.smith.langchain.com",))
    addon._response_filter = cf.build_response_filter()
    return addon


def _flow(tag: str | None = None, url: str = _URL) -> http.HTTPFlow:
    flow = tflow.tflow()
    flow.request = http.Request.make(
        "POST", url, _BODY, {"Content-Type": "application/json", "Authorization": "Bearer sk-x"}
    )
    if tag is not None:
        flow.request.headers["X-CLM-Cassette"] = tag
    return flow


def _record(addon: ClmReplayAddon, flow: http.HTTPFlow, status: int, body: bytes) -> None:
    """Drive one recording-mode round trip: request() forwards, response() persists."""
    addon.request(flow)
    assert flow.response is None, "a recording-mode miss must be forwarded upstream"
    flow.response = http.Response.make(status, body, {"Content-Type": "application/json"})
    addon.response(flow)


def _seed_catch_all(catch_all: Path, status: int = 429) -> None:
    """Record one untagged interaction into the catch-all, like a local
    ``new-episodes`` build of a deck whose client is not tag-routed."""
    body = json.dumps({"error": {"code": "credit_balance_exhausted"}}).encode()
    _record(_addon(MODE_NEW_EPISODES, catch_all), _flow(), status, body)
    assert catch_all.exists()
    assert len(cf.load_interactions(catch_all)) == 1


def _assert_untagged_refusal(flow: http.HTTPFlow) -> dict:
    assert flow.response is not None, "untagged strict-replay flow must not be forwarded"
    assert flow.response.status_code == 404, "refusal must be non-retryable (no 5xx)"
    payload = json.loads(flow.response.content)
    assert payload["clm_replay_miss"] is True
    assert payload["error"]["type"] == "clm_replay_miss"
    assert payload["error"]["code"] == 404  # int: openrouter SDK parses it strictly (#909)
    message = payload["error"]["message"]
    assert "X-CLM-Cassette" in message
    assert "httpx2" in message  # names what IS covered, so the fix is findable
    assert payload["url"] == _URL
    return payload


def test_replay_refuses_untagged_flow_even_when_catch_all_has_a_match(tmp_path: Path) -> None:
    """Regression test for #1055: strict replay must not serve an untagged
    request from the machine-local catch-all (here: a recorded 429)."""
    catch_all = tmp_path / "mitm" / "transport.http-cassette.yaml"
    _seed_catch_all(catch_all)

    flow = _flow()
    _addon(MODE_REPLAY, catch_all).request(flow)

    payload = _assert_untagged_refusal(flow)
    assert payload["untagged"] is True


def test_replay_refuses_untagged_flow_with_empty_catch_all(tmp_path: Path) -> None:
    """The CI shape (no catch-all on disk): still a clean untagged refusal,
    not a generic "no recorded interaction" miss naming the scratch file."""
    catch_all = tmp_path / "mitm" / "transport.http-cassette.yaml"
    flow = _flow()
    _addon(MODE_REPLAY, catch_all).request(flow)
    _assert_untagged_refusal(flow)


@pytest.mark.parametrize("seeded", [True, False], ids=["catch-all-present", "catch-all-absent"])
def test_once_refuses_untagged_flow_regardless_of_catch_all(tmp_path: Path, seeded: bool) -> None:
    """``once`` fails loudly on requests that cannot be recorded to a topic.
    The decision must come from the mode alone: the catch-all persists across
    builds and courses, so keying on its existence made a second ``once``
    build refuse what the first one allowed."""
    catch_all = tmp_path / "mitm" / "transport.http-cassette.yaml"
    if seeded:
        _seed_catch_all(catch_all)

    flow = _flow()
    _addon(MODE_ONCE, catch_all).request(flow)
    _assert_untagged_refusal(flow)


def test_replay_refuses_untagged_flow_without_a_catch_all_configured(tmp_path: Path) -> None:
    """With no catch-all at all (``clm_cassette_path`` unset) an untagged flow
    used to take the passthrough branch and go to the real server, even in
    strict replay."""
    addon = _addon(MODE_REPLAY, tmp_path / "unused.yaml")
    addon._default_cassette = None
    flow = _flow()
    addon.request(flow)
    _assert_untagged_refusal(flow)


def test_replay_refuses_blank_tag_like_a_missing_one(tmp_path: Path) -> None:
    """Routing treats an empty ``X-CLM-Cassette`` as untagged (catch-all), so
    the refusal must too, or a blank tag would be served from the catch-all."""
    catch_all = tmp_path / "mitm" / "transport.http-cassette.yaml"
    _seed_catch_all(catch_all)
    flow = _flow(tag="")
    _addon(MODE_REPLAY, catch_all).request(flow)
    _assert_untagged_refusal(flow)


def test_replay_refusal_does_not_load_the_catch_all(tmp_path: Path) -> None:
    """The catch-all only grows; a strict refusal must not parse it first."""
    catch_all = tmp_path / "mitm" / "transport.http-cassette.yaml"
    _seed_catch_all(catch_all)
    addon = _addon(MODE_REPLAY, catch_all)
    addon.request(_flow())
    assert "" not in addon._targets


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(MODE_REPLAY, "refuses it"), (MODE_NEW_EPISODES, "forwarded to the real server")],
)
def test_untagged_warning_describes_what_happens_in_this_mode(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, mode: str, expected: str
) -> None:
    catch_all = tmp_path / "mitm" / "transport.http-cassette.yaml"
    addon = _addon(mode, catch_all)
    with caplog.at_level(logging.WARNING, logger="clm.http_replay_mitm.addon"):
        addon.request(_flow())
        addon.request(_flow())
    warnings = [r.getMessage() for r in caplog.records if UNTAGGED_FLOW_SENTINEL in r.getMessage()]
    assert len(warnings) == 1, warnings  # once per build
    assert expected in warnings[0]
    assert _URL in warnings[0]


def test_refusal_is_not_recorded(tmp_path: Path) -> None:
    """The synthetic refusal never went upstream, so response() must skip it."""
    catch_all = tmp_path / "mitm" / "transport.http-cassette.yaml"
    _seed_catch_all(catch_all)
    before = catch_all.read_bytes()

    addon = _addon(MODE_REPLAY, catch_all)
    flow = _flow()
    addon.request(flow)
    addon.response(flow)

    assert catch_all.read_bytes() == before


def test_new_episodes_never_serves_untagged_flow_from_catch_all(tmp_path: Path) -> None:
    """The catch-all is record-only in recording modes too: a local
    ``new-episodes`` build used to replay a recorded 429 from it on every
    later build, indefinitely. Now it forwards and records instead."""
    catch_all = tmp_path / "mitm" / "transport.http-cassette.yaml"
    _seed_catch_all(catch_all)  # a recorded 429

    addon = _addon(MODE_NEW_EPISODES, catch_all)
    flow = _flow()
    addon.request(flow)
    assert flow.response is None, "untagged flow must be forwarded, not served"

    flow.response = http.Response.make(200, b'{"ok": true}', {"Content-Type": "application/json"})
    addon.response(flow)
    statuses = [resp["status"]["code"] for _req, resp in cf.load_interactions(catch_all)]
    assert statuses == [429, 200], "the forwarded reply is still recorded (forensics)"


def test_replay_still_serves_tagged_flow_from_its_cassette(tmp_path: Path) -> None:
    """Tagged traffic is unaffected: replay serves it from the topic cassette."""
    catch_all = tmp_path / "mitm" / "transport.http-cassette.yaml"
    canonical = tmp_path / "topic" / ".clm" / "cassettes" / "slides.http-cassette.yaml"
    canonical.parent.mkdir(parents=True)

    recorder = _addon(MODE_NEW_EPISODES, catch_all)
    _record(recorder, _flow(tag=str(canonical)), 200, b'{"choices": []}')
    # Fold the staging file into the canonical, as the host does after a build.
    staging = next(canonical.parent.glob(f"{canonical.name}.staging-mitm-*"))
    cf.write_cassette(canonical, cf.load_interactions(staging))

    flow = _flow(tag=str(canonical))
    _addon(MODE_REPLAY, catch_all).request(flow)
    assert flow.response is not None
    assert flow.response.status_code == 200
    assert json.loads(flow.response.content) == {"choices": []}


def test_named_tagged_clients_match_the_kernel_bootstrap() -> None:
    """The warning/refusal names the covered clients; that list must be the set
    the bootstrap actually patches, or the message points authors at the wrong
    fix."""
    from clm.infrastructure.http_replay_mitm.addon import _TAGGED_CLIENTS
    from clm.workers.notebook.notebook_processor import _HTTP_REPLAY_TAG_BOOTSTRAP_TEMPLATE

    named = set(_TAGGED_CLIENTS.replace(" and ", ", ").split(", "))
    assert named == {"httpx", "httpx2", "requests", "aiohttp"}
    src = _HTTP_REPLAY_TAG_BOOTSTRAP_TEMPLATE
    assert '("httpx", "httpx2")' in src
    assert "import requests as _clm_requests" in src
    assert "import aiohttp as _clm_aiohttp" in src


def test_replay_ignored_host_still_passes_through_untagged(tmp_path: Path) -> None:
    """ignore_hosts (LangSmith telemetry) is forwarded untouched in every mode;
    the untagged refusal must not swallow it."""
    catch_all = tmp_path / "mitm" / "transport.http-cassette.yaml"
    flow = _flow(url="https://api.smith.langchain.com/runs/batch")
    _addon(MODE_REPLAY, catch_all).request(flow)
    assert flow.response is None
