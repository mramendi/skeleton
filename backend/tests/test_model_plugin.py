"""
Test Model Plugin - Provides mock LLM responses for testing without API keys.

This plugin returns fake streaming responses that simulate real LLM behavior
without making any external API calls or requiring API keys.
"""
from typing import List, Dict, Any, AsyncGenerator
import asyncio


class TestModelPlugin:
    """Mock model plugin for testing - returns canned responses without API calls"""

    def get_priority(self) -> int:
        """High priority to override default model client in tests"""
        return 1000

    async def get_available_models(self) -> List[str]:
        """Return fake model list"""
        return [
            "test-model-fast",
            "test-model-smart",
            "test-model-creative"
        ]

    async def generate_response(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        system_prompt: str = "default",
        tools: List[Dict[str, Any]] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Generate fake streaming response based on the last user message.

        Simulates realistic streaming behavior with configurable responses
        based on message content for testing different scenarios.
        """
        # Get the last user message
        last_message = None
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_message = msg.get("content", "")
                break

        # Determine response based on message content (for testing different scenarios)
        if last_message and "error" in last_message.lower():
            # Simulate error scenario
            yield {
                "event": "error",
                "data": {"message": "Simulated error for testing"}
            }
            return

        # Generate response based on model type
        if "creative" in model:
            response = "Once upon a time, in a land of tests..."
        elif "smart" in model:
            response = "The answer is 42. Here's a detailed explanation..."
        else:
            response = "This is a test response from the mock model."

        # Add context from system prompt if needed
        if system_prompt == "code-assistant":
            response = f"```python\n# {response}\nprint('Hello, World!')\n```"

        # Stream the response token by token (simulate realistic streaming)
        words = response.split()
        for i, word in enumerate(words):
            chunk = word + (" " if i < len(words) - 1 else "")
            yield {
                "event": "message_tokens",
                "data": {
                    "content": chunk,
                    "model": model
                }
            }
            # Small delay to simulate network latency (can be removed for faster tests)
            await asyncio.sleep(0.01)

        # Send stream end event
        yield {
            "event": "stream_end",
            "data": {
                "finish_reason": "stop",
                "model": model
            }
        }
