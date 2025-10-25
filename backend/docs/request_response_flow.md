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
- `generate_response(messages, model, system_prompt)` - Stream model responses

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

### 2. Request Validation & Context Building
**File**: `main.py` lines 410-450

```python
# Input validation
if not content or len(content.strip()) == 0:
    raise HTTPException(status_code=400, detail="Message content cannot be empty")

# Build context for function plugins
context = {
    "user_message": content,
    "thread_id": thread_id,
    "model": model or thread_model or "gpt-3.5-turbo",
    "system_prompt": system_prompt or "default", 
    "user": current_user,
    "history": history
}
```

### 3. Function Plugin Execution
**File**: `main.py` lines 460-470  
**Plugin Manager**: `plugin_manager.function.execute_functions()`

```python
# Execute function plugins in priority order (highest first)
context = await plugin_manager.function.execute_functions(context)
```

**Function plugins can**:
- Add user context/preferences
- Log requests
- Filter content
- Modify messages
- Add metadata
- Implement rate limiting

**Execution Order**: Priority 100 → 90 → 80 → ... → 0

### 4. Thread Management
**File**: `main.py` lines 480-520  
**Plugin**: `ThreadManagerPlugin`

```python
# Create new thread or verify existing thread access
if thread_id:
    # Verify user has access to this thread
    existing_messages = await plugin_manager.get_plugin("thread").get_thread_messages(thread_id, current_user)
    if existing_messages is None:
        raise HTTPException(status_code=404, detail="Thread not found")
else:
    # Create new thread
    thread_id = await plugin_manager.get_plugin("thread").create_thread(
        title=content[:50] + "..." if len(content) > 50 else content,
        model=model or "gpt-3.5-turbo",
        system_prompt=system_prompt or "default", 
        user=current_user
    )
```

### 5. Message Storage
**File**: `main.py` lines 530-540  
**Plugin**: `ThreadManagerPlugin`

```python
# Add user message to thread (append-only collection)
success = await plugin_manager.get_plugin("thread").add_message(
    thread_id, current_user, "user", "message_text", content
)
```

**Behind the scenes**: Uses `StorePlugin.collection_append()` for O(1) append operation

### 6. Model Response Generation
**File**: `main.py` lines 550-600  
**Plugin**: `ModelPlugin`

```python
# Stream response from model
async for chunk in plugin_manager.get_plugin("model").generate_response(
    messages=history,
    model=model or thread_model or "gpt-3.5-turbo",
    system_prompt=system_prompt or "default"
):
    # Handle different chunk types
    if chunk.get("event") == "message_tokens":
        yield f"data: {json.dumps(chunk)}\n\n"
    elif chunk.get("event") == "tool_call":
        # Handle tool execution (future feature)
        tool_result = await plugin_manager.tool.execute_tool(...)
```

### 7. Response Storage
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
```

### 8. SSE Stream Termination
**File**: `main.py` lines 640-650

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
Function Plugins (modify context, add metadata)
    ↓
Thread Manager Plugin (create/verify thread, store user message)
    ↓
Model Plugin (generate streaming response)
    ↓
Thread Manager Plugin (store assistant response)
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

- **Function Plugins**: Add logging, rate limiting, content filtering
- **Tool Plugins**: Add external API integrations (weather, files, etc.)
- **Model Plugins**: Support different AI providers (Anthropic, local models)
- **Store Plugins**: Use different databases (PostgreSQL, Redis, etc.)
- **Auth Plugins**: Implement OAuth, LDAP, multi-factor auth

This architecture ensures that the core remains minimal while allowing unlimited extensibility through the plugin system.
