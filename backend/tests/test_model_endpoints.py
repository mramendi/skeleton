"""
Tests for model-related endpoints.

Tests model listing and response generation.
"""
import pytest
import json


@pytest.mark.api
@pytest.mark.unit
def test_get_models(authenticated_client):
    """Test getting list of available models"""
    response = authenticated_client.get("/api/v1/models")

    assert response.status_code == 200
    models = response.json()
    assert isinstance(models, list)
    assert len(models) > 0
    # Should get test models from TestModelPlugin
    assert "test-model-fast" in models
    assert "test-model-smart" in models
    assert "test-model-creative" in models


@pytest.mark.api
@pytest.mark.unit
def test_get_system_prompts(authenticated_client):
    """Test getting list of system prompts"""
    response = authenticated_client.get("/api/v1/system_prompts")

    assert response.status_code == 200
    prompts = response.json()
    assert isinstance(prompts, list)
    assert "default" in prompts
    assert "code-assistant" in prompts


@pytest.mark.api
@pytest.mark.streaming
@pytest.mark.asyncio
async def test_send_message_streaming(async_client, auth_headers):
    """Test sending a message and receiving streaming response"""
    response = await async_client.post(
        "/api/v1/message",
        headers=auth_headers,
        data={
            "content": "Hello, test!",
            "model": "test-model-fast"
        }
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    # Parse SSE events
    events = []
    for line in response.text.split('\n'):
        if line.startswith('data: '):
            event_data = json.loads(line[6:])  # Strip 'data: ' prefix
            events.append(event_data)

    # Verify we got the expected events
    event_types = [e.get('event') for e in events]
    assert 'thread_id' in event_types
    assert 'message_tokens' in event_types
    assert 'stream_end' in event_types

    # Verify message tokens contain content
    token_events = [e for e in events if e.get('event') == 'message_tokens']
    assert len(token_events) > 0

    # Reconstruct full message
    full_message = ''.join([e['data']['content'] for e in token_events])
    assert len(full_message) > 0
    assert "test" in full_message.lower()  # TestModelPlugin returns message mentioning "test"


@pytest.mark.api
@pytest.mark.streaming
@pytest.mark.asyncio
async def test_send_message_to_existing_thread(async_client, auth_headers):
    """Test sending a message to an existing thread"""
    # Create initial thread
    first_response = await async_client.post(
        "/api/v1/message",
        headers=auth_headers,
        data={
            "content": "First message",
            "model": "test-model-fast"
        }
    )

    # Extract thread_id
    thread_id = None
    for line in first_response.text.split('\n'):
        if 'thread_id' in line:
            data = json.loads(line[6:])
            if data.get('event') == 'thread_id':
                thread_id = data['data']['thread_id']
                break

    assert thread_id is not None

    # Send second message to same thread
    second_response = await async_client.post(
        "/api/v1/message",
        headers=auth_headers,
        data={
            "content": "Second message",
            "model": "test-model-fast",
            "thread_id": thread_id
        }
    )

    assert second_response.status_code == 200

    # Verify thread_id event shows same thread
    for line in second_response.text.split('\n'):
        if 'thread_id' in line:
            data = json.loads(line[6:])
            if data.get('event') == 'thread_id':
                assert data['data']['thread_id'] == thread_id
                break


@pytest.mark.api
@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_message_empty_content(async_client, auth_headers):
    """Test that empty message content is rejected"""
    response = await async_client.post(
        "/api/v1/message",
        headers=auth_headers,
        data={
            "content": "   ",  # Only whitespace
            "model": "test-model-fast"
        }
    )

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


@pytest.mark.api
@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_message_content_too_long(async_client, auth_headers):
    """Test that overly long messages are rejected"""
    long_content = "a" * 100001  # Exceeds 100000 char limit
    response = await async_client.post(
        "/api/v1/message",
        headers=auth_headers,
        data={
            "content": long_content,
            "model": "test-model-fast"
        }
    )

    assert response.status_code == 400
    assert "too long" in response.json()["detail"].lower()


@pytest.mark.api
@pytest.mark.streaming
@pytest.mark.asyncio
async def test_different_models(async_client, auth_headers):
    """Test that different model selections work"""
    models = ["test-model-fast", "test-model-smart", "test-model-creative"]

    for model in models:
        response = await async_client.post(
            "/api/v1/message",
            headers=auth_headers,
            data={
                "content": f"Test with {model}",
                "model": model
            }
        )

        assert response.status_code == 200

        # Verify model is mentioned in stream_end event
        for line in response.text.split('\n'):
            if 'stream_end' in line:
                data = json.loads(line[6:])
                if data.get('event') == 'stream_end':
                    assert data['data']['model'] == model


@pytest.mark.api
@pytest.mark.streaming
@pytest.mark.asyncio
async def test_system_prompts(async_client, auth_headers):
    """Test that different system prompts work"""
    response = await async_client.post(
        "/api/v1/message",
        headers=auth_headers,
        data={
            "content": "Write some code",
            "model": "test-model-fast",
            "system_prompt": "code-assistant"
        }
    )

    assert response.status_code == 200

    # TestModelPlugin should include code block for code-assistant prompt
    content = response.text
    assert "```" in content or "python" in content.lower()


@pytest.mark.api
@pytest.mark.streaming
@pytest.mark.asyncio
async def test_message_without_authentication(async_client):
    """Test that message endpoint requires authentication"""
    response = await async_client.post(
        "/api/v1/message",
        data={
            "content": "This should fail",
            "model": "test-model-fast"
        }
    )

    assert response.status_code == 403  # Forbidden - no auth
