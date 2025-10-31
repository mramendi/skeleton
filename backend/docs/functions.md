# Function Plugins Documentation

## Executive Summary

Function plugins are powerful middleware components that can intercept and modify requests at three key points in the message processing lifecycle:

1. **pre_call** - Modify data before it reaches the model (change prompts, tools, model selection)
2. **filter_stream** - Transform or filter streaming responses in real-time
3. **post_call** - Process completed responses and launch background tasks

Function plugins can:
- Mutate model parameters, system prompts, and tools dynamically
- Filter, transform, or inject content into response streams
- Launch long-running background tasks with proper cancellation support
- Access all core plugins (auth, store, thread, context, etc.)
- Use the Raise-to-Return (R2R) pattern for streaming updates
- Store and retrieve data using the store plugin
- Manipulate conversation context using the context plugin

## Function Plugin Lifecycle

```
User Request → pre_call() → Model → filter_stream() → post_call() → Response
                     ↑              ↑                   ↑
                Modify data    Transform         Process & launch
                before model   streaming         background tasks
```

## Core Concepts

### Priority System
- Use priority to control plugin execution order
- Higher priority numbers run first for `pre_call`
- Lower priority numbers run first for `filter_stream` and `post_call`

### Correlation Parameters

Every function plugin call receives the following parameters to correlate their use
and ensure proper context:

- `user_id`
- `thread_id` - the internal ID of the current thread
- `turn_correlation_id` - the ID unique to a "turn". A turn is everything that happens
between the user sending a message and control returning to the user, so if tool calls
are involved, a turn is several LLM calls.

### Mutable Parameters
Function plugins receive mutable parameters that can be modified in-place:
- `model[0]` - Single-element list for mutable string
- `system_prompt[0]` - Single-element list for mutable string
- `tools` - Mutable list of tool schemas
- `new_message` - Mutable message dictionary, to be sent to the model (pre_call only)
- `assistant_message` - Mutable assistant message (post_call only)

### Background Task Management
Function plugins can launch background tasks that:
- Support graceful cancellation on shutdown
- Are tracked in a central registry
- Must implement proper cleanup logic

### Using Core Plugins

Function plugins have full access to all core plugins through the injected `plugin_manager` module:

#### Store Plugin Usage

```python
from backend.core.plugin_manager import plugin_manager

class DataAnalyticsFunction(FunctionPlugin):
    """Analyzes conversation data and stores insights"""

    async def post_call(self, user_id: str, thread_id: str, turn_correlation_id: str,
                       response_metadata: Dict[str, Any], assistant_message: Dict[str, Any]) -> None:
        
        store = plugin_manager.get_plugin("store")
        
        # Create analytics store if needed
        await store.create_store_if_not_exists(
            "conversation_analytics",
            {
                "user_id": "text",
                "thread_id": "text",
                "message_length": "integer",
                "tool_calls_count": "integer",
                "response_time": "real",
                "timestamp": "text",
                "tags": "json_collection"
            }
        )
        
        # Store analytics data
        await store.add(
            user_id=user_id,
            store_name="conversation_analytics",
            data={
                "thread_id": thread_id,
                "message_length": len(assistant_message.get("content", "")),
                "tool_calls_count": len(assistant_message.get("tool_calls", [])),
                "response_time": response_metadata.get("usage", {}).get("total_time", 0),
                "timestamp": datetime.now().isoformat()
            }
        )
        
        # Add tags for categorization
        await store.collection_append(
            user_id=user_id,
            store_name="conversation_analytics",
            record_id=thread_id,
            field_name="tags",
            item="analyzed"
        )
```

#### Context Plugin and Mutation Counter

The context plugin manages the mutable conversation context used by the model. Background tasks that manipulate context must respect the mutation counter to avoid conflicts:

```python
class ContextCompressorFunction(FunctionPlugin):
    """Compresses conversation context in the background"""

    def __init__(self):
        self._compression_tasks: Set[asyncio.Task] = set()
        self._shutdown_event = asyncio.Event()

    async def post_call(self, user_id: str, thread_id: str, turn_correlation_id: str,
                       response_metadata: Dict[str, Any], assistant_message: Dict[str, Any]) -> None:
        
        # Launch context compression if conversation is long
        context_plugin = plugin_manager.get_plugin("context")
        context = await context_plugin.get_context(thread_id, user_id)
        
        if context and len(context) > 50:  # Compress after 50 messages
            task = asyncio.create_task(
                self._compress_context_background(user_id, thread_id)
            )
            self._compression_tasks.add(task)
            task.add_done_callback(self._compression_tasks.discard)
            yield "🗜️ Started context compression task"

    async def _compress_context_background(self, user_id: str, thread_id: str) -> None:
        """Background task that compresses context while respecting mutations"""
        
        context_plugin = plugin_manager.get_plugin("context")
        model_plugin = plugin_manager.get_plugin("model")
        
        try:
            # Get initial mutation count
            initial_mutation_count = await context_plugin.get_mutation_count(thread_id, user_id)
            if initial_mutation_count is None:
                return  # Context doesn't exist
            
            # Get current context
            context = await context_plugin.get_context(thread_id, user_id, strip_extra=False)
            if not context:
                return
            
            # Check for shutdown before starting
            if self._shutdown_event.is_set():
                return
            
            # Generate compressed summary
            summary_prompt = "Compress this conversation into key points while preserving important details:"
            compressed_content = ""
            
            async for event in model_plugin.generate_response(
                messages=context,
                model="gpt-3.5-turbo",
                system_prompt=summary_prompt,
                tools=[]
            ):
                if event.get("event") == "message_tokens":
                    compressed_content += event["data"]["content"]
                
                # Check for shutdown during generation
                if self._shutdown_event.is_set():
                    logger.info(f"Context compression cancelled for thread {thread_id}")
                    return
            
            # CRITICAL: Check if context was mutated during compression
            current_mutation_count = await context_plugin.get_mutation_count(thread_id, user_id)
            if current_mutation_count != initial_mutation_count:
                logger.warning(f"Context mutated during compression for thread {thread_id}. Aborting.")
                return
            
            # Create new compressed context
            new_context = [
                {
                    "role": "system",
                    "content": f"[Context compressed at {datetime.now().isoformat()}] Previous conversation summarized below:",
                    "id": str(uuid.uuid4())
                },
                {
                    "role": "assistant", 
                    "content": compressed_content,
                    "id": str(uuid.uuid4())
                }
            ]
            
            # Update context atomically
            await context_plugin.update_context(thread_id, user_id, new_context)
            
            logger.info(f"Successfully compressed context for thread {thread_id}")
            
        except asyncio.CancelledError:
            logger.info(f"Context compression task cancelled for thread {thread_id}")
        except Exception as e:
            logger.error(f"Error compressing context for thread {thread_id}: {e}")

    async def shutdown(self) -> None:
        """Cancel all compression tasks on shutdown"""
        self._shutdown_event.set()
        for task in self._compression_tasks:
            task.cancel()
        if self._compression_tasks:
            await asyncio.gather(*self._compression_tasks, return_exceptions=True)
```

#### Mutation Counter Best Practices

The mutation counter is critical for background context operations:

1. **Always check before and after**: Get the mutation count before starting and verify it hasn't changed
2. **Abort on mutations**: If the count changed, another process modified the context - abort to avoid conflicts
3. **Atomic updates**: Use `update_context()` for complete replacements, not individual message modifications
4. **Respect shutdown**: Check shutdown events during long-running operations

```python
# Safe pattern for background context operations
async def safe_context_operation(user_id: str, thread_id: str):
    context_plugin = plugin_manager.get_plugin("context")
    
    # Get initial state
    initial_count = await context_plugin.get_mutation_count(thread_id, user_id)
    if initial_count is None:
        return
    
    # Do work...
    await perform_long_operation()
    
    # Verify no mutations occurred
    current_count = await context_plugin.get_mutation_count(thread_id, user_id)
    if current_count != initial_count:
        logger.warning("Context mutated - aborting operation")
        return
    
    # Apply changes atomically
    await context_plugin.update_context(thread_id, user_id, new_context)
```

## Implementation Examples

### 1. Simple Logging Function

```python
import logging
from typing import Dict, Any, List
from backend.core.protocols import FunctionPlugin

logger = logging.getLogger("skeleton.function.logger")

class LoggingFunction(FunctionPlugin):
    """Logs all function calls for debugging"""

    def get_name(self) -> str:
        return "logger"

    def get_priority(self) -> int:
        return 100  # Run early

    async def shutdown(self) -> None:
        logger.info("Logging function shutting down")

    async def pre_call(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        new_message: Dict[str, Any],
        model: List[str],
        system_prompt: List[str],
        tools: List[Dict[str, Any]],
    ) -> None:
        logger.info(f"Pre-call: user={user_id}, thread={thread_id}, model={model[0]}")

    async def filter_stream(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        chunk: Any,
    ) -> Any:
        # Just log and pass through
        logger.debug(f"Filter stream: {chunk.get('event', 'unknown')}")
        return chunk

    async def post_call(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        response_metadata: Dict[str, Any],
        assistant_message: Dict[str, Any],
    ) -> None:
        logger.info(f"Post-call: user={user_id}, response_length={len(assistant_message.get('content', ''))}")
```

### 2. Dynamic Model Switcher

```python
from typing import Dict, Any, List
from backend.core.protocols import FunctionPlugin

class ModelSwitcherFunction(FunctionPlugin):
    """Dynamically switches models based on content analysis"""

    def get_name(self) -> str:
        return "model_switcher"

    def get_priority(self) -> int:
        return 90  # Run before most plugins

    async def pre_call(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        new_message: Dict[str, Any],
        model: List[str],
        system_prompt: List[str],
        tools: List[Dict[str, Any]],
    ) -> None:
        content = new_message.get("content", "").lower()

        # Switch to code model for programming requests
        if any(keyword in content for keyword in ["code", "program", "debug", "function"]):
            model[0] = "GLM-4.6"
            yield "🔧 Switched to code-specialized model"

        # Switch to fast model for simple queries
        elif len(content.split()) < 10 and "?" in content:
            model[0] = "Qwen3-80B-A3B-Instruct"
            yield "⚡ Using fast model for simple query"

    async def filter_stream(self, user_id: str, thread_id: str, turn_correlation_id: str, chunk: Any) -> Any:
        return chunk  # No filtering

    async def post_call(self, user_id: str, thread_id: str, turn_correlation_id: str,
                       response_metadata: Dict[str, Any], assistant_message: Dict[str, Any]) -> None:
        pass  # No post-processing
```

### 3. Content Filter with R2R Pattern

```python
import re
from typing import Dict, Any, List
from backend.core.protocols import FunctionPlugin

class ContentFilterFunction(FunctionPlugin):
    """Filters sensitive content from responses"""

    def get_name(self) -> str:
        return "content_filter"

    def get_priority(self) -> int:
        return 10  # Run late in filter_stream

    async def pre_call(self, user_id: str, thread_id: str, turn_correlation_id: str,
                      new_message: Dict[str, Any], model: List[str],
                      system_prompt: List[str], tools: List[Dict[str, Any]]) -> None:
        # Add content filtering to system prompt
        system_prompt[0] += "\n\nIMPORTANT: Do not include sensitive personal information, API keys, or passwords in your response."
        yield "🛡️ Content filter activated"

    async def filter_stream(self, user_id: str, thread_id: str, turn_correlation_id: str, chunk: Any) -> Any:
        if chunk.get("event") == "message_tokens":
            content = chunk["data"]["content"]

            # Filter potential API keys
            filtered = re.sub(r'[A-Za-z0-9]{32,}', '[REDACTED]', content)

            # Filter email addresses
            filtered = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', filtered)

            if filtered != content:
                yield "🔒 Filtered sensitive content"
                chunk["data"]["content"] = filtered

        return chunk

    async def post_call(self, user_id: str, thread_id: str, turn_correlation_id: str,
                       response_metadata: Dict[str, Any], assistant_message: Dict[str, Any]) -> None:
        pass
```

### 4. Background Task Manager

```python
import asyncio
import logging
from typing import Dict, Any, List, Set
from backend.core.protocols import FunctionPlugin
from backend.core.plugin_manager import plugin_manager

logger = logging.getLogger("skeleton.function.background_tasks")

class BackgroundTaskManager(FunctionPlugin):
    """Manages background tasks with proper cancellation support"""

    def __init__(self):
        self._background_tasks: Set[asyncio.Task] = set()
        self._shutdown_event = asyncio.Event()

    def get_name(self) -> str:
        return "background_task_manager"

    def get_priority(self) -> int:
        return 1  # Run last in post_call

    async def shutdown(self) -> None:
        """Cancel all background tasks on shutdown"""
        logger.info(f"Cancelling {len(self._background_tasks)} background tasks")

        # Signal shutdown to all tasks
        self._shutdown_event.set()

        # Cancel all tasks
        for task in self._background_tasks:
            task.cancel()

        # Wait for tasks to finish
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

        logger.info("All background tasks cancelled")

    async def pre_call(self, user_id: str, thread_id: str, turn_correlation_id: str,
                      new_message: Dict[str, Any], model: List[str],
                      system_prompt: List[str], tools: List[Dict[str, Any]]) -> None:
        pass  # No pre-processing

    async def filter_stream(self, user_id: str, thread_id: str, turn_correlation_id: str, chunk: Any) -> Any:
        return chunk  # No filtering

    async def post_call(self, user_id: str, thread_id: str, turn_correlation_id: str,
                       response_metadata: Dict[str, Any], assistant_message: Dict[str, Any]) -> None:
        """Launch background tasks based on conversation content"""

        # Example: Summarize long conversations
        if self._should_summarize(thread_id, user_id):
            task = asyncio.create_task(
                self._summarize_conversation_background(user_id, thread_id)
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            yield "📝 Started conversation summarization task"

        # Example: Extract and store insights
        if self._should_extract_insights(assistant_message):
            task = asyncio.create_task(
                self._extract_insights_background(user_id, thread_id, assistant_message)
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            yield "💡 Started insight extraction task"

    def _should_summarize(self, thread_id: str, user_id: str) -> bool:
        """Check if conversation needs summarization"""
        try:
            thread_plugin = plugin_manager.get_plugin("thread")
            messages = thread_plugin.get_thread_messages(thread_id, user_id)
            return messages and len(messages) > 20  # Summarize after 20 messages
        except Exception:
            return False

    def _should_extract_insights(self, message: Dict[str, Any]) -> bool:
        """Check if message contains extractable insights"""
        content = message.get("content", "")
        return len(content) > 500 and any(keyword in content.lower()
                                        for keyword in ["important", "key", "critical", "remember"])

    async def _summarize_conversation_background(self, user_id: str, thread_id: str) -> None:
        """Background task to summarize conversation"""
        try:
            # Check for shutdown
            if self._shutdown_event.is_set():
                return

            thread_plugin = plugin_manager.get_plugin("thread")
            model_plugin = plugin_manager.get_plugin("model")
            store_plugin = plugin_manager.get_plugin("store")

            # Get conversation history
            messages = thread_plugin.get_thread_messages(thread_id, user_id)
            if not messages:
                return

            # Create summary
            summary_prompt = "Summarize this conversation in 3-5 bullet points:"
            summary_content = ""

            async for event in model_plugin.generate_response(
                messages=messages[-10:],  # Last 10 messages
                model="gpt-3.5-turbo",
                system_prompt=summary_prompt,
                tools=[]
            ):
                if event.get("event") == "message_tokens":
                    summary_content += event["data"]["content"]

                # Check for shutdown during generation
                if self._shutdown_event.is_set():
                    logger.info("Summary task cancelled during generation")
                    return

            # Store summary
            await store_plugin.create_store_if_not_exists(
                "conversation_summaries",
                {
                    "user_id": "text",
                    "thread_id": "text",
                    "summary": "text",
                    "created_at": "text"
                }
            )

            await store_plugin.add(
                user_id=user_id,
                store_name="conversation_summaries",
                data={
                    "thread_id": thread_id,
                    "summary": summary_content,
                    "created_at": asyncio.get_event_loop().time()
                }
            )

            logger.info(f"Stored summary for thread {thread_id}")

        except asyncio.CancelledError:
            logger.info("Summary task cancelled")
        except Exception as e:
            logger.error(f"Error in summary task: {e}")

    async def _extract_insights_background(self, user_id: str, thread_id: str, message: Dict[str, Any]) -> None:
        """Background task to extract insights from messages"""
        try:
            if self._shutdown_event.is_set():
                return

            store_plugin = plugin_manager.get_plugin("store")
            model_plugin = plugin_manager.get_plugin("model")

            # Extract insights using model
            insight_prompt = "Extract key insights, action items, and important information from this message:"
            content = message.get("content", "")

            insight_content = ""

            async for event in model_plugin.generate_response(
                messages=[{"role": "user", "content": content}],
                model="gpt-3.5-turbo",
                system_prompt=insight_prompt,
                tools=[]
            ):
                if event.get("event") == "message_tokens":
                    insight_content += event["data"]["content"]

                if self._shutdown_event.is_set():
                    return

            # Store insights
            await store_plugin.create_store_if_not_exists(
                "message_insights",
                {
                    "user_id": "text",
                    "thread_id": "text",
                    "message_id": "text",
                    "insights": "text",
                    "created_at": "text"
                }
            )

            await store_plugin.add(
                user_id=user_id,
                store_name="message_insights",
                data={
                    "thread_id": thread_id,
                    "message_id": message.get("id", ""),
                    "insights": insight_content,
                    "created_at": asyncio.get_event_loop().time()
                }
            )

            logger.info(f"Stored insights for message in thread {thread_id}")

        except asyncio.CancelledError:
            logger.info("Insight extraction task cancelled")
        except Exception as e:
            logger.error(f"Error in insight extraction task: {e}")
```

### 5. Tool Injection Function

```python
from typing import Dict, Any, List
from backend.core.protocols import FunctionPlugin

class ToolInjectorFunction(FunctionPlugin):
    """Dynamically injects tools based on context"""

    def get_name(self) -> str:
        return "tool_injector"

    def get_priority(self) -> int:
        return 80  # Run before model selection

    async def pre_call(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        new_message: Dict[str, Any],
        model: List[str],
        system_prompt: List[str],
        tools: List[Dict[str, Any]],
    ) -> None:
        content = new_message.get("content", "").lower()

        # Always include calculator for math expressions
        if any(char in content for char in "+-*/()"):
            calc_schema = {
                "type": "function",
                "function": {
                    "name": "calculate_expression",
                    "description": "Evaluate mathematical expressions safely",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {"type": "string", "description": "Mathematical expression"}
                        },
                        "required": ["expression"]
                    }
                }
            }

            # Check if calculator already exists
            if not any(tool.get("function", {}).get("name") == "calculate_expression" for tool in tools):
                tools.append(calc_schema)
                yield "🧮 Added calculator tool"

        # Add weather tool for location queries
        if any(keyword in content for keyword in ["weather", "temperature", "forecast"]):
            weather_schema = {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather information for a location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "City name or coordinates"},
                            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"], "default": "celsius"}
                        },
                        "required": ["location"]
                    }
                }
            }

            if not any(tool.get("function", {}).get("name") == "get_weather" for tool in tools):
                tools.append(weather_schema)
                yield "🌤️ Added weather tool"

    async def filter_stream(self, user_id: str, thread_id: str, turn_correlation_id: str, chunk: Any) -> Any:
        return chunk

    async def post_call(self, user_id: str, thread_id: str, turn_correlation_id: str,
                       response_metadata: Dict[str, Any], assistant_message: Dict[str, Any]) -> None:
        pass
```

## Background Task Best Practices

### 1. Task Registration Pattern
```python
class MyFunction(FunctionPlugin):
    def __init__(self):
        self._background_tasks: Set[asyncio.Task] = set()
        self._shutdown_event = asyncio.Event()

    async def shutdown(self) -> None:
        self._shutdown_event.set()
        for task in self._background_tasks:
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

    def _create_background_task(self, coro):
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task
```

### 2. Cancellation Checks
```python
async def my_background_task(self):
    try:
        # Periodic cancellation check
        for item in long_running_process():
            if self._shutdown_event.is_set():
                logger.info("Task cancelled by shutdown")
                return

            # Process item
            await process_item(item)

    except asyncio.CancelledError:
        logger.info("Task cancelled")
        raise
```

### 3. Resource Cleanup
```python
async def cleanup_task(self):
    try:
        # Acquire resources
        async with some_resource():
            # Do work
            await do_work()

    except asyncio.CancelledError:
        # Cleanup happens automatically via context manager
        logger.info("Task cancelled, resources cleaned up")
        raise
```

## Installation and Configuration

1. Create function plugin files in `plugins/functions/`
2. Implement the `FunctionPlugin` protocol
3. Set appropriate priority values
4. Handle shutdown properly if using background tasks

## Testing Function Plugins

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from plugins.functions.my_function import MyFunction

@pytest.mark.asyncio
async def test_my_function():
    plugin = MyFunction()

    # Test pre_call
    model = ["gpt-3.5-turbo"]
    system_prompt = ["default"]
    tools = []

    await plugin.pre_call(
        user_id="test_user",
        thread_id="test_thread",
        turn_correlation_id="test_turn",
        new_message={"content": "test message"},
        model=model,
        system_prompt=system_prompt,
        tools=tools
    )

    assert model[0] == "expected_model"
    assert len(tools) > 0

@pytest.mark.asyncio
async def test_background_task_cancellation():
    plugin = MyFunction()

    # Start a background task
    plugin._create_background_task(plugin._long_running_task())

    # Test shutdown cancels tasks
    await plugin.shutdown()

    assert len(plugin._background_tasks) == 0
```

## Security Considerations

1. **Validate Inputs**: Always validate mutable parameters before modification
2. **Resource Limits**: Implement timeouts and resource limits for background tasks
3. **Permission Checks**: Verify user permissions before accessing resources
4. **Error Handling**: Never expose sensitive information in error messages
5. **Task Isolation**: Use separate event loops or processes for untrusted tasks

## Performance Considerations

1. **Async/Await**: Always use async patterns for I/O operations
2. **Batch Operations**: Group multiple small operations into batches
3. **Caching**: Cache expensive computations and API calls
4. **Rate Limiting**: Implement rate limiting for external API calls
5. **Memory Management**: Monitor memory usage in long-running tasks

Function plugins provide a powerful and flexible way to extend Skeleton's capabilities while maintaining clean separation of concerns and proper resource management.
