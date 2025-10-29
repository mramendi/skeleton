from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
import sys
from typing import Optional, List, Dict, Any
from datetime import datetime
import json
import asyncio
import logging
import time
from pydantic import BaseModel, field_validator, Field
from dotenv import load_dotenv
from collections import defaultdict
from contextlib import asynccontextmanager

# Add backend to Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

# Load environment variables from .env file
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Construct the full path to the static and upload directories
DATA_DIR = os.getenv("DATA_PATH", ".")
UPLOAD_DIR_NAME = os.getenv("UPLOAD_DIR", "uploads")
UPLOAD_FULL_PATH = os.path.join(DATA_DIR,UPLOAD_DIR_NAME)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Configure logging - stdout by default for containerized deployments
# Validate LOG_LEVEL and default to INFO if invalid
valid_log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
log_level = LOG_LEVEL.upper() if LOG_LEVEL in valid_log_levels else 'INFO'

logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("skeleton")

if LOG_LEVEL and LOG_LEVEL.upper() not in valid_log_levels:
    logger.error(f"Invalid LOG_LEVEL '{LOG_LEVEL}', defaulting to INFO. Valid levels: {valid_log_levels}")


# Import plugin system
from backend.core.plugin_manager import plugin_manager

# Lifespan context manager - for shutdown purposes
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Skeleton starting up...")
    # Eager plugin initialization here if needed, currently not needed

    yield # <--- Application runs here

    # --- Code after shutdown ---
    logger.info("Skeleton shutting down...")
    try:
        await asyncio.wait_for(plugin_manager.shutdown(), timeout=10.0)
        logger.info("Plugin shutdown completed")
    except asyncio.TimeoutError:
        logger.warning("Plugin shutdown timed out after 10 seconds")
    except Exception as e:
        logger.error(f"Error during plugin shutdown: {e}")

app = FastAPI(title="Skeleton API", version="1.0.0", lifespan=lifespan)

# Initialize plugin system - now self-initializing
plugin_manager.initialize()

# Security
security = HTTPBearer()


# Rate limiting
class RateLimiter:
    """Manages rate limiting with per-endpoint policies

    WARNING: This is an in-memory implementation. In multi-worker deployments
    (e.g., multiple uvicorn/gunicorn workers), each worker maintains its own
    rate limit state independently. Rate limits become per-worker rather than
    global (e.g., 5 login attempts per worker instead of 5 total).

    For single-process deployments, this provides effective rate limiting.
    For multi-worker deployments, rate limits are approximate but still provide
    significant protection against brute-force attacks.

    TODO: Consider implementing SQLite-based or Redis-based rate limiting for
    true global limits across multiple workers.
    """
    __slots__ = ("_buckets",)

    def __init__(self) -> None:
        self._buckets: Dict[str, list[float]] = defaultdict(list)

    def is_rate_limited(self, key: str, max_attempts: int = 5, window: int = 60) -> bool:
        """
        Check if a key has exceeded rate limit.

        Args:
            key: Unique identifier (e.g., "login:192.168.1.1" or "message:user123")
            max_attempts: Maximum attempts allowed in the window
            window: Time window in seconds

        Returns:
            True if rate limited, False otherwise
        """
        now = time.time()
        # Remove entries older than the window
        self._buckets[key] = [t for t in self._buckets[key] if now - t < window]

        if len(self._buckets[key]) >= max_attempts:
            return True

        self._buckets[key].append(now)
        return False

RATE_LIMITER = RateLimiter()

# Helper to get client IP
def client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For header for proxied requests"""
    fwd = request.headers.get("X-Forwarded-For")
    return fwd.split(",")[0].strip() if fwd else request.client.host

# Authentication dependency
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    user = plugin_manager.get_plugin("auth").verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")
    return user

# Rate limit dependencies for different endpoints
async def rate_limit_login(ip: str = Depends(client_ip)):
    """Rate limit login attempts: 5 per 60 seconds per IP"""
    if RATE_LIMITER.is_rate_limited(f"login:{ip}", max_attempts=5, window=60):
        raise HTTPException(status_code=429, detail="Too many login attempts. Please wait 60 seconds.")

async def rate_limit_message(current_user: str = Depends(get_current_user)):
    """Rate limit message sending: 10 per 10 seconds per user"""
    if RATE_LIMITER.is_rate_limited(f"message:{current_user}", max_attempts=10, window=10):
        raise HTTPException(status_code=429, detail="Too many messages. Please wait 10 seconds.")

async def rate_limit_threads(current_user: str = Depends(get_current_user)):
    """Rate limit thread operations: 20 per 60 seconds per user"""
    if RATE_LIMITER.is_rate_limited(f"threads:{current_user}", max_attempts=20, window=60):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait 60 seconds.")

async def rate_limit_search(current_user: str = Depends(get_current_user)):
    """Rate limit search: 60 per 10 seconds per user"""
    if RATE_LIMITER.is_rate_limited(f"search:{current_user}", max_attempts=60, window=10):
        raise HTTPException(status_code=429, detail="Too many search requests. Please wait 10 seconds.")

async def rate_limit_models(current_user: str = Depends(get_current_user)):
    """Rate limit model listing: 60 per 60 seconds per user"""
    if RATE_LIMITER.is_rate_limited(f"models:{current_user}", max_attempts=60, window=60):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait 60 seconds.")


# Pydantic models with validation
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100, description="Username")
    password: str = Field(..., min_length=1, max_length=200, description="Password")

    @field_validator('username', 'password')
    @classmethod
    def strip_whitespace(cls, v):
        return v.strip() if v else v

class MessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=100000, description="Message content")
    thread_id: Optional[str] = Field(None, max_length=100, description="Thread ID")
    model: Optional[str] = Field(None, max_length=100, description="Model name")
    system_prompt: Optional[str] = Field(None, max_length=1000, description="System prompt")

    @field_validator('content')
    @classmethod
    def validate_content(cls, v):
        if not v or not v.strip():
            raise ValueError('Message content cannot be empty')
        return v.strip()

class ThreadUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500, description="Thread title")

    @field_validator('title')
    @classmethod
    def strip_title(cls, v):
        return v.strip() if v else v

# Mount static files - serve frontend from /static path
# Use the correct path relative to the root directory
app.mount("/static", StaticFiles(directory="frontend"), name="static")

# Routes
@app.get("/")
async def serve_frontend():
    """Serve the main frontend HTML file with no-cache headers for development"""
    response = FileResponse("frontend/index.html")
    # Prevent caching during development
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.get('/health')
async def health():
    try:
        await plugin_manager.get_plugin("store").list_stores()
        return {'status':'ok'}
    except Exception:
        raise HTTPException(status_code=500, detail="Skeleton database not working")

@app.post("/login")
async def login(request: LoginRequest, _: None = Depends(rate_limit_login)):
    logger.info(f"Login attempt for user: {request.username}")
    user = plugin_manager.get_plugin("auth").authenticate_user(request.username, request.password)
    if not user:
        logger.warning(f"Failed login attempt for user: {request.username}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = plugin_manager.get_plugin("auth").create_token(user)
    logger.info(f"Successful login for user: {request.username}")
    return {"access_token": token, "token_type": "bearer"}

@app.post("/logout")
async def logout(current_user: str = Depends(get_current_user)):
    logger.info(f"User logged out: {current_user}")
    return {"message": "Logged out successfully"}

@app.get("/api/v1/models")
async def get_models(current_user: str = Depends(get_current_user), _: None = Depends(rate_limit_models)):
    logger.debug(f"User {current_user} requested models list")
    models = await plugin_manager.get_plugin("model").get_available_models()

    # Apply model mask filtering for the current user
    auth_plugin = plugin_manager.get_plugin("auth")
    filtered_models = []
    for model in models:
        if auth_plugin.request_allowed(current_user, model):
            filtered_models.append(model)

    # If no models are allowed, return MODELS NOT AVAILABLE
    if not filtered_models:
        logger.warning(f"User {current_user} has no models allowed by model mask")
        return ["MODELS NOT AVAILABLE"]

    return filtered_models

@app.get("/api/v1/system_prompts")
async def get_system_prompts(current_user: str = Depends(get_current_user)):
    logger.debug(f"User {current_user} requested system prompts")
    return await plugin_manager.get_plugin("system_prompt").list_prompts()

@app.get("/api/v1/threads")
async def get_threads(current_user: str = Depends(get_current_user), _: None = Depends(rate_limit_threads)):
    logger.debug(f"User {current_user} requested threads list")
    return await plugin_manager.get_plugin("thread").get_threads(current_user)

@app.get("/api/v1/threads/{thread_id}/messages")
async def get_thread_messages(thread_id: str, current_user: str = Depends(get_current_user), _: None = Depends(rate_limit_threads)):
    logger.debug(f"User {current_user} requested messages for thread {thread_id}")
    messages = await plugin_manager.get_plugin("thread").get_thread_messages(thread_id, current_user)
    if messages is None:
        logger.warning(f"User {current_user} attempted to access thread {thread_id} - not found or no access")
        raise HTTPException(status_code=404, detail="Thread not found")
    return messages

@app.post("/api/v1/threads/{thread_id}")
async def update_thread(thread_id: str, request: ThreadUpdateRequest, current_user: str = Depends(get_current_user), _: None = Depends(rate_limit_threads)):
    logger.info(f"User {current_user} updating thread {thread_id}")
    success = await plugin_manager.get_plugin("thread").update_thread(thread_id, current_user, request.title)
    if not success:
        logger.warning(f"User {current_user} failed to update thread {thread_id} - not found or no access")
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"message": "Thread updated successfully"}

@app.delete("/api/v1/threads/{thread_id}")
async def archive_thread(thread_id: str, current_user: str = Depends(get_current_user), _: None = Depends(rate_limit_threads)):
    logger.info(f"User {current_user} archiving thread {thread_id}")
    success = await plugin_manager.get_plugin("thread").archive_thread(thread_id, current_user)
    if not success:
        logger.warning(f"User {current_user} failed to archive thread {thread_id} - not found or no access")
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"message": "Thread archived successfully"}

@app.get("/api/v1/search")
async def search_threads(q: str, current_user: str = Depends(get_current_user), _: None = Depends(rate_limit_search)):
    logger.debug(f"User {current_user} searching for: {q}")
    if len(q) > 500:  # Input validation
        raise HTTPException(status_code=400, detail="Search query too long")
    # Sanitize query to prevent injection
    q = q[:500].strip()
    if not q:
        raise HTTPException(status_code=400, detail="Search query cannot be empty")
    return await plugin_manager.get_plugin("thread").search_threads(q, current_user)

@app.post("/api/v1/message")
async def send_message(
    content: str = Form(...),
    thread_id: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    system_prompt: Optional[str] = Form(None),
    current_user: str = Depends(get_current_user),
    _: None = Depends(rate_limit_message)
):
    """Send a message and return SSE stream"""
    # Input validation
    if not content or len(content.strip()) == 0:
        raise HTTPException(status_code=400, detail="Message content cannot be empty")
    if len(content) > 100000: # TODO: this might not be so simple
        raise HTTPException(status_code=400, detail="Message content too long")

    logger.info(f"User {current_user} sending message to thread {thread_id or 'new'}")

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

        except Exception as e:
            logger.error(f"Error in message generation: {e}", exc_info=True)
            error_event = {
                "event": "error",
                "data": {"message": f"Exception raised during the request. Details: {str(e)}"}
            }
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/v1/files")
async def upload_file():
    raise HTTPException(status_code=501, detail="File upload not yet implemented")
#async def upload_file(file: UploadFile = File(...), current_user: str = Depends(get_current_user)):
#    """Upload a file"""
#    try:
#        content = await file.read()
#        file_info = plugin_manager.thread.get_plugin().add_message(...)  # This needs to be fixed
#        # Actually, we need a file manager plugin too, but for now let's create a simple one
#        return {"url": "https://example.com/file.txt"}  # Placeholder
#    except Exception as e:
#        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")

@app.get("/api/v1/files/{file_id}")
async def get_file(file_id: str, current_user: str = Depends(get_current_user)):
    """Get file information"""
    # Placeholder - needs proper file manager plugin
    return {"url": "https://example.com/file.txt", "id": file_id}

# Plugin endpoints
@app.get("/_plugin/{plugin_name}/{path:path}")
async def plugin_endpoint(plugin_name: str, path: str):
    """Route plugin pages"""
    # This would be handled by plugin system
    # For now, return 404
    raise HTTPException(status_code=404, detail="Plugin page not found")

if __name__ == "__main__":
    host = os.getenv("SKELETON_HOST", "0.0.0.0")
    port = int(os.getenv("SKELETON_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
