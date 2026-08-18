"""Scanner and recorder must reach the same verdict (#875, S9/#798).

The audit's whole contract is that every finding is one a re-record
clears: ``clm cassette scan`` exits non-zero on findings, so a finding the
recorder would *not* act on makes the repo-wide gate (#874) unsatisfiable,
and a body the recorder *would* rewrite but the scanner ignores makes the
gate a false all-clear.

Two separate recursive walks enforce that contract —
``cassette_format._redact_json_values`` and
``cassette_doctor._iter_secret_body_keys``. Issue #875 was one wrong value
test written into both of them, agreeing perfectly and wrong together;
inspection had already "verified" them against each other. So the guard
here is executable and runs the *same payload* through both sides,
asserting only that they agree — not what either one does, which is the
job of their own test modules.

Add a row to ``PAYLOADS`` whenever you touch either walk. A shape only
one side handles is exactly the bug this file exists to catch.
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
    # A repeat of an *ordinary* name is not the audit's business, and must
    # stay on the byte-preserving fast path.
    "duplicate_ordinary_key": '{"a":1,"a":2}',
    "duplicate_ordinary_beside_secret": '{"a":1,"a":2,"keep":"x"}',
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


def _scanner_flags_raw(text: str, tmp_path: Path, name: str) -> bool:
    """True when the audit reports a response-body finding."""
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
    report = scan_cassette_secrets(path)
    assert report.error is None, report.error
    return any(f.location == "response body" for f in report.findings)


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
    assert b"LEAK" not in written and b"hunter2" not in written, written
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
