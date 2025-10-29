# Request/Response Flow Documentation

This document explains how a user message flows through the Skeleton system, from the HTTP endpoint through all plugin layers to the final response.

## Core Plugin Types Overview

### 1. Authentication Plugin (`auth`)
**Role**: `auth`  
**Purpose**: User authentication and authorization  
**Key Methods**:
- `authenticate_user(username, password)` - Validate credentials
- `create_token(user)` - Generate JWT token
- `verify_token(token)` - Validate and decode JWT
- `request_allowed(username, model_name)` - Check model access permissions

**Default Implementation**: `YamlFileAuthPlugin` - YAML-based user management with bcrypt passwords and JWT tokens

### 2. Model Plugin (`model`) 
**Role**: `model`  
**Purpose**: AI model interaction and response generation  
**Key Methods**:
- `get_available_models()` - List available models
- `generate_response(messages, model, system_prompt, tools)` - Stream model responses with tool support

**Default Implementation**: `DefaultModelClient` - OpenAI SDK integration with LiteLLM proxy support

### 3. Thread Manager Plugin (`thread`)
**Role**: `thread`  
**Purpose**: Chat thread management and message persistence  
**Key Methods**:
- `create_thread(title, model, system_prompt, user)` - Create new chat thread
- `get_threads(user, query)` - List user's threads
- `get_thread_messages(thread_id, user)` - Get thread message history
- `add_message(thread_id, user, role, type, content, model)` - Add message to thread
- `search_threads(query, user)` - Full-text search across threads

**Default Implementation**: `DefaultThreadManager` - SQLite-based thread storage with append-only message collections

### 4. Store Plugin (`store`)
**Role**: `store`  
**Purpose**: Generic data storage with schema validation and collections  
**Key Methods**:
- `create_store_if_not_exists(store_name, schema)` - Create data store
- `add(user_id, store_name, data, record_id)` - Insert record
- `get(user_id, store_name, record_id)` - Retrieve record
- `find(user_id, store_name, filters, limit, offset)` - Query records
- `collection_append(user_id, store_name, record_id, field_name, item)` - Add to append-only collection

**Default Implementation**: `SQLiteStorePlugin` - SQLite backend with FTS5 search and json_collection support

### 5. Context Plugin (`context`)
**Role**: `context`  
**Purpose**: Mutable conversation context management for model interactions  
**Key Methods**:
- `get_context(thread_id, user_id, strip_extra)` - Retrieve cached conversation context
- `add_message(thread_id, user_id, message, message_id)` - Add message to context
- `update_message(thread_id, user_id, message_id, updates)` - Update specific message
- `remove_messages(thread_id, user_id, message_ids)` - Remove messages by ID
- `regenerate_context(thread_id, user_id)` - Regenerate context from history
- `invalidate_context(thread_id, user_id)` - Clear cached context

**Default Implementation**: `DefaultContextManager` - SQLite-based context caching with message ID tracking

### 6. System Prompt Plugin (`system_prompt`)
**Role**: `system_prompt`  
**Purpose**: System prompt management and resolution  
**Key Methods**:
- `get_prompt(key)` - Get system prompt content by key
- `list_prompts()` - List available prompt keys and descriptions
- `get_all_prompts()` - Get all prompts with full metadata

**Default Implementation**: `YamlSystemPromptManager` - YAML-based prompt storage with validation

### 7. Message Processor Plugin (`message_processor`)
**Role**: `message_processor`  
**Purpose**: Orchestrates the complete message processing flow  
**Key Methods**:
- `process_message(user_id, content, thread_id, model, system_prompt)` - Process message and stream response

**Default Implementation**: `DefaultMessageProcessor` - Complete flow orchestration with tool support

### 8. Tool Plugin Manager (`tool`)
**Role**: `tool` (managed by `ToolPluginManager`)  
**Purpose**: Tool discovery, schema collection, and execution  
**Key Methods**:
- `execute_tool(tool_name, arguments)` - Execute specific tool
- `get_tool_schemas()` - Get all tool schemas for OpenAI function calling

**Implementation**: `ToolPluginManager` - Manages both class-based and function-based tools

### 9. Function Plugin Manager (`function`)
**Role**: `function` (managed by `FunctionPluginManager`)  
**Purpose**: Request context modification and preprocessing  
**Key Methods**:
- `execute_functions(context)` - Execute all function plugins in priority order

**Implementation**: `FunctionPluginManager` - Executes plugins that modify request context

## Request/Response Flow: User Message

### 1. HTTP Request Arrival
**Endpoint**: `POST /api/v1/message`  
**File**: `main.py` lines 400-500

```python
@app.post("/api/v1/message")
async def send_message(content: str = Form(...), thread_id: Optional[str] = Form(None),
                      model: Optional[str] = Form(None), system_prompt: Optional[str] = Form(None),
                      current_user: str = Depends(get_current_user)):
```

**Authentication Check**: `get_current_user()` dependency validates JWT token via `AuthPlugin.verify_token()`

### 2. Message Processor Delegation
**File**: `main.py` lines 410-450

```python
async def event_generator():
    try:
        # Get the message processor plugin
        processor = plugin_manager.get_plugin("message_processor")
        
        # Delegate all message processing to the plugin
        async for event in processor.process_message(
            user_id=current_user,
            content=content,
            thread_id=thread_id,
            model=model,
            system_prompt=system_prompt
        ):
            # Stream events from plugin to client
            yield f"data: {json.dumps(event)}\n\n"
```

**All subsequent processing is handled by the `MessageProcessorPlugin`**

### 7. Response Storage with Thinking and Tools
**File**: `main.py` lines 610-630  
**Plugin**: `ThreadManagerPlugin`

```python
# Store complete assistant response
complete_response = "".join(accumulated_response)
if complete_response:
    await plugin_manager.get_plugin("thread").add_message(
        thread_id, current_user, "assistant", "message_text", 
        complete_response, model=model
    )

# Store thinking content if present
if thinking_content:
    await plugin_manager.get_plugin("thread").add_message(
        thread_id, current_user, "thinking", "message_text", 
        thinking_content, model=model
    )

# Store tool results if present
for tool_result in tool_results:
    await plugin_manager.get_plugin("thread").add_message(
        thread_id, current_user, "tool", "message_text", 
        tool_result["content"], model=model
    )
```

### 8. Tool Execution Loop (if tools are called)
**File**: `main.py` lines 650-700

```python
# Execute tools and add results to context
for tool_call in tool_calls:
    result = await plugin_manager.tool.execute_tool(
        tool_call["function"]["name"], 
        tool_call["function"]["arguments"]
    )
    
    # Add tool result to context for next model round
    await context_plugin.add_message(thread_id, user_id, {
        "role": "tool",
        "tool_call_id": tool_call["id"],
        "content": result
    })
```

### 9. SSE Stream Termination
**File**: `main.py` lines 700-710

```python
# Send end of stream
yield f"data: {json.dumps({'event': 'stream_end', 'data': {'model': model}})}\n\n"
```

## Data Flow Through Plugin Layers

```
HTTP Request
    ↓
Auth Plugin (verify token, check permissions)
    ↓
Message Processor Plugin (orchestrates entire flow)
    ↓
┌─ Thread Manager Plugin (create/verify thread, store user message)
│  └─ Store Plugin (underlying storage operations)
↓
Context Plugin (get/regenerate conversation context)
    ↓
System Prompt Plugin (resolve system prompt key to content)
    ↓
Model Plugin (generate streaming response with thinking/tools)
    ↓
[Tool Loop - if tools called]
    ↓
├─ Tool Plugin Manager (execute tools, return results)
│  ├─ Class-based Tools (manual schema, custom execution)
│  └─ Function-based Tools (auto-schema, Pydantic validation)
↓
Context Plugin (add tool results to context)
    ↓
Model Plugin (continue response with tool results)
    ↓
[/Tool Loop]
    ↓
┌─ Thread Manager Plugin (store thinking, response, and tool results)
│  └─ Store Plugin (underlying storage operations)
↓
SSE Stream to Client
```

## Key Design Principles

1. **Backend-Heavy**: All business logic on backend, frontend is stateless
2. **Plugin-Based**: Each layer can be replaced by implementing the protocol
3. **Multi-Tenancy**: All data operations scoped by user_id
4. **Append-Only Collections**: Messages stored in O(1) append-only collections
5. **Streaming**: Real-time responses via Server-Sent Events
6. **Type Safety**: Protocol-based design with mypy validation

## Error Handling Flow

1. **Authentication Errors**: Return 401/403 immediately
2. **Validation Errors**: Return 400 with specific error message
3. **Plugin Errors**: Log error, continue with fallback behavior
4. **Model Errors**: Stream error event to client
5. **Database Errors**: Retry with exponential backoff, then fail gracefully

## Performance Optimizations

1. **Lazy Plugin Loading**: Plugins initialized only when needed
2. **Connection Pooling**: Single read/write connections for SQLite
3. **Collection Append**: O(1) operations for message storage
4. **FTS5 Indexing**: Full-text search with automatic indexing
5. **Streaming**: No buffering of large responses

## Extension Points

- **Function Plugins**: Add logging, rate limiting, content filtering, request preprocessing
- **Tool Plugins**: Add external API integrations (weather, files, etc.)
  - Class-based Tools: Full control over schema and execution
  - Function-based Tools: Auto-generated schemas with type hints
- **Model Plugins**: Support different AI providers (Anthropic, local models)
- **Store Plugins**: Use different databases (PostgreSQL, Redis, etc.)
- **Auth Plugins**: Implement OAuth, LDAP, multi-factor auth
- **System Prompt Plugins**: Use different prompt storage systems
- **Message Processor Plugins**: Completely customize message processing flow
- **Context Plugins**: Use different context caching strategies

## Tool Support Architecture

### Tool Types

1. **Class-based Tools**: Classes implementing `ToolPlugin` protocol
   - Manual schema definition via `get_schema()` method
   - Full control over execution logic
   - Suitable for complex tools with custom validation
   - Example: `WeatherToolPlugin` with custom API integration

2. **Function-based Tools**: Plain Python functions with type hints
   - Auto-generated schema using `llmio.function_parser`
   - Automatic argument validation with Pydantic
   - Simpler implementation for basic tools
   - Example: `calculate_expression(expression: str) -> Dict[str, Any]`

### Tool Loading Process

1. **File Discovery**: Scan `plugins/tools/` directory for `.py` files
2. **Class Detection**: Classes with `get_schema()` and `execute()` methods
3. **Function Detection**: Functions with type hints (if no class-based tools found)
4. **Deduplication**: Tool names must be unique; duplicates skipped with warnings
5. **Schema Collection**: Collect OpenAI function schemas from all loaded tools

### Tool Execution Flow

1. **Schema Registration**: Tool manager collects schemas from all loaded tools
2. **Model Integration**: Schemas passed to model plugin during response generation
3. **Tool Call Detection**: Model returns tool calls in streaming response
4. **Tool Execution**: Tool manager executes called tools with validated arguments
5. **Result Integration**: Tool results added to context for continued conversation
6. **Loop Continuation**: Model generates follow-up response with tool results
7. **Multi-round Support**: Tool calls can continue across multiple response rounds

### Tool Message Types

- `tool_calls`: Model requests tool execution (streamed from model)
- `tool_update`: Backend streams tool execution status to user
- `tool`: Tool results stored in conversation history (OpenAI format)

### Tool Error Handling

- **Binary Data Detection**: Automatically detected and converted to error messages
- **JSON Serialization**: Handled automatically with fallback for non-serializable data
- **Execution Errors**: Streamed to user and stored in history
- **Validation**: Function-based tools use Pydantic validation, class-based tools use custom validation

This architecture ensures that the core remains minimal while allowing unlimited extensibility through the plugin system.
