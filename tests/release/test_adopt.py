"""Tests for ``clm release adopt`` (issue #1140).

Adopt freezes topics a cohort repository *already holds* -- a course that
used to publish by pushing an output target's repo migrating to release
channels -- without copying anything. The fixture mirrors that situation
with local repositories only: a bare "published" remote, the channel
destination cloned from it, and the old output tree whose ``origin`` still
points at that remote.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from clm.cli.commands.git import (
    ChannelRemoteGuard,
    OutputRepo,
    git_group,
    normalize_remote_url,
)
from clm.cli.commands.release import release_group
from clm.core.provenance_manifest import MANIFEST_FILENAME, topic_digest_from_files
from clm.release.adopt import (
    ADOPT,
    DIFFERS,
    MISSING,
    REFUSE_MISSING,
    SAME,
    SKIP_FROZEN,
    SKIP_UNBUILT,
    apply_adopt,
    classify_files,
    plan_adopt,
)
from clm.release.frozen_manifest import FrozenManifest, frozen_manifest_filename
from clm.release.ledger import Ledger

FROZEN_NAME = frozen_manifest_filename("materials")

SPEC = """
<course>
  <name><de>T</de><en>T</en></name>
  <prog-lang>python</prog-lang>
  <project-slug>ml</project-slug>
  <sections>
    <section id="w01">
      <name><de>S1</de><en>S1</en></name>
      <topics><topic>intro</topic><topic>loops</topic></topics>
    </section>
    <section id="w02">
      <name><de>S2</de><en>S2</en></name>
      <topics><topic>funcs</topic><topic>later</topic></topics>
    </section>
  </sections>
  <output-targets>
    <output-target name="shared">
      <path>output/shared</path>
      <kinds><kind>code-along</kind><kind>completed</kind></kinds>
    </output-target>
  </output-targets>
  <release-channels name="materials" source-target="shared">
    <channel name="de" lang="de" path="cohorts/de" ledger="release/de.txt"/>
  </release-channels>
</course>
""".strip()

# The current build (de, before language re-rooting). ``None`` topic = skeleton.
BUILD: dict[str, tuple[str | None, str, str]] = {
    "ml-de/Folien/01 Intro.py": ("intro", "de", "intro code-along"),
    "ml-de/Folien/01 Intro.ipynb": ("intro", "de", "intro notebook"),
    "ml-de/Folien/02 Loops.py": ("loops", "de", "loops NEW build"),
    "ml-de/Folien/03 Funcs.py": ("funcs", "de", "funcs renumbered"),
    "ml-de/Folien/data/funcs.csv": ("funcs", "de", "a,b\n"),
    "ml-de/Folien/05 Later.py": ("later", "de", "not yet taught"),
    "ml-de/code/starter/a.py": (None, "de", "starter a"),
    "ml-de/code/starter/new.py": (None, "de", "starter added after publish"),
    "ml-en/Slides/01 Intro.py": ("intro", "en", "english intro"),
}

# What students have (the published repo, rooted at the language dir).
PUBLISHED: dict[str, str] = {
    "Folien/01 Intro.py": "intro code-along",
    "Folien/01 Intro.ipynb": "intro notebook",
    "Folien/02 Loops.py": "loops as taught",
    "Folien/04 Funcs.py": "funcs renumbered",  # old name: renumbered since
    "Folien/data/funcs.csv": "a,b\n",
    "code/starter/a.py": "starter a",
    "README.md": "students' readme",
}


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} in {repo} failed:\n{result.stderr}")
    return result.stdout.strip()


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def _write_build(root: Path, build: dict[str, tuple[str | None, str, str]]) -> None:
    entries = [
        {
            "path": rel,
            "topic_id": topic,
            "kind": None if topic is None else "code-along",
            "format": "dir-group" if topic is None else "code",
            "language": lang,
            "content_hash": _sha(text),
        }
        for rel, (topic, lang, text) in build.items()
    ]
    _write_files(root, {rel: text for rel, (_t, _l, text) in build.items()})
    manifest = {"version": 1, "source_commit": "src123", "target": "shared", "files": entries}
    (root / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")


def _tree_hashes(root: Path) -> dict[str, str]:
    """Every file under *root* except ``.git/`` -> content hash."""
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    for key, value in {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        monkeypatch.setenv(key, value)


class Course:
    """A migrated-course fixture: spec, build, published remote, clone, old output tree."""

    def __init__(self, root: Path, *, spec: str = SPEC) -> None:
        self.root = root
        specs = root / "course-specs"
        specs.mkdir(parents=True)
        self.spec = specs / "course.xml"
        self.spec.write_text(spec, encoding="utf-8")
        self.build = root / "output" / "shared"
        _write_build(self.build, BUILD)

        # The published remote, seeded from a scratch working tree.
        self.remote = root / "remotes" / "shared-de.git"
        self.remote.mkdir(parents=True)
        _git(self.remote, "init", "-q", "--bare")
        seed = root / "seed"
        seed.mkdir()
        _git(seed, "init", "-q")
        _git(seed, "checkout", "-q", "-b", "master")
        _write_files(seed, PUBLISHED)
        _git(seed, "add", "-A")
        _git(seed, "commit", "-qm", "published by the old push path")
        _git(seed, "remote", "add", "origin", str(self.remote))
        _git(seed, "push", "-q", "origin", "master")

        # The channel destination: a clone of the published remote.
        self.dest = root / "cohorts" / "de"
        self.dest.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "clone", "-q", str(self.remote), str(self.dest)],
            check=True,
            capture_output=True,
        )
        self.head = _git(self.dest, "rev-parse", "HEAD")

        # The old push path: the output tree still points at the remote.
        self.output_de = self.build / "ml-de"
        _git(self.output_de, "init", "-q")
        _git(self.output_de, "remote", "add", "origin", str(self.remote))

        self.ledger = root / "release" / "de.txt"

    def adopt(self, *args: str):
        return CliRunner().invoke(
            release_group, ["adopt", str(self.spec), "--channel", "de", *args]
        )

    def frozen(self) -> FrozenManifest:
        return FrozenManifest.load(self.dest / FROZEN_NAME, channel="materials/de")

    def output_origin(self) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(self.output_de), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else None


@pytest.fixture
def course(tmp_path) -> Course:
    return Course(tmp_path)


def _rows(result) -> dict[str, dict]:
    docs = json.loads(result.stdout)
    assert len(docs) == 1
    return {row["topic_id"]: row for row in docs[0]["rows"]}


# ---------------------------------------------------------------------------
# Classification and selection
# ---------------------------------------------------------------------------


class TestClassification:
    def test_classify_same_differs_missing(self, tmp_path):
        _write_files(tmp_path, {"a.txt": "A", "b.txt": "changed"})
        entries = [
            {"path": "a.txt", "content_hash": _sha("A")},
            {"path": "b.txt", "content_hash": _sha("B")},
            {"path": "c.txt", "content_hash": _sha("C")},
            {"path": ".git/index", "content_hash": _sha("x")},
        ]
        assert [(f.path, f.status) for f in classify_files(entries, tmp_path)] == [
            ("a.txt", SAME),
            ("b.txt", DIFFERS),
            ("c.txt", MISSING),
        ]

    def test_entry_without_hash_counts_as_differs(self, tmp_path):
        _write_files(tmp_path, {"a.txt": "A"})
        (fc,) = classify_files([{"path": "a.txt"}], tmp_path)
        assert fc.status == DIFFERS

    def test_dry_run_json_classifies_with_lang_rerooting(self, course):
        before = _tree_hashes(course.dest)
        result = course.adopt("--all-present", "--dry-run", "--json")
        assert result.exit_code == 0, result.output

        rows = _rows(result)
        # Re-rooted at the language dir, and the en build never counts.
        assert rows["intro"]["action"] == ADOPT
        assert (rows["intro"]["same"], rows["intro"]["differs"], rows["intro"]["missing"]) == (
            2,
            0,
            0,
        )
        assert rows["loops"]["action"] == ADOPT
        assert rows["loops"]["differs_paths"] == ["Folien/02 Loops.py"]
        assert rows["funcs"]["action"] == REFUSE_MISSING
        assert rows["funcs"]["missing_paths"] == ["Folien/03 Funcs.py"]
        assert rows["funcs"]["same"] == 1
        # Nothing of `later` was ever published: not selected by --all-present.
        assert "later" not in rows

        doc = json.loads(result.stdout)[0]
        assert doc["source_commit"] == f"published:{course.head}"
        assert doc["skeleton"]["missing"] == ["code/starter/new.py"]
        assert [r["action"] for r in doc["output_remotes"]] == ["would-remove"]

        # A dry run records nothing anywhere.
        assert _tree_hashes(course.dest) == before
        assert not (course.dest / FROZEN_NAME).exists()
        assert not course.ledger.exists()
        assert course.output_origin() == str(course.remote)

    def test_section_selector(self, course):
        result = course.adopt("--section", "id:w01", "--dry-run", "--json")
        assert result.exit_code == 0, result.output
        assert set(_rows(result)) == {"intro", "loops"}

    def test_explicit_unbuilt_topic_is_skipped(self, course, tmp_path):
        manifest = {"files": []}
        plan = plan_adopt(
            manifest=manifest,
            topic_ids=["intro"],
            frozen=FrozenManifest(channel="x"),
            dest_root=course.dest,
        )
        assert plan.topics[0].action == SKIP_UNBUILT


class TestSelectorRequired:
    def test_no_selector_is_a_usage_error(self, course):
        result = course.adopt()
        assert result.exit_code == 2
        assert "Choose what to adopt" in result.output
        assert not (course.dest / FROZEN_NAME).exists()

    def test_all_present_excludes_explicit_selection(self, course):
        result = course.adopt("--all-present", "intro")
        assert result.exit_code == 2

    def test_unknown_topic_is_rejected(self, course):
        result = course.adopt("nope")
        assert result.exit_code != 0
        assert "Unknown topic" in result.output


# ---------------------------------------------------------------------------
# The real run: bookkeeping only
# ---------------------------------------------------------------------------


class TestAdopt:
    def test_adopt_writes_only_the_frozen_manifest_and_ledger(self, course):
        before = _tree_hashes(course.dest)
        result = course.adopt("--all-present")
        assert result.exit_code == 0, result.output

        after = _tree_hashes(course.dest)
        assert set(after) - set(before) == {FROZEN_NAME}
        assert {k: v for k, v in after.items() if k != FROZEN_NAME} == before
        assert Ledger.load(course.ledger).released == ["intro", "loops"]

    def test_records_digest_commit_and_skeleton(self, course):
        assert course.adopt("--all-present").exit_code == 0
        frozen = course.frozen()
        assert frozen.skeleton_frozen is True
        assert set(frozen.frozen) == {"intro", "loops"}

        intro = frozen.frozen["intro"]
        build_intro = [
            {"content_hash": _sha(text)}
            for rel, (topic, lang, text) in BUILD.items()
            if topic == "intro" and lang == "de"
        ]
        # differs == 0: the destination digest equals the build digest.
        assert intro.topic_digest == topic_digest_from_files(build_intro)
        assert intro.differs_from_build is False
        assert intro.source_commit == f"published:{course.head}"

        loops = frozen.frozen["loops"]
        assert loops.topic_digest == topic_digest_from_files(
            [{"content_hash": _sha("loops as taught")}]
        )
        assert loops.topic_digest != topic_digest_from_files(
            [{"content_hash": _sha("loops NEW build")}]
        )
        assert loops.differs_from_build is True
        raw = json.loads((course.dest / FROZEN_NAME).read_text(encoding="utf-8"))
        assert raw["frozen"]["loops"]["differs_from_build"] is True
        # Ordinary records keep the pre-#1140 shape.
        assert "differs_from_build" not in raw["frozen"]["intro"]

    def test_later_sync_copies_nothing(self, course):
        assert course.adopt("--all-present").exit_code == 0
        before = _tree_hashes(course.dest)

        runner = CliRunner()
        plan = runner.invoke(
            release_group, ["sync", str(course.spec), "--channel", "de", "--dry-run", "--json"]
        )
        assert plan.exit_code == 0, plan.output
        doc = json.loads(plan.stdout)[0]
        assert {r["topic_id"]: r["action"] for r in doc["rows"]} == {
            "intro": SKIP_FROZEN,
            "loops": SKIP_FROZEN,
        }
        assert doc["skeleton"]["action"] == "frozen"

        real = runner.invoke(release_group, ["sync", str(course.spec), "--channel", "de"])
        assert real.exit_code == 0, real.output
        assert "Copied 0 file(s)" in real.output
        assert _tree_hashes(course.dest) == before

    def test_rerun_skips_frozen_topics(self, course):
        assert course.adopt("intro").exit_code == 0
        again = course.adopt("intro", "--dry-run", "--json")
        assert _rows(again)["intro"]["action"] == SKIP_FROZEN

    def test_unknown_commit_without_a_repo(self, tmp_path):
        course = Course(tmp_path)
        # Make the destination a plain directory holding the same bytes.
        plain = tmp_path / "plain"
        _write_files(plain, PUBLISHED)
        import shutil

        shutil.rmtree(course.dest, onexc=_force_remove)
        shutil.copytree(plain, course.dest)
        assert course.adopt("intro").exit_code == 0
        assert course.frozen().frozen["intro"].source_commit == "published:unknown"


def _force_remove(func, path, _exc):
    import os
    import stat

    os.chmod(path, stat.S_IWRITE)
    func(path)


class TestDeliverMissing:
    def test_delivers_only_missing_files_and_never_overwrites(self, course):
        (course.dest / "Folien/data/funcs.csv").write_bytes(b"student edit\n")
        result = course.adopt("funcs", "--deliver-missing")
        assert result.exit_code == 0, result.output

        assert (course.dest / "Folien/03 Funcs.py").read_bytes() == b"funcs renumbered"
        assert (course.dest / "Folien/data/funcs.csv").read_bytes() == b"student edit\n"
        record = course.frozen().frozen["funcs"]
        assert record.differs_from_build is True
        assert record.topic_digest == topic_digest_from_files(
            [{"content_hash": _sha("funcs renumbered")}, {"content_hash": _sha("student edit\n")}]
        )

    def test_without_flag_missing_topic_is_refused(self, course):
        result = course.adopt("funcs")
        assert result.exit_code == 0, result.output
        assert "refuse-missing" in result.output
        assert not course.frozen().is_frozen("funcs")
        assert not (course.dest / "Folien/03 Funcs.py").exists()
        assert not course.ledger.exists() or "funcs" not in Ledger.load(course.ledger).released

    def test_apply_never_overwrites_a_file_that_appeared_after_planning(self, course):
        manifest = json.loads((course.build / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        from clm.core.provenance_manifest import restrict_manifest_to_language

        manifest = restrict_manifest_to_language(manifest, "de", "ml-de")
        frozen = FrozenManifest(channel="materials/de")
        plan = plan_adopt(
            manifest=manifest,
            topic_ids=["funcs"],
            frozen=frozen,
            dest_root=course.dest,
            deliver_missing=True,
        )
        assert plan.topics[0].to_deliver == ("Folien/03 Funcs.py",)
        (course.dest / "Folien/03 Funcs.py").write_bytes(b"raced in")
        result = apply_adopt(
            plan=plan,
            manifest=manifest,
            source_root=course.build / "ml-de",
            dest_root=course.dest,
            frozen=frozen,
            source_commit="published:x",
            copied_at="t",
        )
        assert result.delivered_files == ()
        assert (course.dest / "Folien/03 Funcs.py").read_bytes() == b"raced in"


# ---------------------------------------------------------------------------
# D6a: adopt cuts the old push path
# ---------------------------------------------------------------------------


class TestOutputRemoteRemoval:
    def test_adopt_removes_matching_origin_and_prints_restore(self, course):
        result = course.adopt("intro")
        assert result.exit_code == 0, result.output
        assert course.output_origin() is None
        assert f'remote add origin "{course.remote}"' in result.output

    def test_keep_output_remote(self, course):
        result = course.adopt("intro", "--keep-output-remote")
        assert result.exit_code == 0, result.output
        assert course.output_origin() == str(course.remote)

    def test_dry_run_leaves_the_remote(self, course):
        assert course.adopt("intro", "--dry-run").exit_code == 0
        assert course.output_origin() == str(course.remote)

    def test_a_different_origin_is_never_touched(self, course):
        other = course.root / "remotes" / "elsewhere.git"
        _git(course.output_de, "remote", "set-url", "origin", str(other))
        result = course.adopt("intro")
        assert result.exit_code == 0, result.output
        assert course.output_origin() == str(other)


# ---------------------------------------------------------------------------
# D6b: output-target git push / init / sync refuse a channel's origin
# ---------------------------------------------------------------------------


class TestChannelRemoteGuard:
    def _git_cli(self, course: Course, *args: str):
        return CliRunner().invoke(git_group, [args[0], str(course.spec), *args[1:]])

    def test_push_to_channel_origin_is_refused(self, course):
        result = self._git_cli(course, "push", "--target", "shared")
        assert result.exit_code == 1
        assert "Refused" in result.output
        assert "materials/de" in result.output

    def test_push_override(self, course):
        result = self._git_cli(course, "push", "--target", "shared", "--allow-channel-remote")
        assert "Refused" not in result.output

    def test_sync_to_channel_origin_is_refused(self, course):
        result = self._git_cli(course, "sync", "--target", "shared", "-m", "x")
        assert result.exit_code == 1
        assert "Refused" in result.output
        # Refused before staging: nothing was committed in the output tree.
        probe = subprocess.run(
            ["git", "-C", str(course.output_de), "rev-parse", "--verify", "-q", "HEAD"],
            capture_output=True,
        )
        assert probe.returncode != 0

    def test_channel_mode_is_not_refused(self, course):
        result = self._git_cli(course, "push", "--channel", "de")
        assert "Refused" not in result.output

    def test_init_refuses_to_add_a_channel_origin(self, course, monkeypatch):
        import shutil

        shutil.rmtree(course.output_de / ".git", onexc=_force_remove)
        fake = [
            OutputRepo(
                path=course.output_de,
                target_name="shared",
                language="de",
                remote_url=str(course.remote),
            )
        ]
        monkeypatch.setattr("clm.cli.commands.git.find_output_repos", lambda *a, **k: fake)
        result = self._git_cli(course, "init", "--target", "shared")
        assert result.exit_code == 1
        assert "Refused" in result.output
        assert not (course.output_de / ".git").exists()

        allowed = self._git_cli(course, "init", "--target", "shared", "--allow-channel-remote")
        assert "Refused" not in allowed.output
        assert (course.output_de / ".git").exists()

    def test_guard_matches_url_spellings(self, course):
        guard = ChannelRemoteGuard(course.spec, allow=False)
        repo = OutputRepo(path=course.output_de, target_name="shared", language="de")
        assert guard.channel_for(repo, str(course.remote) + "/") == "materials/de"
        assert guard.channel_for(repo, str(course.root / "remotes" / "other.git")) is None
        assert (
            ChannelRemoteGuard(course.spec, allow=True).channel_for(repo, str(course.remote))
            is None
        )


class TestNormalizeRemoteUrl:
    def test_https_ssh_and_scp_forms_agree(self):
        keys = {
            normalize_remote_url("https://gitlab.com/Shared/cpp-de.git"),
            normalize_remote_url("ssh://git@gitlab.com/shared/cpp-de"),
            normalize_remote_url("git@gitlab.com:shared/cpp-de.git"),
            normalize_remote_url("https://user@gitlab.com:443/shared/cpp-de/"),
        }
        assert keys == {"gitlab.com/shared/cpp-de"}

    def test_different_repos_differ(self):
        assert normalize_remote_url("git@gitlab.com:shared/cpp-de") != normalize_remote_url(
            "git@gitlab.com:shared/cpp-en"
        )

    def test_local_paths_resolve_relative_to_base(self, tmp_path):
        absolute = normalize_remote_url(str(tmp_path / "r.git"))
        assert normalize_remote_url("../r.git", base=tmp_path / "wt") == absolute
        assert normalize_remote_url((tmp_path / "r").as_uri()) == absolute


# ---------------------------------------------------------------------------
# Shared destination: one frozen manifest per stream
# ---------------------------------------------------------------------------

SPEC_SHARED = SPEC.replace(
    """  </output-targets>""",
    """    <output-target name="completed">
      <path>output/completed</path>
      <kinds><kind>completed</kind></kinds>
    </output-target>
  </output-targets>""",
).replace(
    """</release-channels>""",
    """</release-channels>
  <release-channels name="solutions" source-target="completed">
    <channel name="de" lang="de" path="cohorts/de" ledger="release/solutions-de.txt"/>
  </release-channels>""",
)


def test_shared_destination_keeps_one_frozen_manifest_per_stream(tmp_path):
    course = Course(tmp_path, spec=SPEC_SHARED)
    _write_build(
        tmp_path / "output" / "completed",
        {"ml-de/Folien/data/funcs.csv": ("funcs", "de", "a,b\n")},
    )
    runner = CliRunner()
    first = runner.invoke(
        release_group, ["adopt", str(course.spec), "--channel", "materials/de", "intro"]
    )
    assert first.exit_code == 0, first.output
    materials = (course.dest / FROZEN_NAME).read_bytes()

    second = runner.invoke(
        release_group, ["adopt", str(course.spec), "--channel", "solutions/de", "funcs"]
    )
    assert second.exit_code == 0, second.output
    solutions = FrozenManifest.load(
        course.dest / frozen_manifest_filename("solutions"), channel="solutions/de"
    )
    assert set(solutions.frozen) == {"funcs"}
    assert solutions.skeleton_frozen is True
    # The other stream's record is untouched.
    assert (course.dest / FROZEN_NAME).read_bytes() == materials


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------


class TestReviewFollowUps:
    def test_nothing_adopted_records_nothing(self, course):
        """All refused: no frozen manifest (so a later sync still delivers the
        skeleton), no ledger entry, and the output remote is left alone."""
        result = course.adopt("funcs")
        assert result.exit_code == 0, result.output
        assert "Nothing adopted" in result.output
        assert not (course.dest / FROZEN_NAME).exists()
        assert not course.ledger.exists()
        assert course.output_origin() == str(course.remote)

    def test_rerun_repairs_a_missing_ledger_entry(self, course):
        assert course.adopt("intro").exit_code == 0
        course.ledger.unlink()  # as if interrupted between freeze and ledger write
        again = course.adopt("intro")
        assert again.exit_code == 0, again.output
        assert Ledger.load(course.ledger).released == ["intro"]

    def test_push_guard_uses_the_push_url(self, course):
        harmless = course.root / "remotes" / "harmless.git"
        _git(course.output_de, "remote", "set-url", "origin", str(harmless))
        _git(course.output_de, "remote", "set-url", "--push", "origin", str(course.remote))
        result = CliRunner().invoke(git_group, ["push", str(course.spec), "--target", "shared"])
        assert result.exit_code == 1
        assert "Refused" in result.output

    def test_adopt_removes_an_origin_whose_pushurl_hits_the_channel(self, course):
        harmless = course.root / "remotes" / "harmless.git"
        _git(course.output_de, "remote", "set-url", "origin", str(harmless))
        _git(course.output_de, "remote", "set-url", "--push", "origin", str(course.remote))
        assert course.adopt("intro").exit_code == 0
        assert course.output_origin() is None

    def test_restore_command_quotes_the_url(self, course):
        result = course.adopt("intro")
        assert f'remote add origin "{course.remote}"' in result.output


def test_userless_scp_url_matches_other_spellings():
    assert normalize_remote_url("gitlab.example.com:courses/py-de.git") == normalize_remote_url(
        "https://gitlab.example.com/courses/py-de"
    )
    # A Windows drive letter is not an scp host: the drive survives as part
    # of a local path (absolute on Windows, cwd-relative elsewhere) instead
    # of turning into the scp key "c/repos/py-de".
    assert normalize_remote_url(r"C:\repos\py-de.git").endswith("c:/repos/py-de")
