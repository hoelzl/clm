"""P2 (issue #165): the mitmproxy cassette-routing tag bootstrap.

Under ``CLM_HTTP_REPLAY_TRANSPORT=mitmproxy`` the worker injects a tiny
cell that tags every outgoing httpx request with its destination cassette
(so the single shared proxy demuxes correctly) instead of the heavy
in-kernel vcrpy bootstrap. These tests pin that injection + the tag
resolution and confirm the kernel's httpcore is never patched.
"""

from __future__ import annotations

from types import SimpleNamespace

from nbformat.v4 import new_code_cell, new_notebook

from clm.workers.notebook.notebook_processor import (
    _HTTP_REPLAY_BOOTSTRAP_MARKER,
    NotebookProcessor,
    _inject_http_replay_tag_bootstrap,
    _strip_injected_cells,
)
from clm.workers.notebook.output_spec import CompletedOutput


def _payload(**overrides):
    base = {
        "http_replay_mode": "new-episodes",
        "http_replay_cassette_name": "_cassettes/slides.http-cassette.yaml",
        "source_topic_dir": None,
        "correlation_id": "cid-1",
        "input_file_name": "slides.py",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_inject_tag_bootstrap_inserts_marked_cell():
    nb = new_notebook(cells=[new_code_cell("print(1)")])
    tag = "/src/topic/_cassettes/slides.http-cassette.yaml"

    _inject_http_replay_tag_bootstrap(nb, tag)

    assert len(nb["cells"]) == 2
    cell0 = nb["cells"][0]
    assert cell0["metadata"]["clm_injected"] == _HTTP_REPLAY_BOOTSTRAP_MARKER
    assert "del" in cell0["metadata"]["tags"]
    # The tag is embedded as a repr literal and the routing header is set.
    assert repr(tag) in cell0["source"]
    assert "x-clm-cassette" in cell0["source"]
    # It is the lightweight tag bootstrap, NOT the heavy vcrpy one: no
    # httpcore/vcr patching reaches the kernel.
    assert "httpcore" not in cell0["source"]
    assert "import vcr" not in cell0["source"]
    assert "httpx" in cell0["source"]


def test_strip_removes_tag_bootstrap_cell():
    nb = new_notebook(cells=[new_code_cell("print(1)")])
    _inject_http_replay_tag_bootstrap(nb, "/x/foo.http-cassette.yaml")
    _strip_injected_cells(nb)
    assert len(nb["cells"]) == 1
    assert nb["cells"][0]["source"] == "print(1)"


def test_resolve_mitmproxy_tag_uses_payload_cassette_name(monkeypatch, tmp_path):
    monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
    proc = NotebookProcessor(CompletedOutput(format="code"))
    payload = _payload(source_topic_dir=str(tmp_path))

    tag = proc._resolve_mitmproxy_tag(payload, None)

    assert tag == str(tmp_path / "_cassettes" / "slides.http-cassette.yaml")


def test_resolve_mitmproxy_tag_prefers_source_dir(monkeypatch, tmp_path):
    """Docker-style source mount (``source_dir``) wins over source_topic_dir."""
    monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
    proc = NotebookProcessor(CompletedOutput(format="code"))
    payload = _payload(source_topic_dir="/host/elsewhere")

    tag = proc._resolve_mitmproxy_tag(payload, tmp_path)

    assert tag == str(tmp_path / "_cassettes" / "slides.http-cassette.yaml")


def test_resolve_mitmproxy_tag_none_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
    proc = NotebookProcessor(CompletedOutput(format="code"))
    assert proc._resolve_mitmproxy_tag(_payload(http_replay_mode="disabled"), tmp_path) is None
    assert proc._resolve_mitmproxy_tag(_payload(http_replay_mode=None), tmp_path) is None
    assert proc._resolve_mitmproxy_tag(_payload(http_replay_cassette_name=None), tmp_path) is None


def test_maybe_inject_chooses_tag_bootstrap_under_transport(monkeypatch, tmp_path):
    monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
    proc = NotebookProcessor(CompletedOutput(format="code"))
    nb = new_notebook(cells=[new_code_cell("print(1)")])
    payload = _payload(source_topic_dir=str(tmp_path))

    # ``paths`` is None under the transport (vcrpy staging is skipped), but
    # the tag bootstrap still gets injected.
    injected = proc._maybe_inject_http_replay(nb, payload, None, tmp_path)

    assert injected is True
    assert len(nb["cells"]) == 2
    assert "x-clm-cassette" in nb["cells"][0]["source"]
    assert "import vcr" not in nb["cells"][0]["source"]


def test_maybe_inject_noop_without_transport_and_no_paths(monkeypatch):
    monkeypatch.delenv("CLM_HTTP_REPLAY_TRANSPORT", raising=False)
    proc = NotebookProcessor(CompletedOutput(format="code"))
    nb = new_notebook(cells=[new_code_cell("print(1)")])

    injected = proc._maybe_inject_http_replay(nb, _payload(), None, None)

    assert injected is False
    assert len(nb["cells"]) == 1
