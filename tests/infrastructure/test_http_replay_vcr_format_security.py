"""Security regression tests for cassette deserialization.

Cassettes are tracked files in course repositories (``.gitignore`` explicitly
un-ignores ``.clm/cassettes/``), so they arrive through pull requests and are
parsed host-side by ``clm build``. Before the fix these tests cover, the
deserializer used PyYAML's ``CLoader``, whose constructor chain reaches
``UnsafeConstructor`` *before* ``SafeConstructor`` — so a one-line cassette edit
executed arbitrary code as the user running the build, during parsing and before
any schema validation could reject the document.
"""

import sys
from pathlib import Path

import pytest
import yaml

from clm.infrastructure.http_replay_mitm.vcr_format import (
    _Loader,
    deserialize_cassette,
    load_cassette,
)

# Written by the payload if it ever executes. A module attribute rather than a
# file so the check cannot be confused by leftover state from another test.
sys.modules[__name__].EXPLOIT_MARKER = None  # type: ignore[attr-defined]


def _payload(marker_expr: str) -> str:
    """A cassette whose parsing would run *marker_expr* under an unsafe loader."""
    return f"version: 1\ninteractions:\n  - !!python/object/apply:eval ['{marker_expr}']\n"


def test_loader_is_a_safe_loader():
    """The loader must not be one that resolves python object tags.

    Asserted structurally as well as behaviourally: a future edit that swaps the
    loader back would fail here with a message that says why, rather than only
    tripping the subtler execution test below.
    """
    assert _Loader in (yaml.CSafeLoader, yaml.SafeLoader), (
        f"{_Loader.__name__} is not a safe loader. Cassettes are untrusted "
        "input; an unsafe loader makes parsing them arbitrary code execution."
    )
    assert not issubclass(_Loader, yaml.constructor.UnsafeConstructor)


def test_python_object_tag_does_not_execute(monkeypatch):
    """The exploit must not run — not merely fail after running.

    A loader that executed the payload and *then* raised would still satisfy a
    naive ``pytest.raises`` check, so the marker assertion is the real test.
    """
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "EXPLOIT_MARKER", None, raising=False)

    cassette = _payload(
        "__import__('sys').modules['"
        + __name__
        + "'].__dict__.__setitem__('EXPLOIT_MARKER', 'pwned')"
    )

    with pytest.raises(yaml.YAMLError):
        deserialize_cassette(cassette)

    assert getattr(module, "EXPLOIT_MARKER") is None, (
        "The cassette payload executed during parsing — the loader is unsafe."
    )


def test_python_object_tag_does_not_execute_via_load_cassette(monkeypatch, tmp_path: Path):
    """Same guarantee through the public file-loading entry point.

    ``load_cassette`` is what ``clm build`` actually calls (via
    ``Course.merge_mitmproxy_cassette_staging``), so the guarantee has to hold
    here and not only on the string-level helper.
    """
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "EXPLOIT_MARKER", None, raising=False)

    cassette_path = tmp_path / "topic.en.http-cassette.yaml"
    cassette_path.write_text(
        _payload(
            "__import__('sys').modules['"
            + __name__
            + "'].__dict__.__setitem__('EXPLOIT_MARKER', 'pwned')"
        ),
        encoding="utf-8",
    )

    with pytest.raises(yaml.YAMLError):
        load_cassette(cassette_path)

    assert getattr(module, "EXPLOIT_MARKER") is None


def test_python_name_tag_is_rejected():
    """``!!python/name:`` resolves a dotted name without calling it.

    Distinct enough from ``object/apply`` to be worth pinning separately: a
    partially-safe loader could reject one and accept the other.
    """
    cassette = "version: 1\ninteractions: !!python/name:os.system\n"
    with pytest.raises(yaml.YAMLError):
        deserialize_cassette(cassette)


def test_plain_cassette_still_round_trips():
    """The safe loader must still read everything the format actually uses.

    The v1 format is scalars, maps, sequences and ``!!binary`` — all supported
    by SafeLoader — but this pins it so the fix cannot have narrowed what CLM
    can read.
    """
    from clm.infrastructure.http_replay_mitm.vcr_format import serialize_cassette

    body = b"\x00\x01\x02 binary body \xff"
    requests, responses = deserialize_cassette(
        serialize_cassette(
            {
                "requests": [
                    _FakeRequest(
                        {
                            "method": "GET",
                            "uri": "https://api.example.com/v1/thing?a=1",
                            "body": None,
                            "headers": {"accept": ["application/json"]},
                        }
                    )
                ],
                "responses": [
                    {
                        "status": {"code": 200, "message": "OK"},
                        "headers": {"content-type": ["application/octet-stream"]},
                        "body": {"string": body},
                    }
                ],
            }
        )
    )

    assert len(requests) == 1
    assert requests[0].uri == "https://api.example.com/v1/thing?a=1"
    assert responses[0]["body"]["string"] == body


class _FakeRequest:
    """Minimal stand-in exposing the ``_to_dict`` hook the serializer calls."""

    def __init__(self, data: dict):
        self._data = data

    def _to_dict(self) -> dict:
        return self._data
