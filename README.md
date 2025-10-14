# Skeleton - Minimal Modular AI Chat Interface

A minimal, backend-heavy AI chat interface built to be fully modular. Designed to be extended through plugins while keeping the core simple and stable. Not an everything-hub, not an omni-environment, but the skeleton implementation of the basics.  ou can take this skeleton out of the closet and hand any parts you want on it - or that is the intention when we reach v1.

## IMPORTANT - WORK IN PROGRESS

Current status:

- Single OpenAI endpoint only (recommended way: use LiteLLM Proxy to access any number of endpoints)
- Model selection chat runs in first tests
- System prompts and temperature temporarily hardcoded
- API still not stable
- Test suite exists but not human-reviewed
- Front-end not human-reviewed, back-end IS human-reviewed
- No tools nor functions yet, though some stubs exist
- NO HISTORY PERSISTENCE (threads in memory)
- HARDCODED USER/PASSWORD (admin/admin123)

## DOCUMENTATION

- This file - introduction, usage instructions, extension instructions
- [Project manifesto](skeleton-manifesto-v1.md)
- [Test suite documentation](backend/tests/README.md)


## Features

- **Minimal Core**: Simple, well-documented codebase that's easy to understand and modify
- **Plugin Architecture**: Extensible through plugins without modifying core files. *You can replace any part of the core by implementing its protocol in your code* ; all protocols are in `backend/protocols.py`
- **Backend-Heavy**: All business logic and state management on the backend
- **Real-time Chat**: Server-sent events for streaming responses
- **Thread Management**: Persistent (NOT YET) chat threads with search functionality
- **File Upload**: Support for file attachments (NOT YET)
- **Authentication**: JWT-based authentication with role-based access (NOT YET)
- **Tool Support**: Ready for OpenAI function calling through llmio (NOT YET)

## Quick Start

### Prerequisites

- Python 3.8+
- Node.js (for frontend development, optional)

### Backend Setup

1. Clone the repository:
```bash
git clone <repository-url>
cd skeleton
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
cd backend
pip install -r requirements.txt
```

4. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your API keys and settings
```

5. Run the backend:
```bash
python main.py
```

The API will be available at `http://localhost:8000`

### Frontend

The frontend is served directly from the backend at `http://localhost:8000/`. No separate build process is required.

## Configuration

### Environment Variables

Create a `.env` file in the `backend/` directory with:

```bash
# Required: OpenAI API key
OPENAI_API_KEY=your_openai_api_key_here

# Required: JWT secret for authentication
JWT_SECRET_KEY=your_jwt_secret_key_here

# Optional: Upload directory (default: ./uploads) - NOT YET USED
UPLOAD_DIR=./uploads

# Optional: Server settings (default: 0.0.0.0:8000)
HOST=0.0.0.0
PORT=8000
```

### Default Credentials

- Username: `admin`
- Password: `admin123`

**MINIMAL FILE-BASED USER SETUP TO BE IMPLEMENTED**

## Plugin System

Skeleton uses a plugin system. The `plugins/` directory is git-ignored, allowing you to maintain customizations separately from the core.

Some potentially useful plugins are in the `pluggable/` directory; you can copy any file from it to the `plugins/` directory to install the plugin (PLUGGABLE CONTENT TO BE CHECKED/FIXED)

### Plugin Types

1. **Core Plugins** (`plugins/core/`) - Override default functionality
   - Authentication handlers
   - Model clients
   - Tool loaders

2. **Function Plugins** (`plugins/functions/`) - Modify request context - **TO BE DEVELOPED/EXPANDED**
   - Add user context
   - Logging
   - Request preprocessing

3. **Tool Plugins** (`plugins/tools/`) - Add OpenAI function calling **TO BE COMPLETED**
   - Weather tools
   - File operations
   - External API integrations

### Creating a Plugin

Example authentication plugin:

```python
# plugins/core/my_auth.py
from typing import Optional, Dict, Any

class MyAuthPlugin:
    def get_priority(self) -> int:
        return 10  # Higher than default (0)

    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        # Your authentication logic
        return {"username": username, "role": "user"}

    def create_token(self, user: Dict[str, Any]) -> str:
        # Your JWT creation logic
        return "token"

    def verify_token(self, token: str) -> Optional[str]:
        # Your token verification logic
        return "username"
```

## API Documentation

### Authentication

All API endpoints except `/login` require authentication via Bearer token.

### Key Endpoints

- `POST /login` - Authenticate and receive JWT token
- `GET /api/v1/models` - List available AI models
- `GET /api/v1/threads` - List chat threads
- `POST /api/v1/message` - Send a message (returns SSE stream)
- `GET /api/v1/search?q=query` - Search through messages

### Server-Sent Events (SSE)

The `/api/v1/message` endpoint returns an SSE stream with events:

- `message_tokens` - Text chunks from the assistant
- `tool_call` - Tool execution requests
- `tool_result` - Tool execution results
- `error` - Error messages
- `stream_end` - End of stream marker

## Architecture

### Backend Philosophy

- **Contract-first design**: All modules implement typed protocols
- **Single responsibility**: Each module has one primary function
- **Hot-pluggable**: Components can be replaced without restarting
- **Backend-heavy**: Frontend is minimal and stateless

### Frontend Philosophy

- **Stateless**: All state managed by backend
- **Real-time**: SSE for live updates across multiple clients
- **Minimal**: No configuration panes or pop-ups in core UI
- **Extensible**: Plugin pages can add custom interfaces

## Development

### Adding a New Model Provider

1. Implement the model client protocol in `backend/core/model_client.py`
2. Add the new provider to the `clients` dictionary
3. Update the available models list

### Creating a Tool Plugin

1. Create a new file in `plugins/tools/`
2. Implement the `ToolPlugin` protocol
3. Define the OpenAI function schema
4. Implement the tool execution logic

Example:
```python
# plugins/tools/weather.py
from typing import Dict, Any

class WeatherToolPlugin:
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": "get_weather",
            "description": "Get weather information",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"}
                },
                "required": ["location"]
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Any:
        location = arguments.get("location")
        # Your weather API call here
        return {"temperature": 72, "condition": "sunny"}
```

## Security Considerations

- Change default credentials immediately (WHEN SUPPORTED)
- Use a reverse proxy to provide HTTPS in production
- Implement proper user management for multi-user scenarios - TBD
- Validate and sanitize all inputs - best effort already in, to be improved
- Store sensitive data securely - TBD
- **YOU MUST review plugin code before installation** (trusted code model - the plugin is unconstrained)

## Contributing

1. Read the manifesto (`skeleton-manifesto-v1.md`) to understand the project philosophy
2. Follow the coding standards: every line should be readable at 3 AM
3. Add tests for new functionality
4. Update documentation as needed
5. Keep the core minimal - extend functionality through plugins

## License

MIT License - see LICENSE file for details.
