"""Tests for ``<release-channels>`` spec parsing (issue #208, step 3)."""

import io

from clm.core.course_spec import CourseSpec


def _spec(release_channels_block: str) -> CourseSpec:
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
      {release_channels_block}
    </course>
    """
    return CourseSpec.from_file(io.StringIO(xml))


def test_absent_release_channels_is_none():
    assert _spec("").release_channels is None


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
    rc = _spec(block).release_channels
    assert rc is not None
    assert rc.source_target == "solutions-source"
    assert rc.remote_path == "cohorts"
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
