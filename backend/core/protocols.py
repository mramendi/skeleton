"""
Plugin protocols - define the contracts that plugins must implement.
All protocols are defined here to avoid duplication and maintain consistency.
"""
from typing import Dict, Any, Optional, List, Protocol, Union, Type, AsyncGenerator
from abc import abstractmethod
from typing import runtime_checkable
from asyncio import Task
from openai.types.chat import ChatCompletionChunk

# Define a type alias for the allowed top-level JSON structures
JSONStructuredRepresentation = Union[Dict[str, Any], List[Any]]

@runtime_checkable
class CorePlugin(Protocol):
    """Base protocol for all core functionality plugins"""
    def get_role(self) -> str:
        """Return the role string for this plugin (e.g., 'auth', 'model', 'store')"""
        ...

    def get_priority(self) -> int:
        """Return plugin priority (higher numbers override lower)"""
        ...

    async def shutdown(self) -> None:
        """Graceful shutdown. Can be a no-op"""
        ...



@runtime_checkable
class AuthPlugin(CorePlugin, Protocol):
    """Protocol for authentication plugins - override default authentication"""

    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Authenticate a user"""
        ...

    def create_token(self, user: Dict[str, Any]) -> str:
        """Create JWT token for user"""
        ...

    def verify_token(self, token: str) -> Optional[str]:
        """Verify JWT token and return username"""
        ...

    def request_allowed(self, username: str, model_name: str) -> bool:
        """Check if a user is allowed to request a specific model.

        This method can be extended to include rate limiting, quotas, or other
        request-specific authorization logic.
        """
        ...

@runtime_checkable
class ModelPlugin(CorePlugin, Protocol):
    """Protocol for model client plugins - override default model handling"""

    async def get_available_models(self) -> List[str]:
        """Return list of available models"""
        ...

    async def generate_response(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Any:  # AsyncGenerator[Dict[str, Any], None]
        """Generate streaming response"""
        ...

@runtime_checkable
class ThreadManagerPlugin(CorePlugin, Protocol):
    """Protocol for thread management plugins - override default thread handling

    All methods are async to support both in-memory and database-backed implementations.
    In-memory implementations can use async def with immediate return values.
    Database implementations can use await for async database drivers.
    """

    async def create_thread(self, title: str, model: str, system_prompt: str, user: str) -> str:
        """Create a new thread for a specific user"""
        ...

    async def get_threads(self, user: str, query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all non-archived threads for a user, optionally filtered by query"""
        ...

    async def get_thread_messages(self, thread_id: str, user: str) -> Optional[List[Dict[str, Any]]]:
        """Get all messages for a thread if user has access"""
        ...

    async def add_message(self, thread_id: str, user: str, role: str, type: Optional[str], content: str, model: Optional[str] = None, aux_id: Optional[str] = None) -> bool:
        """Add a message to a thread if user has access. aux_id can be tool call_id, file ID, etc."""
        ...

    async def update_thread(self, thread_id: str, user: str, title: Optional[str] = None) -> bool:
        """Update thread metadata if user has access"""
        ...

    async def archive_thread(self, thread_id: str, user: str) -> bool:
        """Archive a thread if user has access"""
        ...

    async def search_threads(self, query: str, user: str) -> List[Dict[str, Any]]:
        """Search across all thread messages for a user"""
        ...

@runtime_checkable
class StorePlugin(CorePlugin, Protocol):
    """Protocol for store plugins - override default data storage

    Store plugins provide unified CRUD operations across any store/table/collection.
    They support both regular JSON fields and append-only JSON collections.
    """

    async def create_store_if_not_exists(self, store_name: str, schema: Dict[str, str],
                                        cacheable: bool = False) -> bool:
        """Create a new store with required schema if it doesn't already exist"""
        ...

    async def list_stores(self) -> List[str]:
        """List all available store names"""
        ...

    async def find_store(self, store_name: str) -> Optional[Dict[str, str]]:
        """Find a store and return its schema"""
        ...

    async def add(self, user_id: str, store_name: str, data: Dict[str, Any],
                  record_id: Optional[str] = None) -> str:
        """Add a new record to a store"""
        ...

    async def get(self, user_id: str, store_name: str, record_id: str,
                  load_collections: bool = False) -> Optional[Dict[str, Any]]:
        """Get a single record by ID"""
        ...

    async def find(self, user_id: str, store_name: str, filters: Dict[str, Any] = None,
                   limit: Optional[int] = None, offset: int = 0,
                   order_by: str = None, order_desc: bool = False) -> List[Dict[str, Any]]:
        """Find records with optional filters, pagination, and sorting"""
        ...


    async def update(self, user_id: str, store_name: str, record_id: str,
                     updates: Dict[str, Any], partial: bool = True) -> bool:
        """Update a record by ID"""
        ...

    async def delete(self, user_id: str, store_name: str, record_id: str) -> bool:
        """Delete a record by ID"""
        ...

    async def count(self, user_id: str, store_name: str, filters: Dict[str, Any] = None) -> int:
        """Count records matching filters"""
        ...

    async def collection_append(self, user_id: str, store_name: str, record_id: str,
                               field_name: str, item: JSONStructuredRepresentation) -> int:
        """Append an item to a json_collection field"""
        ...

    async def collection_get(self, user_id: str, store_name: str, record_id: str,
                            field_name: str, limit: Optional[int] = None,
                            offset: int = 0) -> List[JSONStructuredRepresentation]:
        """Get items from a json_collection field with pagination"""
        ...

    async def full_text_search(self, user_id: str, store_name: str, query: str,
                               limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Full-text search across all indexable fields in a store.
        Returns matching parent records with all collections loaded.
        """
        ...

@runtime_checkable
class ContextPlugin(CorePlugin, Protocol):
    """
    Protocol for stateful context management plugins.

    Manages a mutable, cached context, distinct from the immutable history.
    This version supports message IDs for efficient, targeted modifications
    like editing and removal, and provides a clean output for the model.
    """

    async def get_context(
        self,
        thread_id: str,
        user_id: str,
        strip_extra: bool = True
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Retrieve the currently cached context for a thread.

        Args:
            thread_id: The ID of the thread.
            user_id: The ID of the user who owns the thread.
            strip_extra: If True (default), returns messages in a clean format
                         suitable for the model API (e.g., stripping internal
                         IDs). If False, returns messages with all internal
                         metadata.

        Returns:
            The cached list of message dictionaries, or None if not found.
        """
        ...

    async def get_mutation_count(
        self,
        thread_id: str,
        user_id: str,
    ) -> Optional[int]:
        """Retrieve the mutation count of the context for a thread.
           An increase in the mutation count means that any message was *modified*
           (and not just appended).
           Background context conpressors need to BACK OFF if they see that
           the mutation count is increased
           Returns None only if the context does not exist"""
        ...

    async def add_message(
        self,
        thread_id: str,
        user_id: str,
        message: Dict[str, Any],
        message_id: Optional[str] = None
    ) -> str:
        """
        Add a single message to the end of the cached context. If the context for
        this thread_id does not exist, it is created.

        Every message is assigned a unique ID for future reference.

        Args:
            thread_id: The ID of the thread.
            user_id: The ID of the user who owns the thread.
            message: The message dictionary to add.
            message_id: An optional unique ID for the message. If None, the
                        plugin will generate and return a unique ID.

        Returns:
            The unique ID of the message that was added.
        """
        ...

    async def get_message(
        self,
        thread_id: str,
        user_id: str,
        message_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific message in the cached context by its ID.
        Args:
            thread_id: The ID of the thread.
            user_id: The ID of the user who owns the thread.
            message_id: The unique ID for the message.

        Returns:
            The message, or None if the thread_id or message_id is not found.
        """
        ...

    async def update_message(
        self,
        thread_id: str,
        user_id: str,
        message_id: str,
        updates: Dict[str, Any]
    ) -> bool:
        """
        Update a specific message in the cached context by its ID.

        This is used for in-place modifications, such as removing a
        'reasoning' key after a tool call loop is complete.

        Args:
            thread_id: The ID of the thread.
            user_id: The ID of the user who owns the thread.
            message_id: The unique ID of the message to update.
            updates: A dictionary of key-value pairs to update in the message.
                     To remove a key, set its value to `None`.

        Returns:
            True if the message was successfully updated, False otherwise.
        """
        ...

    async def remove_messages(
        self,
        thread_id: str,
        user_id: str,
        message_ids: List[str]
    ) -> bool:
        """
        Efficiently remove specific messages from the cached context by their IDs.

        Args:
            thread_id: The ID of the thread.
            user_id: The ID of the user who owns the thread.
            message_ids: A list of unique message IDs to remove.

        Returns:
            True if the operation was successful, False otherwise.
        """
        ...

    async def update_context(
        self,
        thread_id: str,
        user_id: str,
        context: List[Dict[str, Any]]
    ) -> bool:
        """
        Overwrite the entire cached context for a thread.
        Used for bulk transformations like compression.
        """
        ...

    async def regenerate_context(
        self,
        thread_id: str,
        user_id: str
    ) -> List[Dict[str, Any]]:
        """
        Regenerate the context for a thread from its full history.
        """
        ...


@runtime_checkable
class SystemPromptPlugin(CorePlugin, Protocol):
    """Protocol for system prompt management plugins"""

    async def get_prompt(self, key: str) -> Optional[str]:
        """Get system prompt content by key. Returns None if not found."""
        ...

    async def list_prompts(self) -> Dict[str, str]:
        """List all available prompt keys and descriptions."""
        ...

    async def get_all_prompts(self) -> Dict[str, Dict[str, str]]:
        """Get all prompts with full metadata."""
        ...

@runtime_checkable
class MessageProcessorPlugin(CorePlugin, Protocol):
    """
    Protocol for plugins that process user messages and generate streaming responses.

    This plugin orchestrates the entire message-handling flow, including thread
    management, model interaction, and response streaming, yielding a series of
    event dictionaries.
    """

    async def process_message(
        self,
        user_id: str,
        content: str,
        thread_id: Optional[str],
        model: Optional[str],
        system_prompt: Optional[str]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Process a message and yield a stream of event dictionaries.

        The plugin is responsible for the entire flow:
        - Creating or retrieving the thread
        - Adding the user's message to the thread history
        - Calling the model plugin to generate a response
        - Yielding events (e.g., thread_id, message_tokens, tool_call, error)
        - Saving the final assistant response to the thread history
        - Managing the context cache via the context plugin

        Args:
            user_id: The ID of the user sending the message.
            content: The content of the user's message.
            thread_id: The ID of the thread, or None to create a new one.
            model: The model to use for generation.
            system_prompt: The system prompt to use.

        Yields:
            Dictionaries representing events to be sent to the client.
        """
        ...

@runtime_checkable
class FunctionPlugin(Protocol):
    """
    Protocol for function plugins that can intervene at three distinct points
    in the request/response life-cycle:

    1. pre_call      – mutate the data that will be sent to the model
    2. filter_stream – inspect/transform every streamed chunk. Applies to
                       what is DISPLAYED to the user only
    3. post_call     – called after the turn is stored, can mutate context
                       (including the recent message) via the context manager
                       plugin. if you want to mutate the message BOTH in the
                       user-visible thread AND in the context, mutate it BOTH
                       in filter_stream() AND in post_call()
                       post_call() is also a good place to launch background tasks
                       (memory maintenance, context compression)

    The user_id, thread_id, and a turn_correlation_id that is the same throughout the entire turn (including
    any tool calls within the turn) are supplied

    The plugin_manager module is injected into every function module on load
    """

    def get_name(self) -> str:
        """Return unique function name (used for ordering and logging)"""
        ...

    def get_priority(self) -> int:
        """Return priority (higher runs first for pre_call, last for
           filter_stream and post_call)"""
        ...

    async def shutdown(self) -> None:
        """ Clean shutdown. If your function fires background tasks it should
            keep a registry of them and cancel them all on shutdown"""

    async def pre_call(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        new_message: Dict[str, Any],
        model: List[str],  # Single-element list for mutable string
        system_prompt: List[str],  # Single-element list for mutable string
        tools: List[Dict[str, Any]],
    ) -> Any:
        """
        Called before any model call (including calls with tool results)
        To be more exact it is called when a message is added to a call, so if
        multiple tool results are added before a call, pre_call() gets called with
        each of them separately and is free to mutate it.
        Mutate any of the following arguments in-place:
            new_message (this is the new message for the model call - user or tool result)
            model[0] (modify the first element to change the model)
            system_prompt[0] (modify the first element to change the system prompt)
            tools
        The core will apply these changes before calling the model.
        You can use the plugin_manager.get_plugin("thread") to get the thread
        manager and use it to read the thread history, and you can also use
        plugin_manager.get_plugin("context") to access the context if available.
        You can mutate the context (that does not have the new message yet) if
        it is there, but note that if support for stateful APIs like OpenAI
        Responses is ever added, mutating the context might be moot

        Can be an R2R generator if you need to yield user updates
        Any returned values are ignored
        """

    async def filter_stream(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        chunk: ChatCompletionChunk,
    ) -> Any:
        """
        Inspect or mutate every SSE chunk streamed to the user.
        Return:
          - None: drop the chunk (user sees nothing)
          - same chunk: pass through unchanged
          - modified chink: user sees the mutated version. STRONGLY RECOMMENDED
          to use `new_chunk = chunk.model_copy(deep=True)`, as modifying an
          original chunk in place can have unexpected side effects!

        The *original* chunk is stored in the context. The modified version is seen
        by the user and stored in the thread history. You can use post_call() to modify
        the context (if support for a stateful API like OpenAI Responses is added,
        a modified context might not work)

        Can be an R2R generator if you need to yield user updates
        """

    async def post_call(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        response_metadata: Dict[str, any],
        assistant_message: Dict[str, any],
    ) -> Any:
        """
        Called after a completed model call (which can be a tool_calls finish too)
        response_metadata is the part of the object that is not in content/reasoning_content
        This includes the ID as well as usage information and anything else that might be there

        You can mutate the assistant_message - this version will be stored in the
        context (though if support for stateful APIs like OpenAI Responses is added,
        this might be moot). The user-facing assistant message is already displayed by
        this point and cannot be changed.

        This is also a good place to launch background context compressors.

        Can be an R2R generator if you need to yield user updates; returned
        values are ignored


        """

@runtime_checkable
class ToolPlugin(Protocol):
    """Protocol for tool plugins for OpenAI function calling

    Tool plugins can be implemented in two ways:

    1. Class-based (legacy): Implement get_schema() and execute() methods
    2. Function-based (recommended): Just a plain Python function with type hints and docstring
       - The function will be auto-converted to OpenAI schema using llmio.function_parser
       - Arguments will be validated with Pydantic model

    Example function-based tool:
        def get_weather(location: str, unit: str = "celsius") -> dict:
            '''Get weather for a location.

            Args:
                location: City name
                unit: Temperature unit
            '''
            return {"temp": 20, "unit": unit}


    The user_id, thread_id, and a turn_correlation_id that is the same throughout the entire turn (including
    any tool calls within the turn) are supplied. Function-based plugins can define any of these arguments if desired.

    """

    def get_schema(self) -> Dict[str, Any]:
        """Return OpenAI function schema

        NOTE: For plain Python functions, this will be auto-generated
        using llmio.function_parser.model_from_function()
        """
        ...

    async def execute(self,
                user_id: str,
                thread_id: str,
                turn_correlation_id: str,
                arguments: Dict[str, Any]) -> Any:
        """Execute tool with arguments

        NOTE: For plain Python functions, arguments will be validated
        with Pydantic model before calling the function

        NOTE: This function or the underlying Python function can also be an R2R
        async generator
        """
        ...

# Protocol registry mapping role strings to protocol classes
# Must be defined after all protocol classes are declared
PROTOCOL_REGISTRY: Dict[str, Type[CorePlugin]] = {
    "auth": AuthPlugin,
    "model": ModelPlugin,
    "thread": ThreadManagerPlugin,
    "store": StorePlugin,
    "context": ContextPlugin,
    "system_prompt": SystemPromptPlugin,
    "message_processor": MessageProcessorPlugin,
}
