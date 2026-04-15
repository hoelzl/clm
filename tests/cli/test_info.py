"""
Unit tests for the info command.

Tests the info command functionality including:
- Listing available topics
- Displaying topic content
- Version substitution
- Error handling for unknown topics
"""

from click.testing import CliRunner

from clm.__version__ import __version__
from clm.cli.commands.info import TOPICS, load_topic_content
from clm.cli.main import cli


class TestInfoCommandHelp:
    """Test info command help and basic structure."""

    def test_info_help(self):
        """Test info command help text."""
        runner = CliRunner()
        result = runner.invoke(cli, ["info", "--help"])
        assert result.exit_code == 0
        assert (
            "version-accurate" in result.output.lower() or "documentation" in result.output.lower()
        )
        assert "TOPIC" in result.output

    def test_info_appears_in_main_help(self):
        """Test that info command appears in main CLI help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "info" in result.output


class TestInfoTopicListing:
    """Test listing available topics."""

    def test_info_no_args_lists_topics(self):
        """Test that 'clm info' without args lists available topics."""
        runner = CliRunner()
        result = runner.invoke(cli, ["info"])
        assert result.exit_code == 0
        assert "spec-files" in result.output
        assert "commands" in result.output
        assert "migration" in result.output

    def test_info_no_args_shows_version(self):
        """Test that topic listing includes the CLM version."""
        runner = CliRunner()
        result = runner.invoke(cli, ["info"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_info_no_args_shows_descriptions(self):
        """Test that topic listing includes topic descriptions."""
        runner = CliRunner()
        result = runner.invoke(cli, ["info"])
        assert result.exit_code == 0
        for topic_info in TOPICS.values():
            assert topic_info.description in result.output


class TestInfoTopicDisplay:
    """Test displaying individual topic content."""

    def test_info_spec_files(self):
        """Test displaying spec-files topic."""
        runner = CliRunner()
        result = runner.invoke(cli, ["info", "spec-files"])
        assert result.exit_code == 0
        assert "Spec File Reference" in result.output
        assert "<course>" in result.output

    def test_info_commands(self):
        """Test displaying commands topic."""
        runner = CliRunner()
        result = runner.invoke(cli, ["info", "commands"])
        assert result.exit_code == 0
        assert "CLI Command Reference" in result.output
        assert "clm build" in result.output

    def test_info_migration(self):
        """Test displaying migration topic."""
        runner = CliRunner()
        result = runner.invoke(cli, ["info", "migration"])
        assert result.exit_code == 0
        assert "Migration Guide" in result.output

    def test_info_topic_contains_version(self):
        """Test that displayed topics have version substituted."""
        runner = CliRunner()
        result = runner.invoke(cli, ["info", "spec-files"])
        assert result.exit_code == 0
        assert __version__ in result.output
        assert "{version}" not in result.output


class TestInfoErrorHandling:
    """Test error handling for unknown topics."""

    def test_info_unknown_topic(self):
        """Test that unknown topic shows error with available topics."""
        runner = CliRunner()
        result = runner.invoke(cli, ["info", "nonexistent"])
        assert result.exit_code != 0
        assert "nonexistent" in result.output
        assert "spec-files" in result.output
        assert "commands" in result.output
        assert "migration" in result.output

    def test_info_unknown_topic_exit_code(self):
        """Test that unknown topic returns non-zero exit code."""
        runner = CliRunner()
        result = runner.invoke(cli, ["info", "foobar"])
        assert result.exit_code != 0


class TestLoadTopicContent:
    """Test the load_topic_content helper function."""

    def test_load_replaces_version(self):
        """Test that {version} placeholders are replaced."""
        content = load_topic_content("spec-files")
        assert __version__ in content
        assert "{version}" not in content

    def test_load_all_topics(self):
        """Test that all registered topics can be loaded."""
        for slug in TOPICS:
            content = load_topic_content(slug)
            assert len(content) > 0
            assert __version__ in content

    def test_topics_registry_complete(self):
        """Test that TOPICS registry has expected entries."""
        assert "spec-files" in TOPICS
        assert "commands" in TOPICS
        assert "migration" in TOPICS
        assert "jupyterlite" in TOPICS
        assert len(TOPICS) == 4
