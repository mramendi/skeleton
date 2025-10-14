from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
import uvicorn
import os
from typing import Optional, List, Dict, Any
from datetime import datetime
import json
import asyncio
import logging
import sys
from pydantic import BaseModel, field_validator, Field
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging - stdout by default for containerized deployments
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("skeleton")

# Import plugin system
from core.plugin_manager import plugin_manager

app = FastAPI(title="Skeleton API", version="1.0.0")

# Initialize plugin system - now self-initializing
plugin_manager.initialize()

# Security
security = HTTPBearer()

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

# Authentication dependency
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    user = plugin_manager.auth.get_plugin().verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")
    return user

# Mount static files - serve frontend from /static path
# Use the correct path relative to the backend directory
app.mount("/static", StaticFiles(directory="../frontend"), name="static")

# Routes
@app.get("/")
async def serve_frontend():
    """Serve the main frontend HTML file with no-cache headers for development"""
    response = FileResponse("../frontend/index.html")
    # Prevent caching during development
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.post("/login")
async def login(request: LoginRequest):
    logger.info(f"Login attempt for user: {request.username}")
    user = plugin_manager.auth.get_plugin().authenticate_user(request.username, request.password)
    if not user:
        logger.warning(f"Failed login attempt for user: {request.username}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = plugin_manager.auth.get_plugin().create_token(user)
    logger.info(f"Successful login for user: {request.username}")
    return {"access_token": token, "token_type": "bearer"}

@app.post("/logout")
async def logout(current_user: str = Depends(get_current_user)):
    logger.info(f"User logged out: {current_user}")
    return {"message": "Logged out successfully"}

@app.get("/api/v1/models")
async def get_models(current_user: str = Depends(get_current_user)):
    logger.debug(f"User {current_user} requested models list")
    return await plugin_manager.model.get_plugin().get_available_models()

@app.get("/api/v1/system_prompts")
async def get_system_prompts(current_user: str = Depends(get_current_user)):
    logger.debug(f"User {current_user} requested system prompts")
    return ["default", "code-assistant", "creative-writing", "analysis"]

@app.get("/api/v1/threads")
async def get_threads(current_user: str = Depends(get_current_user)):
    logger.debug(f"User {current_user} requested threads list")
    return await plugin_manager.thread.get_plugin().get_threads(current_user)

@app.get("/api/v1/threads/{thread_id}/messages")
async def get_thread_messages(thread_id: str, current_user: str = Depends(get_current_user)):
    logger.debug(f"User {current_user} requested messages for thread {thread_id}")
    messages = await plugin_manager.thread.get_plugin().get_thread_messages(thread_id, current_user)
    if messages is None:
        logger.warning(f"User {current_user} attempted to access thread {thread_id} - not found or no access")
        raise HTTPException(status_code=404, detail="Thread not found")
    return messages

@app.post("/api/v1/threads/{thread_id}")
async def update_thread(thread_id: str, request: ThreadUpdateRequest, current_user: str = Depends(get_current_user)):
    logger.info(f"User {current_user} updating thread {thread_id}")
    success = await plugin_manager.thread.get_plugin().update_thread(thread_id, current_user, request.title)
    if not success:
        logger.warning(f"User {current_user} failed to update thread {thread_id} - not found or no access")
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"message": "Thread updated successfully"}

@app.delete("/api/v1/threads/{thread_id}")
async def archive_thread(thread_id: str, current_user: str = Depends(get_current_user)):
    logger.info(f"User {current_user} archiving thread {thread_id}")
    success = await plugin_manager.thread.get_plugin().archive_thread(thread_id, current_user)
    if not success:
        logger.warning(f"User {current_user} failed to archive thread {thread_id} - not found or no access")
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"message": "Thread archived successfully"}

@app.get("/api/v1/search")
async def search_threads(q: str, current_user: str = Depends(get_current_user)):
    logger.debug(f"User {current_user} searching for: {q}")
    if len(q) > 500:  # Input validation
        raise HTTPException(status_code=400, detail="Search query too long")
    return await plugin_manager.thread.get_plugin().search_threads(q, current_user)

@app.post("/api/v1/message")
async def send_message(
    content: str = Form(...),
    thread_id: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    system_prompt: Optional[str] = Form(None),
    current_user: str = Depends(get_current_user)
):
    """Send a message and return SSE stream"""
    # Input validation
    if not content or len(content.strip()) == 0:
        raise HTTPException(status_code=400, detail="Message content cannot be empty")
    if len(content) > 100000:
        raise HTTPException(status_code=400, detail="Message content too long")

    logger.info(f"User {current_user} sending message to thread {thread_id or 'new'}")

    async def event_generator():
        accumulated_response = []  # Collect all response tokens
        thread_id_val = None

        try:
            # Create or get thread
            if thread_id:
                thread_id_val = thread_id
                # Verify user has access to this thread
                existing_messages = await plugin_manager.thread.get_plugin().get_thread_messages(thread_id_val, current_user)
                if existing_messages is None:
                    error_event = {
                        "event": "error",
                        "data": {"message": "Thread not found or access denied"}
                    }
                    yield f"data: {json.dumps(error_event)}\n\n"
                    return
            else:
                # Create new thread
                thread_id_val = await plugin_manager.thread.get_plugin().create_thread(
                    title=content[:50] + "..." if len(content) > 50 else content,
                    model=model or "gpt-3.5-turbo",
                    system_prompt=system_prompt or "default",
                    user=current_user
                )
                logger.info(f"Created new thread {thread_id_val} for user {current_user}")

            # Add user message to thread
            success = await plugin_manager.thread.get_plugin().add_message(
                thread_id_val, current_user, "user", "message_text", content
            )
            if not success:
                logger.error(f"Failed to add user message to thread {thread_id_val}")
                error_event = {
                    "event": "error",
                    "data": {"message": "Failed to add message to thread"}
                }
                yield f"data: {json.dumps(error_event)}\n\n"
                return

            # Send thread_id to client so it knows which thread this is
            yield f"data: {json.dumps({'event': 'thread_id', 'data': {'thread_id': thread_id_val}})}\n\n"

            # Get message history
            history = await plugin_manager.thread.get_plugin().get_thread_messages(thread_id_val, current_user)

            # Build context for function plugins
            context = {
                "user_message": content,
                "thread_id": thread_id_val,
                "model": model or "gpt-3.5-turbo",
                "system_prompt": system_prompt or "default",
                "user": current_user,
                "history": history
            }

            # Execute function plugins
            context = await plugin_manager.function.execute_functions(context)

            # Get tool schemas for model (for future tool support)
            tool_schemas = plugin_manager.tool.get_tool_schemas()

            # Stream response from model
            model_name = model or "gpt-3.5-turbo"
            async for chunk in plugin_manager.model.get_plugin().generate_response(
                messages=history,
                model=model_name,
                system_prompt=system_prompt or "default"
            ):
                # Handle tool calls if present (future support)
                if chunk.get("event") == "tool_call":
                    try:
                        tool_result = await plugin_manager.tool.execute_tool(
                            chunk["data"]["tool_name"],
                            chunk["data"]["tool_arguments"]
                        )
                        yield f"data: {json.dumps({'event': 'tool_result', 'data': tool_result})}\n\n"
                    except Exception as e:
                        logger.error(f"Tool execution error: {e}")
                        yield f"data: {json.dumps({'event': 'tool_error', 'data': {'message': str(e)}})}\n\n"
                else:
                    # Accumulate message tokens for storage
                    if chunk.get("event") == "message_tokens":
                        token_content = chunk.get("data", {}).get("content", "")
                        accumulated_response.append(token_content)

                    # Stream to client
                    yield f"data: {json.dumps(chunk)}\n\n"

            # Store the complete assistant response
            complete_response = "".join(accumulated_response)
            if complete_response:
                success = await plugin_manager.thread.get_plugin().add_message(
                    thread_id_val, current_user, "assistant", "message_text",
                    complete_response, model=model_name
                )
                if not success:
                    logger.error(f"Failed to store assistant response for thread {thread_id_val}")
            else:
                logger.warning(f"Empty response generated for thread {thread_id_val}")

            logger.info(f"Completed message generation for thread {thread_id_val}, response length: {len(complete_response)}")

        except Exception as e:
            logger.error(f"Error in message generation: {e}", exc_info=True)
            error_event = {
                "event": "error",
                "data": {"message": str(e)}
            }
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/v1/files")
async def upload_file(file: UploadFile = File(...), current_user: str = Depends(get_current_user)):
    """Upload a file"""
    try:
        content = await file.read()
        file_info = plugin_manager.thread.get_plugin().add_message(...)  # This needs to be fixed
        # Actually, we need a file manager plugin too, but for now let's create a simple one
        return {"url": "https://example.com/file.txt"}  # Placeholder
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")

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
    uvicorn.run(app, host="0.0.0.0", port=8000)
