# Skeleton REST API Documentation

## Overview

This document provides complete documentation for the Skeleton REST API (v1). The API follows a backend-heavy architecture where all state and business logic reside on the server.

## Base URL

All API endpoints are prefixed with `/api/v1`. The base URL depends on your deployment:

- Local development: `http://localhost:8000`
- Production: Your configured host and port

## Authentication

All API endpoints except `/login` and `/health` require authentication via Bearer token.

### Authentication Flow

1. Obtain a JWT token by calling `POST /login` with valid credentials
2. Include the token in the `Authorization` header for all subsequent requests:
   ```
   Authorization: Bearer <your_token_here>
   ```
3. If a token is invalid or expired, the API returns `401 Unauthorized`

## Rate Limiting

Rate limits are applied per-endpoint to prevent abuse. In multi-worker deployments, limits are per-worker (approximate but still protective).

| Endpoint Pattern | Limit | Window | Notes |
|-----------------|-------|--------|-------|
| `POST /login` | 5 requests | 60 seconds | Per IP address |
| `POST /api/v1/message` | 10 requests | 10 seconds | Per user |
| Thread operations | 20 requests | 60 seconds | Per user |
| `GET /api/v1/search` | 60 requests | 10 seconds | Per user |
| `GET /api/v1/models` | 60 requests | 60 seconds | Per user |

When rate limited, the API returns `429 Too Many Requests`.

## Error Handling

### Standard JSON Errors

Failed requests (4xx or 5xx) return a FastAPI-standard JSON body:

```json
{
  "detail": "Human-readable error message"
}
```

Common HTTP status codes:
- `400 Bad Request` - Invalid input/validation error
- `401 Unauthorized` - Invalid or missing authentication
- `404 Not Found` - Resource not found or no access
- `429 Too Many Requests` - Rate limit exceeded
- `500 Internal Server Error` - Server-side error

### SSE Stream Errors

When streaming responses via Server-Sent Events (SSE), errors mid-stream are signaled with an error event before closing the connection:

```json
{
  "event": "error",
  "data": {
    "message": "Error description",
    "timestamp": "2025-01-15T10:30:45.123456"
  }
}
```

## Endpoints

### System Endpoints

#### GET `/`
Serves the frontend HTML interface.

**Response:** HTML file with no-cache headers

---

#### GET `/health`
Health check endpoint. Verifies database connectivity.

**Authentication:** Not required

**Response:** `200 OK`
```json
{
  "status": "ok"
}
```

**Error Response:** `500 Internal Server Error`
```json
{
  "detail": "Skeleton database not working"
}
```

---

### Authentication Endpoints

#### POST `/login`
Authenticate user and receive JWT token.

**Authentication:** Not required

**Rate Limit:** 5 requests per 60 seconds per IP

**Request Body:**
```json
{
  "username": "string (1-100 chars, required)",
  "password": "string (1-200 chars, required)"
}
```

**Success Response:** `200 OK`
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer"
}
```

**Error Response:** `401 Unauthorized`
```json
{
  "detail": "Invalid credentials"
}
```

---

#### POST `/logout`
Logout current user (informational - token invalidation is client-side).

**Authentication:** Required

**Success Response:** `200 OK`
```json
{
  "message": "Logged out successfully"
}
```

---

### Model and Configuration Endpoints

#### GET `/api/v1/models`
List available AI models, filtered by user's model mask permissions.

**Authentication:** Required

**Rate Limit:** 60 requests per 60 seconds

**Success Response:** `200 OK`
```json
[
  "gpt-4",
  "gpt-3.5-turbo",
  "claude-3-opus"
]
```

**Special Case:** If the LLM client is not working or the user has no permitted models (due to model mask):
```json
[
  "MODELS NOT AVAILABLE"
]
```

---

#### GET `/api/v1/system_prompts`
List available system prompts defined in the system prompt configuration.

**Authentication:** Required

**Success Response:** `200 OK`
```json
{
  "default": "You are a helpful AI assistant.",
  "code-assistant": "You are an expert programmer...",
  "zero": ""
}
```

**Notes:**
- Keys are system prompt identifiers
- Values are the actual prompt text
- Empty string ("") or key "zero" indicates no system prompt

---

### Thread Management Endpoints

#### GET `/api/v1/threads`
List all non-archived threads for the current user, ordered by creation time (newest first).

**Authentication:** Required

**Rate Limit:** 20 requests per 60 seconds

**Success Response:** `200 OK`
```json
[
  {
    "id": "uuid-string",
    "title": "Conversation about Python",
    "created": "2025-01-15T10:30:00.000000",
    "model": "gpt-4",
    "system_prompt": "default"
  },
  {
    "id": "uuid-string",
    "title": "Help with React",
    "created": "2025-01-14T15:20:00.000000",
    "model": "claude-3-opus",
    "system_prompt": "code-assistant"
  }
]
```

---

#### GET `/api/v1/threads/{thread_id}/messages`
Get all messages in a specific thread. User must have access to the thread.

**Authentication:** Required

**Rate Limit:** 20 requests per 60 seconds

**URL Parameters:**
- `thread_id` (string, required) - UUID of the thread

**Success Response:** `200 OK`

Returns an array of message objects. See [Message History Format](#message-history-format) below for detailed structure.

```json
[
  {
    "role": "user",
    "content": "What is Python?",
    "timestamp": "2025-01-15T10:30:00.000000",
    "type": "message_text"
  },
  {
    "role": "thinking",
    "content": "Let me think about how to explain Python...",
    "timestamp": "2025-01-15T10:30:01.000000",
    "type": "message_text"
  },
  {
    "role": "assistant",
    "content": "Python is a high-level programming language...",
    "timestamp": "2025-01-15T10:30:02.000000",
    "type": "message_text",
    "model": "gpt-4"
  }
  {
    "role": "tool",
    "content": "Web search tool called",
    "timestamp": "2025-01-15T10:31:04.000000",
    "type": "tool_update",
    "model": "gpt-4"
  }
]
```

**Error Response:** `404 Not Found`
```json
{
  "detail": "Thread not found"
}
```

---

#### POST `/api/v1/threads/{thread_id}`
Update thread metadata (currently only title).

**Authentication:** Required

**Rate Limit:** 20 requests per 60 seconds

**URL Parameters:**
- `thread_id` (string, required) - UUID of the thread

**Request Body:**
```json
{
  "title": "New thread title (1-500 chars, optional)"
}
```

**Success Response:** `200 OK`
```json
{
  "message": "Thread updated successfully"
}
```

**Error Response:** `404 Not Found`
```json
{
  "detail": "Thread not found"
}
```

---

#### DELETE `/api/v1/threads/{thread_id}`
Archive a thread (soft delete - sets `is_archived` flag).

**Authentication:** Required

**Rate Limit:** 20 requests per 60 seconds

**URL Parameters:**
- `thread_id` (string, required) - UUID of the thread

**Success Response:** `200 OK`
```json
{
  "message": "Thread archived successfully"
}
```

**Error Response:** `404 Not Found`
```json
{
  "detail": "Thread not found"
}
```

---

### Search Endpoint

#### GET `/api/v1/search`
Full-text search across thread titles and message content for the current user.

**Authentication:** Required

**Rate Limit:** 60 requests per 10 seconds

**Query Parameters:**
- `q` (string, required, max 500 chars) - Search query

**Success Response:** `200 OK`
```json
[
  {
    "id": "uuid-string",
    "title": "Python discussion",
    "created": "2025-01-15T10:30:00.000000",
    "snippet": "Title: Python discussion"
  },
  {
    "id": "uuid-string",
    "title": "React help",
    "created": "2025-01-14T15:20:00.000000",
    "snippet": "...how to use React hooks in functional components..."
  }
]
```

**Notes:**
- Results include threads with matches in either title or message content
- Snippets show context around the match (±50 chars)
- Results are ordered by relevance (FTS scoring)

---

### Message Endpoint

#### POST `/api/v1/message`
Send a message and receive a streaming response via Server-Sent Events (SSE).

**Authentication:** Required

**Rate Limit:** 10 requests per 10 seconds

**Content-Type:** `multipart/form-data`

**Form Parameters:**
- `content` (string, required, 1-100,000 chars) - Message content
- `thread_id` (string, optional) - UUID of existing thread; omit to create new thread
- `model` (string, optional) - Model name; defaults to user's first available model
- `system_prompt` (string, optional) - System prompt key; defaults to "default"

**Success Response:** `200 OK` with `text/event-stream` content type

The response is a Server-Sent Events stream. See [SSE Stream Format](#sse-stream-format) below for event details.

**Error Response:** `400 Bad Request` for validation errors

---

### File Endpoints

WIP. (**The current backend source code is not authoritative n file endpoints**, they are subject to change)

---

## SSE Stream Format

The `POST /api/v1/message` endpoint returns a Server-Sent Events (SSE) stream. Each event follows the SSE protocol format:

```
data: <JSON_EVENT_OBJECT>\n\n
```

### Event Types

All events have the structure:
```json
{
  "event": "event_type",
  "data": { /* event-specific data */ }
}
```

#### `thread_id` Event

Sent once at the beginning of the stream to communicate the thread ID (either newly created or existing).

```json
{
  "event": "thread_id",
  "data": {
    "thread_id": "uuid-string",
    "timestamp": "2025-01-15T10:30:00.123456"
  }
}
```

**Usage:** Frontend should store this ID to send subsequent messages in the same thread.

---

#### `message_tokens` Event

Streams chunks of the assistant's response text. Multiple events are sent as the model generates content.

```json
{
  "event": "message_tokens",
  "data": {
    "content": "text chunk",
    "timestamp": "2025-01-15T10:30:01.234567"
  }
}
```

**Usage:** Accumulate `content` from all `message_tokens` events to build the complete response.

**Notes:**
- Content chunks should be appended in order
- Chunks may be very small (even single characters)
- Frontend should render incrementally for real-time feel

---

#### `thinking_tokens` Event

Streams chunks of the model's reasoning/thinking process (if supported by the model, e.g., Claude with extended thinking or o1).

```json
{
  "event": "thinking_tokens",
  "data": {
    "content": "reasoning text chunk",
    "timestamp": "2025-01-15T10:30:01.345678"
  }
}
```

**Usage:** Accumulate separately from message tokens. Frontend typically displays thinking in a collapsible section.

**Notes:**
- Thinking may interleave with message tokens
- Models without extended thinking won't send these events
- Thinking content is saved to thread history but not included in model context for subsequent turns (unless the message includes tool calls, in which case it's temporarily included then purged)

---

#### `tool_update` Event

Sent during tool/function execution to provide status updates.

```json
{
  "event": "tool_update",
  "data": {
    "call_id": "uuid-string",
    "content": "Tool status message",
    "timestamp": "2025-01-15T10:30:02.456789"
  }
}
```

**Typical Tool Execution Flow:**
1. `🔧 Calling function_name({\"arg\": \"value\"})`
2. `function_name: Intermediate update...` (optional, if tool yields)
3. `✅ function_name: Result preview...`

Or on error:
1. `🔧 Calling function_name({\"arg\": \"value\"})`
2. `❌ function_name: Error message`

**Usage:**
- Group multiple `tool_update` events by `call_id`
- Display in a separate "tool execution" section
- After tool execution completes, model may generate additional `message_tokens` with its final response

**Notes:**
- Multiple tools may execute in sequence (multiple different `call_id` values)
- Tools can yield intermediate updates during execution
- After all tools complete, the model is called again with tool results

---

#### `stream_end` Event

Marks the end of the message stream. Always the last event sent.

```json
{
  "event": "stream_end",
  "data": {
    "timestamp": "2025-01-15T10:30:05.678901"
  }
}
```

**Usage:** Signal to frontend that streaming is complete; stop showing loading indicators.

---

#### `error` Event

Sent when an error occurs during message processing.

```json
{
  "event": "error",
  "data": {
    "message": "Error description",
    "timestamp": "2025-01-15T10:30:03.789012"
  }
}
```

**Usage:** Display error to user and stop processing stream.

**Notes:**
- May be sent at any point during the stream
- After an error event, the stream is closed (no `stream_end`)

---

### Complete SSE Stream Example

```
data: {"event":"thread_id","data":{"thread_id":"abc-123","timestamp":"2025-01-15T10:30:00.000000"}}

data: {"event":"message_tokens","data":{"content":"Let","timestamp":"2025-01-15T10:30:00.100000"}}

data: {"event":"message_tokens","data":{"content":" me","timestamp":"2025-01-15T10:30:00.150000"}}

data: {"event":"message_tokens","data":{"content":" help","timestamp":"2025-01-15T10:30:00.200000"}}

data: {"event":"thinking_tokens","data":{"content":"I should search for this...","timestamp":"2025-01-15T10:30:00.300000"}}

data: {"event":"tool_update","data":{"call_id":"tool-1","content":"🔧 Calling search({\"query\":\"example\"})","timestamp":"2025-01-15T10:30:01.000000"}}

data: {"event":"tool_update","data":{"call_id":"tool-1","content":"✅ search: Found 5 results","timestamp":"2025-01-15T10:30:02.000000"}}

data: {"event":"message_tokens","data":{"content":" Based on the search results...","timestamp":"2025-01-15T10:30:03.000000"}}

data: {"event":"stream_end","data":{"timestamp":"2025-01-15T10:30:05.000000"}}

```

---

## Message History Format

The `GET /api/v1/threads/{thread_id}/messages` endpoint returns a flat array of message objects. This log-like structure preserves the exact sequence of events.

### Message Object Structure

All messages have these required fields:

```typescript
{
  role: "user" | "assistant" | "thinking" | "tool",
  content: string,
  timestamp: string  // ISO8601 format
}
```

Additional optional fields depend on the message role and type:

```typescript
{
  type?: "message_text" | "tool_update",  // Message type
  model?: string,      // Only for role="assistant"
  call_id?: string     // Only for role="tool", groups related tool messages
}
```

### Message Types by Role

#### User Messages

```json
{
  "role": "user",
  "content": "User's message text",
  "timestamp": "2025-01-15T10:30:00.000000",
  "type": "message_text"
}
```

---

#### Assistant Messages

```json
{
  "role": "assistant",
  "content": "Assistant's response text",
  "timestamp": "2025-01-15T10:30:01.000000",
  "type": "message_text",
  "model": "gpt-4"
}
```

**Note:** The `model` field indicates which AI model generated this response.

---

#### Thinking Messages

```json
{
  "role": "thinking",
  "content": "Model's reasoning/thinking process",
  "timestamp": "2025-01-15T10:30:00.500000",
  "type": "message_text"
}
```

**Frontend Handling:** Thinking messages are typically grouped with adjacent assistant messages and displayed in a collapsible section.

---

#### Tool Messages

```json
{
  "role": "tool",
  "content": "🔧 Calling search({\"query\":\"example\"})",
  "timestamp": "2025-01-15T10:30:02.000000",
  "type": "tool_update",
  "call_id": "tool-abc-123"
}
```

**Grouping:** Multiple tool messages with the same `call_id` represent updates from a single tool execution. Group them together in the frontend for display.

**Typical Tool Message Sequence:**
```json
[
  {
    "role": "tool",
    "content": "🔧 Calling search({\"query\":\"example\"})",
    "call_id": "tool-1",
    "type": "tool_update",
    "timestamp": "2025-01-15T10:30:02.000000"
  },
  {
    "role": "tool",
    "content": "search: Processing query...",
    "call_id": "tool-1",
    "type": "tool_update",
    "timestamp": "2025-01-15T10:30:02.500000"
  },
  {
    "role": "tool",
    "content": "✅ search: Found 5 results",
    "call_id": "tool-1",
    "type": "tool_update",
    "timestamp": "2025-01-15T10:30:03.000000"
  }
]
```

---

### Frontend Grouping Strategy

The frontend groups consecutive messages for display:

1. **User messages** - Display as individual bubbles
2. **Assistant + Thinking** - Group consecutive `assistant` and `thinking` messages into segments within a single response bubble:
   - Display `thinking` segments in collapsible sections
   - Display `assistant` segments as regular message content
3. **Tool messages** - Group by `call_id` into tool execution blocks
4. **Message order** - Messages appear in chronological order, but grouping is logical (e.g., a tool call followed by assistant response are separate bubbles)

### Example Message History

```json
[
  {
    "role": "user",
    "content": "Search for Python tutorials",
    "timestamp": "2025-01-15T10:30:00.000000",
    "type": "message_text"
  },
  {
    "role": "thinking",
    "content": "I need to use the search tool to find Python tutorials...",
    "timestamp": "2025-01-15T10:30:01.000000",
    "type": "message_text"
  },
  {
    "role": "assistant",
    "content": "I'll search for Python tutorials for you.",
    "timestamp": "2025-01-15T10:30:02.000000",
    "type": "message_text",
    "model": "gpt-4"
  },
  {
    "role": "tool",
    "content": "🔧 Calling search({\"query\":\"Python tutorials\"})",
    "timestamp": "2025-01-15T10:30:03.000000",
    "type": "tool_update",
    "call_id": "tool-abc-123"
  },
  {
    "role": "tool",
    "content": "✅ search: Found 10 results",
    "timestamp": "2025-01-15T10:30:04.000000",
    "type": "tool_update",
    "call_id": "tool-abc-123"
  },
  {
    "role": "assistant",
    "content": "I found 10 Python tutorials. Here are the top results: ...",
    "timestamp": "2025-01-15T10:30:05.000000",
    "type": "message_text",
    "model": "gpt-4"
  }
]
```

**Frontend Display:**
- Bubble 1: User message
- Bubble 2: Assistant response with collapsible thinking section (combines thinking + first assistant message)
- Bubble 3: Tool execution block (groups both tool messages by call_id)
- Bubble 4: Final assistant response (after tool execution)

---

## Important Notes

### Model Context vs. Thread History

**Thread History** (returned by `GET /api/v1/threads/{thread_id}/messages`):
- Complete log of everything that happened
- Includes all message types: `user`, `assistant`, `thinking`, `tool`
- Used by frontend for display
- Stored permanently in database

**Model Context** (internal, used for API calls to AI model):
- Simplified version sent to the AI model
- Only includes messages in OpenAI format: `user`, `assistant`, `tool` (with proper `tool_call_id`)
- Thinking content may be temporarily included as `reasoning_content` on assistant messages during tool calls, then removed
- Managed by the context plugin

### Thread Lifecycle

1. **Creation**: Threads are created automatically when sending a message without a `thread_id`
2. **Access Control**: Users can only access their own threads
3. **Archival**: Archived threads are hidden from listings but data is retained
4. **Search**: Full-text search indexes both thread titles and message content

### Tool Execution Flow

When the model requests tool execution:

1. Model generates response with `tool_calls`
2. Backend executes each tool sequentially
3. Tool updates are streamed via `tool_update` events
4. Tool results are added to context in OpenAI format
5. Model is called again with tool results
6. This loops until model provides a final response without tool calls

### Security Considerations

- **Model Masks**: Users may have restricted access to certain models via regex patterns in user configuration
- **Rate Limiting**: Protects against brute-force attacks and abuse
- **Input Validation**: All inputs are validated using Pydantic models
- **SQL Injection**: Prevented by using parameterized queries in the SQLite store
- **XSS**: Frontend uses DOMPurify to sanitize HTML from markdown rendering

---

## Versioning

The API is versioned via the `/api/v1` prefix. Breaking changes will result in a new API version (`/api/v2`), while backward-compatible changes may be added to existing versions.

Current API version: **v1**

---

## Support

For issues and feature requests, visit: https://github.com/mramendi/skeleton/issues
