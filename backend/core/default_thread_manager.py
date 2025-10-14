"""
Default thread manager implementation.
Can be overridden by plugins.
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid

class DefaultThreadManager:
    """Default thread manager - can be overridden by plugins"""
    
    def get_priority(self) -> int:
        """Default priority - plugins can override with higher priority"""
        return 0
    
    def __init__(self):
        # In-memory storage (replace with database in production)
        self.threads = {}
        self.messages = {}

    async def create_thread(self, title: str, model: str, system_prompt: str, user: str) -> str:
        """Create a new thread for a specific user"""
        thread_id = str(uuid.uuid4())

        thread = {
            "id": thread_id,
            "title": title,
            "created": datetime.now().isoformat(),
            "model": model,
            "system_prompt": system_prompt,
            "is_archived": False,
            "user": user
        }

        self.threads[thread_id] = thread
        self.messages[thread_id] = []

        return thread_id

    async def get_threads(self, user: str, query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all non-archived threads for a user, optionally filtered by query"""
        threads = []

        for thread in self.threads.values():
            # Skip archived threads
            if thread["is_archived"]:
                continue

            # Skip threads not belonging to this user
            if thread.get("user") != user:
                continue

            if query:
                # Simple text search in title
                if query.lower() not in thread["title"].lower():
                    continue

            threads.append({
                "id": thread["id"],
                "title": thread["title"],
                "created": thread["created"],
                "model": thread["model"],
                "system_prompt": thread["system_prompt"]
            })

        # Sort by creation date (newest first)
        threads.sort(key=lambda x: x["created"], reverse=True)

        return threads
    
    async def get_thread_messages(self, thread_id: str, user: str) -> Optional[List[Dict[str, Any]]]:
        """Get all messages for a thread if user has access"""
        if thread_id not in self.messages:
            return None

        # Check user has access to this thread
        thread = self.threads.get(thread_id)
        if not thread or thread.get("user") != user:
            return None

        return self.messages[thread_id]

    async def add_message(self, thread_id: str, user: str, role: str, type: str, content: str, model: Optional[str] = None) -> bool:
        """Add a message to a thread if user has access"""
        if thread_id not in self.messages:
            return False

        # Check user has access to this thread
        thread = self.threads.get(thread_id)
        if not thread or thread.get("user") != user:
            return False

        message = {
            "role": role,
            "type": type,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }

        if model and role == "assistant":
            message["model"] = model

        self.messages[thread_id].append(message)
        return True

    async def update_thread(self, thread_id: str, user: str, title: Optional[str] = None) -> bool:
        """Update thread metadata if user has access"""
        if thread_id not in self.threads:
            return False

        # Check user has access to this thread
        thread = self.threads.get(thread_id)
        if not thread or thread.get("user") != user:
            return False

        if title:
            self.threads[thread_id]["title"] = title

        return True

    async def archive_thread(self, thread_id: str, user: str) -> bool:
        """Archive a thread if user has access"""
        if thread_id not in self.threads:
            return False

        # Check user has access to this thread
        thread = self.threads.get(thread_id)
        if not thread or thread.get("user") != user:
            return False

        self.threads[thread_id]["is_archived"] = True
        return True

    async def search_threads(self, query: str, user: str) -> List[Dict[str, Any]]:
        """Search across all thread messages for a user"""
        results = []

        for thread_id, messages in self.messages.items():
            thread = self.threads.get(thread_id)
            if not thread or thread["is_archived"]:
                continue

            # Only search in threads belonging to this user
            if thread.get("user") != user:
                continue

            # Search in messages
            for message in messages:
                if query.lower() in message.get("content", "").lower():
                    # Create result with snippet
                    content = message.get("content", "")
                    snippet_start = max(0, content.lower().find(query.lower()) - 50)
                    snippet_end = min(len(content), snippet_start + 100)
                    snippet = content[snippet_start:snippet_end]

                    if snippet_start > 0:
                        snippet = "..." + snippet
                    if snippet_end < len(content):
                        snippet = snippet + "..."

                    results.append({
                        "id": thread_id,
                        "title": thread["title"],
                        "snippet": snippet
                    })
                    break  # Only add thread once

        return results
