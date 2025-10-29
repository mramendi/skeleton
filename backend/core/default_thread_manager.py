"""
Default thread manager implementation using the data store plugin.
Uses collections for efficient append-only message storage.
Can be overridden by plugins.
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid
import logging
from .protocols import ThreadManagerPlugin

logger = logging.getLogger("skeleton.thread_manager")

class DefaultThreadManager():
    """Default thread manager using data store - can be overridden by plugins"""

    def get_role(self) -> str:
        """Return the role string for this plugin"""
        return "thread"

    def get_priority(self) -> int:
        """Default priority - plugins can override with higher priority"""
        return 0

    async def shutdown(self) -> None:
        return

    def __init__(self):
        # Store schema definition - will be used when store is accessed
        self._store_schema = {
            "title": "str",
            "model": "str",
            "system_prompt": "str",
            "user": "str",
            "is_archived": "bool",
            "messages": "json_collection"  # Append-only message collection
        }

    def _get_store(self):
        """Lazy access to store plugin - eliminates initialization order dependency"""
        from .plugin_manager import plugin_manager
        return plugin_manager.get_plugin("store")

    async def create_thread(self, title: str, model: str, system_prompt: str, user: str) -> str:
        """Create a new thread for a specific user"""
        thread_id = str(uuid.uuid4())

        thread_data = {
            "title": title,
            "model": model,
            "system_prompt": system_prompt,
            "user": user,
            "is_archived": False
        }

        # Get store lazily and ensure it exists
        store = self._get_store()
        await store.create_store_if_not_exists("ChatHistoryThreads", self._store_schema)

        # Create the thread
        await store.add(user_id=user, store_name="ChatHistoryThreads", data=thread_data, record_id=thread_id)

        logger.debug(f"Created thread {thread_id} for user {user}")
        return thread_id

    async def get_threads(self, user: str, query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all non-archived threads for a user, optionally filtered by query"""
        # Get store lazily and ensure it exists
        store = self._get_store()
        await store.create_store_if_not_exists("ChatHistoryThreads", self._store_schema)

        # Build filters
        filters = {"user": user, "is_archived": False}

        # Get threads
        threads = await store.find(user_id=user, store_name="ChatHistoryThreads", filters=filters, order_by="created_at", order_desc=True)

        # Filter by query if provided (simple text search in title)
        if query:
            query_lower = query.lower()
            threads = [t for t in threads if query_lower in t.get("title", "").lower()]

        # Format response (exclude internal fields)
        formatted_threads = []
        for thread in threads:
            formatted_threads.append({
                "id": thread["id"],
                "title": thread["title"],
                "created": thread["created_at"],  # Store uses created_at, we expose as created
                "model": thread["model"],
                "system_prompt": thread["system_prompt"]
            })

        return formatted_threads

    async def get_thread_messages(self, thread_id: str, user: str) -> Optional[List[Dict[str, Any]]]:
        """Get all messages for a thread if user has access"""
        # Get store lazily and ensure it exists
        store = self._get_store()
        await store.create_store_if_not_exists("ChatHistoryThreads", self._store_schema)

        # Get thread to verify user access
        thread = await store.get(user_id=user, store_name="ChatHistoryThreads", record_id=thread_id)
        if not thread or thread.get("user") != user:
            return None

        # Get messages from collection
        messages = await store.collection_get(user_id=user, store_name="ChatHistoryThreads", record_id=thread_id, field_name="messages")

        # Format messages (add model field for assistant messages if present)
        # Convert aux_id to call_id for API consistency
        formatted_messages = []
        for msg in messages:
            formatted_msg = {
                "role": msg.get("role"),
                "content": msg.get("content"),
                "timestamp": msg.get("timestamp")
            }
            if msg.get("type"):
                formatted_msg["type"] = msg.get("type")
            if msg.get("model") and msg.get("role") == "assistant":
                formatted_msg["model"] = msg.get("model")
            # Convert aux_id to call_id for API (frontend only knows about call_id)
            if msg.get("aux_id"):
                formatted_msg["call_id"] = msg.get("aux_id")
            formatted_messages.append(formatted_msg)

        return formatted_messages

    async def add_message(self, thread_id: str, user: str, role: str, type: str, content: str, model: Optional[str] = None, aux_id: Optional[str] = None) -> bool:
        """Add a message to a thread if user has access. aux_id can be tool call_id, file ID, etc."""
        # Get store lazily and ensure it exists
        store = self._get_store()
        await store.create_store_if_not_exists("ChatHistoryThreads", self._store_schema)

        # Get thread to verify user access
        thread = await store.get(user, "ChatHistoryThreads", thread_id)
        if not thread or thread.get("user") != user:
            return False

        # Create message data
        message_data = {
            "role": role,
            "type": type,  # Include type in message data
            "content": content,
            "timestamp": datetime.now().isoformat()
        }

        if model and role == "assistant":
            message_data["model"] = model

        if aux_id:
            message_data["aux_id"] = aux_id

        # Append to collection (O(1) operation)
        await store.collection_append(user_id=user, store_name="ChatHistoryThreads", record_id=thread_id, field_name="messages", item=message_data)

        logger.debug(f"Added {role} message to thread {thread_id}")
        return True

    async def update_thread(self, thread_id: str, user: str, title: Optional[str] = None) -> bool:
        """Update thread metadata if user has access"""
        # Get store lazily and ensure it exists
        store = self._get_store()
        await store.create_store_if_not_exists("ChatHistoryThreads", self._store_schema)

        # Get thread to verify user access
        thread = await store.get(user, "ChatHistoryThreads", thread_id)
        if not thread or thread.get("user") != user:
            return False

        # Build updates
        updates = {}
        if title:
            updates["title"] = title

        if not updates:
            return True

        # Update thread
        success = await store.update(user_id=user, store_name="ChatHistoryThreads", record_id=thread_id, updates=updates)
        return success

    async def archive_thread(self, thread_id: str, user: str) -> bool:
        """Archive a thread if user has access"""
        # Get store lazily and ensure it exists
        store = self._get_store()
        await store.create_store_if_not_exists("ChatHistoryThreads", self._store_schema)

        # Get thread to verify user access
        thread = await store.get(user, "ChatHistoryThreads", thread_id)
        if not thread or thread.get("user") != user:
            return False

        # Update to mark as archived
        success = await store.update(user_id=user, store_name="ChatHistoryThreads", record_id=thread_id, updates={"is_archived": True})
        return success

    async def search_threads(self, query: str, user: str) -> List[Dict[str, Any]]:
        """Search across all thread titles and messages for a user using FTS"""
        # Get store lazily and ensure it exists
        store = self._get_store()
        await store.create_store_if_not_exists("ChatHistoryThreads", self._store_schema)

        # Use store's FTS search to find matching threads
        # Search across all indexable fields (title and messages)
        matching_threads = await store.full_text_search(
            user_id=user,
            store_name="ChatHistoryThreads",
            query=query
        )

        if not matching_threads:
            return []

        # Format results with snippets
        results = []
        query_lower = query.lower()

        for thread in matching_threads:
            thread_id = thread["id"]
            title = thread["title"]

            # Check if match is in title
            if query_lower in title.lower():
                # Match in title - use title as snippet
                results.append({
                    "id": thread_id,
                    "title": title,
                    "created": thread.get("created_at"),  # Add created timestamp
                    "snippet": f"Title: {title}"
                })
            else:
                # Match must be in messages - fetch messages to find snippet
                messages = await store.collection_get(user_id=user, store_name="ChatHistoryThreads", record_id=thread_id, field_name="messages")

                for msg in messages:
                    content = msg.get("content", "")
                    if query_lower in content.lower():
                        # Create result with snippet
                        snippet_start = max(0, content.lower().find(query_lower) - 50)
                        snippet_end = min(len(content), snippet_start + 100)
                        snippet = content[snippet_start:snippet_end]

                        if snippet_start > 0:
                            snippet = "..." + snippet
                        if snippet_end < len(content):
                            snippet = snippet + "..."

                        results.append({
                            "id": thread_id,
                            "title": title,
                            "created": thread.get("created_at"),  # Add created timestamp
                            "snippet": snippet
                        })
                        break  # Only add thread once per match

        return results
