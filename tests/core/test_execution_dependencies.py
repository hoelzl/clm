"""Tests for ExecutionDependencyResolver class."""

import pytest

from clm.core.execution_dependencies import (
    EXECUTION_REQUIREMENTS,
    ExecutionDependencyResolver,
    ExecutionRequirement,
    get_execution_requirement,
)
from clm.core.output_target import OutputTarget


class TestExecutionRequirement:
    """Tests for ExecutionRequirement enum and classification."""

    def test_html_code_along_no_execution(self):
        """HTML code-along doesn't need execution (cells cleared)."""
        req = get_execution_requirement("html", "code-along")
        assert req == ExecutionRequirement.NONE

    def test_html_recording_populates_cache(self):
        """HTML recording executes and populates cache (canonical producer)."""
        req = get_execution_requirement("html", "recording")
        assert req == ExecutionRequirement.POPULATES_CACHE

    def test_html_speaker_alias_still_populates_cache(self):
        """``speaker`` is the deprecated alias for ``recording`` and must remain
        a cache producer so any unnormalized callers keep working."""
        req = get_execution_requirement("html", "speaker")
        assert req == ExecutionRequirement.POPULATES_CACHE

    def test_html_trainer_reuses_cache(self):
        """HTML trainer is a Recording subset (drops voiceover) and reuses cache."""
        req = get_execution_requirement("html", "trainer")
        assert req == ExecutionRequirement.REUSES_CACHE

    def test_html_completed_reuses_cache(self):
        """HTML completed reuses cached execution."""
        req = get_execution_requirement("html", "completed")
        assert req == ExecutionRequirement.REUSES_CACHE

    def test_notebook_kinds_no_execution(self):
        """Notebook format doesn't execute (just filters)."""
        assert get_execution_requirement("notebook", "code-along") == ExecutionRequirement.NONE
        assert get_execution_requirement("notebook", "completed") == ExecutionRequirement.NONE
        assert get_execution_requirement("notebook", "trainer") == ExecutionRequirement.NONE
        assert get_execution_requirement("notebook", "recording") == ExecutionRequirement.NONE

    def test_code_kinds_no_execution(self):
        """Code format doesn't execute."""
        assert get_execution_requirement("code", "code-along") == ExecutionRequirement.NONE
        assert get_execution_requirement("code", "completed") == ExecutionRequirement.NONE
        assert get_execution_requirement("code", "trainer") == ExecutionRequirement.NONE
        assert get_execution_requirement("code", "recording") == ExecutionRequirement.NONE

    def test_unknown_combination_defaults_to_none(self):
        """Unknown format/kind combinations default to NONE."""
        req = get_execution_requirement("unknown", "unknown")
        assert req == ExecutionRequirement.NONE

    def test_partial_html_reuses_recording_cache(self):
        """Partial HTML reuses Recording's cached executed notebook and
        post-processes it — pre-workshop cells keep their executed outputs,
        post-workshop cells are blanked with outputs cleared. Notebook/code
        formats don't execute at all.
        """
        assert get_execution_requirement("html", "partial") == ExecutionRequirement.REUSES_CACHE
        assert get_execution_requirement("notebook", "partial") == ExecutionRequirement.NONE
        assert get_execution_requirement("code", "partial") == ExecutionRequirement.NONE


class TestExecutionDependencyResolverImplicitExecutions:
    """Tests for ExecutionDependencyResolver.resolve_implicit_executions()."""

    @pytest.fixture
    def resolver(self):
        return ExecutionDependencyResolver()

    def test_no_implicit_when_all_requested(self, resolver):
        """No implicit executions when recording and completed HTML are both requested."""
        requested = {
            ("en", "html", "recording"),
            ("en", "html", "completed"),
        }
        implicit = resolver.resolve_implicit_executions(requested)
        assert implicit == set()

    def test_implicit_recording_when_only_completed(self, resolver):
        """Recording HTML is implicit when only completed HTML is requested."""
        requested = {
            ("en", "html", "completed"),
        }
        implicit = resolver.resolve_implicit_executions(requested)

        assert ("en", "html", "recording") in implicit

    def test_implicit_recording_when_only_partial(self, resolver):
        """Recording HTML is implicit when only partial HTML is requested —
        partial HTML reuses Recording's cached executed notebook."""
        requested = {
            ("en", "html", "partial"),
        }
        implicit = resolver.resolve_implicit_executions(requested)

        assert ("en", "html", "recording") in implicit

    def test_implicit_recording_when_only_trainer(self, resolver):
        """Recording HTML is implicit when only trainer HTML is requested —
        trainer is a Recording subset (drops voiceover) and reuses its cache."""
        requested = {
            ("en", "html", "trainer"),
        }
        implicit = resolver.resolve_implicit_executions(requested)

        assert ("en", "html", "recording") in implicit

    def test_implicit_for_multiple_languages(self, resolver):
        """Implicit executions generated for each language."""
        requested = {
            ("en", "html", "completed"),
            ("de", "html", "completed"),
        }
        implicit = resolver.resolve_implicit_executions(requested)

        assert ("en", "html", "recording") in implicit
        assert ("de", "html", "recording") in implicit

    def test_no_implicit_for_notebook_format(self, resolver):
        """No implicit executions for notebook format (no caching involved)."""
        requested = {
            ("en", "notebook", "completed"),
            ("de", "notebook", "recording"),
        }
        implicit = resolver.resolve_implicit_executions(requested)
        assert implicit == set()

    def test_no_implicit_for_code_format(self, resolver):
        """No implicit executions for code format."""
        requested = {
            ("en", "code", "completed"),
        }
        implicit = resolver.resolve_implicit_executions(requested)
        assert implicit == set()


class TestExecutionDependencyResolverCollectRequestedOutputs:
    """Tests for ExecutionDependencyResolver.collect_requested_outputs()."""

    @pytest.fixture
    def resolver(self):
        return ExecutionDependencyResolver()

    def test_collect_from_single_target(self, resolver, tmp_path):
        """Collect outputs from a single target."""
        target = OutputTarget(
            name="test",
            output_root=tmp_path / "output",
            kinds=frozenset({"completed"}),
            formats=frozenset({"html"}),
            languages=frozenset({"en"}),
        )

        requested = resolver.collect_requested_outputs([target])

        assert requested == {("en", "html", "completed")}

    def test_collect_from_multiple_targets(self, resolver, tmp_path):
        """Collect outputs from multiple targets."""
        target1 = OutputTarget(
            name="students",
            output_root=tmp_path / "students",
            kinds=frozenset({"code-along"}),
            formats=frozenset({"html", "notebook"}),
            languages=frozenset({"en", "de"}),
        )
        target2 = OutputTarget(
            name="solutions",
            output_root=tmp_path / "solutions",
            kinds=frozenset({"completed"}),
            formats=frozenset({"html"}),
            languages=frozenset({"en"}),
        )

        requested = resolver.collect_requested_outputs([target1, target2])

        # Should include all combinations from both targets
        assert ("en", "html", "code-along") in requested
        assert ("de", "html", "code-along") in requested
        assert ("en", "notebook", "code-along") in requested
        assert ("de", "notebook", "code-along") in requested
        assert ("en", "html", "completed") in requested


class TestExecutionDependencyResolverGetAllRequired:
    """Tests for ExecutionDependencyResolver.get_all_required_executions()."""

    @pytest.fixture
    def resolver(self):
        return ExecutionDependencyResolver()

    def test_explicit_and_implicit_separated(self, resolver, tmp_path):
        """Explicit outputs and implicit executions are separated."""
        # Only request completed HTML (no recording)
        target = OutputTarget(
            name="solutions",
            output_root=tmp_path / "solutions",
            kinds=frozenset({"completed"}),
            formats=frozenset({"html"}),
            languages=frozenset({"en"}),
        )

        explicit, implicit = resolver.get_all_required_executions([target])

        # Explicit should be exactly what was requested
        assert explicit == {("en", "html", "completed")}

        # Implicit should include recording HTML for cache population
        assert implicit == {("en", "html", "recording")}

    def test_no_implicit_when_recording_included(self, resolver, tmp_path):
        """No implicit executions when recording is already included."""
        target = OutputTarget(
            name="all",
            output_root=tmp_path / "all",
            kinds=frozenset({"completed", "recording"}),
            formats=frozenset({"html"}),
            languages=frozenset({"en"}),
        )

        explicit, implicit = resolver.get_all_required_executions([target])

        assert ("en", "html", "completed") in explicit
        assert ("en", "html", "recording") in explicit
        assert implicit == set()  # No implicit needed

    def test_complex_multi_target_scenario(self, resolver, tmp_path):
        """Test complex scenario with multiple targets."""
        # Scenario: Students get code-along, solutions get completed HTML only
        # This means we need implicit recording HTML for cache
        students = OutputTarget(
            name="students",
            output_root=tmp_path / "students",
            kinds=frozenset({"code-along"}),
            formats=frozenset({"html", "notebook"}),
            languages=frozenset({"en", "de"}),
        )
        solutions = OutputTarget(
            name="solutions",
            output_root=tmp_path / "solutions",
            kinds=frozenset({"completed"}),
            formats=frozenset({"html"}),  # Only HTML!
            languages=frozenset({"en", "de"}),
        )

        explicit, implicit = resolver.get_all_required_executions([students, solutions])

        # Students' outputs
        assert ("en", "html", "code-along") in explicit
        assert ("de", "html", "code-along") in explicit

        # Solutions' outputs (completed HTML only)
        assert ("en", "html", "completed") in explicit
        assert ("de", "html", "completed") in explicit

        # Implicit recording HTML for both languages (to populate cache)
        assert ("en", "html", "recording") in implicit
        assert ("de", "html", "recording") in implicit


class TestCacheProviders:
    """Tests for CACHE_PROVIDERS mapping."""

    def test_completed_html_needs_recording_html(self):
        """Completed HTML needs recording HTML to populate cache."""
        providers = ExecutionDependencyResolver.CACHE_PROVIDERS

        assert ("html", "completed") in providers
        assert providers[("html", "completed")] == ("html", "recording")

    def test_trainer_html_needs_recording_html(self):
        """Trainer HTML reuses Recording's cached notebook (drops voiceover)."""
        providers = ExecutionDependencyResolver.CACHE_PROVIDERS

        assert ("html", "trainer") in providers
        assert providers[("html", "trainer")] == ("html", "recording")

    def test_partial_html_needs_recording_html(self):
        """Partial HTML reuses Recording's cached executed notebook."""
        providers = ExecutionDependencyResolver.CACHE_PROVIDERS

        assert ("html", "partial") in providers
        assert providers[("html", "partial")] == ("html", "recording")

    def test_only_html_cache_dependencies(self):
        """Only HTML kinds depend on the Recording HTML cache."""
        providers = ExecutionDependencyResolver.CACHE_PROVIDERS

        for (consumer_fmt, _), (provider_fmt, provider_kind) in providers.items():
            assert consumer_fmt == "html"
            assert provider_fmt == "html"
            assert provider_kind == "recording"
