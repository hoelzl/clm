"""Tests for ``<release-channels>`` spec parsing (issues #208, #291)."""

import io

import pytest

from clm.core.course_spec import CourseSpec, CourseSpecError, release_channel_ref


def _spec(release_channels_block: str, output_targets: str = "") -> CourseSpec:
    xml = f"""
    <course>
      <name><de>T</de><en>T</en></name>
      <prog-lang>python</prog-lang>
      <sections>
        <section>
          <name><de>S</de><en>S</en></name>
          <topics><topic>intro</topic></topics>
        </section>
      </sections>
      {output_targets}
      {release_channels_block}
    </course>
    """
    return CourseSpec.from_file(io.StringIO(xml))


TWO_STREAMS = """
<release-channels name="materials" source-target="shared">
  <channel name="2026-04" path="./release/materials/2026-04" ledger="release/materials-2026-04.txt"/>
  <channel name="2026-10" path="./release/materials/2026-10" ledger="release/materials-2026-10.txt"/>
</release-channels>
<release-channels name="solutions" source-target="completed">
  <channel name="2026-04" path="./release/solutions/2026-04" ledger="release/solutions-2026-04.txt"/>
</release-channels>
"""

TWO_STREAM_TARGETS = """
<output-targets>
  <output-target name="shared"><path>output/shared</path></output-target>
  <output-target name="completed"><path>output/completed</path></output-target>
</output-targets>
"""


def test_absent_release_channels_is_empty_list():
    assert _spec("").release_channel_blocks == []


def test_parses_source_target_channels_and_remote_inheritance():
    block = """
    <release-channels source-target="solutions-source">
      <remote-path>cohorts</remote-path>
      <channel name="cohort-jan" path="./solutions/jan" ledger="release/jan.txt"/>
      <channel name="cohort-may" path="./solutions/may" ledger="release/may.txt">
        <remote-path>special</remote-path>
      </channel>
    </release-channels>
    """
    blocks = _spec(block).release_channel_blocks
    assert len(blocks) == 1
    rc = blocks[0]
    assert rc.source_target == "solutions-source"
    assert rc.remote_path == "cohorts"
    assert rc.name == ""  # single unnamed block: the issue-#208 layout
    assert [c.name for c in rc.channels] == ["cohort-jan", "cohort-may"]

    jan = rc.channel("cohort-jan")
    assert jan is not None
    assert jan.path == "./solutions/jan"
    assert jan.ledger == "release/jan.txt"
    assert jan.remote_path == "cohorts"  # inherits the block default

    may = rc.channel("cohort-may")
    assert may is not None
    assert may.remote_path == "special"  # per-channel override

    assert rc.channel("missing") is None


class TestMultiStream:
    """Several <release-channels> blocks, one per release stream (issue #291)."""

    def test_parses_multiple_named_blocks(self):
        spec = _spec(TWO_STREAMS, TWO_STREAM_TARGETS)
        assert [b.name for b in spec.release_channel_blocks] == ["materials", "solutions"]
        assert [b.source_target for b in spec.release_channel_blocks] == ["shared", "completed"]
        assert spec.release_channel_refs() == [
            "materials/2026-04",
            "materials/2026-10",
            "solutions/2026-04",
        ]

    def test_qualified_ref_resolves_to_its_stream(self):
        spec = _spec(TWO_STREAMS, TWO_STREAM_TARGETS)
        block, channel = spec.resolve_release_channel("solutions/2026-04")
        assert block.name == "solutions"
        assert channel.path == "./release/solutions/2026-04"
        assert release_channel_ref(block, channel) == "solutions/2026-04"

    def test_bare_name_resolves_when_unique(self):
        spec = _spec(TWO_STREAMS, TWO_STREAM_TARGETS)
        block, channel = spec.resolve_release_channel("2026-10")
        assert block.name == "materials"
        assert channel.name == "2026-10"

    def test_bare_name_ambiguous_across_streams(self):
        spec = _spec(TWO_STREAMS, TWO_STREAM_TARGETS)
        with pytest.raises(CourseSpecError, match="several streams"):
            spec.resolve_release_channel("2026-04")

    def test_unknown_stream_and_channel(self):
        spec = _spec(TWO_STREAMS, TWO_STREAM_TARGETS)
        with pytest.raises(CourseSpecError, match="Unknown release stream"):
            spec.resolve_release_channel("nope/2026-04")
        with pytest.raises(CourseSpecError, match="Unknown channel"):
            spec.resolve_release_channel("materials/nope")
        with pytest.raises(CourseSpecError, match="Unknown channel"):
            spec.resolve_release_channel("nope")

    def test_bare_name_in_single_unnamed_block_keeps_208_addressing(self):
        block = """
        <release-channels source-target="shared">
          <channel name="jan" path="./solutions/jan" ledger="release/jan.txt"/>
        </release-channels>
        """
        spec = _spec(block, TWO_STREAM_TARGETS)
        b, c = spec.resolve_release_channel("jan")
        assert release_channel_ref(b, c) == "jan"


class TestMultiStreamValidation:
    def test_two_valid_streams_validate_clean(self):
        assert _spec(TWO_STREAMS, TWO_STREAM_TARGETS).validate() == []

    def test_multiple_blocks_require_names(self):
        unnamed = TWO_STREAMS.replace(' name="materials"', "")
        errors = _spec(unnamed, TWO_STREAM_TARGETS).validate()
        assert any("unique name attribute" in e for e in errors)

    def test_duplicate_stream_names_rejected(self):
        dup = TWO_STREAMS.replace('name="solutions"', 'name="materials"')
        errors = _spec(dup, TWO_STREAM_TARGETS).validate()
        assert any("Duplicate release stream name" in e for e in errors)

    def test_source_target_must_name_an_output_target(self):
        errors = _spec(TWO_STREAMS.replace('"completed"', '"nope"'), TWO_STREAM_TARGETS).validate()
        assert any("does not name an <output-target>" in e for e in errors)

    def test_missing_source_target_rejected(self):
        block = """
        <release-channels name="materials">
          <channel name="jan" path="./a" ledger="l.txt"/>
        </release-channels>
        """
        errors = _spec(block, TWO_STREAM_TARGETS).validate()
        assert any("source-target" in e for e in errors)

    def test_shared_dest_path_across_streams_rejected(self):
        shared_path = TWO_STREAMS.replace(
            "./release/solutions/2026-04", "./release/materials/2026-04"
        )
        errors = _spec(shared_path, TWO_STREAM_TARGETS).validate()
        assert any("share the destination path" in e for e in errors)

    def test_shared_ledger_across_streams_rejected(self):
        shared_ledger = TWO_STREAMS.replace(
            "release/solutions-2026-04.txt", "release/materials-2026-04.txt"
        )
        errors = _spec(shared_ledger, TWO_STREAM_TARGETS).validate()
        assert any("share the ledger" in e for e in errors)

    def test_slash_in_names_rejected(self):
        bad_stream = TWO_STREAMS.replace('name="materials"', 'name="mat/erials"')
        assert any(
            "must not contain '/'" in e for e in _spec(bad_stream, TWO_STREAM_TARGETS).validate()
        )
        bad_channel = TWO_STREAMS.replace('name="2026-10"', 'name="2026/10"')
        assert any(
            "must not contain '/'" in e for e in _spec(bad_channel, TWO_STREAM_TARGETS).validate()
        )

    def test_duplicate_channel_names_within_block_rejected(self):
        dup = TWO_STREAMS.replace('name="2026-10"', 'name="2026-04"')
        errors = _spec(dup, TWO_STREAM_TARGETS).validate()
        assert any("duplicate channel name" in e for e in errors)


class TestChannelRemoteUrl:
    def test_stream_suffix_disambiguates_per_stream_repos(self):
        spec = _spec(
            TWO_STREAMS,
            TWO_STREAM_TARGETS.replace(
                "<output-targets>",
                "<output-targets>",
            ),
        )
        github = spec.github
        url = github.derive_channel_remote_url("2026-04", project_slug="ml", stream="materials")
        # No repository_base configured in this spec -> None
        assert url is None

    def test_stream_and_plain_urls(self):
        xml_github = """
        <github><repository-base>https://gitlab.example.com/ca</repository-base></github>
        <project-slug>ml</project-slug>
        """
        spec = _spec(TWO_STREAMS, TWO_STREAM_TARGETS + xml_github)
        url = spec.github.derive_channel_remote_url(
            "2026-04", project_slug="ml", stream="materials"
        )
        assert url == "https://gitlab.example.com/ca/ml-2026-04-materials"
        plain = spec.github.derive_channel_remote_url("jan", project_slug="ml")
        assert plain == "https://gitlab.example.com/ca/ml-jan"
