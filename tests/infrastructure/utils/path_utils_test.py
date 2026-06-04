import errno
import os
from pathlib import Path

import pytest

from clm.core.course_spec import OutputTargetSpec
from clm.core.output_target import OutputTarget
from clm.infrastructure.utils.path_utils import (
    SKIP_DIRS_FOR_COURSE,
    SKIP_DIRS_FOR_OUTPUT,
    SKIP_OUTPUT_FILE_GLOBS,
    SKIP_OUTPUT_FILE_PATTERNS,
    Format,
    Kind,
    Lang,
    atomic_write_bytes,
    ext_for,
    is_ignored_dir_for_course,
    is_ignored_dir_for_output,
    is_ignored_file_for_course,
    is_ignored_file_for_output,
    is_slides_file,
    output_path_for,
    output_specs,
    simplify_ordered_name,
    slide_family_key,
    split_lang_suffix,
)


def test_is_slides_file():
    assert is_slides_file(Path("slides_1.py"))
    assert is_slides_file(Path("slides_2.cpp"))
    assert is_slides_file(Path("slides_3.md"))
    assert not is_slides_file(Path("slides4.py"))
    assert not is_slides_file(Path("test.py"))


def test_is_slides_file_project_prefix():
    assert is_slides_file(Path("project_setup.md"))
    assert is_slides_file(Path("project_phase_01.py"))
    assert not is_slides_file(Path("project_readme.txt"))


class TestSplitLangSuffix:
    def test_de_suffix(self):
        assert split_lang_suffix(Path("slides_foo.de.py")) == "de"
        assert split_lang_suffix(Path("topic_bar.de.cpp")) == "de"
        assert split_lang_suffix(Path("project_baz.de.md")) == "de"

    def test_en_suffix(self):
        assert split_lang_suffix(Path("slides_foo.en.py")) == "en"
        assert split_lang_suffix(Path("topic_bar.en.cs")) == "en"

    def test_bilingual_returns_none(self):
        assert split_lang_suffix(Path("slides_foo.py")) is None
        assert split_lang_suffix(Path("topic_bar.cpp")) is None

    def test_non_slide_returns_none(self):
        # Even with a language-like suffix, non-slide names don't qualify.
        assert split_lang_suffix(Path("foo.de.py")) is None
        assert split_lang_suffix(Path("notes.en.md")) is None

    def test_unsupported_extension_returns_none(self):
        # Slide prefix but extension not in SUPPORTED_PROG_LANG_EXTENSIONS.
        assert split_lang_suffix(Path("slides_foo.de.txt")) is None

    def test_unrecognised_language_returns_none(self):
        # Random language tags do not qualify (split format is DE/EN only).
        assert split_lang_suffix(Path("slides_foo.fr.py")) is None


class TestSlideFamilyKey:
    def test_bilingual_path_is_its_own_family(self):
        assert slide_family_key(Path("slides_foo.py")) == "slides_foo.py"

    def test_de_path_maps_to_bilingual_companion(self):
        assert slide_family_key(Path("slides_foo.de.py")) == "slides_foo.py"

    def test_en_path_maps_to_bilingual_companion(self):
        assert slide_family_key(Path("slides_foo.en.py")) == "slides_foo.py"

    def test_split_pair_shares_family_key(self):
        de_key = slide_family_key(Path("slides_alpha.de.py"))
        en_key = slide_family_key(Path("slides_alpha.en.py"))
        bare_key = slide_family_key(Path("slides_alpha.py"))
        assert de_key == en_key == bare_key

    def test_non_slide_returns_none(self):
        assert slide_family_key(Path("foo.de.py")) is None
        assert slide_family_key(Path("README.md")) is None

    def test_handles_non_python_extensions(self):
        assert slide_family_key(Path("slides_foo.de.cpp")) == "slides_foo.cpp"
        assert slide_family_key(Path("topic_x.en.cs")) == "topic_x.cs"


def test_output_spec(course_1):
    unit = list(output_specs(course_1, Path("slides_1.py")))
    # 3 formats × 5 kinds × 2 languages = 30 outputs
    # (kinds: code-along, completed, trainer, recording, partial)
    assert len(unit) == 30

    # Half the outputs should be in each language.
    assert len([os for os in unit if os.language == Lang.DE]) == 15
    assert len([os for os in unit if os.language == Lang.EN]) == 15

    # We generate HTML, notebook, and code files for each language and kind.
    assert len([os for os in unit if os.format == Format.HTML]) == 10
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 10
    assert len([os for os in unit if os.format == Format.CODE]) == 10

    # Each kind has 3 formats × 2 languages = 6 outputs
    assert len([os for os in unit if os.kind == Kind.CODE_ALONG]) == 6
    assert len([os for os in unit if os.kind == Kind.COMPLETED]) == 6
    assert len([os for os in unit if os.kind == Kind.TRAINER]) == 6
    assert len([os for os in unit if os.kind == Kind.RECORDING]) == 6
    assert len([os for os in unit if os.kind == Kind.PARTIAL]) == 6

    os1 = unit[0]
    assert os1.language == Lang.DE
    assert os1.format == Format.HTML
    assert os1.kind == Kind.CODE_ALONG


def test_simplify_ordered_name():
    assert simplify_ordered_name("topic_100_abc_def") == "abc_def"
    assert simplify_ordered_name("topic_100_abc_def.py") == "abc_def"


def test_ext_for_python():
    assert ext_for("html", "python") == ".html"
    assert ext_for("notebook", "python") == ".ipynb"
    assert ext_for("code", "python") == ".py"


def test_ext_for_cpp():
    assert ext_for("html", "cpp") == ".html"
    assert ext_for("notebook", "cpp") == ".ipynb"
    assert ext_for("code", "cpp") == ".cpp"


def test_ext_for_typescript():
    assert ext_for("html", "typescript") == ".html"
    assert ext_for("notebook", "typescript") == ".ipynb"
    assert ext_for("code", "typescript") == ".ts"


# Tests for output_specs filtering


def test_output_specs_single_language_de(course_1):
    """Test that output_specs filters to only German when languages=['de']."""
    unit = list(output_specs(course_1, Path("slides_1.py"), languages=["de"]))

    # Should have 15 outputs (3 formats × 5 kinds × 1 language)
    assert len(unit) == 15

    # All outputs should be in German
    assert all(os.language == Lang.DE for os in unit)
    assert len([os for os in unit if os.language == Lang.EN]) == 0

    # Should still have all formats and kinds for German
    assert len([os for os in unit if os.format == Format.HTML]) == 5
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 5
    assert len([os for os in unit if os.format == Format.CODE]) == 5

    assert len([os for os in unit if os.kind == Kind.CODE_ALONG]) == 3
    assert len([os for os in unit if os.kind == Kind.COMPLETED]) == 3
    assert len([os for os in unit if os.kind == Kind.TRAINER]) == 3
    assert len([os for os in unit if os.kind == Kind.RECORDING]) == 3
    assert len([os for os in unit if os.kind == Kind.PARTIAL]) == 3


def test_output_specs_single_language_en(course_1):
    """Test that output_specs filters to only English when languages=['en']."""
    unit = list(output_specs(course_1, Path("slides_1.py"), languages=["en"]))

    # Should have 15 outputs (3 formats × 5 kinds × 1 language)
    assert len(unit) == 15

    # All outputs should be in English
    assert all(os.language == Lang.EN for os in unit)
    assert len([os for os in unit if os.language == Lang.DE]) == 0


def test_output_specs_recording_only(course_1):
    """Test that output_specs generates only recording outputs when kinds=['recording']."""
    unit = list(output_specs(course_1, Path("slides_1.py"), kinds=["recording"]))

    # Should have 6 recording outputs (3 formats × 2 languages)
    assert len(unit) == 6

    # All outputs should be recording kind
    assert all(os.kind == Kind.RECORDING for os in unit)

    # Should have both languages
    assert len([os for os in unit if os.language == Lang.DE]) == 3
    assert len([os for os in unit if os.language == Lang.EN]) == 3

    # Should have all formats including code
    assert len([os for os in unit if os.format == Format.HTML]) == 2
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 2
    assert len([os for os in unit if os.format == Format.CODE]) == 2


def test_output_specs_legacy_speaker_alias_resolves_to_recording(course_1):
    """``kinds=['speaker']`` is the deprecated alias and resolves to recording."""
    unit = list(output_specs(course_1, Path("slides_1.py"), kinds=["speaker"]))

    # Should produce the same 6 outputs as recording-only
    assert len(unit) == 6
    assert all(os.kind == Kind.RECORDING for os in unit)


def test_output_specs_recording_only_single_language(course_1):
    """Test combining recording-only with single language filter."""
    unit = list(output_specs(course_1, Path("slides_1.py"), languages=["en"], kinds=["recording"]))

    # Should have 3 outputs (3 formats × 1 language)
    assert len(unit) == 3

    # All outputs should be English and recording
    assert all(os.language == Lang.EN for os in unit)
    assert all(os.kind == Kind.RECORDING for os in unit)

    # Should have all formats
    assert len([os for os in unit if os.format == Format.HTML]) == 1
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 1
    assert len([os for os in unit if os.format == Format.CODE]) == 1


def test_output_specs_completed_only(course_1):
    """Test filtering to only completed outputs."""
    unit = list(output_specs(course_1, Path("slides_1.py"), kinds=["completed"]))

    # 2 languages x (2 HTML/notebook + 1 code) = 6 outputs
    assert len(unit) == 6

    # All outputs should be completed
    assert all(os.kind == Kind.COMPLETED for os in unit)

    # Should have code outputs
    assert len([os for os in unit if os.format == Format.CODE]) == 2


def test_output_specs_code_along_only(course_1):
    """Test filtering to only code-along outputs."""
    unit = list(output_specs(course_1, Path("slides_1.py"), kinds=["code-along"]))

    # 3 formats × 2 languages = 6 outputs
    assert len(unit) == 6

    # All outputs should be code-along
    assert all(os.kind == Kind.CODE_ALONG for os in unit)

    # Code format is now generated for all kinds
    assert len([os for os in unit if os.format == Format.CODE]) == 2


def test_output_specs_multiple_kinds(course_1):
    """Test filtering to multiple specific kinds."""
    unit = list(output_specs(course_1, Path("slides_1.py"), kinds=["code-along", "completed"]))

    # 3 formats × 2 kinds × 2 languages = 12 outputs
    assert len(unit) == 12

    # Should have code-along and completed but not the private kinds
    assert len([os for os in unit if os.kind == Kind.CODE_ALONG]) == 6
    assert len([os for os in unit if os.kind == Kind.COMPLETED]) == 6
    assert len([os for os in unit if os.kind == Kind.RECORDING]) == 0
    assert len([os for os in unit if os.kind == Kind.TRAINER]) == 0


def test_output_specs_with_skip_html_and_filters(course_1):
    """Test that skip_html works together with language and kinds filters."""
    unit = list(
        output_specs(
            course_1,
            Path("slides_1.py"),
            skip_html=True,
            languages=["de"],
            kinds=["recording"],
        )
    )

    # Should have 2 outputs (1 language × 2 formats: notebook and code)
    assert len(unit) == 2

    # All outputs should be German and recording
    assert all(os.language == Lang.DE for os in unit)
    assert all(os.kind == Kind.RECORDING for os in unit)

    # Should have notebook and code formats, but no HTML
    assert len([os for os in unit if os.format == Format.NOTEBOOK]) == 1
    assert len([os for os in unit if os.format == Format.CODE]) == 1
    assert len([os for os in unit if os.format == Format.HTML]) == 0


# Tests for output_path_for with skip_toplevel


class TestOutputPathFor:
    """Tests for output_path_for function and skip_toplevel parameter."""

    def test_output_path_for_default_includes_public(self, tmp_path):
        """Test that by default, public output includes 'public' in path."""
        path = output_path_for(tmp_path, is_speaker=False, lang="de", dir_name="my-course-de")

        assert "public" in path.parts
        assert "my-course-de" in path.parts
        assert path == tmp_path / "public" / "my-course-de"

    def test_output_path_for_default_includes_speaker(self, tmp_path):
        """Test that by default, speaker output includes 'speaker' in path."""
        path = output_path_for(tmp_path, is_speaker=True, lang="de", dir_name="my-course-de")

        assert "speaker" in path.parts
        assert "public" not in path.parts
        assert "my-course-de" in path.parts
        assert path == tmp_path / "speaker" / "my-course-de"

    def test_output_path_for_skip_toplevel_excludes_public(self, tmp_path):
        """Test that skip_toplevel=True excludes 'public' from path."""
        path = output_path_for(
            tmp_path, is_speaker=False, lang="de", dir_name="my-course-de", skip_toplevel=True
        )

        assert "public" not in path.parts
        assert "speaker" not in path.parts
        assert "my-course-de" in path.parts
        assert path == tmp_path / "my-course-de"

    def test_output_path_for_skip_toplevel_excludes_speaker(self, tmp_path):
        """Test that skip_toplevel=True excludes 'speaker' from path."""
        path = output_path_for(
            tmp_path, is_speaker=True, lang="de", dir_name="my-course-de", skip_toplevel=True
        )

        assert "speaker" not in path.parts
        assert "public" not in path.parts
        assert "my-course-de" in path.parts
        assert path == tmp_path / "my-course-de"

    def test_output_path_for_both_audiences_same_with_skip_toplevel(self, tmp_path):
        """Test that with skip_toplevel, both audiences produce the same path."""
        public_path = output_path_for(
            tmp_path, is_speaker=False, lang="de", dir_name="my-course-de", skip_toplevel=True
        )
        speaker_path = output_path_for(
            tmp_path, is_speaker=True, lang="de", dir_name="my-course-de", skip_toplevel=True
        )

        # Both paths should be identical when skip_toplevel=True
        assert public_path == speaker_path


class TestOutputSpecsWithExplicitTarget:
    """Tests for output_specs with explicit targets (skip_toplevel behavior)."""

    def test_output_specs_with_explicit_target_skips_toplevel(self, course_1, tmp_path):
        """Test that output_specs with explicit target skips public/speaker directories."""
        # Create an explicit target
        spec = OutputTargetSpec(name="test", path=str(tmp_path / "output"))
        target = OutputTarget.from_spec(spec, tmp_path)
        assert target.is_explicit is True

        # Get output specs with the target
        specs = list(
            output_specs(
                course_1,
                tmp_path / "output",
                languages=["en"],
                kinds=["completed"],
                target=target,
            )
        )

        # All specs should have paths without public/speaker
        for spec in specs:
            assert "public" not in str(spec.output_dir)
            assert "speaker" not in str(spec.output_dir)

    def test_output_specs_with_default_target_includes_toplevel(self, course_1, tmp_path):
        """Test that output_specs with default target includes public/speaker directories."""
        # Create a default (non-explicit) target and apply filters to restrict to completed only
        target = OutputTarget.default_target(tmp_path / "output")
        assert target.is_explicit is False

        # Apply filters to restrict to completed kind only (which uses "public" directory)
        target = target.with_cli_filters(languages=["en"], kinds=["completed"])

        # Get output specs with the target
        specs = list(
            output_specs(
                course_1,
                tmp_path / "output",
                target=target,
            )
        )

        # All specs should have paths with public (completed is public, not speaker)
        for spec in specs:
            assert "public" in str(spec.output_dir)
            assert "speaker" not in str(spec.output_dir)

    def test_output_specs_without_target_includes_toplevel(self, course_1, tmp_path):
        """Test that output_specs without target includes public/speaker directories."""
        # Get output specs without a target
        specs = list(
            output_specs(
                course_1,
                tmp_path / "output",
                languages=["en"],
                kinds=["recording"],
            )
        )

        # All specs should have paths under the private (``speaker/``) toplevel
        for spec in specs:
            assert "speaker" in str(spec.output_dir)


class TestSidecarSubdirSkips:
    """Topic sidecar subdirectories: ``voiceover/`` is fully walk-excluded;
    ``cassettes/`` (and legacy ``_cassettes/``) are output-suppressed."""

    def test_voiceover_dir_in_skip_dirs_for_course(self):
        # Voiceover companions are merged host-side, so the dir is excluded from
        # the course walk entirely (and therefore from output too).
        assert "voiceover" in SKIP_DIRS_FOR_COURSE
        assert "voiceover" in SKIP_DIRS_FOR_OUTPUT  # superset

    def test_cassettes_dirs_output_suppressed_but_in_course(self):
        # Cassettes are read by the kernel at runtime: kept in the course map
        # (NOT in SKIP_DIRS_FOR_COURSE) but suppressed from output.
        assert "cassettes" in SKIP_DIRS_FOR_OUTPUT
        assert "_cassettes" in SKIP_DIRS_FOR_OUTPUT
        assert "cassettes" not in SKIP_DIRS_FOR_COURSE
        assert "_cassettes" not in SKIP_DIRS_FOR_COURSE

    def test_is_ignored_dir_for_course_voiceover(self, tmp_path):
        assert is_ignored_dir_for_course(tmp_path / "topic_x" / "voiceover")

    def test_is_ignored_dir_for_course_cassettes_not_skipped(self, tmp_path):
        # cassettes/ must still be walked into the course map for runtime use.
        assert not is_ignored_dir_for_course(tmp_path / "topic_x" / "cassettes")
        assert not is_ignored_dir_for_course(tmp_path / "topic_x" / "_cassettes")

    def test_is_ignored_dir_for_output_cassettes(self, tmp_path):
        assert is_ignored_dir_for_output(tmp_path / "topic_x" / "cassettes")
        assert is_ignored_dir_for_output(tmp_path / "topic_x" / "_cassettes")

    def test_is_ignored_file_for_output_file_in_cassettes_dir(self, tmp_path):
        nested = tmp_path / "cassettes"
        nested.mkdir()
        cassette = nested / "slides_010v.http-cassette.yaml"
        cassette.write_text("interactions: []")
        assert is_ignored_file_for_output(cassette)


class TestOutputFilePatterns:
    """Ensure HTTP-replay cassettes are excluded from public/speaker output."""

    def test_cassette_dir_in_skip_dirs_for_output(self):
        assert "_cassettes" in SKIP_DIRS_FOR_OUTPUT

    def test_cassette_regex_matches_filename(self):
        names = ["slides_010v.http-cassette.yaml", "notebook.http-cassette.yaml"]
        for name in names:
            assert any(p.match(name) for p in SKIP_OUTPUT_FILE_PATTERNS), name

    def test_cassette_regex_rejects_unrelated_yaml(self):
        assert not any(p.match("config.yaml") for p in SKIP_OUTPUT_FILE_PATTERNS)
        assert not any(p.match("manifest.yml") for p in SKIP_OUTPUT_FILE_PATTERNS)

    def test_cassette_glob_parallels_regex(self):
        # Keep the glob form in sync with the regex form (same files filtered).
        assert "*.http-cassette.yaml" in SKIP_OUTPUT_FILE_GLOBS

    def test_staging_cassette_regex_matches_pid_uuid_form(self):
        # Per-worker staging files use the form
        # ``<stem>.http-cassette.yaml.staging-<pid>-<uuid-hex>``. The
        # regex must accept the full set so payload enumeration and the
        # output sweep both filter them.
        names = [
            "slides_010.http-cassette.yaml.staging-1234-abc",
            "notebook.http-cassette.yaml.staging-99-70e64c6cfd334794bb9bc3e8b05c3110",
            "x.http-cassette.yaml.staging-",
        ]
        for name in names:
            assert any(p.match(name) for p in SKIP_OUTPUT_FILE_PATTERNS), name

    def test_staging_glob_present_alongside_canonical(self):
        assert "*.http-cassette.yaml.staging-*" in SKIP_OUTPUT_FILE_GLOBS

    def test_is_ignored_file_for_output_staging_cassette(self, tmp_path):
        staging = tmp_path / "slides_010v.http-cassette.yaml.staging-1234-abc"
        staging.write_text("interactions: []")
        assert is_ignored_file_for_output(staging)

    def test_is_ignored_file_for_output_sibling_cassette(self, tmp_path):
        cassette = tmp_path / "slides_010v.http-cassette.yaml"
        cassette.write_text("interactions: []")
        assert is_ignored_file_for_output(cassette)

    def test_is_ignored_file_for_output_nested_cassette(self, tmp_path):
        nested = tmp_path / "_cassettes"
        nested.mkdir()
        cassette = nested / "slides_010v.http-cassette.yaml"
        cassette.write_text("interactions: []")
        # Both the dir membership and the filename pattern catch it;
        # either is sufficient for the predicate.
        assert is_ignored_file_for_output(cassette)

    def test_is_ignored_file_for_output_normal_python_file(self, tmp_path):
        py = tmp_path / "slides_010v.py"
        py.write_text("# ...")
        assert not is_ignored_file_for_output(py)

    def test_voiceover_companion_regex_matches(self):
        # Bilingual and split-per-language companion forms must all match so the
        # raw author file never reaches public/speaker output (its narration is
        # merged into the slide notebook at build time instead).
        names = [
            "voiceover_intro.py",
            "voiceover_010v_topic.py",
            "voiceover_intro.de.py",
            "voiceover_intro.en.py",
        ]
        for name in names:
            assert any(p.match(name) for p in SKIP_OUTPUT_FILE_PATTERNS), name

    def test_voiceover_companion_regex_rejects_non_companion(self):
        # Only files whose name *starts with* ``voiceover_`` are companions.
        for name in ("slides_010v.py", "my_voiceover_helper.py", "voiceover.md"):
            assert not any(p.match(name) for p in SKIP_OUTPUT_FILE_PATTERNS), name

    def test_voiceover_companion_glob_parallels_regex(self):
        assert "voiceover_*.py" in SKIP_OUTPUT_FILE_GLOBS

    def test_is_ignored_file_for_output_voiceover_companion(self, tmp_path):
        for name in ("voiceover_intro.py", "voiceover_intro.de.py", "voiceover_intro.en.py"):
            comp = tmp_path / name
            comp.write_text('# %% [markdown] tags=["voiceover"] for_slide="x"\n# vo\n')
            assert is_ignored_file_for_output(comp), name

    def test_is_ignored_file_for_output_keras_suffix(self, tmp_path):
        # SKIP_FILE_SUFFIXES still applies via is_ignored_file_for_course.
        model = tmp_path / "model.keras"
        model.write_text("")
        assert is_ignored_file_for_output(model)

    def test_is_ignored_file_for_course_skips_sync_includes_ledger(self, tmp_path):
        # The per-topic .clm-include ledger written by `clm sync-includes`
        # is a build-internal artifact and must not enter the course file
        # map (so it does not reach workers, source mounts, or output).
        ledger = tmp_path / ".clm-include"
        ledger.write_text('{"version": 1, "entries": []}')
        assert is_ignored_file_for_course(ledger)
        assert is_ignored_file_for_output(ledger)


class TestAtomicWriteBytes:
    """Verify the cache-hit write path stays robust under transient OS errors."""

    def test_writes_bytes_and_creates_parent_dir(self, tmp_path):
        target = tmp_path / "nested" / "img" / "diagram.png"
        atomic_write_bytes(target, b"\x89PNGdata")

        assert target.read_bytes() == b"\x89PNGdata"
        # No temp leftovers in the destination directory.
        assert list(target.parent.glob("*.tmp")) == []

    def test_overwrites_existing_file_atomically(self, tmp_path):
        target = tmp_path / "diagram.png"
        target.write_bytes(b"old")

        atomic_write_bytes(target, b"new-bytes")

        assert target.read_bytes() == b"new-bytes"

    def test_retries_on_transient_einval_then_succeeds(self, tmp_path, monkeypatch):
        target = tmp_path / "img" / "diagram.png"

        real_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError(errno.EINVAL, "Invalid argument", str(dst))
            return real_replace(src, dst)

        # Sleep is the only non-trivial side effect on the retry path; stub it
        # so the test stays fast.
        monkeypatch.setattr(os, "replace", flaky_replace)
        monkeypatch.setattr("clm.infrastructure.utils.path_utils.time.sleep", lambda _s: None)

        atomic_write_bytes(target, b"payload")

        assert target.read_bytes() == b"payload"
        assert calls["n"] == 2
        # The failed-attempt temp file must be cleaned up.
        assert list(target.parent.glob("*.tmp")) == []

    def test_non_transient_oserror_propagates_immediately(self, tmp_path, monkeypatch):
        target = tmp_path / "diagram.png"
        calls = {"n": 0}

        def always_enospc(src, dst):
            calls["n"] += 1
            raise OSError(errno.ENOSPC, "No space left", str(dst))

        monkeypatch.setattr(os, "replace", always_enospc)

        with pytest.raises(OSError) as excinfo:
            atomic_write_bytes(target, b"payload")

        assert excinfo.value.errno == errno.ENOSPC
        # Single attempt — no retries on non-transient errnos.
        assert calls["n"] == 1
        assert not target.exists()
        assert list(target.parent.glob("*.tmp")) == []

    def test_gives_up_after_max_retries(self, tmp_path, monkeypatch):
        target = tmp_path / "diagram.png"
        calls = {"n": 0}

        def always_einval(src, dst):
            calls["n"] += 1
            raise OSError(errno.EINVAL, "Invalid argument", str(dst))

        monkeypatch.setattr(os, "replace", always_einval)
        monkeypatch.setattr("clm.infrastructure.utils.path_utils.time.sleep", lambda _s: None)

        with pytest.raises(OSError) as excinfo:
            atomic_write_bytes(target, b"payload", max_retries=3)

        assert excinfo.value.errno == errno.EINVAL
        assert calls["n"] == 3
        assert list(target.parent.glob("*.tmp")) == []

    def test_uses_unique_temp_name_per_attempt(self, tmp_path, monkeypatch):
        # If a worker briefly held a temp file open, retries must not reuse
        # the same temp name (which could be the file currently locked).
        target = tmp_path / "diagram.png"
        seen_temps: list[str] = []

        original_write_bytes = Path.write_bytes

        def recording_write_bytes(self, data):
            seen_temps.append(self.name)
            return original_write_bytes(self, data)

        monkeypatch.setattr(Path, "write_bytes", recording_write_bytes)

        real_replace = os.replace

        def flaky_replace(src, dst):
            if len(seen_temps) < 2:
                raise OSError(errno.EACCES, "Permission denied", str(dst))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", flaky_replace)
        monkeypatch.setattr("clm.infrastructure.utils.path_utils.time.sleep", lambda _s: None)

        atomic_write_bytes(target, b"payload")

        # We saw at least two different temp names.
        assert len(seen_temps) >= 2
        assert len(set(seen_temps)) == len(seen_temps)
