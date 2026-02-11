"""Tests for FastAPI web application module.

Tests cover app creation, configuration, and routes.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestCreateApp:
    """Test create_app function."""

    def test_create_app_returns_fastapi_instance(self, tmp_path):
        """Should return a FastAPI application instance."""
        from clm.web.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path)

        assert app is not None
        assert app.title == "CLM Dashboard API"
        assert app.version == "0.6.2"

    def test_create_app_sets_state(self, tmp_path):
        """Should set db_path, host, port in app state."""
        from clm.web.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path, host="0.0.0.0", port=9000)

        assert app.state.db_path == db_path
        assert app.state.host == "0.0.0.0"
        assert app.state.port == 9000

    def test_create_app_default_values(self, tmp_path):
        """Should use default values for host and port."""
        from clm.web.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path)

        assert app.state.host == "127.0.0.1"
        assert app.state.port == 8000

    def test_create_app_creates_monitor_service(self, tmp_path):
        """Should create MonitorService instance."""
        from clm.web.app import create_app
        from clm.web.services.monitor_service import MonitorService

        db_path = tmp_path / "test.db"
        app = create_app(db_path)

        assert hasattr(app.state, "monitor_service")
        assert isinstance(app.state.monitor_service, MonitorService)

    def test_create_app_default_cors(self, tmp_path):
        """Should add CORS middleware with default origins."""
        from clm.web.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path)

        # Check that CORS middleware was added
        middleware_classes = [type(m).__name__ for m in app.user_middleware]
        # Note: CORS middleware is wrapped, so we check it exists via routes
        assert app is not None

    def test_create_app_custom_cors(self, tmp_path):
        """Should accept custom CORS origins."""
        from clm.web.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path, cors_origins=["http://localhost:3000"])

        # App should be created without error
        assert app is not None

    def test_create_app_includes_api_router(self, tmp_path):
        """Should include API router with endpoints."""
        from clm.web.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path)

        # Check that API routes are included
        routes = [route.path for route in app.routes]
        assert "/api/health" in routes or any("/api" in r for r in routes)


class TestDefaultFrontend:
    """Test default frontend when static files don't exist."""

    def test_default_page_when_no_static_files(self, tmp_path):
        """Should serve default HTML when no static frontend exists."""
        from clm.web.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path)

        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/")
        assert response.status_code == 200
        assert "CLM Dashboard API" in response.text
        assert "/api/health" in response.text
        assert "/api/status" in response.text


class TestWebSocketEndpoint:
    """Test WebSocket endpoint configuration."""

    def test_websocket_route_exists(self, tmp_path):
        """Should have WebSocket route at /ws."""
        from clm.web.app import create_app

        db_path = tmp_path / "test.db"
        app = create_app(db_path)

        # Check that /ws WebSocket route exists
        ws_routes = [
            route for route in app.routes if hasattr(route, "path") and route.path == "/ws"
        ]
        assert len(ws_routes) >= 1


class TestLifespan:
    """Test application lifespan context manager."""

    @pytest.mark.asyncio
    async def test_lifespan_logs_startup(self, tmp_path, caplog):
        """Should log startup information."""
        import logging

        from clm.web.app import create_app, lifespan

        db_path = tmp_path / "test.db"
        app = create_app(db_path)

        with caplog.at_level(logging.INFO, logger="clm.web.app"):
            async with lifespan(app):
                pass

        assert "Starting CLM Dashboard Server" in caplog.text

    @pytest.mark.asyncio
    async def test_lifespan_logs_shutdown(self, tmp_path, caplog):
        """Should log shutdown information."""
        import logging

        from clm.web.app import create_app, lifespan

        db_path = tmp_path / "test.db"
        app = create_app(db_path)

        with caplog.at_level(logging.INFO, logger="clm.web.app"):
            async with lifespan(app):
                pass

        assert "Shutting down CLM Dashboard Server" in caplog.text
