"""``image_ref_missing`` / ``image_name_conflict`` — spec-mode image checks (#1008).

Step 1b of the shared-diagram design (``docs/claude/design/shared-diagram-
renders.md`` §3.2): a deck that references ``img/<name>`` which nothing in
its topic produces, and two topics that ship different bytes under one
section-output ``img/`` name, are found at ``clm validate`` time instead of
after a build.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from clm.slides.spec_validator import validate_spec
from tests.slides.test_spec_validator import _make_topic, _write_spec

OWNER = "slides/module_100_basics/topic_020_owner"


def _deck(topic_dir: Path, name: str, *refs: str) -> Path:
    body = "".join(f'# <img src="img/{ref}">\n' for ref in refs)
    path = topic_dir / name
    path.write_text(f"# %% [markdown]\n# Hello\n{body}", encoding="utf-8")
    return path


def _spec(tmp_path: Path, topics_xml: str) -> Path:
    return _write_spec(
        tmp_path,
        f"""\
        <sections><section>
          <name><de>S</de><en>S</en></name>
          <topics>
            {topics_xml}
          </topics>
        </section></sections>""",
    )


def _findings(result, kind: str):
    return [f for f in result.findings if f.type == kind]


# ---------------------------------------------------------------------------
# image_ref_missing
# ---------------------------------------------------------------------------


class TestImageRefMissing:
    def test_reference_nothing_produces_is_a_warning(self, tmp_path):
        topic = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _deck(topic, "slides_intro.py", "ghost.png")
        spec_file = _spec(tmp_path, "<topic>intro</topic>")

        result = validate_spec(spec_file, tmp_path / "slides")

        [finding] = _findings(result, "image_ref_missing")
        assert finding.severity == "warning"
        assert finding.topic_id == "intro"
        assert finding.details == {
            "image": "ghost.png",
            "topic": "intro",
            "section": "S",
            "referenced_in": ["slides_intro.py"],
        }
        assert "Sharing a diagram between topics" in finding.suggestion

    def test_file_in_img_or_img_generated_satisfies(self, tmp_path):
        topic = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _deck(topic, "slides_intro.py", "photo.png", "render.png", "sub/deep.svg")
        (topic / "img" / "sub").mkdir(parents=True)
        (topic / "img" / "photo.png").write_bytes(b"photo")
        (topic / "img" / "sub" / "deep.svg").write_bytes(b"deep")
        (topic / "img-generated").mkdir()
        (topic / "img-generated" / "render.png").write_bytes(b"render")
        spec_file = _spec(tmp_path, "<topic>intro</topic>")

        result = validate_spec(spec_file, tmp_path / "slides")

        assert _findings(result, "image_ref_missing") == []

    def test_diagram_source_render_name_satisfies_including_multi_dot_stems(self, tmp_path):
        topic = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _deck(topic, "slides_intro.py", "flow.png", "embed.de.svg", "missing.png")
        (topic / "pu").mkdir()
        (topic / "pu" / "flow.pu").write_text("@startuml\n@enduml\n", encoding="utf-8")
        (topic / "drawio").mkdir()
        (topic / "drawio" / "embed.de.drawio").write_text("<mxfile/>", encoding="utf-8")
        spec_file = _spec(tmp_path, "<topic>intro</topic>")

        result = validate_spec(spec_file, tmp_path / "slides")

        assert [f.details["image"] for f in _findings(result, "image_ref_missing")] == [
            "missing.png"
        ]

    def test_markdown_image_syntax_is_scanned_too(self, tmp_path):
        topic = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        (topic / "slides_intro.py").write_text(
            "# %% [markdown]\n# ![alt](img/md.png)\n# ![ok](./img/ok.png)\n", encoding="utf-8"
        )
        (topic / "img").mkdir()
        (topic / "img" / "ok.png").write_bytes(b"ok")
        spec_file = _spec(tmp_path, "<topic>intro</topic>")

        result = validate_spec(spec_file, tmp_path / "slides")

        assert [f.details["image"] for f in _findings(result, "image_ref_missing")] == ["md.png"]

    def test_included_diagram_source_satisfies_only_in_the_spec_that_includes_it(self, tmp_path):
        """The #987 objection answered: a spec that forgets the include fails validate."""
        owner = _make_topic(tmp_path, "module_100_basics", "topic_020_owner")
        (owner / "drawio").mkdir()
        (owner / "drawio" / "cosine.drawio").write_text("<mxfile/>", encoding="utf-8")
        consumer = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _deck(consumer, "slides_intro.py", "cosine.png")

        with_include = _spec(
            tmp_path,
            f'<topic id="intro"><include source="{OWNER}/drawio/cosine.drawio" '
            'as="drawio/cosine.drawio"/></topic>',
        )
        assert (
            _findings(validate_spec(with_include, tmp_path / "slides"), "image_ref_missing") == []
        )

        without = _spec(tmp_path, "<topic>intro</topic>")
        [finding] = _findings(validate_spec(without, tmp_path / "slides"), "image_ref_missing")
        assert finding.details["image"] == "cosine.png"

    def test_included_image_file_and_directory_satisfy(self, tmp_path):
        consumer = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _deck(consumer, "slides_intro.py", "logo.png", "shared/a.png")
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / "logo.png").write_bytes(b"logo")
        (tmp_path / "assets" / "pack").mkdir()
        (tmp_path / "assets" / "pack" / "a.png").write_bytes(b"a")
        spec_file = _spec(
            tmp_path,
            '<topic id="intro">'
            '<include source="assets/logo.png" as="img/logo.png"/>'
            '<include source="assets/pack" as="img/shared"/>'
            "</topic>",
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert _findings(result, "image_ref_missing") == []


# ---------------------------------------------------------------------------
# image_name_conflict
# ---------------------------------------------------------------------------


class TestImageNameConflict:
    def _two_topics(self, tmp_path: Path) -> tuple[Path, Path]:
        a = _make_topic(tmp_path, "module_100_basics", "topic_010_a")
        b = _make_topic(tmp_path, "module_100_basics", "topic_020_b")
        (a / "img").mkdir()
        (b / "img").mkdir()
        return a, b

    def test_different_bytes_under_one_name_in_one_section(self, tmp_path):
        a, b = self._two_topics(tmp_path)
        (a / "img" / "x.png").write_bytes(b"A")
        (b / "img" / "x.png").write_bytes(b"B")
        spec_file = _spec(tmp_path, "<topic>a</topic><topic>b</topic>")

        result = validate_spec(spec_file, tmp_path / "slides")

        [finding] = _findings(result, "image_name_conflict")
        assert finding.severity == "warning"
        assert finding.section == "S"
        assert finding.details["image"] == "x.png"
        assert [(p["topic"], p["source"]) for p in finding.details["providers"]] == [
            ("a", "img/x.png"),
            ("b", "img/x.png"),
        ]
        assert "Sharing a diagram between topics" in finding.suggestion

    def test_identical_bytes_dedup_without_a_finding(self, tmp_path):
        a, b = self._two_topics(tmp_path)
        (a / "img" / "x.png").write_bytes(b"same")
        (b / "img" / "x.png").write_bytes(b"same")
        spec_file = _spec(tmp_path, "<topic>a</topic><topic>b</topic>")

        assert _findings(validate_spec(spec_file, tmp_path / "slides"), "image_name_conflict") == []

    def test_topics_in_different_sections_do_not_conflict(self, tmp_path):
        a, b = self._two_topics(tmp_path)
        (a / "img" / "x.png").write_bytes(b"A")
        (b / "img" / "x.png").write_bytes(b"B")
        spec_file = _write_spec(
            tmp_path,
            """\
            <sections>
              <section><name><de>S1</de><en>S1</en></name><topics><topic>a</topic></topics></section>
              <section><name><de>S2</de><en>S2</en></name><topics><topic>b</topic></topics></section>
            </sections>""",
        )

        assert _findings(validate_spec(spec_file, tmp_path / "slides"), "image_name_conflict") == []

    def test_static_copy_versus_owners_committed_render(self, tmp_path):
        """The PythonCourses case: a drifted copy of another topic's render."""
        owner = _make_topic(tmp_path, "module_100_basics", "topic_020_owner")
        (owner / "drawio").mkdir()
        (owner / "drawio" / "cosine.drawio").write_text("<mxfile/>", encoding="utf-8")
        (owner / "img-generated").mkdir()
        (owner / "img-generated" / "cosine.png").write_bytes(b"render-v2")
        consumer = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        (consumer / "img").mkdir()
        (consumer / "img" / "cosine.png").write_bytes(b"render-v1")  # stale copy
        spec_file = _spec(tmp_path, "<topic>intro</topic><topic>owner</topic>")

        [finding] = _findings(validate_spec(spec_file, tmp_path / "slides"), "image_name_conflict")
        assert finding.details["image"] == "cosine.png"
        assert {p["source"] for p in finding.details["providers"]} == {
            "img/cosine.png",
            "render of drawio/cosine.drawio",
        }

        # Byte-identical copy: no conflict (it dedups at build time).
        (consumer / "img" / "cosine.png").write_bytes(b"render-v2")
        assert _findings(validate_spec(spec_file, tmp_path / "slides"), "image_name_conflict") == []

    def test_including_the_owners_source_never_conflicts(self, tmp_path):
        owner = _make_topic(tmp_path, "module_100_basics", "topic_020_owner")
        (owner / "drawio").mkdir()
        (owner / "drawio" / "cosine.drawio").write_text("<mxfile/>", encoding="utf-8")
        (owner / "img-generated").mkdir()
        (owner / "img-generated" / "cosine.png").write_bytes(b"render")
        consumer = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        _deck(consumer, "slides_intro.py", "cosine.png")
        spec_file = _spec(
            tmp_path,
            f'<topic id="intro"><include source="{OWNER}/drawio/cosine.drawio" '
            'as="drawio/cosine.drawio"/></topic><topic>owner</topic>',
        )

        result = validate_spec(spec_file, tmp_path / "slides")

        assert _findings(result, "image_name_conflict") == []
        assert _findings(result, "image_ref_missing") == []

    def test_legacy_render_beside_new_render_in_one_topic(self, tmp_path):
        topic = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
        (topic / "img").mkdir()
        (topic / "img" / "x.png").write_bytes(b"old")
        (topic / "img-generated").mkdir()
        (topic / "img-generated" / "x.png").write_bytes(b"new")
        spec_file = _spec(tmp_path, "<topic>intro</topic>")

        [finding] = _findings(validate_spec(spec_file, tmp_path / "slides"), "image_name_conflict")
        assert [p["source"] for p in finding.details["providers"]] == [
            "img/x.png",
            "img-generated/x.png",
        ]


# ---------------------------------------------------------------------------
# --json surface
# ---------------------------------------------------------------------------


def test_validate_json_carries_details(tmp_path):
    from clm.cli.commands.validate import validate_cmd

    topic = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
    _deck(topic, "slides_intro.py", "ghost.png")
    spec_file = _spec(tmp_path, "<topic>intro</topic>")

    result = CliRunner().invoke(validate_cmd, [str(spec_file), "--json"])
    payload = json.loads(result.output)
    findings = payload.get("findings") or payload.get("spec", {}).get("findings")
    assert findings is not None, result.output
    [finding] = [f for f in findings if f["type"] == "image_ref_missing"]
    assert finding["details"]["image"] == "ghost.png"
    assert finding["details"]["referenced_in"] == ["slides_intro.py"]


def test_same_source_rendered_by_two_topics_with_only_one_committed_render(tmp_path):
    """Both topics own a byte-identical .pu; one has committed its SVG render, the other not."""
    a = _make_topic(tmp_path, "module_100_basics", "topic_010_a")
    b = _make_topic(tmp_path, "module_100_basics", "topic_020_b")
    for topic in (a, b):
        (topic / "pu").mkdir()
        (topic / "pu" / "bank-dm.pu").write_text("@startuml\nA -> B\n@enduml\n", encoding="utf-8")
    (b / "img-generated").mkdir()
    (b / "img-generated" / "bank-dm.svg").write_bytes(b"<svg/>")
    spec_file = _spec(tmp_path, "<topic>a</topic><topic>b</topic>")

    assert _findings(validate_spec(spec_file, tmp_path / "slides"), "image_name_conflict") == []

    # Diverged sources DO conflict, committed render or not — one finding, under
    # the course's render format (svg, read off b's committed render).
    (a / "pu" / "bank-dm.pu").write_text("@startuml\nA -> C\n@enduml\n", encoding="utf-8")
    found = _findings(validate_spec(spec_file, tmp_path / "slides"), "image_name_conflict")
    assert [f.details["image"] for f in found] == ["bank-dm.svg"]


def test_renamed_diagram_include_provides_the_virtual_name(tmp_path):
    """The build renders the VIRTUAL path's stem (a renamed include lands under its new name)."""
    owner = _make_topic(tmp_path, "module_100_basics", "topic_020_owner")
    (owner / "drawio").mkdir()
    (owner / "drawio" / "a.drawio").write_text("<mxfile/>", encoding="utf-8")
    consumer = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
    _deck(consumer, "slides_intro.py", "b.png")
    spec_file = _spec(
        tmp_path,
        f'<topic id="intro"><include source="{OWNER}/drawio/a.drawio" as="drawio/b.drawio"/></topic>',
    )

    assert _findings(validate_spec(spec_file, tmp_path / "slides"), "image_ref_missing") == []


def test_shadowed_include_is_not_a_provider(tmp_path):
    """A real local file wins over the include at build time — no conflict, only include_shadowed."""
    topic = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
    (topic / "img").mkdir()
    (topic / "img" / "logo.png").write_bytes(b"local override")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(b"shared")
    spec_file = _spec(
        tmp_path, '<topic id="intro"><include source="assets/logo.png" as="img/logo.png"/></topic>'
    )

    result = validate_spec(spec_file, tmp_path / "slides")

    assert _findings(result, "image_name_conflict") == []
    assert _findings(result, "include_shadowed")


def test_diagram_source_outside_any_topic_does_not_crash(tmp_path):
    consumer = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
    _deck(consumer, "slides_intro.py", "loose.png")
    (tmp_path / "loose.drawio").write_text("<mxfile/>", encoding="utf-8")
    spec_file = _spec(
        tmp_path,
        '<topic id="intro"><include source="loose.drawio" as="drawio/loose.drawio"/></topic>',
    )

    result = validate_spec(spec_file, tmp_path / "slides")

    assert _findings(result, "image_ref_missing") == []


def test_legacy_render_with_stale_img_generated_twin_conflicts(tmp_path):
    """The build renders into a committed legacy img/ render; a stale img-generated/ twin still ships."""
    topic = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
    (topic / "drawio").mkdir()
    (topic / "drawio" / "x.drawio").write_text("<mxfile/>", encoding="utf-8")
    (topic / "img").mkdir()
    (topic / "img" / "x.png").write_bytes(b"legacy render")
    (topic / "img-generated").mkdir()
    (topic / "img-generated" / "x.png").write_bytes(b"stale twin")
    spec_file = _spec(tmp_path, "<topic>intro</topic>")

    [finding] = _findings(validate_spec(spec_file, tmp_path / "slides"), "image_name_conflict")
    assert sorted(p["source"] for p in finding.details["providers"]) == [
        "img-generated/x.png",
        "render of drawio/x.drawio",
    ]

    # Migrated (twin removed): one provider, no finding.
    (topic / "img-generated" / "x.png").unlink()
    assert _findings(validate_spec(spec_file, tmp_path / "slides"), "image_name_conflict") == []


def test_reference_in_either_render_format_is_satisfied(tmp_path):
    topic = _make_topic(tmp_path, "module_100_basics", "topic_010_intro")
    _deck(topic, "slides_intro.py", "flow.svg")
    (topic / "pu").mkdir()
    (topic / "pu" / "flow.pu").write_text("@startuml\n@enduml\n", encoding="utf-8")
    spec_file = _spec(tmp_path, "<topic>intro</topic>")

    assert _findings(validate_spec(spec_file, tmp_path / "slides"), "image_ref_missing") == []
