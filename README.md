# Skeleton - Minimal Modular AI Chat Interface

A minimal, backend-heavy AI chat interface built to be fully modular. Designed to be extended through plugins while keeping the core simple and stable. Not an everything-hub, not an omni-environment, but the skeleton implementation of the basics. You can take this skeleton out of the closet and hang any parts you want on it - or that is the intention when we reach v1.

## IMPORTANT - WORK IN PROGRESS

Current status:

- Not tested NEARLY enough! Needs a lot of use-testing, please feel free to raise issues on GitHub and/or email mr (at) ramendik.eu and/or ping on Discord misha_r or Telegram @ramendik
- Single OpenAI endpoint only. Recommended usage: set up your own [https://docs.litellm.ai/docs/simple_proxy](LiteLLM Proxy) to access any number of endpoints, and/or use a routing and/or subscription service such as OpenRouter or [https://nano-gpt.com/invite/K4xLN4W9](NanoGPT) (note this is an invite link, I find their subscription to be good value but they are still ironing out glitches).
- Model selection and chat runs; threads persist and full-test search for threads is available
- System prompts configured via YAML file
- User management with a YAML file that includes usernames, bcrypt-hashed passwords, roles (which don't do anything yet), and an optional model mask to allow a user to use only a subset of models
- Tools work. You can use "function" tools where you simply provide a function with type hints and a docstring and it ghets converted to a tool schema (thanks, llmio team). Just drop your tools as *.py files into plugins/tools/ and restart the server. OpenWebUI compatible tools are likely to work if they don't use any OpenWebUI internals.
- Temperature temporarily hardcoded
- API still not stable
- Front-end is fully AI-generated, not human-reviewed, and therefore suboptimal and hard to modify. No CORS as I am not sure of security implications. (The back-end IS human-reviewed)
- No functions yet and no file uploads yet - these are the two items on the todo list before a wider announdement
- The sole available data store is SQLite, which has a key scaling limitation: only a single writing transaction can be active at any one time. Skeleton's SQLite data store mitigates this limitation by serializing all writes in a single worker process and also implementing automatic retry logic with exponential backoff to handle concurrent write operations safely across multiple worker processes.
- For any database changes, the SQLite data store can handle adding new fields but cannot handle destructive changes (such as changing a field's type or renaming a field). If such a change is required between versions, you may need to delete your skeleton.db file to start fresh. However we will aim to avoid such changes; if they happen, a very loud warning will be provided and the system will cleanly fail to start.
- There is a test suite from an earlier stage of development, now out of date and significantly incomplete

## DOCUMENTATION

- This file - introduction, usage instructions, extension instructions
- [Project manifesto](skeleton-manifesto-v1.md)
- [Test suite documentation](backend/tests/README.md)
- [Explanation of the duck typing protocol system for plugins](protocol_explanation.md)
- The `backend/docs` directory contains explanations of backend protocols and plugins in more detail

## Features

- **Minimal Core**: Simple, well-documented codebase that's easy to understand and modify
- **Plugin Architecture**: Extensible through plugins without modifying core files. *You can replace any part of the core by implementing its protocol in your code*; all protocols are in `backend/protocols.py`
- **Backend-Heavy**: All business logic and state management on the backend
- **Real-time Chat**: Server-sent events for streaming responses
- **Thread Management**: Persistent chat threads with search functionality
- **File Upload**: Support for file attachments (NOT YET)
- **Authentication**: User authentication, in the future with role-based access. Currently implemented with a YAML configuration file that allows limiting user access to specific models
- **Tool Support**: Ready for OpenAI function calling; uses llmio to create schemas from Python functions.
- **Function support**: Functions can hook into the process at several points to modify the requests, responses, and model context (NOT YET)

## Quick Start

### Prerequisites

- Python 3.8+
- Docker or Podman (optional, for containerized deployment)

### Local Development Setup

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

5. Set up authentication:

**Option A: Quick Setup (Recommended for testing)**
```bash
# Generate a JWT secret
python manage_users.py --generate-jwt jwt.secret

# Create an admin user
python manage_users.py -u admin -p yourpassword -r admin

# Set environment variable for JWT secret
export JWT_SECRET_FILE=$(pwd)/jwt.secret
```

**Option B: Ephemeral Mode (No authentication setup)**
```bash
export SKELETON_MODE=ephemeral
```

6. Run the backend:
```bash
python main.py
```

The application will be available at `http://localhost:8000` (or the host/port configured in your `.env` file)

### Alternative: Running with uvicorn directly

For more advanced configuration, you can run the application using uvicorn directly:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

This allows you to use additional uvicorn options such as:
- `--workers N` - Use multiple worker processes for better performance
- `--reload` - Enable auto-reload during development
- `--log-level debug` - Set log level
- `--ssl-keyfile` and `--ssl-certfile` - Enable HTTPS (however, a reverse proxy is generally preferable)

Example with multiple workers:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

✅ **Automatic Retry with Backoff**: The SQLite store plugin uses `BEGIN IMMEDIATE` to detect write locks early and implements exponential backoff with jitter. This allows safe use of multiple workers by automatically retrying failed write operations with staggered delays.

Note: When using uvicorn directly, the `SKELETON_HOST` and `SKELETON_PORT` environment variables are ignored - you must specify them via command line arguments.

### Frontend

The frontend is served directly from the backend at `http://localhost:8000/`. No separate build process is required.

## Docker Deployment

NOTE: you can also use Podman instead of Docker. To use Podman, Replace the `docker` command with `podman`; if you want to use `podman compose`, install the `podman-compose` package. You can also install the `podman-docker` package to use `docker` commands unchanged.

### Building the Docker Image

First, build the Docker image from the repository:

```bash
# Build the image (tag it as 'skeleton')
docker build -t skeleton .

# Or with a custom tag
docker build -t my-skeleton:latest .
```

The build process:
- Installs Python dependencies
- Compiles any required packages
- Sets up the application directory structure
- Creates necessary data and upload directories

### Quick Ephemeral Mode

For quick testing without persistent data:

```bash
docker run -p 8000:8000 -e SKELETON_MODE=ephemeral skeleton
```

This creates a default user `default` with password `default`. No other users are available. **DO NOT use for production!**

In ephemeral mode users are logged out on restart.

### Production Deployment with Persistent Data

1. Create a data directory:
```bash
mkdir -p ./skeleton-data
```

2. Generate JWT secret and users file:
```bash
# Generate JWT secret
docker run --rm -v $(pwd)/skeleton-data:/app/data skeleton \
  python manage_users.py --generate-jwt /app/data/jwt.secret

# Create admin user
docker run --rm -v $(pwd)/skeleton-data:/app/data skeleton \
  python manage_users.py -f /app/data/users.yaml -u admin -p yourpassword -r admin
```

3. Run the container:
```bash
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/skeleton-data:/app/data \
  -e OPENAI_API_KEY=your_api_key_here \
  -e JWT_SECRET_FILE=/app/data/jwt.secret \
  -e SKELETON_HOST=0.0.0.0 \
  -e SKELETON_PORT=8000 \
  --name skeleton \
  skeleton
```

### Docker Compose

Create `docker-compose.yml`:

```yaml
version: '3.8'
services:
  skeleton:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./uploads:/app/uploads
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - JWT_SECRET_FILE=/app/data/jwt.secret
      - SKELETON_HOST=0.0.0.0
      - SKELETON_PORT=8000
    restart: unless-stopped
```

Run with:
```bash
docker-compose up -d
```

## Configuration

### Environment Variables

Create a `.env` file in the `backend/` directory with:

```bash
# Required: OpenAI API key
OPENAI_API_KEY=your_openai_api_key_here

# Optional: JWT secret for authentication (see Authentication section below)
# JWT_SECRET_KEY=your_jwt_secret_key_here

# Optional: Server settings (default: 0.0.0.0:8000)
SKELETON_HOST=0.0.0.0
SKELETON_PORT=8000

# Optional: Data directory for users.yaml and jwt.secret (default: .)
DATA_PATH=.

# Optional: Custom users filename (default: users.yaml)
USERS_FILENAME=users.yaml

# Optional: Upload directory (default: ./uploads) - NOT YET USED
UPLOAD_DIR=./uploads
```

### Authentication

#### JWT Secret Configuration

The system requires a JWT secret for authentication. You can provide it in three ways (in order of priority):

1. **JWT_SECRET_KEY environment variable** (recommended for production):
   ```bash
   export JWT_SECRET_KEY=your_secure_random_secret_here
   ```

2. **JWT_SECRET_FILE environment variable** (points to a file containing the secret):
   ```bash
   python manage_users.py --generate-jwt /path/to/your/jwt.secret
   export JWT_SECRET_FILE=/path/to/your/jwt.secret
   ```

3. **Default file location** (`$DATA_PATH/jwt.secret`):
   ```bash
   python manage_users.py --generate-jwt jwt.secret
   # The system will automatically read from ./jwt.secret (or $DATA_PATH/jwt.secret)
   ```

**Generating a JWT Secret:**
```bash
# Generate a secure JWT secret and save it to a file
python manage_users.py --generate-jwt jwt.secret

# The file will be created with secure permissions (600)
```

#### User Management

Users are managed through a YAML file (default: `users.yaml`). Each user requires:
- `hash`: bcrypt-hashed password
- `role`: user role (admin/user)
- `model_mask` (optional): regex pattern to restrict which models the user can access

**Creating Users:**
```bash
# Create an admin user
python manage_users.py -u admin -p secretpassword -r admin

# Create a regular user with model restrictions
python manage_users.py -u user1 -p password123 -r user -m "^(?!.*gpt).*$"

# Create user in custom file
python manage_users.py -f custom_users.yaml -u test -p test123 -r user
```

**Example users.yaml:**
```yaml
admin:
  hash: "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewdBPj6ukx.LFvO."
  role: "admin"
  # No model_mask means access to all models

user1:
  hash: "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewdBPj6ukx.LFvO."
  role: "user"
  model_mask: "^(?!.*gpt).*$"  # Block access to GPT models

user2:
  hash: "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewdBPj6ukx.LFvO."
  role: "user"
  model_mask: "^claude-.*$"  # Only allow Claude models
```

**Validating User Configuration:**
```bash
# Check if your users.yaml file is valid
python manage_users.py --validate -f users.yaml
```

**Model Mask Examples:**
- `".*"` - Allow all models (default if not specified)
- `"^(?!.*gpt).*$"` - Block all models containing "gpt"
- `"^claude-.*"` - Only allow models starting with "claude-"
- `"^(gpt-4|claude-3).*$"` - Only allow specific model series

#### Ephemeral Mode (Testing Only)

For quick testing without setting up authentication:
```bash
export SKELETON_MODE=ephemeral
python main.py
```

This creates a default user `default` with password `default` and admin privileges. **DO NOT use this in production!**

## Plugin System

Skeleton uses a plugin system. The `plugins/` directory is git-ignored, allowing you to maintain customizations separately from the core.

Some potentially useful plugins are in the `plugin_library/` directory. You can symlink any of them to your plugins directory to use it and receive any updates automatically (`ln -s plugin_library/example-plugin.py plugins/core/`). Alternatively you can copy any file from `plugin_library/` to a subdirectory in `plugins/` to modify it; in that case, watch for updates of the original plugin (the plugins list a last update date). (THE CURRENT EXAMPLES IN `plugin_library/` ARE NOT YET SUPPORTABLE)

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

    def request_allowed(self, username: str, model_name: str) -> bool:
        # your logic to test if this user is permitted to sent a request to this model at this time
        return True
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

(THIS DOES NOT WORK YET)

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

- Use a reverse proxy to provide HTTPS in production
- **YOU MUST review any third-party plugin code before installation** (trusted code model - the plugin is unconstrained)

## Contributing

1. Read the manifesto (`skeleton-manifesto-v1.md`) to understand the project philosophy
2. Follow the coding standards: every line should be readable at 3 AM
3. Add tests for new functionality
4. Update documentation as needed
5. Keep the core minimal - extend functionality through plugins

## License

MIT License - see LICENSE file for details.
