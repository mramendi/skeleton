"""
Plugin protocols - define the contracts that plugins must implement.
All protocols are defined here to avoid duplication and maintain consistency.
"""
from typing import Dict, Any, Optional, List, Protocol
from abc import abstractmethod

class CorePlugin(Protocol):
    """Base protocol for all core functionality plugins"""
    def get_priority(self) -> int:
        """Return plugin priority (higher numbers override lower)"""
        ...

class AuthPlugin(CorePlugin):
    """Protocol for authentication plugins - override default authentication"""
    
    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Authenticate a user"""
        ...

    def create_token(self, user: Dict[str, Any]) -> str:
        """Create JWT token for user"""
        ...

    def verify_token(self, token: str) -> Optional[str]:
        """Verify JWT token and return username"""
        ...

class ModelPlugin(CorePlugin):
    """Protocol for model client plugins - override default model handling"""

    async def get_available_models(self) -> List[str]:
        """Return list of available models"""
        ...

    async def generate_response(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        system_prompt: str
    ) -> Any:  # AsyncGenerator[Dict[str, Any], None]
        """Generate streaming response"""
        ...

class ThreadManagerPlugin(CorePlugin):
    """Protocol for thread management plugins - override default thread handling

    All methods are async to support both in-memory and database-backed implementations.
    In-memory implementations can use async def with immediate return values.
    Database implementations can use await for async database drivers.
    """

    async def create_thread(self, title: str, model: str, system_prompt: str, user: str) -> str:
        """Create a new thread for a specific user"""
        ...

    async def get_threads(self, user: str, query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all non-archived threads for a user, optionally filtered by query"""
        ...

    async def get_thread_messages(self, thread_id: str, user: str) -> Optional[List[Dict[str, Any]]]:
        """Get all messages for a thread if user has access"""
        ...

    async def add_message(self, thread_id: str, user: str, role: str, type: str, content: str, model: Optional[str] = None) -> bool:
        """Add a message to a thread if user has access"""
        ...

    async def update_thread(self, thread_id: str, user: str, title: Optional[str] = None) -> bool:
        """Update thread metadata if user has access"""
        ...

    async def archive_thread(self, thread_id: str, user: str) -> bool:
        """Archive a thread if user has access"""
        ...

    async def search_threads(self, query: str, user: str) -> List[Dict[str, Any]]:
        """Search across all thread messages for a user"""
        ...

class FunctionPlugin(Protocol):
    """Protocol for function plugins that modify request context"""
    
    def get_name(self) -> str:
        """Return function name"""
        ...

    def get_priority(self) -> int:
        """Return function priority"""
        ...

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute function with context"""
        ...

class ToolPlugin(Protocol):
    """Protocol for tool plugins for OpenAI function calling

    Tool plugins can be implemented in two ways:

    1. Class-based (legacy): Implement get_schema() and execute() methods
    2. Function-based (recommended): Just a plain Python function with type hints and docstring
       - The function will be auto-converted to OpenAI schema using llmio.function_parser
       - Arguments will be validated with Pydantic model

    Example function-based tool:
        def get_weather(location: str, unit: str = "celsius") -> dict:
            '''Get weather for a location.

            Args:
                location: City name
                unit: Temperature unit
            '''
            return {"temp": 20, "unit": unit}
    """

    def get_schema(self) -> Dict[str, Any]:
        """Return OpenAI function schema

        NOTE: For plain Python functions, this will be auto-generated
        using llmio.function_parser.model_from_function()
        """
        ...

    async def execute(self, arguments: Dict[str, Any]) -> Any:
        """Execute tool with arguments

        NOTE: For plain Python functions, arguments will be validated
        with Pydantic model before calling the function
        """
        ...
