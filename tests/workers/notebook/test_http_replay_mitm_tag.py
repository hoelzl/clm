"""P2 (issue #165): the mitmproxy cassette-routing tag bootstrap.

Under ``CLM_HTTP_REPLAY_TRANSPORT=mitmproxy`` the worker injects a tiny
cell that tags every outgoing httpx request with its destination cassette
(so the single shared proxy demuxes correctly) instead of the heavy
in-kernel vcrpy bootstrap. These tests pin that injection + the tag
resolution and confirm the kernel's httpcore is never patched.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from nbformat.v4 import new_code_cell, new_notebook

from clm.workers.notebook.notebook_processor import (
    _HTTP_REPLAY_BOOTSTRAP_MARKER,
    _HTTP_REPLAY_SOCKET_TRACE_TEMPLATE,
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


def test_tag_bootstrap_without_trace_dir_omits_socket_trace():
    nb = new_notebook(cells=[new_code_cell("print(1)")])
    _inject_http_replay_tag_bootstrap(nb, "/x/foo.http-cassette.yaml")
    src = nb["cells"][0]["source"]
    assert "x-clm-cassette" in src  # still the tag bootstrap
    assert "SOCKET TRACE" not in src
    assert "addaudithook" not in src


def test_tag_bootstrap_with_trace_dir_appends_socket_trace(tmp_path):
    """Issue #165 P5: under the transport the kernel's socket ground-truth
    stream must be installed by the tag bootstrap (the vcr trace template
    cannot run — vcr is never imported)."""
    nb = new_notebook(cells=[new_code_cell("print(1)")])
    _inject_http_replay_tag_bootstrap(nb, "/x/foo.http-cassette.yaml", trace_dir=str(tmp_path))
    src = nb["cells"][0]["source"]
    # Tag bootstrap is still present...
    assert "x-clm-cassette" in src
    # ...plus the self-contained socket trace (audit hook, worker file)...
    assert "addaudithook" in src
    assert "socket.connect" in src
    assert "worker-" in src
    # ...and it must NOT pull in the heavy vcr trace machinery (vcr is never
    # imported under the transport, so those symbols would NameError).
    assert "import vcr" not in src
    assert "force_reset" not in src
    assert "_clm_vcr_patch" not in src
    assert "play_response" not in src


def test_socket_trace_template_execs_standalone_and_emits(tmp_path):
    """The socket trace must run with NONE of the vcrpy-bootstrap symbols
    defined (it is self-contained). Exec it in a clean subprocess and confirm
    it writes a worker JSONL with the socket bootstrap.complete event."""
    rendered = _HTTP_REPLAY_SOCKET_TRACE_TEMPLATE.format(trace_dir=str(tmp_path))
    script = rendered + "\n_clm_strace_close()\n"
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    files = list(tmp_path.glob("worker-*.jsonl"))
    assert len(files) == 1, files
    records = [json.loads(line) for line in files[0].read_text().splitlines() if line]
    streams_events = {(r["stream"], r["event"]) for r in records}
    assert ("socket", "bootstrap.complete") in streams_events
    assert all(r["stream"] == "socket" for r in records)


def test_maybe_inject_under_transport_injects_socket_trace_when_traced(monkeypatch, tmp_path):
    monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
    proc = NotebookProcessor(CompletedOutput(format="code"))
    nb = new_notebook(cells=[new_code_cell("print(1)")])
    payload = _payload(source_topic_dir=str(tmp_path), http_replay_trace_dir=str(tmp_path))

    injected = proc._maybe_inject_http_replay(nb, payload, None, tmp_path)

    assert injected is True
    src = nb["cells"][0]["source"]
    assert "x-clm-cassette" in src
    assert "addaudithook" in src  # socket trace wired through the payload field
    assert "import vcr" not in src


def test_resolve_mitmproxy_tag_uses_payload_cassette_name(monkeypatch, tmp_path):
    monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
    proc = NotebookProcessor(CompletedOutput(format="code"))
    payload = _payload(source_topic_dir=str(tmp_path))

    tag = proc._resolve_mitmproxy_tag(payload, None)

    assert tag == str(tmp_path / "_cassettes" / "slides.http-cassette.yaml")


def test_resolve_mitmproxy_tag_uses_host_topic_dir_not_container_source_dir(monkeypatch, tmp_path):
    """The tag must name a HOST path even in Docker mode (issue #165 P4).

    The proxy and the host-side merge run on the host, so a container-mapped
    ``source_dir`` (``/source/...``) would make the proxy write staging to a
    bogus host path and the merge would never find it. The host
    ``source_topic_dir`` therefore wins over the container ``source_dir``.
    """
    monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
    proc = NotebookProcessor(CompletedOutput(format="code"))
    host_dir = tmp_path / "host_topic"
    container_dir = Path("/source/topic")  # what a Docker worker would pass
    payload = _payload(source_topic_dir=str(host_dir))

    tag = proc._resolve_mitmproxy_tag(payload, container_dir)

    # Resolved against the host dir, NOT the container /source path.
    assert tag == str(host_dir / "_cassettes" / "slides.http-cassette.yaml")


def test_resolve_mitmproxy_tag_falls_back_to_source_dir_when_no_host_dir(monkeypatch, tmp_path):
    """If no host ``source_topic_dir`` is available, fall back to ``source_dir``."""
    monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
    proc = NotebookProcessor(CompletedOutput(format="code"))
    payload = _payload(source_topic_dir=None)

    tag = proc._resolve_mitmproxy_tag(payload, tmp_path)

    assert tag == str(tmp_path / "_cassettes" / "slides.http-cassette.yaml")


def test_resolve_mitmproxy_tag_none_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
    proc = NotebookProcessor(CompletedOutput(format="code"))
    assert proc._resolve_mitmproxy_tag(_payload(http_replay_mode="disabled"), tmp_path) is None
    assert proc._resolve_mitmproxy_tag(_payload(http_replay_mode=None), tmp_path) is None
    assert proc._resolve_mitmproxy_tag(_payload(http_replay_cassette_name=None), tmp_path) is None


def test_resolve_mitmproxy_tag_none_when_no_dir_available(monkeypatch):
    """No host source_topic_dir and no container source_dir -> no tag."""
    monkeypatch.setenv("CLM_HTTP_REPLAY_TRANSPORT", "mitmproxy")
    proc = NotebookProcessor(CompletedOutput(format="code"))
    assert proc._resolve_mitmproxy_tag(_payload(source_topic_dir=None), None) is None


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
