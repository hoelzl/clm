"""Regression tests for #1309: kernels of non-opted-in topics stay off the replay proxy.

While a build runs the replay proxy, the host (Direct) or container (Docker)
environment points every HTTP client at it. Only notebooks of topics that
opted into ``http-replay`` get the cassette-routing tag bootstrap, so before
#1309 any other notebook's kernel reached the proxy untagged: recorded into the
machine-local catch-all in recording modes, refused with a ``clm_replay_miss``
404 in strict ones. Such a kernel must now be launched with the proxy env
restored to what it was before the proxy was set up.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from nbclient import NotebookClient
from nbformat import NotebookNode

from clm.build.engine import _export_mitmproxy_env, _restore_env, _snapshot_mitmproxy_env
from clm.core.messaging.notebook_classes import NotebookPayload
from clm.infrastructure.http_replay_mitm.proxy_env import (
    HOST_PROXY_ENV_KEYS,
    PREPROXY_ENV_VAR,
    preproxy_kernel_env,
)
from clm.infrastructure.workers.worker_executor import (
    _docker_config_proxy_env,
    _mitmproxy_docker_env,
)
from clm.workers.notebook.notebook_processor import (
    NotebookProcessor,
    TrackingExecutePreprocessor,
)
from clm.workers.notebook.output_spec import SpeakerOutput

PROXY = "http://127.0.0.1:63564"
BUNDLE = Path("/mitm/ca-bundle.pem")


def _proxied_host_env(**before: str) -> dict[str, str]:
    """A host env as ``_maybe_start_mitmproxy_transport`` leaves it."""
    env = {"PATH": "/usr/bin", **before}
    _export_mitmproxy_env(env, PROXY, BUNDLE)
    return env


# --------------------------------------------------------------------------
# The marker and its inverse
# --------------------------------------------------------------------------


def test_no_marker_means_inherit() -> None:
    """Without an active replay proxy the kernel simply inherits."""
    assert preproxy_kernel_env({"HTTPS_PROXY": "http://corp:3128"}) is None


def test_export_records_originals_and_sets_proxy() -> None:
    env = _proxied_host_env(HTTPS_PROXY="http://corp:3128")
    assert env["HTTPS_PROXY"] == PROXY
    assert env["SSL_CERT_FILE"] == str(BUNDLE)
    originals = json.loads(env[PREPROXY_ENV_VAR])
    assert set(originals) == set(HOST_PROXY_ENV_KEYS)
    assert originals["HTTPS_PROXY"] == "http://corp:3128"
    assert originals["HTTP_PROXY"] is None


def test_export_overrides_exactly_the_recorded_keys() -> None:
    """Every variable the export overrides must be recorded, or the kernel
    restore would leave a proxy setting behind."""
    env: dict[str, str] = {}
    _export_mitmproxy_env(env, PROXY, BUNDLE)
    assert set(env) - {PREPROXY_ENV_VAR} == set(HOST_PROXY_ENV_KEYS)


def test_kernel_env_removes_proxy_vars_that_were_unset() -> None:
    kernel_env = preproxy_kernel_env(_proxied_host_env())
    assert kernel_env is not None
    for key in HOST_PROXY_ENV_KEYS:
        assert key not in kernel_env
    assert PREPROXY_ENV_VAR not in kernel_env
    assert kernel_env["PATH"] == "/usr/bin"


def test_kernel_env_restores_a_users_own_proxy() -> None:
    """A corporate proxy set before the build comes back for the kernel."""
    kernel_env = preproxy_kernel_env(
        _proxied_host_env(HTTPS_PROXY="http://corp:3128", SSL_CERT_FILE="/corp/ca.pem")
    )
    assert kernel_env is not None
    assert kernel_env["HTTPS_PROXY"] == "http://corp:3128"
    assert kernel_env["SSL_CERT_FILE"] == "/corp/ca.pem"
    assert "HTTP_PROXY" not in kernel_env


def test_second_in_process_build_keeps_the_first_originals() -> None:
    """Nothing restores ``os.environ`` after a build, so a second build in the
    same process must not record the first build's proxy as "original"."""
    env = _proxied_host_env(HTTPS_PROXY="http://corp:3128")
    _export_mitmproxy_env(env, "http://127.0.0.1:1", BUNDLE)
    kernel_env = preproxy_kernel_env(env)
    assert kernel_env is not None
    assert kernel_env["HTTPS_PROXY"] == "http://corp:3128"


@pytest.mark.parametrize("raw", ["not json", "[1, 2]"])
def test_malformed_marker_fails_safe_by_removing_the_proxy(raw: str) -> None:
    env = {**_proxied_host_env(), PREPROXY_ENV_VAR: raw}
    kernel_env = preproxy_kernel_env(env)
    assert kernel_env is not None
    assert "HTTPS_PROXY" not in kernel_env
    assert "SSL_CERT_FILE" not in kernel_env


def test_docker_container_env_restores_to_no_proxy_at_all(tmp_path: Path) -> None:
    """Docker: the container had no proxy env before ``_mitmproxy_docker_env``
    added it, so a non-opted-in kernel gets none of it back — including the
    container's NO_PROXY — while the routing variable stays."""
    ca = tmp_path / "ca-bundle.pem"
    ca.write_bytes(b"-----BEGIN CERTIFICATE-----\n")
    container_env, _mount = _mitmproxy_docker_env(
        {
            "CLM_HTTP_REPLAY_TRANSPORT": "mitmproxy",
            "HTTPS_PROXY": PROXY,
            "SSL_CERT_FILE": str(ca),
        }
    )
    assert PREPROXY_ENV_VAR in container_env
    kernel_env = preproxy_kernel_env({"PATH": "/usr/bin", **container_env})
    assert kernel_env == {"PATH": "/usr/bin", "CLM_HTTP_REPLAY_TRANSPORT": "mitmproxy"}


def test_docker_container_env_restores_the_docker_config_proxy(tmp_path: Path) -> None:
    """docker-py injects the ``~/.docker/config.json`` proxy into every
    container (``use_config_proxy=True``); a non-opted-in kernel gets it back."""
    docker_proxy = {
        "HTTPS_PROXY": "http://corp:3128",
        "https_proxy": "http://corp:3128",
        "NO_PROXY": "intranet",
        "no_proxy": "intranet",
    }
    container_env, _mount = _mitmproxy_docker_env(
        {"CLM_HTTP_REPLAY_TRANSPORT": "mitmproxy", "HTTPS_PROXY": PROXY}, docker_proxy
    )
    kernel_env = preproxy_kernel_env(container_env)
    assert kernel_env is not None
    assert kernel_env["HTTPS_PROXY"] == "http://corp:3128"
    assert kernel_env["NO_PROXY"] == "intranet"
    assert "HTTP_PROXY" not in kernel_env


def test_docker_config_proxy_env_reads_docker_py_and_degrades() -> None:
    client = MagicMock()
    client.api._proxy_configs.get_environment.return_value = {"HTTP_PROXY": "http://c:1"}
    assert _docker_config_proxy_env(client) == {"HTTP_PROXY": "http://c:1"}
    broken = MagicMock()
    broken.api._proxy_configs.get_environment.side_effect = AttributeError("gone")
    assert _docker_config_proxy_env(broken) == {}


def test_build_teardown_restores_the_host_env() -> None:
    """After the proxy stops, the host env is exactly as before the build: no
    dead proxy and no stale marker for a later in-process build."""
    env = {"PATH": "/usr/bin", "HTTPS_PROXY": "http://corp:3128"}
    before = dict(env)
    snapshot = _snapshot_mitmproxy_env(env)
    _export_mitmproxy_env(env, PROXY, BUNDLE)
    _restore_env(env, snapshot)
    assert env == before


# --------------------------------------------------------------------------
# The preprocessor passes the env to the kernel launch
# --------------------------------------------------------------------------


def _capture_kernel_start(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    async def fake_start(self, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(NotebookClient, "async_start_new_kernel", fake_start)
    return calls


def test_sync_kernel_start_passes_kernel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``setup_kernel`` (the path ``preprocess`` takes) calls the *sync*
    ``start_new_kernel``, which nbclient binds to the base coroutine at class
    creation — it must carry the env too, not just the async entry point."""
    calls: list[dict] = []
    monkeypatch.setattr(NotebookClient, "start_new_kernel", lambda self, **kw: calls.append(kw))
    ep = TrackingExecutePreprocessor(MagicMock(), kernel_env={"A": "1"})
    ep.start_new_kernel()
    assert calls == [{"env": {"A": "1"}}]


@pytest.mark.asyncio
async def test_async_kernel_start_passes_kernel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_kernel_start(monkeypatch)
    ep = TrackingExecutePreprocessor(MagicMock(), kernel_env={"A": "1"})
    await ep.async_start_new_kernel()
    assert calls == [{"env": {"A": "1"}}]


@pytest.mark.asyncio
async def test_kernel_start_without_kernel_env_inherits(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_kernel_start(monkeypatch)
    ep = TrackingExecutePreprocessor(MagicMock())
    await ep.async_start_new_kernel()
    assert calls == [{}]


# --------------------------------------------------------------------------
# The worker chooses the env per notebook
# --------------------------------------------------------------------------


def _notebook() -> NotebookNode:
    return NotebookNode(
        {
            "cells": [
                NotebookNode(
                    {
                        "id": "c1",
                        "cell_type": "code",
                        "source": "x = 1",
                        "metadata": {"tags": []},
                        "outputs": [],
                        "execution_count": None,
                    }
                )
            ],
            "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )


def _payload(tmp_path: Path, **http_replay) -> NotebookPayload:
    return NotebookPayload(
        input_file=str(tmp_path / "slides.py"),
        input_file_name="slides.py",
        output_file=str(tmp_path / "out" / "slides.html"),
        data="",
        format="html",
        kind="speaker",
        language="en",
        prog_lang="python",
        correlation_id="cid-1309",
        other_files={},
        **http_replay,
    )


async def _kernel_env_used(payload: NotebookPayload) -> object:
    """Run ``_create_using_nbconvert`` and return the ``kernel_env`` the
    preprocessor was constructed with."""
    processor = NotebookProcessor(SpeakerOutput(format="html"))
    nb = _notebook()
    with (
        patch("clm.workers.notebook.notebook_processor.TrackingExecutePreprocessor") as MockEP,
        patch("clm.workers.notebook.notebook_processor.HTMLExporter") as MockHTML,
    ):
        mock_ep = MagicMock()
        mock_ep.preprocess.return_value = (nb, {})
        mock_ep.km = None
        mock_ep.kc = None
        MockEP.return_value = mock_ep
        MockHTML.return_value.from_notebook_node.return_value = ("<html></html>", {})
        await processor._create_using_nbconvert(nb, payload)
    return MockEP.call_args.kwargs["kernel_env"]


@pytest.mark.asyncio
async def test_non_opted_in_notebook_kernel_runs_without_the_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for #1309: the issue's tiktoken deck lived in a topic
    without ``http-replay``; its kernel inherited the proxy and its download
    reached the proxy untagged."""
    for key, value in _proxied_host_env().items():
        monkeypatch.setenv(key, value)

    kernel_env = await _kernel_env_used(_payload(tmp_path))

    assert isinstance(kernel_env, dict)
    for key in HOST_PROXY_ENV_KEYS:
        assert key not in kernel_env, key
    assert kernel_env["PATH"] == os.environ["PATH"]


@pytest.mark.asyncio
async def test_opted_in_notebook_kernel_keeps_the_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in _proxied_host_env().items():
        monkeypatch.setenv(key, value)

    kernel_env = await _kernel_env_used(
        _payload(
            tmp_path,
            http_replay_mode="replay",
            http_replay_cassette_name="slides.http-cassette.yaml",
            source_topic_dir=str(tmp_path),
        )
    )

    assert kernel_env is None  # inherits the proxy env; the bootstrap tags it


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["replay", "not-a-known-mode"])
async def test_opted_in_notebook_without_a_tag_keeps_the_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """A ``replay`` build of an opted-in deck whose cassette is missing gets
    no cassette name, hence no tag. Its kernel must still use the proxy so
    strict replay refuses the request; restoring the pre-proxy env would let
    it reach the live API (with real keys and cost). An unknown mode fails
    closed the same way."""
    for key, value in _proxied_host_env().items():
        monkeypatch.setenv(key, value)

    kernel_env = await _kernel_env_used(
        _payload(tmp_path, http_replay_mode=mode, source_topic_dir=str(tmp_path))
    )

    assert kernel_env is None


@pytest.mark.asyncio
async def test_non_opted_in_kernel_env_keeps_the_pydevd_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker sets PYDEVD_DISABLE_FILE_VALIDATION just before execution;
    an explicit kernel env must be taken after that, or a fresh worker's
    first notebook would run without it."""
    for key, value in _proxied_host_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PYDEVD_DISABLE_FILE_VALIDATION", raising=False)

    kernel_env = await _kernel_env_used(_payload(tmp_path))

    assert isinstance(kernel_env, dict)
    assert kernel_env["PYDEVD_DISABLE_FILE_VALIDATION"] == "1"


@pytest.mark.asyncio
async def test_without_replay_proxy_every_kernel_inherits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(PREPROXY_ENV_VAR, raising=False)
    assert await _kernel_env_used(_payload(tmp_path)) is None


# --------------------------------------------------------------------------
# End to end with a real kernel
# --------------------------------------------------------------------------


# Spawns a REAL ipykernel subprocess (~5s): integration, like the other
# real-kernel tests in test_notebook_processor.py.
@pytest.mark.integration
def test_real_kernel_of_non_opted_in_notebook_sees_no_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real ``os.environ`` (case-insensitive on Windows, where
    ``HTTP_PROXY`` and ``http_proxy`` are one variable)."""
    for key in (*HOST_PROXY_ENV_KEYS, PREPROXY_ENV_VAR):
        monkeypatch.setenv(key, "x")  # register for restore, then clear
        monkeypatch.delenv(key)
    monkeypatch.setenv("HTTP_PROXY", "http://corp:3128")
    monkeypatch.setenv("http_proxy", "http://corp:3128")
    _export_mitmproxy_env(os.environ, PROXY, BUNDLE)
    assert os.environ["HTTPS_PROXY"] == PROXY

    out = tmp_path / "env.json"
    nb = _notebook()
    nb.cells[0]["source"] = (
        "import json, os\n"
        f"keys = {list(HOST_PROXY_ENV_KEYS)!r}\n"
        f"open({str(out)!r}, 'w').write(json.dumps({{k: os.environ.get(k) for k in keys}}))\n"
    )
    ep = TrackingExecutePreprocessor(
        NotebookProcessor(SpeakerOutput(format="html")),
        timeout=60,
        startup_timeout=60,
        kernel_env=preproxy_kernel_env(os.environ),
    )
    ep.preprocess(nb, resources={"metadata": {"path": str(tmp_path)}})

    seen = json.loads(out.read_text())
    user_proxy = {"HTTP_PROXY", "http_proxy"}
    assert {k: seen[k] for k in user_proxy} == dict.fromkeys(user_proxy, "http://corp:3128")
    assert {k: v for k, v in seen.items() if k not in user_proxy} == dict.fromkeys(
        set(HOST_PROXY_ENV_KEYS) - user_proxy
    )
