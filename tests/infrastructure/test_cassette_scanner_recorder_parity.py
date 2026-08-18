"""Scanner and recorder must reach the same verdict (#875, S9/#798).

The audit's whole contract is that every finding is one a re-record
clears: ``clm cassette scan`` exits non-zero on findings, so a finding the
recorder would *not* act on makes the repo-wide gate (#874) unsatisfiable,
and a body the recorder *would* rewrite but the scanner ignores makes the
gate a false all-clear.

On the **response** side two separate recursive walks enforce that
contract — ``cassette_format._redact_json_values`` and
``cassette_doctor._iter_secret_body_keys``. Issue #875 was one wrong value
test written into both of them, agreeing perfectly and wrong together;
inspection had already "verified" them against each other. So the guard
here is executable and runs the *same payload* through both sides,
asserting only that they agree — not what either one does, which is the
job of their own test modules.

On the **request** side there is one walk: the audit calls the recorder's
own ``vcr_format.filter_json_parameters`` (#877). The rows below still
earn their keep — they pin the shapes, and they catch a future
reimplementation — but the structural guarantee is stronger there.

Add a row to ``PAYLOADS`` or ``REQUEST_BODIES`` whenever you touch either
side. A shape only one side handles is exactly the bug this file exists to
catch — with the caveat #877 taught: two sides that are *consistently*
wrong agree, so a shared blind spot passes here. The response table had no
request-body rows at all, which is how a nested ``api_key`` recorded
verbatim and scanned clean while this suite stayed green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from clm.infrastructure.http_replay_mitm import cassette_format as cf
from clm.workers.notebook.cassette_doctor import scan_cassette_secrets

# Each entry is a JSON response body. Names describe the *shape* under
# test, not the expected verdict — the point is that both sides agree,
# whatever the verdict is.
PAYLOADS: dict[str, object] = {
    "empty": {},
    "no_secret_keys": {"model": "gpt", "choices": [{"text": "hi"}]},
    "usage_counters": {"usage": {"prompt_tokens": 1, "completion_tokens": 2}},
    # The #875 corpus case and its neighbours.
    "word_keyed_vocabulary": {"hello": 31373, "secret": 21078, "password": 28712},
    "numeric_secret": {"secret": 0},
    "float_secret": {"api_key": 3.5},
    "negative_secret": {"password": -1},
    "bigint_secret": {"secret": 2**70},
    "bool_secret_true": {"authorization": True},
    "bool_secret_false": {"secret": False},
    "null_secret": {"client_secret": None},
    # Values that must still be redacted.
    "string_secret": {"access_token": "ya29.SECRET"},
    "dict_secret": {"secret": {"value": "sk-live-abc"}},
    "list_secret": {"secret": ["sk-live-abc"]},
    "empty_dict_secret": {"secret": {}},
    "empty_list_secret": {"secret": []},
    "dict_secret_of_numbers": {"secret": {"a": 1, "b": 2}},
    # Nesting and position.
    "nested_secret": {"data": {"items": [{"refresh_token": "ya29.S"}]}},
    "list_of_dicts": [{"password": "p"}, {"keep": 1}],
    "deeply_nested": {"a": {"b": {"c": {"d": {"api_key": "sk"}}}}},
    "secret_beside_clean": {"secret": "s", "keep": "visible"},
    # Case variants — the recorder lowercases the key before matching and
    # the scanner must too. Mutating either to a case-sensitive compare
    # used to leave the whole suite green.
    "upper_key": {"SECRET": "sk-live-abc"},
    "title_key": {"Password": "hunter2"},
    "mixed_key": {"AcCeSs_ToKeN": "ya29.S"},
    "upper_key_numeric": {"SECRET": 21078},
    "title_key_numeric": {"Password": 28712},
    # Near-misses that must NOT match (whole-key, never substring).
    "substring_key": {"my_password_hint": "abc"},
    "prefix_key": {"secretive": "abc"},
    "unicode_lookalike": {"paѕѕword": "abc"},
    # Already-redacted values are clean on both sides.
    "already_redacted": {"secret": cf.SECRET_PLACEHOLDER},
    "already_redacted_nested": {"a": {"api_key": cf.SECRET_PLACEHOLDER}},
    "already_redacted_in_list": {"data": [{"secret": cf.SECRET_PLACEHOLDER}]},
    # Non-object roots and odd scalars.
    "top_level_list": [{"secret": "s"}],
    "top_level_scalar": "just a string",
    "top_level_number": 42,
    "top_level_null": None,
    "zero_and_negative_zero": {"secret": -0.0},
}


# Bodies that only a *raw* string can express. ``json.dumps`` of a Python
# dict can never produce a repeated name, and a repeated name is precisely
# where parse-tree equality stops describing the bytes (#875 review).
RAW_PAYLOADS: dict[str, str] = {
    "duplicate_secret_string_then_number": '{"secret":"sk-live-LEAK","secret":1}',
    "duplicate_secret_number_then_string": '{"secret":1,"secret":"sk-live-LEAK"}',
    "duplicate_secret_null": '{"password":"hunter2","password":null}',
    "duplicate_secret_bool": '{"api_key":"sk-LEAK","api_key":false}',
    "duplicate_secret_both_strings": '{"secret":"a","secret":"b"}',
    "duplicate_secret_nested": '{"data":{"api_key":"sk-LEAK","api_key":2}}',
    "duplicate_secret_cased": '{"Secret":"sk-live-LEAK","Secret":1}',
    # The duplicate must be found at *any* depth, not just in whichever
    # object the hook happens to see first. ``object_pairs_hook`` fires
    # innermost-first, so a table where every duplicate sits in the
    # first-parsed object cannot tell a correct implementation from one
    # restricted to that object — a regression that re-leaks plaintext
    # with a green suite (found in review round 2).
    "duplicate_secret_after_nested_object": ('{"data":{"x":1},"secret":"sk-live-LEAK","secret":1}'),
    "duplicate_secret_after_usage_block": (
        '{"usage":{"total_tokens":3},"api_key":"sk-live-LEAK","api_key":0}'
    ),
    "duplicate_secret_in_array_of_objects": '[{"x":1},{"secret":"sk-live-LEAK","secret":1}]',
    # Both values exempt: still reported. The shadowed value cannot be
    # inspected for what it might have been, so this is conservative on
    # purpose — and it stays satisfiable, since a re-record emits no
    # duplicate.
    "duplicate_secret_both_exempt": '{"secret":1,"secret":2}',
    # A repeat of an *ordinary* name is not the audit's business, and must
    # stay on the byte-preserving fast path.
    "duplicate_ordinary_key": '{"a":1,"a":2}',
    "duplicate_ordinary_beside_secret": '{"a":1,"a":2,"keep":"x"}',
    # The same secret name in two *different* objects is not a repeat.
    # Sharing the hook's ``seen`` across objects would make it one.
    "same_secret_name_in_sibling_objects": '{"a":{"api_key":1},"b":{"api_key":2}}',
    "same_secret_name_in_array_elements": '{"choices":[{"secret":1},{"secret":2}]}',
    # Sanity anchors in raw form.
    "raw_clean": '{"model":"gpt","usage":{"total_tokens":3}}',
    "raw_numeric_secret": '{"secret":21078}',
    "raw_string_secret": '{"secret":"sk-live-abc"}',
    "raw_odd_whitespace": '{  "secret" : 21078 ,  "keep" : 1  }',
}


def _recorder_rewrites(payload: object) -> bool:
    """True when the recorder would change the committed bytes."""
    return _recorder_rewrites_raw(json.dumps(payload))


def _recorder_rewrites_raw(text: str) -> bool:
    raw = text.encode()
    out = cf.build_response_filter()(
        {
            "status": {"code": 200, "message": "OK"},
            "headers": {"content-type": ["application/json"]},
            "body": {"string": raw},
        }
    )
    return out["body"]["string"] != raw


def _scanner_flags(payload: object, tmp_path: Path, name: str) -> bool:
    return _scanner_flags_raw(json.dumps(payload), tmp_path, name)


def _scanner_findings_raw(text: str | bytes, tmp_path: Path, name: str) -> list:
    """Response-body findings the audit reports for *text*."""
    path = _write_cassette(text, tmp_path, name)
    report = scan_cassette_secrets(path)
    assert report.error is None, report.error
    return [f for f in report.findings if f.location.startswith("response body")]


def _scanner_flags_raw(text: str | bytes, tmp_path: Path, name: str) -> bool:
    """True when the audit reports a response-body finding."""
    return bool(_scanner_findings_raw(text, tmp_path, name))


def _write_cassette(text: str | bytes, tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.http-cassette.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "interactions": [
                    {
                        "request": {
                            "method": "POST",
                            "uri": "https://api.example.com/v1/chat",
                            "body": "{}",
                            "headers": {},
                        },
                        "response": {
                            "status": {"code": 200, "message": "OK"},
                            "headers": {"content-type": ["application/json"]},
                            "body": {"string": text},
                        },
                    }
                ],
                "version": 1,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("name", sorted(PAYLOADS))
def test_scanner_and_recorder_agree(name: str, tmp_path: Path) -> None:
    """The audit flags a body if and only if re-recording would rewrite it."""
    payload = PAYLOADS[name]
    rewrites = _recorder_rewrites(payload)
    flags = _scanner_flags(payload, tmp_path, name)
    assert rewrites == flags, (
        f"{name}: recorder rewrites={rewrites} but scanner flags={flags}. "
        "A finding no re-record clears makes `clm cassette scan` unsatisfiable; "
        "a rewrite the scan misses makes it a false all-clear."
    )


@pytest.mark.parametrize("name", sorted(RAW_PAYLOADS))
def test_scanner_and_recorder_agree_on_raw_bodies(name: str, tmp_path: Path) -> None:
    """Same contract, for bodies a Python object cannot express.

    Repeated names are the case: ``json.loads`` keeps the last pair, so
    the tree both sides read is missing the first one. Before the fix the
    recorder's byte shortcut re-emitted a plaintext token verbatim and the
    audit called the file clean — agreeing with each other, and both
    wrong, which is the failure mode this whole file exists for.
    """
    rewrites = _recorder_rewrites_raw(RAW_PAYLOADS[name])
    flags = _scanner_flags_raw(RAW_PAYLOADS[name], tmp_path, name)
    assert rewrites == flags, f"{name}: recorder rewrites={rewrites} but scanner flags={flags}."


@pytest.mark.parametrize(
    "name",
    [n for n in sorted(RAW_PAYLOADS) if n.startswith("duplicate_secret")],
)
def test_a_shadowed_secret_never_survives_in_the_recorded_bytes(name: str, tmp_path: Path) -> None:
    """Parity alone would be satisfied by both sides ignoring it.

    So pin the direction too: the plaintext must be gone from what the
    recorder writes, and the audit must say the file needs re-recording.
    """
    text = RAW_PAYLOADS[name]
    out = cf.build_response_filter()(
        {
            "status": {"code": 200, "message": "OK"},
            "headers": {"content-type": ["application/json"]},
            "body": {"string": text.encode()},
        }
    )
    written = out["body"]["string"]
    for plaintext in (b"LEAK", b"hunter2"):
        if plaintext in text.encode():
            assert plaintext not in written, written
    assert _scanner_flags_raw(text, tmp_path, name)


def test_a_repeated_ordinary_name_stays_on_the_fast_path(tmp_path: Path) -> None:
    """The duplicate check is scoped to filter-list names, deliberately.

    Re-serializing every body with any repeated name would rewrite bytes
    for something that is not the audit's business — and the fast path is
    what keeps a 1 MB vocabulary from being reformatted on every record.
    """
    text = '{"a":1,"a":2}'
    assert not _recorder_rewrites_raw(text)
    assert not _scanner_flags_raw(text, tmp_path, "ordinary")


def test_the_table_covers_both_verdicts() -> None:
    """Guard against the parity test passing because nothing ever matches.

    If a refactor made ``_recorder_rewrites`` always return ``False``,
    every row above would still "agree" with a scanner that also found
    nothing. The table has to exercise both answers.
    """
    verdicts = {_recorder_rewrites(payload) for payload in PAYLOADS.values()}
    assert verdicts == {True, False}


# How many response-body findings each shape is worth. A boolean parity
# assert cannot see a *count* regression: dropping the ``continue`` in
# ``_iter_secret_body_keys`` makes it recurse into an already-matched
# container and yield one finding per nested secret name, where the
# recorder makes a single wholesale replacement. Both sides still say
# "dirty", so parity holds while the report triples (found in review 2).
EXPECTED_FINDING_COUNT: dict[str, int] = {
    "dict_secret": 1,
    "list_secret": 1,
    "dict_secret_of_numbers": 1,
    "string_secret": 1,
    "nested_secret": 1,
    "deeply_nested": 1,
    "secret_beside_clean": 1,
    "no_secret_keys": 0,
    "usage_counters": 0,
    "word_keyed_vocabulary": 0,
    "already_redacted": 0,
}


@pytest.mark.parametrize("name", sorted(EXPECTED_FINDING_COUNT))
def test_a_matched_container_yields_exactly_one_finding(name: str, tmp_path: Path) -> None:
    """One replacement in, one finding out."""
    findings = _scanner_findings_raw(json.dumps(PAYLOADS[name]), tmp_path, name)
    assert len(findings) == EXPECTED_FINDING_COUNT[name], [(f.location, f.key) for f in findings]


def test_a_nested_secret_under_a_matched_key_is_one_finding(tmp_path: Path) -> None:
    """The shape that makes the count regression visible.

    The recorder replaces the whole ``secret`` subtree with one
    placeholder, so the audit must report one thing to re-record — not
    three, which is what recursing past the match would produce.
    """
    payload = {"secret": {"api_key": "a", "password": "b", "nested": {"secret": "c"}}}
    findings = _scanner_findings_raw(json.dumps(payload), tmp_path, "nested_under_match")
    assert [(f.location, f.key) for f in findings] == [("response body", "secret")]


JSON = "application/json"
FORM = "application/x-www-form-urlencoded"

# Request bodies, as the **raw text** a cassette holds. Same contract as
# the response table, and the reason it exists is #877: the recorder and
# the audit were *both* top-level-only, so they agreed — and the table
# above, which only ever fed response bodies, could not see it. A shared
# blind spot is the one failure a parity suite cannot catch by itself, so
# these rows are here to pin the shapes rather than to have discovered them.
REQUEST_BODIES: dict[str, tuple[str, str]] = {
    # --- JSON, nothing to strip -------------------------------------------
    "req_empty_object": ("{}", JSON),
    "req_empty_body": ("", JSON),
    "req_no_secrets": ('{"model":"gpt","messages":[{"role":"user","content":"hi"}]}', JSON),
    "req_unparseable": ("not json at all", JSON),
    "req_clean_array": ("[1,2,3]", JSON),
    "req_scalar": ('"just a string"', JSON),
    "req_null_root": ("null", JSON),
    # A *response*-list name that is not on the request list. The two lists
    # differ on purpose, and reading one where the other belongs would make
    # every LLM cassette dirty.
    "req_response_only_key": ('{"secret":"sk-live-LEAK"}', JSON),
    # Whole-key matching, never substring.
    "req_near_miss_substring": ('{"my_password_hint":"x"}', JSON),
    "req_near_miss_plural": ('{"tokens":5,"api_keys":[]}', JSON),
    "req_max_tokens": ('{"max_tokens":10,"model":"gpt"}', JSON),
    # A repeat of an ordinary name removes nothing.
    "req_duplicate_ordinary": ('{"a":1,"a":2}', JSON),
    # --- JSON, stripped ---------------------------------------------------
    "req_top_level_secret": ('{"api_key":"sk-live-LEAK"}', JSON),
    "req_nested_secret": ('{"data":{"api_key":"sk-live-LEAK"}}', JSON),
    "req_secret_in_list": ('{"items":[{"id":1},{"password":"hunter2"}]}', JSON),
    "req_secret_in_top_level_array": ('[{"token":"t"},{"keep":1}]', JSON),
    "req_deeply_nested_secret": ('{"a":{"b":{"c":{"d":{"api_key":"sk"}}}}}', JSON),
    "req_container_secret": ('{"api_key":{"inner":"sk-live-LEAK"}}', JSON),
    "req_secret_inside_a_match": ('{"api_key":{"password":"y"}}', JSON),
    # No value-type exemption on the request side — see
    # ``test_the_request_side_has_no_value_type_exemption``.
    "req_numeric_secret": ('{"a":{"token":5}}', JSON),
    "req_bool_secret": ('{"token":true}', JSON),
    "req_null_secret": ('{"api_key":null}', JSON),
    # Case, at depth as well as at the top.
    "req_upper_key": ('{"API_KEY":"sk-live-LEAK"}', JSON),
    "req_title_key_nested": ('{"auth":{"Password":"hunter2"}}', JSON),
    # ``json.loads`` keeps the last pair, but the request filter re-dumps
    # rather than preserving bytes, so the shadowed plaintext is dropped —
    # and the surviving pair is matched whatever its type, so unlike the
    # response side there is no shadowed-value blind spot to report around.
    "req_duplicate_secret": ('{"api_key":"sk-live-LEAK","api_key":1}', JSON),
    "req_duplicate_secret_nested": ('{"d":{"token":"sk-live-LEAK","token":2}}', JSON),
    # --- form-encoded -----------------------------------------------------
    "form_empty": ("", FORM),
    "form_clean": ("a=1&b=2", FORM),
    "form_near_miss": ("my_password=x&keep=1", FORM),
    "form_valueless_ordinary": ("flag", FORM),
    "form_secret": ("grant_type=password&password=hunter2", FORM),
    "form_case": ("API_KEY=sk-live-LEAK&keep=1", FORM),
    "form_blank_secret": ("password=", FORM),
    # No ``=`` at all: the recorder strips it (``partition`` yields an empty
    # separator, not ``None``), and the audit used to skip such bodies
    # wholesale — a false all-clear on a body that will replay-miss.
    "form_valueless_secret": ("token", FORM),
    "form_secret_and_valueless": ("token&keep=1", FORM),
    # --- not filtered at all ----------------------------------------------
    # A JSON payload under a content-type the recorder does not read as
    # JSON goes down the form branch, which finds no ``&``/``=`` parameters
    # in it. Neither side may act.
    "json_under_text_plain": ('{"api_key":"sk-live-LEAK"}', "text/plain"),
}

#: Rows that carry a leak marker and which **neither** side may act on,
#: with the reason. Explicit rather than inferred, and checked by its own
#: test below: an exclusion nobody verifies is a place to hide a
#: regression.
REQUEST_BODIES_DELIBERATELY_UNFILTERED = {
    "req_response_only_key": "`secret` is on the response key list, not the request one",
    "json_under_text_plain": "the recorder does not read this content-type as JSON",
}

#: Names above whose *bodies* the recorder must not leave the plaintext in.
#: Parity alone is satisfied by both sides ignoring a row, so pin the
#: direction too, exactly as the response table does.
REQUEST_BODIES_THAT_MUST_LOSE_THE_PLAINTEXT = [
    name
    for name, (text, _ct) in REQUEST_BODIES.items()
    if ("sk-live-LEAK" in text or "hunter2" in text)
    and name not in REQUEST_BODIES_DELIBERATELY_UNFILTERED
]


def _request_cassette(body: str, content_type: str, tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.http-cassette.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "interactions": [
                    {
                        "request": {
                            "method": "POST",
                            "uri": "https://api.example.com/v1/chat",
                            "body": body,
                            "headers": {"content-type": [content_type]},
                        },
                        "response": {
                            "status": {"code": 200, "message": "OK"},
                            "headers": {"content-type": ["text/plain"]},
                            "body": {"string": "ok"},
                        },
                    }
                ],
                "version": 1,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _recorded_request_body(body: str, content_type: str) -> bytes:
    out = cf.build_request_filter()(
        cf.Request(
            "POST", "https://api.example.com/v1/chat", body.encode(), {"content-type": content_type}
        )
    )
    assert out is not None, "an unfilterable request must never become a network bypass"
    recorded = out.body
    return recorded if isinstance(recorded, bytes) else str(recorded).encode()


def _recorder_strips_request_param(body: str, content_type: str) -> bool:
    """True when the recorder *removes a parameter* from this request body.

    Deliberately not "the bytes changed", which is the response-side
    predicate. A JSON **object** request body is re-dumped through
    ``json.dumps`` even when nothing matched — the inherited vcrpy quirk
    committed cassettes were recorded through — so byte inequality would
    call every JSON request body dirty and the gate would be
    unsatisfiable. Comparing parse trees sees past the reformatting while
    still catching a repeated name (``json.loads`` keeps the last pair, so
    a dropped one shows up as a tree difference).
    """
    recorded = _recorded_request_body(body, content_type)
    if content_type.startswith("application/json"):

        def parse(raw: bytes) -> object:
            try:
                return json.loads(raw)
            except (ValueError, RecursionError):
                return "<unparseable>"

        return parse(body.encode()) != parse(recorded)
    return recorded != body.encode()


def _scanner_request_findings(body: str, content_type: str, tmp_path: Path, name: str) -> list:
    path = _request_cassette(body, content_type, tmp_path, name)
    report = scan_cassette_secrets(path)
    assert report.error is None, report.error
    return [f for f in report.findings if f.location == "request body"]


@pytest.mark.parametrize("name", sorted(REQUEST_BODIES))
def test_scanner_and_recorder_agree_on_request_bodies(name: str, tmp_path: Path) -> None:
    """Same contract as the response table, on the match-key side.

    Worse when broken, in fact: responses are not part of the replay match
    key and request bodies are, so a recorder change the audit does not
    report leaves a cassette that will replay-miss with nothing pointing
    at it.
    """
    body, content_type = REQUEST_BODIES[name]
    strips = _recorder_strips_request_param(body, content_type)
    flags = bool(_scanner_request_findings(body, content_type, tmp_path, name))
    assert strips == flags, (
        f"{name}: recorder strips={strips} but scanner flags={flags}. "
        "A finding no re-record clears makes `clm cassette scan` unsatisfiable; "
        "a stripped parameter the scan misses hides a cassette that will replay-miss."
    )


def test_the_request_table_covers_both_verdicts() -> None:
    """Guard against every row agreeing because nothing ever matches."""
    verdicts = {_recorder_strips_request_param(body, ct) for body, ct in REQUEST_BODIES.values()}
    assert verdicts == {True, False}


@pytest.mark.parametrize("name", sorted(REQUEST_BODIES_THAT_MUST_LOSE_THE_PLAINTEXT))
def test_a_request_side_secret_never_survives_in_the_recorded_bytes(
    name: str, tmp_path: Path
) -> None:
    """Direction, not just agreement — and the audit must point at it."""
    body, content_type = REQUEST_BODIES[name]
    recorded = _recorded_request_body(body, content_type)
    for plaintext in (b"sk-live-LEAK", b"hunter2"):
        if plaintext in body.encode():
            assert plaintext not in recorded, recorded
    assert _scanner_request_findings(body, content_type, tmp_path, name)


@pytest.mark.parametrize("name", sorted(REQUEST_BODIES_DELIBERATELY_UNFILTERED))
def test_a_deliberately_unfiltered_row_really_is_unfiltered(name: str, tmp_path: Path) -> None:
    """The exclusions above are claims, so check them.

    Both rows carry a leak marker and are excused from the direction
    check. If either ever *did* get filtered, the excuse would silently
    stop being true and the row would quietly test nothing.
    """
    body, content_type = REQUEST_BODIES[name]
    reason = REQUEST_BODIES_DELIBERATELY_UNFILTERED[name]
    assert not _recorder_strips_request_param(body, content_type), reason
    assert not _scanner_request_findings(body, content_type, tmp_path, name), reason


def test_the_request_side_has_no_value_type_exemption(tmp_path: Path) -> None:
    """The response side's number/bool/null exemption must not be copied here.

    It exists because redaction rewrites what replayed code *reads* —
    GPT-2's ``encoder.json`` maps ``"secret"`` to an integer (#875). A
    request body is never handed back to the notebook, so the recorder
    removes the key whatever its type. Adding the exemption here would
    change the replay match key for every already-committed cassette
    carrying a numeric one, for no gain.
    """
    body = '{"a":{"token":5}}'
    assert _recorder_strips_request_param(body, JSON)
    assert [f.key for f in _scanner_request_findings(body, JSON, tmp_path, "numeric")] == ["token"]


def test_a_matched_request_container_yields_exactly_one_finding(tmp_path: Path) -> None:
    """One removal in, one finding out — the count regression the booleans miss."""
    body = '{"api_key":{"password":"a","inner":{"token":"b"}}}'
    findings = _scanner_request_findings(body, JSON, tmp_path, "container")
    assert [(f.location, f.key) for f in findings] == [("request body", "api_key")]


# Known, deliberate divergence — tracked separately rather than silently
# omitted from the table above, because an untested shape and a shape
# tested to diverge look identical in a passing suite.
#
# A percent-encoded parameter *name* (``api%5Fkey=SECRET``) is decoded by
# the audit's ``parse_qsl`` and not by the recorder's raw ``partition``,
# so the audit reports a finding re-recording cannot clear. Fixing it on
# the recorder side changes the form-encoded replay match key, which is a
# migration and a separate decision; fixing it on the audit side would
# make the audit blind to a genuinely leaked credential. See issue #881.


class TestBodyEncodings:
    """``json.loads`` sniffs BOMs and UTF-16/32; the audit must not lose them.

    The scanner used to decode bytes as strict UTF-8 before parsing, so a
    body with a BOM — or encoded as UTF-16 — was silently unparseable and
    reported **clean**, while the recorder (which hands bytes straight to
    ``json.loads``) redacted the token in it. A false all-clear, on exactly
    the population the audit targets: bodies recorded verbatim before the
    response filter existed (review round 2).
    """

    SECRET_BODY = '{"access_token":"ya29.REAL-SECRET","keep":1}'

    @pytest.mark.parametrize(
        "encoding",
        ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-32"],
    )
    def test_an_encoded_body_is_seen_by_both_sides(self, encoding: str, tmp_path: Path) -> None:
        raw = self.SECRET_BODY.encode(encoding)
        out = cf.build_response_filter()(
            {
                "status": {"code": 200, "message": "OK"},
                "headers": {"content-type": ["application/json"]},
                "body": {"string": raw},
            }
        )
        rewrites = out["body"]["string"] != raw
        assert b"REAL-SECRET" not in out["body"]["string"]

        findings = _scanner_findings_raw(raw, tmp_path, f"enc-{encoding}")
        assert rewrites == bool(findings), (
            f"{encoding}: recorder rewrites={rewrites} but scanner findings={findings}"
        )
        assert [f.key for f in findings] == ["access_token"]

    def test_a_body_that_is_not_text_at_all_is_left_alone_by_both(self, tmp_path: Path) -> None:
        """Undecodable bytes are not a finding — re-recording clears nothing."""
        raw = b"\xff\xfe\x00\x00not json"
        out = cf.build_response_filter()(
            {
                "status": {"code": 200, "message": "OK"},
                "headers": {"content-type": ["application/json"]},
                "body": {"string": raw},
            }
        )
        assert out["body"]["string"] == raw
        assert _scanner_findings_raw(raw, tmp_path, "undecodable") == []
