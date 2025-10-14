"""
Pytest configuration and fixtures for Skeleton API tests.

This module provides test fixtures that:
- Set up test plugins (mock auth, mock model)
- Configure test client with FastAPI TestClient
- Provide authenticated clients for different test users
- Handle test database/storage cleanup
"""
import pytest
import sys
import os
from pathlib import Path

# Add parent directory to path so we can import from backend modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from httpx import AsyncClient
import asyncio

# Import main app and plugin manager
from main import app
from core.plugin_manager import plugin_manager

# Import test plugins
from tests.test_auth_plugin import TestAuthPlugin
from tests.test_model_plugin import TestModelPlugin


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def test_plugins():
    """
    Register test plugins before each test, restore original plugins after.

    This ensures tests use mock auth and model plugins instead of real ones.
    """
    # Save original plugins
    original_auth = plugin_manager.auth.get_plugin()
    original_model = plugin_manager.model.get_plugin()

    # Create test plugins
    test_auth = TestAuthPlugin()
    test_model = TestModelPlugin()

    # Override active plugins directly (CorePluginManager doesn't have register_plugin)
    plugin_manager.auth._active_plugin = test_auth
    plugin_manager.model._active_plugin = test_model

    yield {
        "auth": test_auth,
        "model": test_model
    }

    # Restore original plugins after test
    plugin_manager.auth._active_plugin = original_auth
    plugin_manager.model._active_plugin = original_model


@pytest.fixture(scope="function")
def client(test_plugins):
    """
    Create test client with test plugins registered.

    Returns a synchronous TestClient for simple endpoint testing.
    """
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="function")
async def async_client(test_plugins):
    """
    Create async test client for testing streaming endpoints.

    Use this for SSE streaming tests or when you need async/await support.
    """
    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client


@pytest.fixture(scope="function")
def auth_headers(test_plugins):
    """
    Provide authentication headers for testuser1.

    Usage:
        response = client.get("/api/v1/threads", headers=auth_headers)
    """
    test_auth = test_plugins["auth"]
    creds = test_auth.get_test_credentials("testuser1")
    return {"Authorization": f"Bearer {creds['token']}"}


@pytest.fixture(scope="function")
def auth_headers_user2(test_plugins):
    """Provide authentication headers for testuser2"""
    test_auth = test_plugins["auth"]
    creds = test_auth.get_test_credentials("testuser2")
    return {"Authorization": f"Bearer {creds['token']}"}


@pytest.fixture(scope="function")
def auth_headers_admin(test_plugins):
    """Provide authentication headers for admin user"""
    test_auth = test_plugins["auth"]
    creds = test_auth.get_test_credentials("admin")
    return {"Authorization": f"Bearer {creds['token']}"}


@pytest.fixture(scope="function")
def test_credentials(test_plugins):
    """
    Provide test user credentials for login tests.

    Returns:
        dict: {"username": "testuser1", "password": "password1", "token": "test-token-user1"}
    """
    test_auth = test_plugins["auth"]
    return test_auth.get_test_credentials("testuser1")


@pytest.fixture(scope="function")
def authenticated_client(client, auth_headers):
    """
    Provide a test client that's pre-configured with auth headers.

    Usage:
        response = authenticated_client.get("/api/v1/threads")
    """
    client.headers.update(auth_headers)
    return client


@pytest.fixture(autouse=True)
def reset_thread_storage():
    """
    Reset in-memory thread storage before each test.

    This ensures tests don't interfere with each other by leaving data behind.
    Since we're using the default in-memory thread manager, we need to clear it.
    """
    # Get the thread manager and reset its storage
    thread_manager = plugin_manager.thread.get_plugin()
    if hasattr(thread_manager, 'threads'):
        thread_manager.threads.clear()
    if hasattr(thread_manager, 'messages'):
        thread_manager.messages.clear()

    yield

    # Cleanup after test (optional, but good practice)
    if hasattr(thread_manager, 'threads'):
        thread_manager.threads.clear()
    if hasattr(thread_manager, 'messages'):
        thread_manager.messages.clear()
