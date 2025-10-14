# Skeleton – Manifesto v1

## Why exist? – A Different Philosophy
The landscape of AI web UIs is rich with powerful, feature-filled applications. As these tools mature, they often evolve into complex, opinionated ecosystems. An attempt at customization or even bug-fixing by someone outside a core development team can mean untangling a monolithic codebase. Extending functionality often requires developers to work within the project's specific, and sometimes opaque, internal structure.

Skeleton is born from a different philosophy. Instead of building another all-in-one solution, we wanted to create a minimal, stable, and transparent core. The goal is to provide a simple, scriptable component that connects a user to a model, giving the developer full control to build upon it, replace parts of it, or integrate it into a larger workflow.

Many projects become a **cathedral** you must **join**; we wanted to build a **pipe** you can **script**. This project must **never** bloat, remaining lean and easy to understand, take apart, and rearrange by anyone with basic domain skills, so that nothing depends on the goodwill (or bandwidth) of the original developer.

"Skeleton" is a declaration of intent: a minimalistic core, able to support any extensions necessary. To that end, the entire `plugins/` directory is git-ignored, allowing you to run your entire custom configuration as a separate, independent repository, while keeping easy access to main-branch updates of the core. The plugins can override most modules of the core while remaining compatible with the rest of the code. Plugins can also include functions (with a num,ber of diverse hooks available) and tools (a Python to FastAPI translation layer is to be provided, probably by using `llmio`).

One key design decisions is that all sources of truth are _on the backend_. Skeleton is "backend-heavy, frontend-light".

----------------------------------------------------
## Backend Philosophy & Security
----------------------------------------------------
The back-end is a Python/FastAPI application built on a **formal calling contract** (`typing.Protocol`).

Every module in the backend, except the FastAPI REST implementation itself (which is intentionally minimal), has a defined calling contract. This design enables any developer to replace one module by implementing the same contract; the new code can call other existing modules by following their contracts too, and so every single component becomes "pluggable". Whenever possible, "hot-plugging" overrides from the `plugins` directory is supported.

The defined calling contract is also intended to avoid bloat; every module has _one_ function, expansion of functionality is done by new modules whenever possible while keeping every source file simple, and every module is expected to be well-documented too.

This design creates a natural two-level user hierarchy:
* **Admin/Owner:** Has shell (e.g., SSH) access to the server. Can manage configuration (`.env` files, secrets) and install/modify plugins. This user has total control of the system.
* **Web UI User:** Authenticates through the `/login` endpoint. Can use the chat application but cannot modify its underlying code or configuration.

----------------------------------------------------
## Frontend and interaction philosophy & security
----------------------------------------------------

The front-end is a minimal environment that stays light and depends on the backend for all sources of truth. For example, the authoritative chat history is on the backend, so even if the frontend is open in several windows or on several devices for the same environment/user, every open instance updates correctly from the backend.

No pop-up windows and no configuration panes are included. The front-end main UI is always the same, consisting of a chat window and new entry line, char list and a search pane, selectors of the model and system prompt, and a few other buttons such as file upload. For absence of a separate status display, plugins are able to emit "meta" messages that are included in the chat flow.

(The current version of the front-end is implemented in Alpine.js)

Plugin pages (`/_plugin/<name>/`) can provide additional configuration or other UI enhancements. They are separate paghes that can be opened on selarate tabs and do not block the main UI.

Plugin pages can be secured by requiring a valid user login. The core will provide a reusable **FastAPI dependency** that plugins can use to protect their routes. Chathack operates on a **trusted code** model; a "Big Red Warning" in the documentation will make it clear that installing a plugin is an act of trust by the Admin.

----------------------------------------------------
## Core Principles & Promise
----------------------------------------------------
- **One weekend → skeleton runs.**
- **Every line commented like you’ll read it at 3 AM.**
- **Every interface contract-first and replaceable with a single file.**
- **Graceful Evolution:** Core contracts will evolve without breaking existing plugins.
- **No cathedral, no branding, no pop-ups—just pipes you can script.**

----------------------------------------------------
## API Contract (v1)
----------------------------------------------------
The software version is independent of the API version. The API contract is prefixed with `/api/v1` to signal a commitment to stability. UI-specific HTML endpoints are not versioned.

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves the `index.html` UI. |
| `POST` | `/login` | Accepts credentials, sets a time-limited auth cookie on success. |
| `POST` | `/logout` | Clears the auth cookie. |
| `GET` | `/api/v1/models` | `["gpt-4","qwen-72b", …]` |
| `GET` | `/api/v1/system_prompts` | `["default", "code-assistant", …]` |
| `GET` | `/api/v1/threads` | Returns list of all non-archived threads: `[{id, title, created, model, system_prompt}]`. |
| `GET` | `/api/v1/threads/{id}/messages`| Returns the full, flat message history for a thread. |
| `POST`| `/api/v1/threads/{id}`| Updates thread metadata (e.g., `{"title": "..."}`). |
| `DELETE`| `/api/v1/threads/{id}`| Archives a thread by setting an `is_archived` flag. |
| `GET` | `/api/v1/search?q=…` | Full-text search over messages. Returns `[{id, title, snippet}]`. |
| `POST` | `/api/v1/message` | `{content, thread_id, model, system_prompt}` → SSE stream. |
| `POST` | `/api/v1/files` | Uploads a file via `multipart/form-data`. Returns `{"url": "..."}`. |

----------------------------------------------------
## API Error Handling
----------------------------------------------------
- **JSON API Errors:** Failed requests (`4xx` or `5xx`) will return a standard JSON body: `{"error": "A human-readable error message"}`.
- **SSE Stream Errors:** If an error occurs mid-stream, the final event emitted will be `{"event": "error", "data": {"message": "Error description."}}` before the connection is closed.

----------------------------------------------------
## SSE Stream Format
----------------------------------------------------
The SSE stream's data payload is a structured event object. The back-end is responsible for parsing special model outputs (e.g., `<thinking>` tags), and the front-end simply renders the events it receives.

**Event Types:**
* **`message_tokens`**: A standard text chunk for the final assistant message.
* **`thought_tokens`**: A text chunk from a "chain-of-thought" step.
* **`tool_update`**: A discrete status message about an executing tool.
* **`file`**: A download link for a model-generated file (e.g., an image).
* **`error`**: A message describing a fatal error during generation.
* **`stream_end`**: A structured end-of-stream message.

----------------------------------------------------
## Message History Format
----------------------------------------------------
The message history for a thread is a flat array of "chunk" objects. This simple, log-like structure is easy to render sequentially.

**Chunk Structure:**
```json
{
  "role": "assistant" | "user" | "meta",
  "type": "message_tokens" | "thought_tokens" | "tool_update" | "file" | "message_text",
  "content": "The text or data for the chunk.",
  "timestamp": "ISO8601 string",
  "model": "model-name-string"
}
```
The `model` field is only present for chunks with `role: "assistant"`. The front-end is responsible for grouping adjacent chunks for display (e.g., rendering all chunks between user messages as a single "turn").


----------------------------------------------------
## Plugin Layers (all optional, hot-swappable)
----------------------------------------------------
*(All plugin files are loaded from their respective subdirectories inside the `plugins/` folder.)*

| layer | fallback file | plugin override | purpose |
|-------|---------------|-----------------|---------|
| **core** | `core/default_*.py` | `plugins/core/<name>.py` | context builder, model client, auth, tool loader, stream hooks |
| **functions** | – | `plugins/functions/*.py` | priority-ordered callbacks with access to a modifiable context. |
| **tools** | – | `plugins/tools/*.py` | OpenAI JSON schemata harvested by the API. |
| **html** | `core/static/index.html` | `plugins/html/index.html` | drop-in replacement UI. |
