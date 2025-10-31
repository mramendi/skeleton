"""
Default model client implementation using OpenAI SDK directly.
Uses llmio only for function_parser to auto-generate tool specs.
Can be overridden by plugins.
"""
from typing import List, Dict, Any, AsyncGenerator
import os
import logging
from datetime import datetime
import asyncio
from .protocols import ModelPlugin

try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logging.getLogger("skeleton.model_client").warning("OpenAI package not available - model client will fail")

logger = logging.getLogger("skeleton.model_client")

class DefaultModelClient():
    """Default model client using OpenAI SDK - can be overridden by plugins"""

    def get_role(self) -> str:
        """Return the role string for this plugin"""
        return "model"

    def get_priority(self) -> int:
        """Default priority - plugins can override with higher priority"""
        return 0

    async def shutdown(self) -> None:
        return

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
                logger.info(f"OpenAI client initialized (API key present, base_url: {openai_base_url})")
            except Exception as e:
                logger.error(f"Failed to initialize OpenAI client: {e}", exc_info=True)
                self.client = None
        else:
            logger.error("🚨 NO OPENAI_API_KEY CONFIGURED 🚨")
            logger.error("The OpenAI API key is missing from your environment configuration.")
            logger.error("Please set the OPENAI_API_KEY environment variable.")
            logger.error("Example: export OPENAI_API_KEY='your-api-key-here'")
            logger.error("Or add it to your .env file: OPENAI_API_KEY=your-api-key-here")
            self.client = None

        # Default model if none specified
        self.default_model = "MODELS NOT AVAILABLE"

    async def get_available_models(self) -> List[str]:
        """Return list of available models from the configured API"""
        logger.info("=" * 60)
        logger.info("get_available_models() called (async)")
        logger.info(f"Client initialized: {self.client is not None}")

        if self.client:
            logger.info(f"Client base_url: {self.client.base_url}")
            logger.info(f"Client API key present: {str(bool(self.client.api_key))}")

        if not self.client:
            logger.error("🚨 NO OPENAI_API_KEY CONFIGURED - MODEL CLIENT DISABLED 🚨")
            logger.error("Cannot fetch models without API key configuration.")
            logger.error("Please set OPENAI_API_KEY environment variable.")
            logger.error("Example: export OPENAI_API_KEY='your-api-key-here'")
            return ["MODELS NOT AVAILABLE"]

        try:
            # Return cached models if available - DISABLED
            # if self.model_cache:
            #     logger.info(f"Returning {len(self.model_cache)} cached models")
            #     logger.info(f"Cached models: {self.model_cache}")
            #     logger.info("=" * 60)
            #     return self.model_cache

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
            "MODELS NOT AVAILABLE"
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
                    "message": "🚨 NO OPENAI_API_KEY CONFIGURED 🚨 Please set the OPENAI_API_KEY environment variable to use the model client.",
                    "timestamp": datetime.now().isoformat()
                }
            }
            return

        # Prepare messages for OpenAI API
        # Use the supplied messages directly and prepend the system prompt
        openai_messages = []
        if system_prompt is not None:
            openai_messages.append({
                "role": "system",
                "content": system_prompt
            })
        openai_messages.extend(messages)

        logger.info(f"Generating response with model {model}, {len(openai_messages)} messages, {len(tools) if tools else 0} tools")

        # Accumulate metadata from the stream
        response_metadata = {}

        # Stream tokens
        try:
            # Build request parameters
            request_params = {
                "model": model,
                "messages": openai_messages,
                "temperature": 0.7,
                "max_tokens": 2000,
                "stream": True,
                "stream_options": {"include_usage": True}
            }

            # Add tools if provided
            if tools:
                request_params["tools"] = tools
                logger.info(f"Tool calling enabled with {len(tools)} tools")

            # Stream response
            stream = await self.client.chat.completions.create(**request_params)

            async for chunk in stream:
                # --- Comprehensive Metadata Capture ---
                # Capture all top-level fields that are not streamed
                for key, value in chunk.model_dump(exclude_unset=True, exclude={'choices'}).items():
                    if key not in response_metadata or response_metadata[key] is None:
                        response_metadata[key] = value

                # Capture all choice-level fields that are not streamed (e.g., finish_reason)
                if chunk.choices:
                    if 'choices' not in response_metadata:
                        response_metadata['choices'] = []
                    # Ensure we have enough slots in the metadata choices list
                    while len(response_metadata['choices']) <= chunk.choices[0].index:
                        response_metadata['choices'].append({})

                    choice_metadata = response_metadata['choices'][chunk.choices[0].index]
                    for key, value in chunk.choices[0].model_dump(exclude_unset=True, exclude={'delta'}).items():
                        if key not in choice_metadata or choice_metadata[key] is None:
                            choice_metadata[key] = value

                # --- Streamed Content Handling ---
                delta = chunk.choices[0].delta if chunk.choices else None

                if delta:
                    # 1. Handle thinking/reasoning tokens (yield first)
                    # Use attribute access for Pydantic models
                    reasoning_content = None
                    if hasattr(delta, 'reasoning'):
                        reasoning_content = delta.reasoning
                    elif hasattr(delta, 'reasoning_content'):
                        reasoning_content = delta.reasoning_content

                    if reasoning_content:
                        yield {
                            "event": "thinking_tokens",
                            "data": {
                                "content": reasoning_content,
                                "timestamp": datetime.now().isoformat(),
                                "model": model
                            }
                        }

                    # 2. Handle tool calls
                    if hasattr(delta, 'tool_calls') and delta.tool_calls:
                        for tool_call in delta.tool_calls:

                            tool_call_data = tool_call.model_dump(exclude_unset=True)

                            # # Use attribute access for Pydantic models
                            # tool_call_data = {
                            #     "id": tool_call.id if hasattr(tool_call, 'id') else None,
                            #     "index": tool_call.index if hasattr(tool_call, 'index') else None,
                            #     "type": tool_call.type if hasattr(tool_call, 'type') else None,
                            # }
                            #
                            # if hasattr(tool_call, 'function') and tool_call.function:
                            #     tool_call_data["function"] = {
                            #         "name": tool_call.function.name if hasattr(tool_call.function, 'name') else None,
                            #         "arguments": tool_call.function.arguments if hasattr(tool_call.function, 'arguments') else None
                            #     }

                            yield {
                                "event": "tool_calls",
                                "data": {
                                    "tool_call": tool_call_data,
                                    "timestamp": datetime.now().isoformat(),
                                    "model": model
                                }
                            }

                    # 3. Handle regular content tokens (yield after thinking)
                    if hasattr(delta, 'content') and delta.content:
                        yield {
                            "event": "message_tokens",
                            "data": {
                                "content": delta.content,
                                "timestamp": datetime.now().isoformat(),
                                "model": model
                            }
                        }

                # Check if stream is done
                if chunk.choices and chunk.choices[0].finish_reason:
                    logger.info(f"Stream finished: {chunk.choices[0].finish_reason}")
                    # The loop will naturally terminate after the last chunk is processed.

            # Send end of stream with accumulated metadata
            yield {
                "event": "stream_end",
                "data": {
                    "timestamp": datetime.now().isoformat(),
                    "model": model,
                    "metadata": response_metadata
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
