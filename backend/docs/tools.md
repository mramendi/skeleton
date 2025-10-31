# Tools Support Documentation

## Overview

Skeleton supports OpenAI-compatible function calling through a flexible tool plugin system. Tools can be either simple coroutines or streaming generators that use the **Raise-to-Return (R2R) pattern**.

Two types of tools are supported. Class-based tools provide their own OpenAI JSON Schema and get a list of parameter values. Function-based calls are plain Python functions or class methods; the schema is generated automatically (using `llmio`). OpenWebUI compatible tools that do not use OWUI internals (such as event emitters) should run.

The full plugin system of Skeleton, including a full storage plugin (SQLite by default), is available to tools, as the `plugin_manager` module is injected into every loaded tool.

### Correlation Parameters

Every tool call receives the following parameters to correlate their use
and ensure proper context:

- `user_id` - The user making the request (for multi-tenancy and permissions)
- `thread_id` - The internal ID of the current thread (for conversation context)
- `turn_correlation_id` - The ID unique to a "turn". A turn is everything that happens
between the user sending a message and control returning to the user, so if tool calls
are involved, a turn is several LLM calls.

These parameters are essential for:
- **Multi-tenancy**: Ensuring data isolation between users
- **Context tracking**: Correlating tool calls with specific conversations
- **Debugging**: Tracing the flow of a single user request through multiple tool calls
- **Analytics**: Tracking usage patterns per user, thread, or conversation turn
- **Background operations**: Correlating async background tasks with their originating request

A function-based tool can optionally take any or all of these parameters. The system will automatically provide them if the tool's signature includes them.

## Tool Types

### 1. Class-based Tools

Classes implementing the `ToolPlugin` protocol with full control over schema and execution.

```python
from backend.core.protocols import ToolPlugin
from typing import Dict, Any

class WeatherToolPlugin:
    def get_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "City name"},
                        "units": {"type": "string", "enum": ["celsius", "fahrenheit"]}
                    },
                    "required": ["location"]
                }
            }
        }

    async def execute(self, user_id: str, thread_id: str, turn_correlation_id: str, arguments: Dict[str, Any]) -> Any:
        location = arguments.get("location")
        units = arguments.get("units", "celsius")

        # Use correlation parameters for logging and context
        logger.info(f"Tool {self.__class__.__name__} called for user {user_id}, thread {thread_id}, turn {turn_correlation_id}")
        
        # Call weather API
        weather_data = await self._call_weather_api(location, units)

        # Store request in user's history for analytics
        store = plugin_manager.get_plugin("store")
        await store.create_store_if_not_exists(
            "weather_requests",
            {
                "user_id": "text",
                "thread_id": "text", 
                "turn_correlation_id": "text",
                "location": "text",
                "units": "text",
                "timestamp": "text"
            }
        )
        await store.add(
            user_id=user_id,
            store_name="weather_requests",
            data={
                "thread_id": thread_id,
                "turn_correlation_id": turn_correlation_id,
                "location": location,
                "units": units,
                "timestamp": datetime.now().isoformat()
            }
        )

        return {
            "location": location,
            "temperature": weather_data["temp"],
            "conditions": weather_data["conditions"],
            "units": units
        }
```

### 2. Function-based Tools

Plain Python functions with type hints. The system auto-generates schemas and handles validation.

The doctring fot the function must contain the AI-readable tool description that goes into the schema.

Note: the functions can also be class methods. In this way, typical OpenWebUI compatible
tools can work **if** they don't use OWUI specifics, such as the OWUI event emitters.
(In Skeleton, yielding then raising-to-return is used for updates to the user, instead
of an event emitter).

#### Option A: Simple Coroutine Tools

Use `async def` with `return` for quick operations without progress updates.


```python
from typing import Dict, Any

async def calculate_expression(expression: str) -> Dict[str, Any]:
    """
    Evaluate a mathematical expression safely.

    Args:
        expression: Mathematical expression to evaluate (e.g., "2 + 3 * 4")

    Returns:
        Dictionary with the result and any error information
    """
    import ast
    import operator

    try:
        node = ast.parse(expression, mode='eval')
        operators = {
            ast.Add: operator.add, ast.Sub: operator.sub,
            ast.Mult: operator.mul, ast.Div: operator.truediv,
        }

        def eval_node(node):
            if isinstance(node, ast.Constant):
                return node.value
            elif isinstance(node, ast.BinOp):
                left = eval_node(node.left)
                right = eval_node(node.right)
                op_type = type(node.op)
                if op_type in operators:
                    return operators[op_type](left, right)
                raise ValueError(f"Operator {op_type} not allowed")
            else:
                raise ValueError(f"Expression type {type(node)} not allowed")

        result = eval_node(node.body)
        return {"expression": expression, "result": result, "success": True}

    except Exception as e:
        return {"expression": expression, "error": str(e), "success": False}
```

#### Option B: Streaming Generator Tools (R2R Pattern)

Use `yield` for progress updates and `raise StopAsyncIteration(result)` to return values.

```python
from typing import Dict, Any
import asyncio

async def get_weather(location: str, unit: str = "celsius") -> Dict[str, Any]:
    """
    Gets the weather, yielding progress updates.

    Args:
        location: City name or coordinates
        unit: Temperature unit (celsius or fahrenheit)

    Returns:
        Dictionary with weather information

    NOTE: Uses the Raise-to-Return pattern. See backend/docs/raise-to-return-generator.md
    """

    # 1. Yield progress updates
    yield f"🔍 Looking up weather for {location}..."
    await asyncio.sleep(0.5)

    yield "📡 Contacting weather service..."
    await asyncio.sleep(0.3)

    # 2. Prepare final result
    final_result = {
        "location": location,
        "temperature": 22,
        "unit": unit,
        "conditions": "sunny"
    }

    # 3. Use 'raise StopAsyncIteration' to return the result
    raise StopAsyncIteration(final_result)
```

**R2R Pattern Rules:**
- Use `yield` to stream progress updates
- Use `raise StopAsyncIteration(result)` to return final value
- **NEVER** use `return <value>` in an async generator (causes SyntaxError)
- The system's `GeneratorWrapper` handles extracting the return value

## Tool Installation

Place tool files in `plugins/tools/` directory:

```
plugins/tools/
├── weather.py          # Class-based tool
├── calculator.py       # Function-based coroutine
└── streaming_weather.py # Function-based generator (R2R)
```

If you use any tools provided in plugin_library/ you can symlink them into plugins/

**Loading Rules:**
1. System scans all `.py` files in `plugins/tools/`
2. Classes with `get_schema()` and `execute()` → class-based tools
3. Functions with type hints → function-based tools
4. Tool names must be unique; duplicates are skipped

## Tool Execution Flow

1. **Schema Registration**: Tool manager collects schemas from all loaded tools
2. **Model Integration**: Schemas passed to model during response generation
3. **Tool Call Detection**: Model returns tool calls in streaming response
4. **Tool Execution**: Backend executes tool with validated arguments
5. **User Feedback**: Progress updates and results streamed to user
6. **Context Storage**: Results added to conversation history

**User sees:**
- Coroutine tools: `✅ tool_name: result`
- R2R tools: Progress updates + `✅ tool_name: result`

## Error Handling

The system automatically handles:
- Tool execution exceptions
- Input validation (Pydantic for function-based tools)
- Binary data detection and conversion
- JSON serialization with fallback

## Best Practices

### 1. Choose the Right Tool Type

**Use coroutine tools when:**
- Quick execution (< 1 second)
- No meaningful progress updates
- Simple input/output

**Use R2R generator tools when:**
- Longer execution time
- Multiple steps or stages
- User benefits from progress feedback
- External API calls or file operations

### 2. Input Validation

```python
# Function-based: Use type hints
async def safe_divide(a: float, b: float) -> Dict[str, Any]:
    if b == 0:
        return {"error": "Division by zero", "success": False}
    return {"result": a / b, "success": True}

# Class-based: Manual validation
async def execute(self, arguments: Dict[str, Any]) -> Any:
    a = arguments.get("a")
    b = arguments.get("b")

    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        raise ValueError("Both arguments must be numbers")

    if b == 0:
        raise ValueError("Division by zero")

    return a / b
```

### 3. Progress Updates in R2R Tools

```python
async def process_file(filepath: str) -> Dict[str, Any]:
    yield f"📂 Opening file: {filepath}"

    try:
        with open(filepath, 'r') as f:
            lines = f.readlines()

        yield f"📊 Found {len(lines)} lines to process"

        processed = []
        for i, line in enumerate(lines):
            processed.append(line.strip().upper())

            # Yield progress every 100 lines
            if i % 100 == 0:
                yield f"⚙️ Processed {i}/{len(lines)} lines..."

        yield "✅ File processing complete"

        final_result = {
            "filepath": filepath,
            "total_lines": len(lines),
            "processed_lines": len(processed)
        }

        raise StopAsyncIteration(final_result)

    except Exception as e:
        error_result = {"error": f"Failed to process {filepath}: {str(e)}"}
        raise StopAsyncIteration(error_result)
```

### 4. Async Operations

```python
async def fetch_url(url: str) -> Dict[str, Any]:
    import aiohttp

    yield f"🌐 Fetching: {url}"

    try:
        async with aiohttp.ClientSession() as session:
            yield "📡 Connecting to server..."

            async with session.get(url) as response:
                yield f"📥 Receiving data (status: {response.status})..."

                content = await response.text()

                result = {
                    "url": url,
                    "status": response.status,
                    "content_length": len(content)
                }

                raise StopAsyncIteration(result)

    except Exception as e:
        error_result = {"error": f"Failed to fetch {url}: {str(e)}"}
        raise StopAsyncIteration(error_result)
```

## Testing Tools

### Testing Coroutine Tools

```python
import pytest
from plugins.tools.calculator import calculate_expression

@pytest.mark.asyncio
async def test_calculator():
    result = await calculate_expression("2 + 3 * 4")
    assert result["success"] is True
    assert result["result"] == 14
```

### Testing R2R Generator Tools

```python
import pytest
from plugins.tools.streaming_weather import get_weather
from generator_wrapper import GeneratorWrapper

@pytest.mark.asyncio
async def test_streaming_weather():
    wrapped = GeneratorWrapper(get_weather("New York"))

    # Collect progress updates
    progress = []
    async for update in wrapped.yields():
        progress.append(update)

    # Get final result
    result = await wrapped.returns()

    assert result["location"] == "New York"
    assert "temperature" in result
```

## Security Considerations

1. **Input Validation**: Always validate and sanitize user inputs
2. **Resource Limits**: Implement timeouts for external operations
3. **Permission Checks**: Verify tool execution is allowed for the user
4. **Error Information**: Don't expose sensitive system information
5. **File Access**: Restrict file system access to safe directories
6. **Network Calls**: Use allowlists for external API endpoints

## Tool Composition

Tools can call other tools through the plugin manager:

```python
from backend.core.plugin_manager import plugin_manager

async def complex_analysis(data: str) -> Dict[str, Any]:
    yield "🔍 Starting analysis..."

    # Call calculator tool (coroutine)
    calc_result = await plugin_manager.tool.execute_tool(
        "calculate_expression",
        {"expression": f"len('{data}') * 2"}
    )

    yield f"📊 Calculated: {calc_result.get('result')}"

    # Call weather tool (R2R generator)
    if "location" in data.lower():
        wrapped = plugin_manager.tool.execute_tool(
            "get_weather",
            {"location": extract_location(data)}
        )

        # Stream weather tool's progress
        async for update in wrapped.yields():
            yield f"Weather: {update}"

        weather_result = await wrapped.returns()

    final_result = {
        "analysis": data,
        "calculated_value": calc_result.get("result"),
        "weather": weather_result if "weather_result" in locals() else None
    }

    raise StopAsyncIteration(final_result)
```

## Using the Store Plugin in Tools

Tools can access Skeleton's built-in storage system through the store plugin, enabling persistent data storage, retrieval, and search capabilities. This is powerful for tools that need to maintain state, cache results, or store user-specific data.

### Store Plugin Access

The store plugin is available through the injected `plugin_manager` module:

```python
from backend.core.plugin_manager import plugin_manager

async def my_tool_with_storage(user_id: str, data: str) -> Dict[str, Any]:
    """Example tool using the store plugin for data persistence"""

    # Get the store plugin
    store = plugin_manager.get_plugin("store")

    # Create a store if it doesn't exist
    await store.create_store_if_not_exists(
        "my_tool_data",
        {
            "user_id": "text",
            "data": "text",
            "timestamp": "text",
            "tags": "json_collection"  # Append-only collection for tags
        }
    )

    # Add a record
    record_id = await store.add(
        user_id=user_id,
        store_name="my_tool_data",
        data={
            "user_id": user_id,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
    )

    # Add tags to the collection
    await store.collection_append(
        user_id=user_id,
        store_name="my_tool_data",
        record_id=record_id,
        field_name="tags",
        item="processed"
    )

    # Retrieve the record
    record = await store.get(
        user_id=user_id,
        store_name="my_tool_data",
        record_id=record_id,
        load_collections=True  # Load the tags collection too
    )

    return {"stored": record, "record_id": record_id}
```

### Advanced Store Operations

```python
async def advanced_storage_example(user_id: str, query: str) -> Dict[str, Any]:
    """Advanced example showing search, filtering, and pagination"""

    store = plugin_manager.get_plugin("store")

    # Full-text search across all stores
    search_results = await store.full_text_search(
        user_id=user_id,
        store_name="my_tool_data",
        query=query,
        limit=10
    )

    # Find records with filters
    filtered_results = await store.find(
        user_id=user_id,
        store_name="my_tool_data",
        filters={"tags": {"$contains": "important"}},  # JSON collection query
        limit=5,
        offset=0,
        order_by="timestamp",
        order_desc=True
    )

    # Count matching records
    total_count = await store.count(
        user_id=user_id,
        store_name="my_tool_data",
        filters={"timestamp": {"$gte": "2024-01-01"}}
    )

    # Get collection items with pagination
    record = await store.get(user_id=user_id, store_name="my_tool_data", record_id="some_id")
    if record:
        tags = await store.collection_get(
            user_id=user_id,
            store_name="my_tool_data",
            record_id="some_id",
            field_name="tags",
            limit=20,
            offset=0
        )

    return {
        "search_results": search_results,
        "filtered_results": filtered_results,
        "total_count": total_count,
        "tags": tags if record else []
    }
```

### Store Plugin Features

- **Schema Validation**: Define required fields and types for each store
- **Multi-tenancy**: All operations are automatically scoped by `user_id`
- **Full-text Search**: FTS5-powered search across all text fields
- **JSON Collections**: Append-only arrays for tags, comments, or logs
- **Pagination**: Built-in `limit` and `offset` support
- **Filtering**: Complex query filters with JSON operators
- **CRUD Operations**: Complete Create, Read, Update, Delete support

### Best Practices for Store Usage

1. **Use Descriptive Store Names**: Prefix with your tool name to avoid conflicts
2. **Define Clear Schemas**: Use appropriate field types (`text`, `integer`, `json_collection`)
3. **Handle Multi-tenancy**: Always pass `user_id` to ensure data isolation
4. **Use Collections for Append-Only Data**: Tags, logs, comments, history
5. **Implement Error Handling**: Store operations can fail (duplicate keys, validation errors)
6. **Consider Performance**: Use indexes and pagination for large datasets

This tool system provides a flexible foundation for extending Skeleton's capabilities while maintaining security and reliability.
