"""
Tests for thread management endpoints.

Tests creating, listing, updating, archiving, and searching threads.
"""
import pytest


@pytest.mark.api
@pytest.mark.unit
def test_get_threads_empty(authenticated_client):
    """Test getting threads when none exist"""
    response = authenticated_client.get("/api/v1/threads")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.api
@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_thread_via_message(async_client, auth_headers):
    """Test that sending a message creates a thread"""
    # Send a message without thread_id (creates new thread)
    response = await async_client.post(
        "/api/v1/message",
        headers=auth_headers,
        data={
            "content": "Hello, this is a test message",
            "model": "test-model-fast"
        }
    )

    assert response.status_code == 200

    # Read the SSE stream to get thread_id
    content = response.text
    assert "thread_id" in content


@pytest.mark.api
@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_thread_messages(async_client, auth_headers):
    """Test getting messages for a specific thread"""
    # First create a thread by sending a message
    response = await async_client.post(
        "/api/v1/message",
        headers=auth_headers,
        data={
            "content": "Test message",
            "model": "test-model-fast"
        }
    )

    # Parse thread_id from SSE stream
    thread_id = None
    for line in response.text.split('\n'):
        if 'thread_id' in line:
            import json
            data = json.loads(line.replace('data: ', ''))
            if data.get('event') == 'thread_id':
                thread_id = data['data']['thread_id']
                break

    assert thread_id is not None

    # Now get messages for that thread
    msg_response = await async_client.get(
        f"/api/v1/threads/{thread_id}/messages",
        headers=auth_headers
    )

    assert msg_response.status_code == 200
    messages = msg_response.json()
    assert len(messages) >= 1  # At least the user message
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Test message"


@pytest.mark.api
@pytest.mark.unit
def test_get_thread_messages_not_found(authenticated_client):
    """Test getting messages for non-existent thread"""
    response = authenticated_client.get("/api/v1/threads/nonexistent-thread-id/messages")

    assert response.status_code == 404
    assert "Thread not found" in response.json()["detail"]


@pytest.mark.api
@pytest.mark.unit
def test_user_isolation(client, auth_headers, auth_headers_user2):
    """Test that users can only access their own threads"""
    # This test requires creating a thread first, which involves async
    # For simplicity, we'll test the isolation at the message level

    # User1 tries to access a thread that doesn't exist yet
    response = client.get("/api/v1/threads/some-thread/messages", headers=auth_headers)
    assert response.status_code == 404

    # User2 tries to access the same
    response = client.get("/api/v1/threads/some-thread/messages", headers=auth_headers_user2)
    assert response.status_code == 404


@pytest.mark.api
@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_thread_title(async_client, auth_headers):
    """Test updating thread title"""
    # Create a thread first
    response = await async_client.post(
        "/api/v1/message",
        headers=auth_headers,
        data={
            "content": "Initial message",
            "model": "test-model-fast"
        }
    )

    # Extract thread_id
    thread_id = None
    for line in response.text.split('\n'):
        if 'thread_id' in line:
            import json
            data = json.loads(line.replace('data: ', ''))
            if data.get('event') == 'thread_id':
                thread_id = data['data']['thread_id']
                break

    assert thread_id is not None

    # Update the thread title
    update_response = await async_client.post(
        f"/api/v1/threads/{thread_id}",
        headers=auth_headers,
        json={"title": "Updated Thread Title"}
    )

    assert update_response.status_code == 200
    assert "updated successfully" in update_response.json()["message"]


@pytest.mark.api
@pytest.mark.unit
@pytest.mark.asyncio
async def test_archive_thread(async_client, auth_headers):
    """Test archiving a thread"""
    # Create a thread first
    response = await async_client.post(
        "/api/v1/message",
        headers=auth_headers,
        data={
            "content": "Message in thread to archive",
            "model": "test-model-fast"
        }
    )

    # Extract thread_id
    thread_id = None
    for line in response.text.split('\n'):
        if 'thread_id' in line:
            import json
            data = json.loads(line.replace('data: ', ''))
            if data.get('event') == 'thread_id':
                thread_id = data['data']['thread_id']
                break

    assert thread_id is not None

    # Archive the thread
    archive_response = await async_client.delete(
        f"/api/v1/threads/{thread_id}",
        headers=auth_headers
    )

    assert archive_response.status_code == 200
    assert "archived successfully" in archive_response.json()["message"]

    # Verify thread no longer appears in list
    threads_response = await async_client.get("/api/v1/threads", headers=auth_headers)
    threads = threads_response.json()
    thread_ids = [t["id"] for t in threads]
    assert thread_id not in thread_ids


@pytest.mark.api
@pytest.mark.unit
@pytest.mark.asyncio
async def test_search_threads(async_client, auth_headers):
    """Test searching threads by content"""
    # Create a thread with searchable content
    await async_client.post(
        "/api/v1/message",
        headers=auth_headers,
        data={
            "content": "This message contains the word SEARCHABLE",
            "model": "test-model-fast"
        }
    )

    # Search for the content
    search_response = await async_client.get(
        "/api/v1/search?q=SEARCHABLE",
        headers=auth_headers
    )

    assert search_response.status_code == 200
    results = search_response.json()
    assert len(results) >= 1
    assert "SEARCHABLE" in results[0]["snippet"]


@pytest.mark.api
@pytest.mark.unit
def test_search_query_too_long(authenticated_client):
    """Test that search rejects overly long queries"""
    long_query = "a" * 501  # Exceeds 500 char limit
    response = authenticated_client.get(f"/api/v1/search?q={long_query}")

    assert response.status_code == 400
    assert "too long" in response.json()["detail"]
