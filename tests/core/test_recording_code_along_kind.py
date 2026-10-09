"""Build-side wiring of the ``recording-code-along`` output kind (#1023).

The kind is private (its typing targets are the solutions), opt-in (never part
of a "build everything" default), and notebook-only (typing replay needs a live
notebook).
"""

from pathlib import Path
from xml.etree import ElementTree as ETree

from clm.core.course_spec import DEFAULT_OUTPUT_TARGET_SPECS, VALID_KINDS, OutputTargetSpec
from clm.core.execution_dependencies import EXECUTION_REQUIREMENTS, ExecutionRequirement
from clm.core.output_target import ALL_KINDS, ALL_LANGUAGES, DEFAULT_FORMATS, OutputTarget
from clm.core.utils.path_utils import PRIVATE_KINDS, Format, Kind, output_specs

KIND = "recording-code-along"


def _target(tmp_path: Path, kinds: set[str], formats=DEFAULT_FORMATS) -> OutputTarget:
    return OutputTarget(
        name="rec",
        output_root=tmp_path / "output",
        kinds=frozenset(kinds),
        formats=frozenset(formats),
        languages=ALL_LANGUAGES,
    )


def test_kind_is_valid_private_and_opt_in():
    """Regression test for #1023: known, routed privately, never a default."""
    assert KIND in VALID_KINDS
    assert Kind(KIND) == Kind.RECORDING_CODE_ALONG
    assert KIND in PRIVATE_KINDS
    assert KIND not in ALL_KINDS
    assert all(KIND not in (spec.kinds or []) for spec in DEFAULT_OUTPUT_TARGET_SPECS)


def test_target_spec_with_kind_parses_and_validates():
    element = ETree.fromstring(
        "<output-target name='rec'><path>output/rec</path>"
        "<kinds><kind>recording</kind><kind>recording-code-along</kind></kinds>"
        "</output-target>"
    )
    spec = OutputTargetSpec.from_element(element)
    assert spec.kinds == ["recording", KIND]
    assert spec.validate() == []


def test_target_without_kinds_does_not_build_it(tmp_path):
    target = OutputTarget.from_spec(OutputTargetSpec(name="t", path="./out"), tmp_path)
    assert not target.includes_kind(KIND)


def test_output_specs_are_notebook_only(course_1, tmp_path):
    """Listing the kind with every format still yields notebooks only."""
    target = _target(tmp_path, {KIND})
    specs = list(output_specs(course_1, tmp_path, target=target))
    assert {s.format for s in specs} == {Format.NOTEBOOK}
    assert {s.language for s in specs} == {"de", "en"}
    assert all(s.kind == Kind.RECORDING_CODE_ALONG for s in specs)


def test_output_specs_route_to_private_toplevel(course_1, tmp_path):
    target = _target(tmp_path, {KIND, "code-along"}, formats={"notebook"})
    specs = {
        s.kind: s for s in output_specs(course_1, tmp_path, target=target) if s.language == "en"
    }
    private = specs[Kind.RECORDING_CODE_ALONG].output_dir
    public = specs[Kind.CODE_ALONG].output_dir
    assert "speaker" in private.parts and "public" not in private.parts
    assert "public" in public.parts
    assert private.name == "Recording-Code-Along"


def test_other_kinds_unaffected_by_notebook_only_rule(course_1, tmp_path):
    target = _target(tmp_path, {KIND, "recording"})
    recording = [
        s for s in output_specs(course_1, tmp_path, target=target) if s.kind == Kind.RECORDING
    ]
    assert {s.format for s in recording} == {Format.HTML, Format.NOTEBOOK, Format.CODE}


def test_notebook_output_needs_no_execution():
    assert EXECUTION_REQUIREMENTS[("notebook", KIND)] is ExecutionRequirement.NONE


def test_target_with_only_this_kind_gets_private_image_copies(tmp_path):
    """Without private image copies the recorded notebook shows broken images."""
    from clm.core.provenance_manifest import _shared_image_audiences

    assert _shared_image_audiences(_target(tmp_path, {KIND})) == [True]


def test_target_with_kind_but_no_notebook_format_is_rejected():
    """Notebook-only: listing it without ``notebook`` would silently build nothing."""
    spec = OutputTargetSpec(name="rec", path="output/rec", kinds=[KIND], formats=["html"])
    errors = spec.validate()
    assert any("recording-code-along" in e and "notebook" in e for e in errors)
    ok = OutputTargetSpec(name="rec", path="output/rec", kinds=[KIND], formats=["notebook"])
    assert ok.validate() == []


def test_public_and_private_kinds_partition_every_kind():
    """#1026 drift guard: a hand-written public subset forgot ``partial``.

    Every kind is either public or private, never both, so the two shared
    constants together answer the audience question for any kind — including
    one added later.
    """
    from clm.core.utils.path_utils import PUBLIC_KINDS

    assert PUBLIC_KINDS.isdisjoint(PRIVATE_KINDS)
    assert PUBLIC_KINDS | PRIVATE_KINDS == {k.value for k in Kind}
    assert VALID_KINDS <= PUBLIC_KINDS | PRIVATE_KINDS
