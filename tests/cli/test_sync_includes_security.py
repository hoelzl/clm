"""Security regression tests for the ``.clm-include`` ledger removal path (S4).

``clm course sync-includes --remove`` deletes whatever the per-topic
``.clm-include`` ledger names. The ledger is a file inside the course repo,
so its contents are only as trustworthy as the repo: a hostile or corrupted
ledger must never be able to steer deletion outside the topic directory.

These tests surgically corrupt a real ledger (produced by a real
materialization run) and assert the security contract:

- absolute paths (native and Windows drive form, both slash styles) are
  refused and the outside file survives;
- ``..`` traversals (both slash styles) are refused;
- a ledger path that travels *through* an in-topic symlinked directory is
  refused (the resolve step must follow the symlink);
- an entry that names the in-topic symlink itself is honored — removing a
  legitimate ``--mode=symlink`` materialization unlinks the link, not its
  target (positive behavior preserved);
- refusal fails closed: nonzero exit, outside sentinels intact, the ledger
  is NOT deleted as though cleanup had succeeded;
- a mixed ledger does not partially delete earlier valid entries before the
  invalid one is discovered (the whole removal plan is validated first);
- ``--dry-run`` never touches the filesystem;
- positive pins: valid nested ledger paths are still removed, and the
  removal still spares untracked user files.

Adversarial review 2026-07-24, finding S4 (HIGH).
Tracked in #798 (Phase 4).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from clm.cli.commands.course.sync_includes import LEDGER_NAME
from tests.cli.test_sync_includes import _invoke, _make_include_source, _make_topic, _write_spec

# Four `..` segments climb from <course>/slides/module_100/topic_010_intro
# to the course root's parent (where the outside sentinels live).
_DOTDOT_FILE = "../../../../outside_file.txt"
_DOTDOT_DIR = "../../../../outside_dir"

_SPEC_REL = Path("course-specs") / "test.xml"


def _materialize(tmp_path: Path) -> tuple[Path, Path]:
    """Create course + topic, run a real sync so a real ledger exists."""
    topic_dir = _make_topic(tmp_path, "module_100", "topic_010_intro")
    _make_include_source(tmp_path)
    spec = _write_spec(
        tmp_path,
        """\
        <sections><section>
          <name><de>S</de><en>S</en></name>
          <topics>
            <topic>
              intro
              <include source="examples/pkg" as="pkg"/>
            </topic>
          </topics>
        </section></sections>""",
    )
    result = _invoke("course", "sync-includes", str(spec), "--data-dir", str(tmp_path))
    assert result.exit_code == 0
    assert (topic_dir / LEDGER_NAME).is_file()
    return topic_dir, spec


def _surgeon_ledger(topic_dir: Path, entries: list[dict]) -> None:
    """Overwrite the ledger's entries wholesale (hostile/corrupted ledger)."""
    ledger = topic_dir / LEDGER_NAME
    data = json.loads(ledger.read_text(encoding="utf-8"))
    data["entries"] = entries
    ledger.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _patch_entry(topic_dir: Path, **overrides: str) -> None:
    """Corrupt only the given fields of the (single) existing entry."""
    ledger = topic_dir / LEDGER_NAME
    data = json.loads(ledger.read_text(encoding="utf-8"))
    data["entries"][0].update(overrides)
    ledger.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _outside_sentinels(tmp_path: Path) -> tuple[Path, Path]:
    """Sentinels in tmp_path (the course root's parent — outside the course)."""
    outside_file = tmp_path / "outside_file.txt"
    outside_file.write_text("SENTINEL-FILE\n", encoding="utf-8")
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir(exist_ok=True)
    (outside_dir / "keep.txt").write_text("SENTINEL-DIR\n", encoding="utf-8")
    return outside_file, outside_dir


def _remove(tmp_path: Path, spec: Path, *extra: str):
    return _invoke(
        "course", "sync-includes", str(spec), "--data-dir", str(tmp_path), "--remove", *extra
    )


class TestLedgerPathValidationRefused:
    """Hostile ledger entries must be refused, deleting nothing outside."""

    @pytest.mark.parametrize(
        "as_path",
        [
            pytest.param(_DOTDOT_FILE, id="dotdot-forward"),
            pytest.param("..\\..\\..\\..\\outside_file.txt", id="dotdot-backslash"),
            pytest.param(_DOTDOT_DIR, id="dotdot-forward-dir"),
            pytest.param("..\\..\\..\\..\\outside_dir", id="dotdot-backslash-dir"),
        ],
    )
    def test_remove_refuses_dotdot_ledger_entries(self, tmp_path, as_path):
        topic_dir, spec = _materialize(tmp_path)
        outside_file, outside_dir = _outside_sentinels(tmp_path)
        _patch_entry(topic_dir, as_path=as_path)

        result = _remove(tmp_path, spec)

        # Fail closed: refusal, not silent success.
        assert result.exit_code != 0, result.output
        # Outside sentinels intact (both the exact target file and the tree).
        assert outside_file.read_text(encoding="utf-8") == "SENTINEL-FILE\n"
        assert (outside_dir / "keep.txt").read_text(encoding="utf-8") == "SENTINEL-DIR\n"
        # The ledger itself is not deleted as though cleanup had succeeded.
        assert (topic_dir / LEDGER_NAME).is_file()
        # The materialization itself is untouched too (refusal is atomic).
        assert (topic_dir / "pkg").is_dir()

    @pytest.mark.parametrize(
        "as_path",
        [
            # Windows drive paths are only absolute ON WINDOWS — on POSIX
            # `Path("C:/x")` is a relative path that stays inside the topic
            # dir (contained, no escape possible), so the refusal contract
            # is Windows-specific for these shapes. CI's Linux runners skip
            # them; the local Windows run exercises the refusal.
            pytest.param(
                "C:/Users/somebody/outside.txt",
                id="drive-forward",
                marks=pytest.mark.skipif(
                    sys.platform != "win32", reason="drive paths only escape on Windows"
                ),
            ),
            pytest.param(
                "C:\\Users\\somebody\\outside.txt",
                id="drive-backslash",
                marks=pytest.mark.skipif(
                    sys.platform != "win32", reason="drive paths only escape on Windows"
                ),
            ),
            pytest.param("/etc/motd", id="posix-absolute"),
            # Windows extended-length prefix: after separator normalization
            # it starts with "/" and is refused as absolute (cross-platform).
            pytest.param("\\\\?\\C:\\outside.txt", id="extended-length"),
            # Drive-relative "C:" carries a drive, so `topic_dir / "C:"`
            # leaves the topic dir on Windows (POSIX treats it as a plain
            # filename — skip there).
            pytest.param(
                "C:",
                id="drive-relative",
                marks=pytest.mark.skipif(
                    sys.platform != "win32", reason="drive-relative only escapes on Windows"
                ),
            ),
        ],
    )
    def test_remove_refuses_absolute_ledger_entries(self, tmp_path, as_path):
        topic_dir, spec = _materialize(tmp_path)
        outside_file, outside_dir = _outside_sentinels(tmp_path)
        _patch_entry(topic_dir, as_path=as_path)

        result = _remove(tmp_path, spec)

        assert result.exit_code != 0, result.output
        assert outside_file.exists()
        assert (outside_dir / "keep.txt").exists()
        assert (topic_dir / LEDGER_NAME).is_file()
        # No deletion of the legit materialization either.
        assert (topic_dir / "pkg").is_dir()

    def test_remove_refuses_dot_for_topic_dir_itself(self, tmp_path):
        # "." normalizes to the topic dir itself; refusing it protects the
        # whole topic (slides_intro.py et al.) from a hostile ledger.
        topic_dir, spec = _materialize(tmp_path)
        _patch_entry(topic_dir, as_path=".")

        result = _remove(tmp_path, spec)

        assert result.exit_code != 0, result.output
        assert (topic_dir / "slides_intro.py").is_file()
        assert (topic_dir / "pkg").is_dir()
        assert (topic_dir / LEDGER_NAME).is_file()

    def test_remove_refuses_empty_ledger_entry(self, tmp_path):
        topic_dir, spec = _materialize(tmp_path)
        _patch_entry(topic_dir, as_path="   ")

        result = _remove(tmp_path, spec)

        assert result.exit_code != 0, result.output
        assert (topic_dir / LEDGER_NAME).is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege on Windows CI")
class TestLedgerSymlinkEscapes:
    def test_remove_refuses_path_through_symlinked_dir(self, tmp_path):
        """`link/inner` where link -> outside dir: resolve must follow it."""
        topic_dir, spec = _materialize(tmp_path)
        link_target = tmp_path / "outside_linktarget"
        (link_target / "inner").mkdir(parents=True)
        (link_target / "inner" / "treasure.txt").write_text("SENTINEL-SYMLINK\n", encoding="utf-8")
        os.symlink(link_target, topic_dir / "link", target_is_directory=True)
        _patch_entry(topic_dir, as_path="link/inner")

        result = _remove(tmp_path, spec)

        assert result.exit_code != 0, result.output
        # The outside tree survived: the link was NOT followed into it.
        assert (link_target / "inner" / "treasure.txt").read_text(
            encoding="utf-8"
        ) == "SENTINEL-SYMLINK\n"
        # Ledger kept; the link itself also stays (refused before deletion).
        assert (topic_dir / LEDGER_NAME).is_file()
        assert (topic_dir / "link").is_symlink()

    def test_remove_refuses_symlinked_intermediate_dir(self, tmp_path):
        """`vendor/pkg` where vendor -> outside dir: the entry's parent
        chain resolves outside; must be refused before deleting pkg."""
        topic_dir, spec = _materialize(tmp_path)
        link_target = tmp_path / "outside_target"
        (link_target / "pkg").mkdir(parents=True)
        (link_target / "pkg" / "treasure.txt").write_text(
            "SENTINEL-INTERMEDIATE\n", encoding="utf-8"
        )
        os.symlink(link_target, topic_dir / "vendor", target_is_directory=True)
        _patch_entry(topic_dir, as_path="vendor/pkg")

        result = _remove(tmp_path, spec)

        assert result.exit_code != 0, result.output
        assert (link_target / "pkg" / "treasure.txt").read_text(
            encoding="utf-8"
        ) == "SENTINEL-INTERMEDIATE\n"
        assert (topic_dir / LEDGER_NAME).is_file()
        assert (topic_dir / "vendor").is_symlink()

    def test_remove_symlink_entry_itself_is_honored(self, tmp_path):
        """Positive pin: removing a legit symlink materialization unlinks
        the link only — containment must not resolve the FINAL component."""
        topic_dir, spec = _materialize(tmp_path)
        source = tmp_path / "examples" / "pkg"
        link = topic_dir / "pkg"
        # Replace the copy materialization with a symlink to the source.
        shutil.rmtree(link)
        os.symlink(source, link, target_is_directory=True)
        ledger = topic_dir / LEDGER_NAME
        data = json.loads(ledger.read_text(encoding="utf-8"))
        data["entries"][0]["mode"] = "symlink"
        ledger.write_text(json.dumps(data, indent=2), encoding="utf-8")

        result = _remove(tmp_path, spec)

        assert result.exit_code == 0, result.output
        # The link is gone, the source it pointed at is intact.
        assert not link.exists()
        assert not link.is_symlink()
        assert (source / "__init__.py").is_file()
        assert (source / "core.py").is_file()
        assert not (topic_dir / LEDGER_NAME).exists()


@pytest.mark.skipif(
    sys.platform != "win32" or not hasattr(Path("."), "is_junction"),
    reason="NTFS junctions are Windows-only (Path.is_junction needs Python 3.12+)",
)
class TestLedgerJunctionEntries:
    def test_remove_junction_entry_unlinks_link_not_target(self, tmp_path):
        """A ledger entry naming an NTFS junction must unlink the junction,
        never traverse it into the target (rmtree refuses reparse points,
        so without the explicit unlink this crashed with OSError)."""
        topic_dir, spec = _materialize(tmp_path)
        outside = tmp_path / "outside_treasure"
        (outside / "crown").mkdir(parents=True)
        (outside / "crown" / "jewel.txt").write_text("JEWEL\n", encoding="utf-8")
        jct = topic_dir / "evil_junction"
        import subprocess

        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(jct), str(outside)],
            check=True,
            capture_output=True,
        )
        _patch_entry(topic_dir, as_path="evil_junction")

        result = _remove(tmp_path, spec)

        assert result.exit_code == 0, result.output
        assert (outside / "crown" / "jewel.txt").read_text(encoding="utf-8") == "JEWEL\n"
        assert not jct.exists()
        assert not (topic_dir / LEDGER_NAME).exists()


class TestRefusalMessageContract:
    def test_refusal_message_names_the_topic_directory_boundary(self, tmp_path):
        """The ``root_label`` plumbing is load-bearing: the refusal must say
        the deletion boundary is the *topic directory* (not the course root),
        so operators can tell the two validators apart."""
        topic_dir = _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            "<sections><section>\n  <name><de>S</de><en>S</en></name>\n"
            "  <topics><topic>\n    intro\n"
            '    <include source="examples/pkg" as="pkg"/>\n'
            "  </topic></topics>\n</section></sections>",
        )
        result = _invoke("course", "sync-includes", str(spec), "--data-dir", str(tmp_path))
        assert result.exit_code == 0, result.output
        _patch_entry(topic_dir, as_path="../../outside_evil.txt")
        result = _invoke(
            "course", "sync-includes", str(spec), "--data-dir", str(tmp_path), "--remove"
        )
        assert result.exit_code == 1
        combined = (result.stderr or "") + (result.output or "")
        assert "topic directory" in combined
        assert "course root" not in combined


class TestUnresolvedTopicRemove:
    def test_unresolved_topic_ledger_is_not_processed(self, tmp_path):
        """A topic that does not resolve is skipped before planning; a
        hostile ledger inside it is ignored (exit 0, nothing deleted).
        Pre-existing behavior — pinned so a future refactor that starts
        planning unresolved topics cannot silently change it."""
        topic_dir = _make_topic(tmp_path, "module_100", "topic_010_intro")
        _make_include_source(tmp_path)
        spec = _write_spec(
            tmp_path,
            "<sections><section>\n  <name><de>S</de><en>S</en></name>\n"
            "  <topics><topic>\n    ghost_topic_xyz\n"
            '    <include source="examples/pkg" as="pkg"/>\n'
            "  </topic></topics>\n</section></sections>",
        )
        result = _invoke("course", "sync-includes", str(spec), "--data-dir", str(tmp_path))
        assert result.exit_code == 0, result.output
        # A ledger in a skipped topic is neither processed nor cleaned up.
        ledger = topic_dir / LEDGER_NAME
        ledger.write_text(
            json.dumps(
                {"entries": [{"source": "examples/pkg", "as_path": "../../evil", "mode": "copy"}]}
            ),
            encoding="utf-8",
        )
        result = _invoke(
            "course", "sync-includes", str(spec), "--data-dir", str(tmp_path), "--remove"
        )
        assert result.exit_code == 0
        assert ledger.exists()
        combined = (result.output or "") + (result.stderr or "")
        assert "did not resolve" in combined or "unresolved topic" in combined


class TestMixedLedgerNoPartialDeletion:
    def test_invalid_entry_prevents_deleting_valid_ones(self, tmp_path):
        """If one entry is invalid, earlier VALID entries must not already be
        deleted — the whole plan is validated before any deletion happens."""
        topic_dir, spec = _materialize(tmp_path)
        outside_file, _outside_dir = _outside_sentinels(tmp_path)
        # Entry 0 valid (the real pkg materialization), entry 1 hostile.
        _surgeon_ledger(
            topic_dir,
            [
                {"as_path": "pkg", "source": "examples/pkg", "mode": "copy"},
                {"as_path": _DOTDOT_FILE, "source": "examples/pkg", "mode": "copy"},
            ],
        )

        result = _remove(tmp_path, spec)

        assert result.exit_code != 0, result.output
        # The VALID entry's materialization still stands.
        assert (topic_dir / "pkg").is_dir()
        assert (topic_dir / "pkg" / "__init__.py").is_file()
        assert outside_file.exists()
        assert (topic_dir / LEDGER_NAME).is_file()


class TestRemoveDryRunContract:
    def test_dry_run_refuses_but_touches_nothing(self, tmp_path):
        """`--remove --dry-run` with a hostile ledger: validation refusal
        must fire (fail closed), and nothing may be deleted anywhere."""
        topic_dir, spec = _materialize(tmp_path)
        outside_file, _outside_dir = _outside_sentinels(tmp_path)
        _patch_entry(topic_dir, as_path=_DOTDOT_FILE)

        result = _remove(tmp_path, spec, "--dry-run")

        assert result.exit_code != 0, result.output
        assert outside_file.exists()
        assert (topic_dir / LEDGER_NAME).is_file()
        assert (topic_dir / "pkg").is_dir()

    def test_dry_run_valid_ledger_prints_plan_and_deletes_nothing(self, tmp_path):
        """Positive pin: dry-run on a valid ledger reports the removal but
        leaves the materialization and the ledger in place."""
        topic_dir, spec = _materialize(tmp_path)

        result = _remove(tmp_path, spec, "--dry-run")

        assert result.exit_code == 0, result.output
        assert "would remove" in result.stdout
        assert (topic_dir / "pkg").is_dir()
        assert (topic_dir / LEDGER_NAME).is_file()


class TestRemovePositivePins:
    def test_valid_nested_ledger_path_is_removed(self, tmp_path):
        """Positive pin: nested `as` paths (vendor/pkg) still remove fine."""
        topic_dir, _spec = _materialize(tmp_path)
        # Rebuild the materialization at a nested path, update the ledger.
        shutil.rmtree(topic_dir / "pkg")
        (topic_dir / "vendor" / "pkg").mkdir(parents=True)
        (topic_dir / "vendor" / "pkg" / "__init__.py").write_text("V='1'\n", encoding="utf-8")
        _patch_entry(topic_dir, as_path="vendor/pkg")

        result = _remove(tmp_path, tmp_path / _SPEC_REL)

        assert result.exit_code == 0, result.output
        # The materialization is gone; the (now empty) parent shell that
        # contained it may remain — same as pre-fix behavior, which only
        # ever deleted the ledger-recorded path itself.
        assert not (topic_dir / "vendor" / "pkg").exists()
        assert not (topic_dir / LEDGER_NAME).exists()

    def test_untracked_user_file_survives_remove(self, tmp_path):
        """Positive pin: the original safety promise — bystanders stay."""
        topic_dir, spec = _materialize(tmp_path)
        bystander = topic_dir / "user_file.txt"
        bystander.write_text("keep me\n", encoding="utf-8")

        result = _remove(tmp_path, spec)

        assert result.exit_code == 0, result.output
        assert bystander.read_text(encoding="utf-8") == "keep me\n"
        assert not (topic_dir / "pkg").exists()
        assert not (topic_dir / LEDGER_NAME).exists()


class TestRemoveFailureIsolatedAcrossTopics:
    def test_hostile_ledger_in_one_topic_refuses_whole_run(self, tmp_path):
        """Two topics, one hostile ledger: fail-closed means the RUN refuses
        (no partial state): the valid topic's materialization and ledger stay
        untouched too, and the exit is nonzero."""
        topic_a, spec = _materialize(tmp_path)
        outside_file, _outside_dir = _outside_sentinels(tmp_path)
        # Second topic with its own (valid) ledger and materialization.
        topic_b = _make_topic(tmp_path, "module_100", "topic_020_deep")
        (topic_b / "pkg").mkdir()
        (topic_b / "pkg" / "__init__.py").write_text("V='1'\n", encoding="utf-8")
        (topic_b / LEDGER_NAME).write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [{"as_path": "pkg", "source": "examples/pkg", "mode": "copy"}],
                }
            ),
            encoding="utf-8",
        )
        _patch_entry(topic_a, as_path=_DOTDOT_FILE)

        result = _remove(tmp_path, spec)

        assert result.exit_code != 0, result.output
        # The valid topic was NOT cleaned (its ledger/materialization stay).
        assert (topic_b / "pkg" / "__init__.py").is_file()
        assert (topic_b / LEDGER_NAME).is_file()
        # Outside sentinels and the hostile ledger survive too.
        assert outside_file.exists()
        assert (topic_a / LEDGER_NAME).is_file()
