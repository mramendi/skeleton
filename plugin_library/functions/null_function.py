import logging
from typing import Dict, Any, List
from backend.core.protocols import FunctionPlugin

logger = logging.getLogger("skeleton.function.null_function")


class NullFunctionPlugin(FunctionPlugin):
    """
    A no-op function plugin that logs all calls but does not modify anything.
    Useful as a template or for debugging the function plugin lifecycle.
    """

    def get_name(self) -> str:
        return "null_function"

    def get_priority(self) -> int:
        return 0  # Run last

    async def shutdown(self) -> None:
        """Clean shutdown - no-op for null function"""
        logger.debug("null_function.shutdown() called")

    async def pre_call(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        new_message: Dict[str, Any],
        model: List[str],  # Single-element list for mutable string
        system_prompt: List[str],  # Single-element list for mutable string
        tools: List[Dict[str, Any]],
    ) -> None:
        logger.info(
            "null_function.pre_call() called with: "
            f"user_id={user_id!r}, thread_id={thread_id!r}, "
            f"turn_correlation_id={turn_correlation_id!r}, "
            f"new_message={new_message!r}, model={model!r}, "
            f"system_prompt={system_prompt!r}, tools={tools!r}"
        )
        # No modifications - pass through unchanged

    async def filter_stream(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        chunk: Any,  # ChatCompletionChunk
    ) -> Any:
        logger.info(
            "null_function.filter_stream() called with: "
            f"user_id={user_id!r}, thread_id={thread_id!r}, "
            f"turn_correlation_id={turn_correlation_id!r}, "
            f"chunk={chunk!r}"
        )
        # Pass through unchanged
        return chunk

    async def post_call(
        self,
        user_id: str,
        thread_id: str,
        turn_correlation_id: str,
        response_metadata: Dict[str, Any],
        assistant_message: Dict[str, Any],
    ) -> None:
        logger.info(
            "null_function.post_call() called with: "
            f"user_id={user_id!r}, thread_id={thread_id!r}, "
            f"turn_correlation_id={turn_correlation_id!r}, "
            f"response_metadata={response_metadata!r}, "
            f"assistant_message={assistant_message!r}"
        )
        # No modifications - pass through unchanged
