"""Tests for Backend base class.

Tests cover the Backend abstract base class behavior.
"""

import asyncio
from pathlib import Path

import pytest

from clx.infrastructure.backends.dummy_backend import DummyBackend


class TestBackend:
    """Test Backend base class."""

    @pytest.mark.asyncio
    async def test_context_manager_enter_returns_self(self):
        """__aenter__ should return the backend instance."""
        backend = DummyBackend()

        async with backend as b:
            assert b is backend

    @pytest.mark.asyncio
    async def test_context_manager_exit_returns_none(self):
        """__aexit__ should return None."""
        backend = DummyBackend()

        async with backend:
            pass

        # If we reach here, context manager worked correctly

    @pytest.mark.asyncio
    async def test_cancel_jobs_for_file_default(self):
        """Default cancel_jobs_for_file should return 0."""
        backend = DummyBackend()

        result = await backend.cancel_jobs_for_file(Path("/some/file.txt"))

        assert result == 0
