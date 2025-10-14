"""
Example thread manager plugin that demonstrates how to override default thread management.
This plugin adds logging and could connect to a real database.
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid
import json

class ExampleThreadManagerPlugin:
    """Example thread manager plugin with enhanced logging and persistence"""
    
    def get_priority(self) -> int:
        """Higher priority than default (which is 0)"""
        return 10
    
    def __init__(self):
        # In-memory storage for demo (could be database in real implementation)
        self.threads = {}
        self.messages = {}
        self.stats = {
            "threads_created": 0,
            "messages_added": 0,
            "searches_performed": 0
        }
        
        print("[ThreadManagerPlugin] Initialized with enhanced logging")
    
    def _log_operation(self, operation: str, thread_id: str = None, details: Dict[str, Any] = None):
        """Log thread operations for debugging/monitoring"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "operation": operation,
            "thread_id": thread_id,
            "details": details,
            "stats": self.stats.copy()
        }
        print(f"[ThreadManagerPlugin] {json.dumps(log_entry)}")
    
    def create_thread(self, title: str, model: str, system_prompt: str, user: str) -> str:
        """Create a new thread with logging and user isolation"""
        thread_id = str(uuid.uuid4())

        thread = {
            "id": thread_id,
            "title": title,
            "created": datetime.now().isoformat(),
            "model": model,
            "system_prompt": system_prompt,
            "is_archived": False,
            "user": user,
            "metadata": {
                "plugin_version": "1.0",
                "created_by_plugin": True
            }
        }

        self.threads[thread_id] = thread
        self.messages[thread_id] = []
        self.stats["threads_created"] += 1

        self._log_operation("create_thread", thread_id, {"title": title, "model": model, "user": user})

        return thread_id
    
    def get_threads(self, user: str, query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get threads with optional search, logging, and user isolation"""
        self._log_operation("get_threads", details={"query": query, "user": user})

        threads = []

        for thread in self.threads.values():
            if thread["is_archived"]:
                continue

            # User isolation - only show threads belonging to this user
            if thread.get("user") != user:
                continue

            if query:
                # Enhanced search - search in title and system prompt
                search_text = f"{thread['title']} {thread['system_prompt']}".lower()
                if query.lower() not in search_text:
                    continue

            threads.append({
                "id": thread["id"],
                "title": thread["title"],
                "created": thread["created"],
                "model": thread["model"],
                "system_prompt": thread["system_prompt"],
                "metadata": thread.get("metadata", {})
            })

        # Sort by creation date (newest first)
        threads.sort(key=lambda x: x["created"], reverse=True)

        self._log_operation("get_threads_complete", details={"count": len(threads), "user": user})

        return threads
    
    def get_thread_messages(self, thread_id: str, user: str) -> Optional[List[Dict[str, Any]]]:
        """Get messages with validation, logging, and user access control"""
        self._log_operation("get_thread_messages", thread_id, {"user": user})

        if thread_id not in self.messages:
            self._log_operation("get_thread_messages_not_found", thread_id)
            return None

        # Check user has access to this thread
        thread = self.threads.get(thread_id)
        if not thread or thread.get("user") != user:
            self._log_operation("get_thread_messages_access_denied", thread_id, {"user": user})
            return None

        messages = self.messages[thread_id]
        self._log_operation("get_thread_messages_complete", thread_id, {"count": len(messages)})

        return messages

    def add_message(self, thread_id: str, user: str, role: str, type: str, content: str, model: Optional[str] = None) -> bool:
        """Add message with validation, logging, and user access control"""
        self._log_operation("add_message", thread_id, {"user": user, "role": role, "type": type, "content_length": len(content)})

        if thread_id not in self.messages:
            self._log_operation("add_message_thread_not_found", thread_id)
            return False

        # Check user has access to this thread
        thread = self.threads.get(thread_id)
        if not thread or thread.get("user") != user:
            self._log_operation("add_message_access_denied", thread_id, {"user": user})
            return False

        # Validate message content
        if not content or not content.strip():
            self._log_operation("add_message_empty_content", thread_id)
            return False

        message = {
            "role": role,
            "type": type,
            "content": content.strip(),
            "timestamp": datetime.now().isoformat(),
            "metadata": {
                "added_by_plugin": True
            }
        }

        if model and role == "assistant":
            message["model"] = model

        self.messages[thread_id].append(message)
        self.stats["messages_added"] += 1

        self._log_operation("add_message_complete", thread_id, {"message_count": len(self.messages[thread_id])})

        return True

    def update_thread(self, thread_id: str, user: str, title: Optional[str] = None) -> bool:
        """Update thread with validation, logging, and user access control"""
        self._log_operation("update_thread", thread_id, {"user": user, "title": title})

        if thread_id not in self.threads:
            self._log_operation("update_thread_not_found", thread_id)
            return False

        # Check user has access to this thread
        thread = self.threads.get(thread_id)
        if not thread or thread.get("user") != user:
            self._log_operation("update_thread_access_denied", thread_id, {"user": user})
            return False

        if title:
            # Validate title
            title = title.strip()
            if len(title) > 200:  # Reasonable limit
                title = title[:197] + "..."
            self.threads[thread_id]["title"] = title
            self.threads[thread_id]["updated"] = datetime.now().isoformat()

        self._log_operation("update_thread_complete", thread_id)
        return True

    def archive_thread(self, thread_id: str, user: str) -> bool:
        """Archive thread with logging and user access control"""
        self._log_operation("archive_thread", thread_id, {"user": user})

        if thread_id not in self.threads:
            self._log_operation("archive_thread_not_found", thread_id)
            return False

        # Check user has access to this thread
        thread = self.threads.get(thread_id)
        if not thread or thread.get("user") != user:
            self._log_operation("archive_thread_access_denied", thread_id, {"user": user})
            return False

        self.threads[thread_id]["is_archived"] = True
        self.threads[thread_id]["archived_at"] = datetime.now().isoformat()

        self._log_operation("archive_thread_complete", thread_id)
        return True

    def search_threads(self, query: str, user: str) -> List[Dict[str, Any]]:
        """Search threads with enhanced logging, metrics, and user isolation"""
        self._log_operation("search_threads", details={"query": query, "user": user})
        self.stats["searches_performed"] += 1

        results = []

        for thread_id, messages in self.messages.items():
            thread = self.threads.get(thread_id)
            if not thread or thread["is_archived"]:
                continue

            # User isolation - only search in threads belonging to this user
            if thread.get("user") != user:
                continue

            # Enhanced search - search in messages and thread metadata
            found = False
            best_snippet = ""

            for message in messages:
                content = message.get("content", "")
                if query.lower() in content.lower():
                    # Create better snippet with context
                    query_pos = content.lower().find(query.lower())
                    start = max(0, query_pos - 30)
                    end = min(len(content), query_pos + len(query) + 30)
                    snippet = content[start:end]

                    if start > 0:
                        snippet = "..." + snippet
                    if end < len(content):
                        snippet = snippet + "..."

                    if len(snippet) > len(best_snippet):  # Keep longest match
                        best_snippet = snippet
                    found = True

            if found:
                results.append({
                    "id": thread_id,
                    "title": thread["title"],
                    "snippet": best_snippet,
                    "metadata": {
                        "message_count": len(messages),
                        "last_activity": messages[-1]["timestamp"] if messages else thread["created"]
                    }
                })

        # Sort by relevance (could be improved with proper scoring)
        results.sort(key=lambda x: len(x["snippet"]), reverse=True)

        self._log_operation("search_threads_complete", details={"result_count": len(results), "user": user})

        return results
