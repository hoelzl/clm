"""Every spec consumer sees the topics inside an agenda ``<block>`` (issue #1163).

``tests/test-data/course-specs/block-spec.xml`` is ``subsection-spec.xml`` with
its topics wrapped in ``<block>``s. Blocks are presentation/planning only, so
the outline and the schedule must come out identical, and a topic that only
appears inside a block is not an orphan.
"""

import json
from pathlib import Path
from textwrap import dedent

from click.testing import CliRunner

from clm.cli.commands.export.outline import generate_outline, generate_outline_json
from clm.cli.commands.export.schedule import build_schedule, render_csv, render_markdown
from clm.cli.main import cli
from clm.core.course import Course
from clm.core.course_spec import CourseSpec

DATA = Path("tests/test-data")
BLOCK_SPEC = DATA / "course-specs" / "block-spec.xml"
PLAIN_SPEC = DATA / "course-specs" / "subsection-spec.xml"


def _course(path: Path) -> Course:
    return Course.from_spec(CourseSpec.from_file(path), DATA, output_root=None)


def test_build_topics_identical():
    blocks, plain = _course(BLOCK_SPEC), _course(PLAIN_SPEC)
    assert [[t.id for t in s.topics] for s in blocks.sections] == [
        [t.id for t in s.topics] for s in plain.sections
    ]


def test_schedule_identical():
    for lang in ("de", "en"):
        blocks = build_schedule(_course(BLOCK_SPEC), lang)
        plain = build_schedule(_course(PLAIN_SPEC), lang)
        assert render_csv(blocks) == render_csv(plain)
        assert render_markdown("C", blocks, lang) == render_markdown("C", plain, lang)


def test_outline_identical():
    for show in (False, True):
        assert generate_outline(_course(BLOCK_SPEC), "en", show_weekdays=show) == (
            generate_outline(_course(PLAIN_SPEC), "en", show_weekdays=show)
        )
    assert generate_outline_json(_course(BLOCK_SPEC), "de") == generate_outline_json(
        _course(PLAIN_SPEC), "de"
    )


def test_topic_only_inside_a_block_is_not_an_orphan(tmp_path):
    deck = '# %% [markdown] lang="en" tags=["slide"]\n# ## Intro\n'
    module = tmp_path / "slides" / "module_100_x"
    for topic in ("topic_010_a", "topic_020_b"):
        (module / topic).mkdir(parents=True)
        (module / topic / f"slides_{topic[-1]}.py").write_text(deck, encoding="utf-8")
    specs = tmp_path / "course-specs"
    specs.mkdir()
    (specs / "c.xml").write_text(
        dedent("""\
        <course><name>C</name><prog-lang>python</prog-lang>
        <sections><section><name>S</name><topics>
          <subsection weekday="mon"><block units="8"><topic>b</topic></block></subsection>
          <topic>a</topic>
        </topics></section></sections></course>
        """),
        encoding="utf-8",
    )
    r = CliRunner().invoke(cli, ["course", "orphans", str(specs), "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output[r.output.index("{") : r.output.rindex("}") + 1])
    assert data["orphan_count"] == 0, data
