"""Tests for routing keys module.

Tests that routing key constants are properly defined.
"""

from clm.infrastructure.messaging.routing_keys import (
    DRAWIO_PROCESS_ROUTING_KEY,
    IMG_RESULT_ROUTING_KEY,
    NB_PROCESS_ROUTING_KEY,
    NB_RESULT_ROUTING_KEY,
    PLANTUML_PROCESS_ROUTING_KEY,
)


class TestRoutingKeys:
    """Test routing key constants."""

    def test_drawio_process_routing_key(self):
        """DrawIO process routing key should be defined."""
        assert DRAWIO_PROCESS_ROUTING_KEY == "drawio.process"

    def test_plantuml_process_routing_key(self):
        """PlantUML process routing key should be defined."""
        assert PLANTUML_PROCESS_ROUTING_KEY == "plantuml.process"

    def test_nb_process_routing_key(self):
        """Notebook process routing key should be defined."""
        assert NB_PROCESS_ROUTING_KEY == "notebook.process"

    def test_img_result_routing_key(self):
        """Image result routing key should be defined."""
        assert IMG_RESULT_ROUTING_KEY == "img.result"

    def test_nb_result_routing_key(self):
        """Notebook result routing key should be defined."""
        assert NB_RESULT_ROUTING_KEY == "notebook.result"

    def test_routing_keys_are_strings(self):
        """All routing keys should be strings."""
        assert isinstance(DRAWIO_PROCESS_ROUTING_KEY, str)
        assert isinstance(PLANTUML_PROCESS_ROUTING_KEY, str)
        assert isinstance(NB_PROCESS_ROUTING_KEY, str)
        assert isinstance(IMG_RESULT_ROUTING_KEY, str)
        assert isinstance(NB_RESULT_ROUTING_KEY, str)

    def test_routing_keys_follow_format(self):
        """All routing keys should follow the type.action format."""
        for key in [
            DRAWIO_PROCESS_ROUTING_KEY,
            PLANTUML_PROCESS_ROUTING_KEY,
            NB_PROCESS_ROUTING_KEY,
            IMG_RESULT_ROUTING_KEY,
            NB_RESULT_ROUTING_KEY,
        ]:
            parts = key.split(".")
            assert len(parts) == 2
            assert parts[0]  # Non-empty type
            assert parts[1]  # Non-empty action
