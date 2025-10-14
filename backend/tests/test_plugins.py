"""
Tests for the plugin system itself.

Tests plugin registration, priority, and plugin-specific functionality.
"""
import pytest


@pytest.mark.unit
def test_test_auth_plugin_priority(test_plugins):
    """Test that TestAuthPlugin has high priority"""
    auth_plugin = test_plugins["auth"]
    assert auth_plugin.get_priority() == 1000


@pytest.mark.unit
def test_test_model_plugin_priority(test_plugins):
    """Test that TestModelPlugin has high priority"""
    model_plugin = test_plugins["model"]
    assert model_plugin.get_priority() == 1000


@pytest.mark.unit
def test_test_auth_plugin_users(test_plugins):
    """Test that TestAuthPlugin has expected test users"""
    auth_plugin = test_plugins["auth"]

    # Test user1
    user1 = auth_plugin.authenticate_user("testuser1", "password1")
    assert user1 is not None
    assert user1["username"] == "testuser1"
    assert user1["role"] == "user"

    # Test user2
    user2 = auth_plugin.authenticate_user("testuser2", "password2")
    assert user2 is not None
    assert user2["username"] == "testuser2"

    # Test admin
    admin = auth_plugin.authenticate_user("admin", "admin123")
    assert admin is not None
    assert admin["role"] == "admin"


@pytest.mark.unit
def test_test_auth_plugin_invalid_credentials(test_plugins):
    """Test that TestAuthPlugin rejects invalid credentials"""
    auth_plugin = test_plugins["auth"]

    # Wrong password
    assert auth_plugin.authenticate_user("testuser1", "wrongpassword") is None

    # Non-existent user
    assert auth_plugin.authenticate_user("nonexistent", "password") is None


@pytest.mark.unit
def test_test_auth_plugin_tokens(test_plugins):
    """Test that TestAuthPlugin creates and verifies tokens"""
    auth_plugin = test_plugins["auth"]

    # Create token
    user = {"username": "testuser1", "role": "user"}
    token = auth_plugin.create_token(user)
    assert token == "test-token-user1"

    # Verify token
    username = auth_plugin.verify_token(token)
    assert username == "testuser1"

    # Invalid token
    assert auth_plugin.verify_token("invalid-token") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_test_model_plugin_models(test_plugins):
    """Test that TestModelPlugin returns expected models"""
    model_plugin = test_plugins["model"]

    models = await model_plugin.get_available_models()
    assert isinstance(models, list)
    assert len(models) == 3
    assert "test-model-fast" in models
    assert "test-model-smart" in models
    assert "test-model-creative" in models


@pytest.mark.unit
@pytest.mark.asyncio
async def test_test_model_plugin_streaming(test_plugins):
    """Test that TestModelPlugin generates streaming responses"""
    model_plugin = test_plugins["model"]

    messages = [
        {"role": "user", "content": "Hello"}
    ]

    events = []
    async for chunk in model_plugin.generate_response(messages, "test-model-fast"):
        events.append(chunk)

    # Should have multiple message_tokens events and a stream_end
    event_types = [e.get("event") for e in events]
    assert "message_tokens" in event_types
    assert "stream_end" in event_types

    # Reconstruct message
    tokens = [e["data"]["content"] for e in events if e.get("event") == "message_tokens"]
    full_message = "".join(tokens)
    assert len(full_message) > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_test_model_plugin_different_models(test_plugins):
    """Test that TestModelPlugin returns different responses for different models"""
    model_plugin = test_plugins["model"]

    messages = [{"role": "user", "content": "Tell me something"}]

    # Test fast model
    events_fast = []
    async for chunk in model_plugin.generate_response(messages, "test-model-fast"):
        events_fast.append(chunk)

    # Test creative model
    events_creative = []
    async for chunk in model_plugin.generate_response(messages, "test-model-creative"):
        events_creative.append(chunk)

    # Reconstruct messages
    message_fast = "".join([e["data"]["content"] for e in events_fast if e.get("event") == "message_tokens"])
    message_creative = "".join([e["data"]["content"] for e in events_creative if e.get("event") == "message_tokens"])

    # Different models should produce different responses
    assert message_fast != message_creative
    assert "test" in message_fast.lower()
    assert "once upon a time" in message_creative.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_test_model_plugin_error_handling(test_plugins):
    """Test that TestModelPlugin handles error simulation"""
    model_plugin = test_plugins["model"]

    # Message containing "error" should trigger error response
    messages = [{"role": "user", "content": "This should trigger an error"}]

    events = []
    async for chunk in model_plugin.generate_response(messages, "test-model-fast"):
        events.append(chunk)

    # Should receive an error event
    event_types = [e.get("event") for e in events]
    assert "error" in event_types

    error_event = next(e for e in events if e.get("event") == "error")
    assert "error" in error_event["data"]["message"].lower()


@pytest.mark.unit
def test_test_credentials_helper(test_plugins):
    """Test the test credentials helper method"""
    auth_plugin = test_plugins["auth"]

    creds = auth_plugin.get_test_credentials("testuser1")
    assert creds["username"] == "testuser1"
    assert creds["password"] == "password1"
    assert creds["token"] == "test-token-user1"

    creds2 = auth_plugin.get_test_credentials("testuser2")
    assert creds2["username"] == "testuser2"
    assert creds2["password"] == "password2"
