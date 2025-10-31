"""
Default message processor implementation.
This plugin orchestrates the entire message-handling flow, including thread
management, model interaction, and response streaming.
"""
from typing import Dict, Any, Optional, AsyncGenerator, List
import logging
import json
import uuid
from datetime import datetime
from dataclasses import dataclass, field

from .protocols import MessageProcessorPlugin
from generator_wrapper import GeneratorWrapper

logger = logging.getLogger("skeleton.message_processor")

@dataclass
class MessageProcessingState:
    """Holds the mutable state for a single message processing turn."""
    # Inputs
    user_id: str
    content: str
    initial_thread_id: Optional[str]
    initial_model: Optional[str]
    initial_system_prompt: Optional[str]

    # Core Mutable Parameters (can be changed by pre_call)
    model: str
    system_prompt: str
    actual_system_prompt: Optional[str] = None
    tool_schemas: List[Dict[str, Any]] = field(default_factory=list)

    # Generated IDs
    thread_id: Optional[str] = None
    turn_correlation_id: Optional[str] = None
    user_message_id: Optional[str] = None

    # Context Management
    context_message_ids_to_purge_thinking: List[str] = field(default_factory=list)

    # Model Turn Accumulated Data
    message_id: Optional[str] = None
    total_thinking: str = ""
    total_response: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    response_metadata: Dict[str, Any] = field(default_factory=dict)


class DefaultMessageProcessor():
    """
    Default message processor using other plugins - can be overridden by plugins.

    This class implements the MessageProcessorPlugin protocol to handle the
    complete flow of processing a user message, from thread management to
    generating and streaming a response.
    """

    def get_role(self) -> str:
        """Return the role string for this plugin"""
        return "message_processor"

    def get_priority(self) -> int:
        """Default priority - plugins can override with higher priority"""
        return 0

    async def shutdown(self) -> None:
        """Graceful shutdown"""
        return

    def __init__(self):
        """Initialize the message processor"""
        pass

    async def _add_message_with_pre_call(
        self,
        state: MessageProcessingState,
        new_message: Dict[str, Any],
        message_id: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Helper to run pre_call hooks before adding a message to the context."""
        from .plugin_manager import plugin_manager
        thread_plugin = plugin_manager.get_plugin("thread")
        context_plugin = plugin_manager.get_plugin("context")

        # Wrap string parameters in single-element lists for mutable string behavior
        model_list = [state.model]
        system_prompt_list = [state.actual_system_prompt]

        # Execute pre_call hooks for all function plugins
        gen_or_coro = plugin_manager.function.pre_call(
            user_id=state.user_id,
            thread_id=state.thread_id,
            turn_correlation_id=state.turn_correlation_id,
            new_message=new_message,
            model=model_list,
            system_prompt=system_prompt_list,
            tools=state.tool_schemas
        )

        # Wrap the result to handle both coroutines and generators
        wrapped = GeneratorWrapper(gen_or_coro)

        # Iterate over any yielded messages from the pre_call hooks
        async for item in wrapped.yields():
            # Yield a tool_update event for each message
            temp_call_id = str(uuid.uuid4())
            await thread_plugin.add_message(
                thread_id=state.thread_id,
                user=state.user_id,
                role="tool",
                type="tool_update",
                content=str(item),
                aux_id=temp_call_id,
                model=state.model
            )

            yield {
                "event": "tool_update",
                "data": {
                    "call_id": temp_call_id,
                    "content": str(item),
                    "timestamp": datetime.now().isoformat()
                }
            }

        # Ensure the generator/coroutine is fully consumed
        await wrapped.returns()

        # Extract mutated values from lists and update state
        state.model = model_list[0]
        state.actual_system_prompt = system_prompt_list[0]

        # After all hooks, add the (potentially mutated) message to the context
        await context_plugin.add_message(state.thread_id, state.user_id, new_message, message_id)

    async def _run_model_turn(self, state: MessageProcessingState) -> AsyncGenerator[Dict[str, Any], None]:
        """Runs a single turn with the model, yielding events and populating state with accumulated results."""
        from .plugin_manager import plugin_manager
        model_plugin = plugin_manager.get_plugin("model")
        thread_plugin = plugin_manager.get_plugin("thread")

        # Refresh context at the start of each loop iteration to get latest tool results
        context = await plugin_manager.get_plugin("context").get_context(state.thread_id, state.user_id)
        logger.debug(f"Loop iteration: Retrieved fresh context with {len(context) if context else 0} messages")

        # Reset accumulated data for this turn
        state.message_id = None
        state.total_thinking = ""
        state.total_response = ""
        state.tool_calls = []
        state.response_metadata = {}

        current_thinking = ""
        current_response = ""
        last_content_type = None

        try:
            async for event in model_plugin.generate_response(
                messages=context,
                model=state.model,
                system_prompt=state.actual_system_prompt,
                tools=state.tool_schemas
            ):
                # Apply filter_stream hooks to the event chunk
                gen_or_coro = plugin_manager.function.filter_stream(
                    user_id=state.user_id,
                    thread_id=state.thread_id,
                    turn_correlation_id=state.turn_correlation_id,
                    chunk=event
                )

                # Wrap the result to handle both coroutines and generators
                wrapped = GeneratorWrapper(gen_or_coro)

                # Iterate over any yielded messages from the filter_stream hooks
                async for item in wrapped.yields():
                    # Yield a tool_update event for each message
                    temp_call_id = str(uuid.uuid4())
                    await thread_plugin.add_message(
                        thread_id=state.thread_id,
                        user=state.user_id,
                        role="tool",
                        type="tool_update",
                        content=str(item),
                        aux_id=temp_call_id,
                        model=state.model
                    )

                    yield {
                        "event": "tool_update",
                        "data": {
                            "call_id": temp_call_id,
                            "content": str(item),
                            "timestamp": datetime.now().isoformat()
                        }
                    }

                # Ensure the generator/coroutine is fully consumed
                filtered_event = await wrapped.returns()

                # If the filter returned None, drop the chunk and continue to the next event
                if filtered_event is None:
                    continue

                # Use the potentially modified event for further processing
                event = filtered_event
                # Debug: Log all events from model
                logger.debug(f"Received event from model: {event.get('event', 'unknown')} - {event.get('data', {})}")
                if event.get("event") == "thinking_tokens":
                    # Stream thinking tokens to client
                    yield event

                    # Save previous content if switching to thinking
                    if last_content_type == "message" and current_response:
                        # Save previous response when switching to thinking
                        await thread_plugin.add_message(
                            thread_id=state.thread_id,
                            user=state.user_id,
                            role="assistant",
                            type="message_text",
                            content=current_response,
                            model=state.model
                        )
                        current_response = ""
                    elif last_content_type != "thinking" and current_thinking:
                        # Save previous thinking if switching back to thinking
                        await thread_plugin.add_message(
                            thread_id=state.thread_id,
                            user=state.user_id,
                            role="thinking",
                            type="message_text",
                            content=current_thinking,
                            model=state.model
                        )
                        current_thinking = ""

                    # Accumulate thinking content
                    current_thinking += event["data"]["content"]
                    state.total_thinking += event["data"]["content"]
                    last_content_type = "thinking"

                elif event.get("event") == "message_tokens":
                    # Stream message tokens to client
                    yield event

                    # Save previous thinking segment if we're switching to response
                    if last_content_type == "thinking" and current_thinking:
                        await thread_plugin.add_message(
                            thread_id=state.thread_id,
                            user=state.user_id,
                            role="thinking",
                            type="message_text",
                            content=current_thinking,
                            model=state.model
                        )
                        current_thinking = ""

                    # Save previous response segment if we're switching back to response
                    elif last_content_type != "message" and current_response:
                        await thread_plugin.add_message(
                            thread_id=state.thread_id,
                            user=state.user_id,
                            role="assistant",
                            type="message_text",
                            content=current_response,
                            model=state.model
                        )
                        current_response = ""

                    # Accumulate response content
                    current_response += event["data"]["content"]
                    state.total_response += event["data"]["content"]
                    last_content_type = "message"

                elif event.get("event") == "tool_calls":
                    # Collect tool calls (don't stream to client)
                    # Handle both singular and plural forms for compatibility
                    tool_calls_data = event["data"].get("tool_calls", [])
                    if not tool_calls_data:  # If tool_calls is empty/None, try tool_call (singular)
                        tool_call_data = event["data"].get("tool_call")
                        if tool_call_data:
                            tool_calls_data = [tool_call_data]

                    logger.debug(f"Raw tool_calls data: {tool_calls_data}")

                    # Accumulate tool call data - this handles streaming/partial tool calls
                    if tool_calls_data:
                        for new_call in tool_calls_data:
                            if isinstance(new_call, dict):
                                # Find existing tool call by index or ID to merge with
                                existing_call = None
                                call_index = new_call.get('index', 0)

                                # Look for existing call with same index
                                for call in state.tool_calls:
                                    if call.get('index') == call_index:
                                        existing_call = call
                                        break

                                if existing_call:
                                    # Merge with existing call - accumulate arguments
                                    if 'function' in new_call:
                                        if 'function' not in existing_call:
                                            existing_call['function'] = {}

                                        # Merge function properties
                                        for key, value in new_call['function'].items():
                                            if key == 'arguments':
                                                # Accumulate arguments
                                                existing_args = existing_call['function'].get('arguments', '')
                                                if isinstance(value, str):
                                                    existing_call['function']['arguments'] = existing_args + value
                                                else:
                                                    existing_call['function']['arguments'] = str(value)
                                            else:
                                                existing_call['function'][key] = value

                                    # Copy other properties
                                    for key, value in new_call.items():
                                        if key != 'function':
                                            existing_call[key] = value
                                else:
                                    # New tool call - add as-is
                                    state.tool_calls.append(new_call)

                        logger.debug(f"Accumulated tool calls: {state.tool_calls}")
                        # Log detailed structure of each tool call for debugging
                        for i, call in enumerate(state.tool_calls):
                            logger.debug(f"Tool call {i}: {call}")
                            if 'function' in call:
                                func = call['function']
                                logger.debug(f"  Function: {func}")
                                logger.debug(f"  Name: {func.get('name', 'MISSING')}")
                                logger.debug(f"  Arguments: {func.get('arguments', 'MISSING')}")
                        logger.info(f"Model requested {len(state.tool_calls)} tool call(s): {[call.get('function', {}).get('name', 'unknown') for call in state.tool_calls]}")

                elif event.get("event") == "stream_end":
                    # Log metadata if present
                    if event.get("data", {}).get("metadata"):
                        state.response_metadata = event["data"]["metadata"]
                        logger.info(f"Response metadata for thread {state.thread_id}: {state.response_metadata}")

                        # Check if tool calls are in the metadata
                        if "tool_calls" in state.response_metadata:
                            tool_calls_from_metadata = state.response_metadata["tool_calls"]
                            logger.info(f"Found {len(tool_calls_from_metadata)} tool call(s) in stream_end metadata: {[call.get('function', {}).get('name', 'unknown') for call in tool_calls_from_metadata]}")
                            if isinstance(tool_calls_from_metadata, dict):
                                tool_calls_from_metadata = [tool_calls_from_metadata]
                            state.tool_calls.extend(tool_calls_from_metadata)

                    break

                elif event.get("event") == "error":
                    # Model error - yield and stop
                    yield event
                    return

        except Exception as e:
            logger.error(f"Error generating response: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": {
                    "message": f"Error generating response: {str(e)}",
                    "timestamp": datetime.now().isoformat()
                }
            }
            return

        # Save any remaining content from this round
        if state.response_metadata:
            # Try to get ID from metadata (OpenAI response format)
            if 'id' in state.response_metadata:
                state.message_id = state.response_metadata['id']
                logger.info(f"Using message ID from metadata: {state.message_id}")

        # we need to know the message ID for the context here anyway because we might need to save it
        if not state.message_id: 
            state.message_id = str(uuid.uuid4())

        if current_thinking:
            await thread_plugin.add_message(
                thread_id=state.thread_id,
                user=state.user_id,
                role="thinking",
                type="message_text",
                content=current_thinking,
                model=state.model
            )
        if current_response:
            await thread_plugin.add_message(
                thread_id=state.thread_id,
                user=state.user_id,
                role="assistant",
                type="message_text",
                content=current_response,
                model=state.model
            )

        # No return needed - all data is now in the state object

    async def _save_assistant_message(self, state: MessageProcessingState, assistant_message: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """Runs post_call hooks and saves the assistant message to the context."""
        from .plugin_manager import plugin_manager
        context_plugin = plugin_manager.get_plugin("context")

        # Execute post_call hooks to potentially mutate the assistant_message
        gen_or_coro = plugin_manager.function.post_call(
            user_id=state.user_id,
            thread_id=state.thread_id,
            turn_correlation_id=state.turn_correlation_id,
            response_metadata=state.response_metadata,
            assistant_message=assistant_message
        )

        # Wrap the result to handle both coroutines and generators
        wrapped = GeneratorWrapper(gen_or_coro)

        # Iterate over any yielded messages from the post_call hooks
        async for item in wrapped.yields():
            # Yield a tool_update event for each message
            temp_call_id = str(uuid.uuid4())
            await plugin_manager.get_plugin("thread").add_message(
                thread_id=state.thread_id,
                user=state.user_id,
                role="tool",
                type="tool_update",
                content=str(item),
                aux_id=temp_call_id,
                model=state.model
            )
            yield {
                "event": "tool_update",
                "data": {
                    "call_id": temp_call_id,
                    "content": str(item),
                    "timestamp": datetime.now().isoformat()
                }
            }

        # Ensure the generator/coroutine is fully consumed
        await wrapped.returns()

        # add the message (potentially mutated) to the context
        await context_plugin.add_message(state.thread_id, state.user_id, assistant_message, assistant_message.get("id"))

    async def _execute_tool_calls(self, state: MessageProcessingState, tool_calls: List[Dict[str, Any]]) -> AsyncGenerator[Dict[str, Any], None]:
        """Executes all tool calls from the last model turn."""
        from .plugin_manager import plugin_manager

        # Process all tool calls at once
        valid_tool_calls = []
        for tool_call in tool_calls:

            # Get call_id safely at the start.
            call_id = str(uuid.uuid4()) # Default
            if isinstance(tool_call, dict):
                call_id = tool_call.get("id", call_id)

            # Validate tool call structure
            if not isinstance(tool_call, dict):
                logger.error(f"Invalid tool call (not a dict): {tool_call}")
                yield await self._tool_message_to_yield(state, call_id, f"🔧❌ Invalid tool call (not a dict): {tool_call}")
                continue

            if "function" not in tool_call:
                logger.error(f"Tool call missing 'function' field: {tool_call}")
                yield await self._tool_message_to_yield(state, call_id, f"🔧❌ Tool call missing 'function' field: {tool_call}")
                continue

            function = tool_call["function"]
            if not isinstance(function, dict):
                logger.error(f"Tool call 'function' is not a dict: {function}")
                yield await self._tool_message_to_yield(state, call_id, f"🔧❌ Tool call 'function' is not a dict: {function}")
                continue

            function_name = function.get("name")
            if not function_name:
                logger.error(f"Tool call missing function name: {tool_call}")
                yield await self._tool_message_to_yield(state, call_id, f"🔧❌ Tool call missing function name: {function}")
                continue

            function_args = function.get("arguments", "")
            # calls without arguments might actually be valid - commented out check
            #if not function_args:
            #    logger.error(f"Tool call missing arguments: {tool_call}")
            #    continue

            valid_tool_calls.append(tool_call)
            logger.debug(f"Validated tool call: {function_name} with args: {function_args}")

        # Only process valid tool calls
        if not valid_tool_calls:
            logger.warning("No valid tool calls found to process")
            # Continue the conversation without tool calls
            yield {
                "event": "stream_end",
                "data": {
                    "timestamp": datetime.now().isoformat()
                }
            }
            return

        logger.info(f"Processing {len(valid_tool_calls)} valid tool calls")

        for tool_call in valid_tool_calls:
            function_name = tool_call["function"]["name"]
            function_args = tool_call["function"]["arguments"]
            call_id = tool_call.get("id", str(uuid.uuid4()))  # Use model's ID or generate one

            # Stream "tool called" event (formatted on backend)
            yield await self._tool_message_to_yield(state, call_id, f"🔧 Calling {function_name}({function_args})")

            try:
                args_dict = json.loads(function_args) if isinstance(function_args, str) else function_args
                logger.info(f"Executing tool '{function_name}' with arguments: {args_dict}")

                # get the tool plugin object - if it is not found, an exception is raised
                tool_plugin = plugin_manager.tool.get_tool(function_name)

                # NOT awaiting - this is either a generator or a coroutine
                gen_or_coro = tool_plugin.execute(state.user_id, state.thread_id, state.turn_correlation_id, args_dict)

                # wrap it
                tool_wrapped = GeneratorWrapper(gen_or_coro)

                # if it yields any update messages, forward them to users
                # note the tool actually runs within this loop
                async for msg in tool_wrapped.yields():
                    yield await self._tool_message_to_yield(state, call_id, f"{function_name}: {str(msg)}")

                # get the return value
                result = await tool_wrapped.returns()
                logger.info(f"Tool '{function_name}' executed successfully, result type: {type(result).__name__}")

                # Sanitize and validate tool result for JSON serialization
                try:
                    if isinstance(result, str):
                        # Check if string contains binary/non-printable data
                        if any(ord(c) < 32 and c not in '\t\n\r' for c in result):
                            # Likely binary data - create error message
                            sanitized_result = f"Error: Tool returned binary data ({len(result)} bytes). Binary data cannot be stored in conversation history."
                            logger.warning(f"Tool {function_name} returned binary data, converting to error message")
                        else:
                            # Regular string - use as-is
                            sanitized_result = result
                    else:
                        # Try to JSON serialize non-string results
                        sanitized_result = json.dumps(result)
                except (TypeError, ValueError) as e:
                    # JSON serialization failed - create error message
                    sanitized_result = f"Error: Tool returned data that cannot be serialized to JSON. Error: {str(e)}"
                    logger.error(f"Failed to serialize tool {function_name} result: {e}")


                # Truncate result for display
                display_result = sanitized_result
                if len(display_result) > 250:
                    display_result = display_result[:247] + "..."

                # Stream "call completed" event (formatted on backend)
                yield await self._tool_message_to_yield(state, call_id, f"✅ {function_name}: {display_result}")

                # Add tool result to context in OpenAI format
                tool_result_message = {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": sanitized_result
                }
                # Add the tool result message to the context via the helper
                async for event in self._add_message_with_pre_call(state, tool_result_message):
                    yield event

            except Exception as e:
                logger.error(f"Error executing tool {function_name}: {e}", exc_info=True)
                error_result = f"Error executing tool {function_name}: {str(e)}"

                # Sanitize error result as well
                try:
                    # Check if error result contains binary data
                    if isinstance(error_result, str) and any(ord(c) < 32 and c not in '\t\n\r' for c in error_result):
                        sanitized_error = f"Error: Tool execution failed and returned binary data. {function_name}: {str(e)}"
                    else:
                        sanitized_error = error_result
                except Exception:
                    sanitized_error = f"Error: Tool execution failed for {function_name}"

                yield await self._tool_message_to_yield(state, call_id, f"❌ {function_name}: {sanitized_error}")

                # Add error to context - CRITICAL: This must include tool_call_id for the model to understand
                tool_result_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", "unknown"),  # Ensure we have the tool_call_id
                    "content": sanitized_error
                }
                logger.debug(f"Adding tool error to context: {tool_result_message}")
                # Add the tool result message to the context via the helper
                async for event in self._add_message_with_pre_call(state, tool_result_message):
                    yield event

    async def _tool_message_to_yield(self, state: MessageProcessingState, call_id: str, msg: str) -> Dict[str, Any]:
        """save tool message to history and return tool event to yield"""
        from .plugin_manager import plugin_manager
        thread_plugin = plugin_manager.get_plugin("thread")

        await thread_plugin.add_message(
            thread_id=state.thread_id,
            user=state.user_id,
            role="tool",
            type="tool_update",
            content=msg,
            aux_id=call_id,
            model=state.model
        )

        return {
            "event": "tool_update",
            "data": {
                "call_id": call_id,
                "content": msg,
                "timestamp": datetime.now().isoformat()
            }
        }

    async def process_message(
        self,
        user_id: str,
        content: str,
        thread_id: Optional[str],
        model: Optional[str],
        system_prompt: Optional[str]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Process a message and yield a stream of event dictionaries.

        This method orchestrates the complete message processing flow:
        1. Thread creation/retrieval
        2. Adding user message to history
        3. Context management
        4. Model interaction
        5. Response streaming
        6. Saving assistant response
        """
        from .plugin_manager import plugin_manager

        logger.info(f"Processing message for user {user_id} in thread {thread_id or 'new'}")

        # Get required plugins
        thread_plugin = plugin_manager.get_plugin("thread")

        # Initialize state
        state = MessageProcessingState(
            user_id=user_id,
            content=content,
            initial_thread_id=thread_id,
            initial_model=model,
            initial_system_prompt=system_prompt,
            model=model or "default",
            system_prompt=system_prompt or "default"
        )

        # Step 1: Handle thread creation/retrieval
        if not state.initial_thread_id:
            # Create new thread
            state.thread_id = await thread_plugin.create_thread(
                title=state.content[:50] + "..." if len(state.content) > 50 else state.content,
                model=state.model or "default",
                system_prompt=state.system_prompt or "default",
                user=state.user_id
            )
            logger.info(f"Created new thread {state.thread_id}")
        else:
            # Verify thread exists and user has access
            state.thread_id = state.initial_thread_id
            messages = await thread_plugin.get_thread_messages(state.thread_id, state.user_id)
            if messages is None:
                yield {
                    "event": "error",
                    "data": {
                        "message": "Thread not found or access denied",
                        "timestamp": datetime.now().isoformat()
                    }
                }
                return

        # Yield thread_id to client
        yield {
            "event": "thread_id",
            "data": {
                "thread_id": state.thread_id,
                "timestamp": datetime.now().isoformat()
            }
        }

        # Step 2: Add user message to history
        await thread_plugin.add_message(
            thread_id=state.thread_id,
            user=state.user_id,
            role="user",
            type="message_text",  # Keep this for internal storage, but won't be in model context
            content=state.content
        )

        # Step 3: Create message/turn IDs and message to the context (auto-created if it did not exist)
        state.user_message_id = str(uuid.uuid4())
        state.turn_correlation_id = "turn_" + state.user_message_id

        user_message = {
            "role": "user",
            "content": state.content,
            "timestamp": datetime.now().isoformat()
        }

        # Step 4: Resolve system prompt using system prompt plugin
        system_prompt_plugin = plugin_manager.get_plugin("system_prompt")
        state.actual_system_prompt = await system_prompt_plugin.get_prompt(state.system_prompt or "default")

        # If prompt not found or is "zero", use None (no system prompt)
        if state.actual_system_prompt == "" or state.actual_system_prompt is None:
            state.actual_system_prompt = None

        # Get available tool schemas from tool manager
        state.tool_schemas = plugin_manager.tool.get_tool_schemas()

        # Add the user message to the context via the helper
        async for event in self._add_message_with_pre_call(state, user_message, state.user_message_id):
            yield event

        # Step 5: Main conversation loop - handle multiple rounds of tool calls
        while True:
            # Run a model turn, streaming its events
            async for event in self._run_model_turn(state):
                yield event

            # Extract results from the state object
            # prepare the message for the context
            # if tool calls exist, add thinking and tool call and mark for thinking purging
            assistant_message = {
                "id": state.message_id,
                "role": "assistant",
                "content": state.total_response,
                "model": state.model,
                "timestamp": datetime.now().isoformat()
            }

            if state.tool_calls:
                assistant_message["tool_calls"] = state.tool_calls
                assistant_message["reasoning_content"] = state.total_thinking
                state.context_message_ids_to_purge_thinking.append(state.message_id)

            # Save the assistant message and run post_call hooks
            async for event in self._save_assistant_message(state, assistant_message):
                yield event

            # Check if tool calls exist - if not, we're done
            logger.debug(f"Checking for tool calls in model response. Found {len(state.tool_calls)} tool call(s)")
            if not state.tool_calls:
                logger.debug("No tool calls found in model response")
                # Clean up thinking content from messages that had tool calls
                # Now that tool call loop is complete, thinking is no longer needed in context
                for msg_id in state.context_message_ids_to_purge_thinking:
                    await plugin_manager.get_plugin("context").update_message(
                        state.thread_id, state.user_id, msg_id,
                        {"reasoning_content": None}
                    )

                # No tool calls - send stream_end and break
                yield {
                    "event": "stream_end",
                    "data": {
                        "timestamp": datetime.now().isoformat()
                    }
                }
                break

            # Execute tools and loop back
            async for event in self._execute_tool_calls(state, state.tool_calls):
                yield event
