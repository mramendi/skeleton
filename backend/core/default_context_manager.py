"""
Default context manager implementation using the data store plugin.
Provides a mutable, cached context that can be regenerated from history.
"""
import uuid
import logging
from typing import Dict, Any, List, Optional

from .protocols import ContextPlugin

logger = logging.getLogger("skeleton.context_manager")

class DefaultContextManager():
    """Default context manager using data store - can be overridden by plugins"""

    def get_role(self) -> str:
        """Return the role string for this plugin"""
        return "context"

    def get_priority(self) -> int:
        """Default priority - plugins can override with higher priority"""
        return 0

    async def shutdown(self) -> None:
        return

    def __init__(self):
        # Store schema definition for the context cache.
        # The context list is stored in a 'context' field.
        self._store_schema = {
            "context": "json"  # The context list is stored in this field.
        }
        self._store_name = "ThreadContext"

    def _get_store(self):
        """Lazy access to store plugin - eliminates initialization order dependency"""
        from .plugin_manager import plugin_manager
        return plugin_manager.get_plugin("store")

    async def get_context(
        self,
        thread_id: str,
        user_id: str,
        strip_extra: bool = True
    ) -> Optional[List[Dict[str, Any]]]:
        """Retrieve the currently cached context for a thread."""
        store = self._get_store()
        await store.create_store_if_not_exists(self._store_name, self._store_schema, cacheable=True)

        # Get the record containing the context
        record = await store.get(user_id=user_id, store_name=self._store_name, record_id=thread_id)
        if not record or "context" not in record:
            return None

        context = record["context"]

        # Handle JSON string deserialization
        if isinstance(context, str):
            import json
            try:
                context = json.loads(context)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to deserialize context JSON for thread {thread_id}: {e}")
                return None

        # Validate that context is a list
        if not isinstance(context, list):
            logger.error(f"Context for thread {thread_id} is not a list: {type(context)}")
            return None

        if not strip_extra:
            return context

        # Strip internal metadata like '_id' from each message
        clean_context = []
        for msg in context:
            # Validate that each message is a dictionary
            if not isinstance(msg, dict):
                logger.error(f"Message in context is not a dictionary: {type(msg)} - {msg}")
                continue
            clean_msg = {k: v for k, v in msg.items() if not k.startswith('_')}
            clean_context.append(clean_msg)
        
        return clean_context

    async def add_message(
        self,
        thread_id: str,
        user_id: str,
        message: Dict[str, Any],
        message_id: Optional[str] = None
    ) -> str:
        """Add a single message to the end of the cached context."""
        store = self._get_store()
        await store.create_store_if_not_exists(self._store_name, self._store_schema, cacheable=True)

        # Generate a unique ID if not provided
        if not message_id:
            message_id = str(uuid.uuid4())

        # Add the internal ID to the message for tracking
        message_with_id = {"_id": message_id, **message}

        # Read-modify-write operation
        record = await store.get(user_id=user_id, store_name=self._store_name, record_id=thread_id)
        if record is None or "context" not in record:
            # If no context exists, create a new one with this message
            new_context = [message_with_id]
            await store.add(user_id=user_id, store_name=self._store_name, data={"context": new_context}, record_id=thread_id)
        else:
            # If context exists, append to it
            current_context = record["context"]
            
            # Handle JSON string deserialization
            if isinstance(current_context, str):
                import json
                try:
                    current_context = json.loads(current_context)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to deserialize context JSON for thread {thread_id}: {e}")
                    # Treat as if no context exists
                    new_context = [message_with_id]
                    await store.update(user_id=user_id, store_name=self._store_name, record_id=thread_id, updates={"context": new_context})
                    return message_id
            
            # Validate that context is a list
            if not isinstance(current_context, list):
                logger.error(f"Context for thread {thread_id} is not a list: {type(current_context)} - {current_context}")
                # Treat as if no context exists
                new_context = [message_with_id]
                await store.update(user_id=user_id, store_name=self._store_name, record_id=thread_id, updates={"context": new_context})
            else:
                current_context.append(message_with_id)
                await store.update(user_id=user_id, store_name=self._store_name, record_id=thread_id, updates={"context": current_context})
        
        return message_id

    async def update_message(
        self,
        thread_id: str,
        user_id: str,
        message_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """Update a specific message in the cached context by its ID."""
        store = self._get_store()
        await store.create_store_if_not_exists(self._store_name, self._store_schema, cacheable=True)

        # Read-modify-write operation
        record = await store.get(user_id=user_id, store_name=self._store_name, record_id=thread_id)
        if not record or "context" not in record:
            return False
        
        context = record["context"]
        
        # Handle JSON string deserialization
        if isinstance(context, str):
            import json
            try:
                context = json.loads(context)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to deserialize context JSON for thread {thread_id}: {e}")
                return False
        message_found = False

        for msg in context:
            if msg.get("_id") == message_id:
                for key, value in updates.items():
                    if value is None:
                        msg.pop(key, None)
                    else:
                        msg[key] = value
                message_found = True
                break
        
        if not message_found:
            return False

        await store.update(user_id=user_id, store_name=self._store_name, record_id=thread_id, updates={"context": context})
        return True

    async def remove_messages(
        self,
        thread_id: str,
        user_id: str,
        message_ids: List[str]
    ) -> bool:
        """Efficiently remove specific messages from the cached context by their IDs."""
        store = self._get_store()
        await store.create_store_if_not_exists(self._store_name, self._store_schema, cacheable=True)

        # Read-modify-write operation
        record = await store.get(user_id=user_id, store_name=self._store_name, record_id=thread_id)
        if not record or "context" not in record:
            return False
        
        original_context = record["context"]
        
        # Handle JSON string deserialization
        if isinstance(original_context, str):
            import json
            try:
                original_context = json.loads(original_context)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to deserialize context JSON for thread {thread_id}: {e}")
                return False
        ids_to_remove = set(message_ids)
        
        # Filter out messages with matching IDs
        new_context = [msg for msg in original_context if msg.get("_id") not in ids_to_remove]

        # Check if any messages were actually removed
        if len(new_context) == len(original_context):
            return False

        await store.update(user_id, self._store_name, thread_id, {"context": new_context})
        return True

    async def update_context(
        self,
        thread_id: str,
        user_id: str,
        context: List[Dict[str, Any]]
    ) -> bool:
        """Overwrite the entire cached context for a thread."""
        store = self._get_store()
        await store.create_store_if_not_exists(self._store_name, self._store_schema, cacheable=True)

        # Ensure all messages have an ID
        context_with_ids = []
        for msg in context:
            if "_id" not in msg:
                msg_with_id = {"_id": str(uuid.uuid4()), **msg}
                context_with_ids.append(msg_with_id)
            else:
                context_with_ids.append(msg)

        # Check if a record already exists to decide between add and update
        existing_record = await store.get(user_id, self._store_name, thread_id)
        if existing_record is None:
            await store.add(user_id=user_id, store_name=self._store_name, data={"context": context_with_ids}, record_id=thread_id)
        else:
            await store.update(user_id=user_id, store_name=self._store_name, record_id=thread_id, updates={"context": context_with_ids})
        return True

    async def regenerate_context(
        self,
        thread_id: str,
        user_id: str
    ) -> List[Dict[str, Any]]:
        """Regenerate the context for a thread from its full history."""
        # Invalidate first to ensure a clean slate
        await self.invalidate_context(thread_id, user_id)

        # Get history from the thread manager
        from .plugin_manager import plugin_manager
        thread_plugin = plugin_manager.get_plugin("thread")
        history = await thread_plugin.get_thread_messages(thread_id, user_id)

        if history is None:
            # Thread not found or no access, return empty context
            return []

        # Create new context from history, adding IDs
        # Only include user and assistant messages, strip extra fields
        context_with_ids = []
        for msg in history:
            # Skip messages that are not user or assistant
            if msg.get("role") not in ["user", "assistant"]:
                continue
            
            # Create clean message with only role and content
            # Use .get() to prevent crashes if content is missing
            clean_msg = {
                "role": msg["role"],
                "content": msg.get("content", "")
            }
            
            # Add ID for tracking
            context_with_ids.append({"_id": str(uuid.uuid4()), **clean_msg})

        # Store the newly generated context
        await self.update_context(thread_id, user_id, context_with_ids)
        
        return context_with_ids

    async def invalidate_context(
        self,
        thread_id: str,
        user_id: str
    ) -> bool:
        """Invalidate (delete) the cached context for a thread."""
        store = self._get_store()
        # The store must exist before we can delete from it
        await store.create_store_if_not_exists(self._store_name, self._store_schema, cacheable=True)
        return await store.delete(user_id=user_id, store_name=self._store_name, record_id=thread_id)
