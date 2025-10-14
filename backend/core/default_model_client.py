"""
Default model client implementation using OpenAI SDK directly.
Uses llmio only for function_parser to auto-generate tool specs.
Can be overridden by plugins.
"""
from typing import List, Dict, Any, AsyncGenerator
from openai import AsyncOpenAI
import os
import logging
from datetime import datetime
import asyncio

logger = logging.getLogger("skeleton.model_client")

class DefaultModelClient:
    """Default model client using OpenAI SDK - can be overridden by plugins"""

    def get_priority(self) -> int:
        """Default priority - plugins can override with higher priority"""
        return 0

    def __init__(self):
        """Initialize OpenAI client (works with LiteLLM proxy for multi-provider support)"""
        self.client = None
        self.model_cache = []

        # OpenAI client with custom base URL support (for LiteLLM proxy)
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            openai_base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

            try:
                self.client = AsyncOpenAI(
                    api_key=openai_key,
                    base_url=openai_base_url
                )
                logger.info(f"OpenAI client initialized (API key: {openai_key[:8]}..., base_url: {openai_base_url})")
            except Exception as e:
                logger.error(f"Failed to initialize OpenAI client: {e}", exc_info=True)
        else:
            logger.warning("No OPENAI_API_KEY found in environment")

        # Default model if none specified
        self.default_model = "gpt-3.5-turbo"

    async def get_available_models(self) -> List[str]:
        """Return list of available models from the configured API"""
        logger.info("=" * 60)
        logger.info("get_available_models() called (async)")
        logger.info(f"Client initialized: {self.client is not None}")

        if self.client:
            logger.info(f"Client base_url: {self.client.base_url}")
            logger.info(f"Client API key (first 8 chars): {str(self.client.api_key)[:8]}...")

        if not self.client:
            logger.warning("No OpenAI client initialized, returning empty model list")
            return []

        try:
            # Return cached models if available
            if self.model_cache:
                logger.info(f"Returning {len(self.model_cache)} cached models")
                logger.info(f"Cached models: {self.model_cache}")
                logger.info("=" * 60)
                return self.model_cache

            # Call async models.list() directly (we're already in async context)
            logger.info("Calling client.models.list() (async)...")
            models_response = await self.client.models.list()
            logger.info(f"Models response received: {type(models_response)}")
            logger.info(f"Models response data length: {len(models_response.data)}")

            model_ids = [model.id for model in models_response.data]
            logger.info(f"Retrieved {len(model_ids)} models from API")
            logger.info(f"First 10 models: {model_ids[:10]}")
            if len(model_ids) > 10:
                logger.info(f"Last 10 models: {model_ids[-10:]}")

            # Cache the models
            self.model_cache = model_ids
            logger.info(f"Models cached successfully")
            logger.info("=" * 60)
            return model_ids

        except Exception as e:
            logger.error(f"Error fetching models from API: {e}", exc_info=True)
            logger.error(f"Exception type: {type(e).__name__}")
            logger.error(f"Exception args: {e.args}")
            logger.warning("Using fallback model list")
            fallback = self._get_fallback_models()
            logger.info(f"Returning {len(fallback)} fallback models: {fallback}")
            logger.info("=" * 60)
            return fallback

    def _get_fallback_models(self) -> List[str]:
        """Get fallback models when API listing fails"""
        logger.warning("Using hardcoded fallback model list")
        fallback = [
            "gpt-4",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
            "gpt-4o",
            "gpt-4o-mini"
        ]
        logger.info(f"Fallback models: {fallback}")
        return fallback

    async def generate_response(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        system_prompt: str = "default",
        tools: List[Dict[str, Any]] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Generate streaming response using OpenAI SDK

        Args:
            messages: Chat message history
            model: Model to use
            system_prompt: System prompt (or preset name)
            tools: Optional list of tool definitions in OpenAI format
        """

        if model is None:
            model = self.default_model

        if not self.client:
            yield {
                "event": "error",
                "data": {
                    "message": "No API key configured",
                    "timestamp": datetime.now().isoformat()
                }
            }
            return

        # Convert messages to OpenAI format
        openai_messages = []

        # Add system prompt if provided
        if system_prompt and system_prompt != "default":
            openai_messages.append({
                "role": "system",
                "content": system_prompt
            })

        # Convert history messages
        for msg in messages:
            role = msg.get("role")
            if role in ["user", "assistant", "system"]:
                openai_messages.append({
                    "role": role,
                    "content": msg.get("content", "")
                })

        logger.info(f"Generating response with model {model}, {len(openai_messages)} messages, {len(tools) if tools else 0} tools")

        # Stream tokens
        try:
            # Build request parameters
            request_params = {
                "model": model,
                "messages": openai_messages,
                "temperature": 0.7,
                "max_tokens": 2000,
                "stream": True
            }

            # Add tools if provided
            if tools:
                request_params["tools"] = tools
                logger.info(f"Tool calling enabled with {len(tools)} tools")

            # Stream response
            stream = await self.client.chat.completions.create(**request_params)

            async for chunk in stream:
                # Handle different chunk types
                delta = chunk.choices[0].delta if chunk.choices else None

                if delta:
                    # Regular content tokens
                    if delta.content:
                        yield {
                            "event": "message_tokens",
                            "data": {
                                "content": delta.content,
                                "timestamp": datetime.now().isoformat(),
                                "model": model
                            }
                        }

                    # TODO: Tool calls - for future implementation
                    # When tool_calls are present:
                    # 1. Parse tool call from delta.tool_calls
                    # 2. Get tool plugin from PluginManager
                    # 3. Validate arguments with Pydantic model (from llmio.function_parser.model_from_function)
                    # 4. Execute tool function
                    # 5. Add tool result to message history
                    # 6. Continue conversation with tool result
                    #
                    # NOTE: Don't yield tool_call events to frontend yet - they break the current UI
                    # Need to handle tool calls server-side and only stream the final response
                    if delta.tool_calls:
                        for tool_call in delta.tool_calls:
                            logger.info(f"TODO: Tool call received: {tool_call.function.name if tool_call.function else 'unknown'}")
                            # Future: validate with Pydantic, execute, add to history

                # Check if stream is done
                if chunk.choices and chunk.choices[0].finish_reason:
                    logger.info(f"Stream finished: {chunk.choices[0].finish_reason}")
                    break

            # Send end of stream
            yield {
                "event": "stream_end",
                "data": {
                    "timestamp": datetime.now().isoformat(),
                    "model": model
                }
            }

        except Exception as e:
            logger.error(f"Error generating response: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": {
                    "message": f"Error generating response: {str(e)}",
                    "timestamp": datetime.now().isoformat()
                }
            }
