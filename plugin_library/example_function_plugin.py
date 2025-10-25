"""
Example function plugin that demonstrates how to add functions that modify context.
This plugin adds user context and logging functionality.

UNSUPPORTED!! FUNCTION SCAFFOLDING NOT READY AND WILL CHANGE
"""
from typing import Dict, Any
import time
import logging

logger = logging.getLogger("skeleton.plugins.functions.example")

class UserContextPlugin:
    """Example function plugin that adds user context"""

    def get_name(self) -> str:
        return "user_context"

    def get_priority(self) -> int:
        return 5

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Add user-specific context to the conversation"""
        # This could fetch user preferences, history, etc.
        user_context = {
            "user_timezone": "America/New_York",
            "user_preferences": {
                "response_style": "concise",
                "technical_level": "intermediate"
            }
        }

        return {
            "user_context": user_context
        }

class LoggingFunctionPlugin:
    """Example function plugin that logs conversation data"""

    def get_name(self) -> str:
        return "conversation_logger"

    def get_priority(self) -> int:
        return 1  # Low priority - run last

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Log conversation for analytics"""
        # In reality, this would log to a database or analytics service
        conversation_data = {
            "timestamp": time.time(),
            "user_message": context.get("user_message", ""),
            "thread_id": context.get("thread_id"),
            "model": context.get("model", "unknown")
        }

        logger.debug(f"Conversation: {conversation_data}")

        # Return empty dict - this plugin doesn't modify context
        return {}
