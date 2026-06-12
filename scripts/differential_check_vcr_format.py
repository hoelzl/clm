"""Differential validation: CLM's vcr_format vs real vcrpy.

One-time gate for issue #355 stage 2 (vcrpy dependency removal), kept so the
vendored implementation can be re-validated against vcrpy at any time:

    uv run --with vcrpy python scripts/differential_check_vcr_format.py

Compares serialization bytes, load round-trips, filters, decode_response,
and matchers over a diverse case set. Last run green against vcrpy 8.1.1
(plus a byte-identical round-trip of all 2072 committed course cassettes).
"""

import sys

from vcr import matchers as vcr_matchers
from vcr.filters import decode_response as vcr_decode_response
from vcr.filters import replace_headers as vcr_replace_headers
from vcr.filters import replace_post_data_parameters as vcr_replace_post
from vcr.filters import replace_query_parameters as vcr_replace_query
from vcr.persisters.filesystem import FilesystemPersister
from vcr.request import Request as VcrRequest
from vcr.serialize import serialize as vcr_serialize
from vcr.serializers import yamlserializer

from clm.infrastructure.http_replay_mitm import vcr_format as vf

failures: list[str] = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ok: {label}")
    else:
        failures.append(label)
        print(f"  FAIL: {label} {detail}")


import gzip as _gzip
import zlib as _zlib

CASES = [
    # (method, uri, body, headers, status, message, resp_headers, resp_body)
    (
        "GET",
        "https://restcountries.com/v3.1/name/germany",
        None,
        {"accept": "*/*", "user-agent": "python-requests/2.32"},
        200,
        "OK",
        {"content-type": ["application/json"]},
        b'[{"name":"Germany"}]',
    ),
    (
        "POST",
        "https://openrouter.ai/api/v1/chat/completions?api_key=SECRET&x=1",
        b'{"messages":[{"role":"user","content":"hi"}],"stream":false,"api_key":"S"}',
        {
            "content-type": "application/json",
            "authorization": "Bearer sk-XYZ",
            "x-api-key": "k",
            "cookie": "session=1",
        },
        200,
        None,
        {"content-type": ["application/json"], "set-cookie": ["a=1", "b=2"]},
        '{"choices":[{"text":"hällo wörld"}]}'.encode(),
    ),
    (
        "POST",
        "http://example.com/form",
        b"password=p&token=t&keep=1",
        {"content-type": "application/x-www-form-urlencoded"},
        201,
        "Created",
        {"x-many": ["v1", "v2", "v3"]},
        b"",
    ),
    (
        "GET",
        "http://example.com:8080/binary",
        None,
        {},
        200,
        "OK",
        {"content-type": ["application/octet-stream"]},
        bytes(range(256)),
    ),
    (
        "GET",
        "https://example.com/unicode",
        "ünïcode-body".encode(),
        {},
        200,
        "OK",
        {},
        "ünïcode response ✓ ☃".encode(),
    ),
    ("DELETE", "https://example.com/empty", b"", {}, 204, "No Content", {}, b""),
    (
        "GET",
        "https://example.com/gzip",
        None,
        {},
        200,
        "OK",
        {"content-encoding": ["gzip"], "content-type": ["text/plain"]},
        _gzip.compress(b"hello compressed world" * 10),
    ),
    (
        "GET",
        "https://example.com/deflate",
        None,
        {},
        200,
        "OK",
        {"content-encoding": ["deflate"]},
        _zlib.compress(b"deflated body"),
    ),
    (
        "GET",
        "https://example.com/crlf",
        None,
        {},
        200,
        "OK",
        {"content-type": ["text/plain"]},
        b"line1\r\nline2\nline3",
    ),
]

print("== build interactions through cassette_format (new vf-backed path) ==")
from clm.infrastructure.http_replay_mitm import cassette_format as cf

interactions_new = []
interactions_old = []
for method, uri, body, headers, status, message, resp_headers, resp_body in CASES:
    header_fields = list(headers.items())
    resp_fields = [(k, v) for k, vals in resp_headers.items() for v in vals]
    # New path (vf Request)
    req_new = cf.vcr_request_from_parts(method, uri, header_fields, body or b"")
    resp_new = cf.vcr_response_dict_from_parts(status, message, resp_fields, resp_body)
    interactions_new.append((req_new, resp_new))
    # Old path: identical construction but with the real vcr Request + filters
    grouped: dict = {}
    for name, value in header_fields:
        grouped.setdefault(name, []).append(value)
    joined = {name: ", ".join(vals) for name, vals in grouped.items()}
    req_old = VcrRequest(method, uri, body or b"", joined)
    resp_dict = {
        "status": {"code": status, "message": message if message else None},
        "headers": {k: list(v) for k, v in resp_headers.items()},
        "body": {"string": resp_body},
    }
    resp_old = vcr_decode_response(resp_dict)
    interactions_old.append((req_old, resp_old))

print("== response dict equality (decode_response parity) ==")
for i, ((_, rn), (_, ro)) in enumerate(zip(interactions_new, interactions_old, strict=True)):
    check(f"response dict case {i}", rn == ro, f"\n    new={rn}\n    old={ro}")

print("== serialization byte identity ==")
import copy

old_payload = vcr_serialize(
    copy.deepcopy(
        {
            "requests": [r for r, _ in interactions_old],
            "responses": [r for _, r in interactions_old],
        }
    ),
    yamlserializer,
)
new_payload = cf.serialize_interactions(interactions_new)
check("serialized YAML byte-identical", old_payload == new_payload)
if old_payload != new_payload:
    import difflib

    diff = list(
        difflib.unified_diff(old_payload.splitlines(), new_payload.splitlines(), lineterm="")
    )
    print("\n".join(diff[:40]))

print("== load round-trip ==")
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "c.yaml"
    p.write_text(new_payload, encoding="utf-8", newline="\n")
    reqs_old, resps_old = FilesystemPersister.load_cassette(p, serializer=yamlserializer)
    reqs_new, resps_new = vf.load_cassette(p)
    check("load: same count", len(reqs_old) == len(reqs_new) == len(CASES))
    for i, (ro, rn) in enumerate(zip(reqs_old, reqs_new, strict=True)):
        check(
            f"load req {i} fields",
            ro.method == rn.method
            and ro.uri == rn.uri
            and ro.body == rn.body
            and dict(ro.headers.items()) == dict(rn.headers.items()),
        )
    for i, (so, sn) in enumerate(zip(resps_old, resps_new, strict=True)):
        check(f"load resp {i}", so == sn)
    # Re-serialize what the NEW loader read -> must equal the file bytes.
    interactions_rt = list(zip(reqs_new, resps_new, strict=True))
    rt_payload = cf.serialize_interactions(interactions_rt)
    check("round-trip byte-stable", rt_payload == new_payload)

print("== filter parity ==")
repl_h = [("authorization", None), ("cookie", None), ("x-api-key", None), ("set-cookie", None)]
repl_q = [("api_key", None), ("token", None)]
repl_p = [("password", None), ("token", None), ("api_key", None)]
for i, (method, uri, body, headers, *_rest) in enumerate(CASES):
    joined = dict(headers)
    a = VcrRequest(method, uri, body or b"", dict(joined))
    b = vf.Request(method, uri, body or b"", dict(joined))
    for fn_old, fn_new, repl in [
        (vcr_replace_headers, vf.replace_headers, repl_h),
        (vcr_replace_query, vf.replace_query_parameters, repl_q),
        (vcr_replace_post, vf.replace_post_data_parameters, repl_p),
    ]:
        a = fn_old(a, repl)
        b = fn_new(b, repl)
    check(
        f"filters case {i}",
        a.method == b.method
        and a.uri == b.uri
        and a.body == b.body
        and dict(a.headers.items()) == dict(b.headers.items()),
        f"\n    old uri={a.uri} body={a.body!r} h={dict(a.headers.items())}"
        f"\n    new uri={b.uri} body={b.body!r} h={dict(b.headers.items())}",
    )

print("== matcher parity ==")
pairs = [
    ("GET", "https://a.com/x?b=2&a=1", "GET", "https://a.com/x?a=1&b=2", True),
    ("GET", "https://a.com/x?a=1", "GET", "https://a.com/y?a=1", False),
    ("GET", "https://a.com/x", "POST", "https://a.com/x", False),
    ("GET", "https://a.com:443/x", "GET", "https://a.com/x", True),  # default port
    ("GET", "http://a.com:80/x", "GET", "http://a.com/x", True),
    ("GET", "https://a.com/x", "GET", "http://a.com/x", False),  # scheme
    ("GET", "https://A.example/x", "GET", "https://a.example/x", True),  # host case
]
m_old = [
    vcr_matchers.method,
    vcr_matchers.scheme,
    vcr_matchers.host,
    vcr_matchers.port,
    vcr_matchers.path,
    vcr_matchers.query,
]
m_new = [vf.method, vf.scheme, vf.host, vf.port, vf.path, vf.query]
for i, (m1, u1, m2, u2, expected) in enumerate(pairs):
    r1o, r2o = VcrRequest(m1, u1, b"", {}), VcrRequest(m2, u2, b"", {})
    r1n, r2n = vf.Request(m1, u1, b"", {}), vf.Request(m2, u2, b"", {})
    old_res = vcr_matchers.requests_match(r1o, r2o, m_old)
    new_res = vf.requests_match(r1n, r2n, m_new)
    check(
        f"matcher pair {i}",
        old_res == new_res == expected,
        f"old={old_res} new={new_res} expected={expected}",
    )

print()
if failures:
    print(f"DIFFERENTIAL CHECK FAILED: {len(failures)} failures: {failures}")
    sys.exit(1)
print("DIFFERENTIAL CHECK PASSED")
