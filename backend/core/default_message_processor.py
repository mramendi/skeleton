"""
Default message processor implementation.
This plugin orchestrates the entire message-handling flow, including thread
management, model interaction, and response streaming.
"""
from typing import Dict, Any, Optional, AsyncGenerator
import logging
import json
import uuid
from datetime import datetime

from .protocols import MessageProcessorPlugin

logger = logging.getLogger("skeleton.message_processor")

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
        model_plugin = plugin_manager.get_plugin("model")
        context_plugin = plugin_manager.get_plugin("context")

        # Step 1: Handle thread creation/retrieval
        if not thread_id:
            # Create new thread
            thread_id = await thread_plugin.create_thread(
                title=content[:50] + "..." if len(content) > 50 else content,
                model=model or "default",
                system_prompt=system_prompt or "default",
                user=user_id
            )
            logger.info(f"Created new thread {thread_id}")
        else:
            # Verify thread exists and user has access
            messages = await thread_plugin.get_thread_messages(thread_id, user_id)
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
                "thread_id": thread_id,
                "timestamp": datetime.now().isoformat()
            }
        }

        # Step 2: Add user message to history
        await thread_plugin.add_message(
            thread_id=thread_id,
            user=user_id,
            role="user",
            type="message_text",  # Keep this for internal storage, but won't be in model context
            content=content
        )

        # Step 3: Check if context exists, then either regenerate or add message
        context = await context_plugin.get_context(thread_id, user_id)
        if context is None:
            # No cached context - regenerate from history (which already has the user message)
            context = await context_plugin.regenerate_context(thread_id, user_id)
            if context:
                logger.info(f"Regenerated context for thread {thread_id} with {len(context)} messages")
            else:
                logger.warning(f"Failed to regenerate context for thread {thread_id}")
        else:
            # Context exists - add the new user message to it
            user_message = {
                "role": "user",
                "content": content,
                "timestamp": datetime.now().isoformat()
            }
            await context_plugin.add_message(thread_id, user_id, user_message)

        # we read the context later, inside the loop


        # Step 4: Resolve system prompt using system prompt plugin
        system_prompt_plugin = plugin_manager.get_plugin("system_prompt")
        actual_system_prompt = await system_prompt_plugin.get_prompt(system_prompt or "default")

        # If prompt not found or is "zero", use None (no system prompt)
        if actual_system_prompt == "" or actual_system_prompt is None:
            actual_system_prompt = None

        # Step 5: Main conversation loop - handle multiple rounds of tool calls
        # Get available tool schemas from tool manager
        tool_schemas = plugin_manager.tool.get_tool_schemas()

        # thinking is saved to context only for the duretion of the tool call, then purged from the messages
        context_message_ids_to_purge_thinking=[]

        while True:
            # Refresh context at the start of each loop iteration to get latest tool results
            context = await context_plugin.get_context(thread_id, user_id)
            logger.debug(f"Loop iteration: Retrieved fresh context with {len(context) if context else 0} messages")

            # (a) Send current context to model and process response
            current_thinking = ""
            current_response = ""
            total_thinking = ""
            total_response = ""
            tool_calls = []
            last_content_type = None
            response_metadata = {}

            try:
                async for event in model_plugin.generate_response(
                    messages=context,
                    model=model,
                    system_prompt=actual_system_prompt,
                    tools=tool_schemas
                ):
                    # Debug: Log all events from model
                    logger.debug(f"Received event from model: {event.get('event', 'unknown')} - {event.get('data', {})}")
                    if event.get("event") == "thinking_tokens":
                        # Stream thinking tokens to client
                        yield event

                        # Save previous content if switching to thinking
                        if last_content_type == "message" and current_response:
                            # Save previous response when switching to thinking
                            await thread_plugin.add_message(
                                thread_id=thread_id,
                                user=user_id,
                                role="assistant",
                                type="message_text",
                                content=current_response,
                                model=model
                            )
                            current_response = ""
                        elif last_content_type != "thinking" and current_thinking:
                            # Save previous thinking if switching back to thinking
                            await thread_plugin.add_message(
                                thread_id=thread_id,
                                user=user_id,
                                role="thinking",
                                type="message_text",
                                content=current_thinking,
                                model=model
                            )
                            current_thinking = ""

                        # Accumulate thinking content
                        current_thinking += event["data"]["content"]
                        total_thinking += event["data"]["content"]
                        last_content_type = "thinking"

                    elif event.get("event") == "message_tokens":
                        # Stream message tokens to client
                        yield event

                        # Save previous thinking segment if we're switching to response
                        if last_content_type == "thinking" and current_thinking:
                            await thread_plugin.add_message(
                                thread_id=thread_id,
                                user=user_id,
                                role="thinking",
                                type="message_text",
                                content=current_thinking,
                                model=model
                            )
                            current_thinking = ""

                        # Save previous response segment if we're switching back to response
                        elif last_content_type != "message" and current_response:
                            await thread_plugin.add_message(
                                thread_id=thread_id,
                                user=user_id,
                                role="assistant",
                                type="message_text",
                                content=current_response,
                                model=model
                            )
                            current_response = ""

                        # Accumulate response content
                        current_response += event["data"]["content"]
                        total_response += event["data"]["content"]
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
                                    for call in tool_calls:
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
                                        tool_calls.append(new_call)

                            logger.debug(f"Accumulated tool calls: {tool_calls}")
                            # Log detailed structure of each tool call for debugging
                            for i, call in enumerate(tool_calls):
                                logger.debug(f"Tool call {i}: {call}")
                                if 'function' in call:
                                    func = call['function']
                                    logger.debug(f"  Function: {func}")
                                    logger.debug(f"  Name: {func.get('name', 'MISSING')}")
                                    logger.debug(f"  Arguments: {func.get('arguments', 'MISSING')}")
                            logger.info(f"Model requested {len(tool_calls)} tool call(s): {[call.get('function', {}).get('name', 'unknown') for call in tool_calls]}")

                    elif event.get("event") == "stream_end":
                        # Log metadata if present
                        if event.get("data", {}).get("metadata"):
                            response_metadata = event["data"]["metadata"]
                            logger.info(f"Response metadata for thread {thread_id}: {response_metadata}")

                            # Check if tool calls are in the metadata
                            if "tool_calls" in response_metadata:
                                tool_calls_from_metadata = response_metadata["tool_calls"]
                                logger.info(f"Found {len(tool_calls_from_metadata)} tool call(s) in stream_end metadata: {[call.get('function', {}).get('name', 'unknown') for call in tool_calls_from_metadata]}")
                                if isinstance(tool_calls_from_metadata, dict):
                                    tool_calls_from_metadata = [tool_calls_from_metadata]
                                tool_calls.extend(tool_calls_from_metadata)

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
            message_id = None
            if response_metadata:
                # Try to get ID from metadata (OpenAI response format)
                if 'id' in response_metadata:
                    message_id = response_metadata['id']
                    logger.info(f"Using message ID from metadata: {message_id}")

            # we need to know the message ID for the context here anyway because we might need to save it
            if not message_id: message_id = str(uuid.uuid4())

            if current_thinking:
                await thread_plugin.add_message(
                    thread_id=thread_id,
                    user=user_id,
                    role="thinking",
                    type="message_text",
                    content=current_thinking,
                    model=model
                )
            if current_response:
                await thread_plugin.add_message(
                    thread_id=thread_id,
                    user=user_id,
                    role="assistant",
                    type="message_text",
                    content=current_response,
                    model=model
                )

            # save the message to the context
            # if tool calls exist, add thinking and tool call and mark for thinking purging
            # Use the message ID from metadata if available
            assistant_message = {
                "role": "assistant",
                "content": total_response,
                "model": model,
                "timestamp": datetime.now().isoformat()
            }

            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
                assistant_message["reasoning_content"] = total_thinking
                context_message_ids_to_purge_thinking.append(message_id)


            await context_plugin.add_message(thread_id, user_id, assistant_message, message_id)

            # (b) Check if tool calls exist - if not, we're done
            logger.debug(f"Checking for tool calls in model response. Found {len(tool_calls)} tool call(s)")
            if not tool_calls:
                logger.debug("No tool calls found in model response")
                # Clean up thinking content from messages that had tool calls
                # Now that tool call loop is complete, thinking is no longer needed in context
                for msg_id in context_message_ids_to_purge_thinking:
                    await context_plugin.update_message(
                        thread_id, user_id, msg_id,
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

            # Process all tool calls at once
            valid_tool_calls = []
            for tool_call in tool_calls:
                # Validate tool call structure
                if not isinstance(tool_call, dict):
                    logger.error(f"Invalid tool call (not a dict): {tool_call}")
                    continue

                if "function" not in tool_call:
                    logger.error(f"Tool call missing 'function' field: {tool_call}")
                    continue

                function = tool_call["function"]
                if not isinstance(function, dict):
                    logger.error(f"Tool call 'function' is not a dict: {function}")
                    continue

                function_name = function.get("name")
                if not function_name:
                    logger.error(f"Tool call missing function name: {tool_call}")
                    continue

                function_args = function.get("arguments", "")
                if not function_args:
                    logger.error(f"Tool call missing arguments: {tool_call}")
                    continue

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
                break

            logger.info(f"Processing {len(valid_tool_calls)} valid tool calls")

            for tool_call in valid_tool_calls:
                function_name = tool_call["function"]["name"]
                function_args = tool_call["function"]["arguments"]
                call_id = tool_call.get("id", str(uuid.uuid4()))  # Use model's ID or generate one

                # Stream "tool called" event (formatted on backend)
                tool_called_event = {
                    "event": "tool_update",
                    "data": {
                        "call_id": call_id,
                        "content": f"🔧 Calling {function_name}({function_args})",
                        "timestamp": datetime.now().isoformat()
                    }
                }
                yield tool_called_event

                # Save tool call to history immediately with call_id
                await thread_plugin.add_message(
                    thread_id=thread_id,
                    user=user_id,
                    role="tool",
                    type="tool_update",
                    content=tool_called_event["data"]["content"],
                    aux_id=call_id,
                    model=model
                )

                try:
                    # Execute the tool
                    import json
                    args_dict = json.loads(function_args) if isinstance(function_args, str) else function_args
                    logger.info(f"Executing tool '{function_name}' with arguments: {args_dict}")
                    result = await plugin_manager.tool.execute_tool(function_name, args_dict)
                    logger.info(f"Tool '{function_name}' executed successfully, result type: {type(result).__name__}")

                    # Truncate result for display
                    result_str = str(result)
                    display_result = result_str
                    if len(result_str) > 250:
                        display_result = result_str[:247] + "..."

                    # Stream "call completed" event (formatted on backend)
                    tool_completed_event = {
                        "event": "tool_update",
                        "data": {
                            "call_id": call_id,
                            "content": f"✅ {function_name}: {display_result}",
                            "timestamp": datetime.now().isoformat()
                        }
                    }
                    yield tool_completed_event

                    # Save tool result to history immediately with call_id
                    await thread_plugin.add_message(
                        thread_id=thread_id,
                        user=user_id,
                        role="tool",
                        type="tool_update",
                        content=tool_completed_event["data"]["content"],
                        aux_id=call_id,
                        model=model
                    )

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

                    # Add tool result to context in OpenAI format
                    tool_result_message = {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": sanitized_result
                    }
                    await context_plugin.add_message(thread_id, user_id, tool_result_message)

                except Exception as e:
                    logger.error(f"Error executing tool {function_name}: {e}", exc_info=True)
                    error_result = f"Error executing tool {function_name}: {str(e)}"

                    # Stream error event (formatted on backend)
                    tool_error_event = {
                        "event": "tool_update",
                        "data": {
                            "call_id": call_id,
                            "content": f"❌ {function_name}: {error_result}",
                            "timestamp": datetime.now().isoformat()
                        }
                    }
                    yield tool_error_event

                    # Save tool error to history immediately with call_id
                    await thread_plugin.add_message(
                        thread_id=thread_id,
                        user=user_id,
                        role="tool",
                        type="tool_update",
                        content=tool_error_event["data"]["content"],
                        aux_id=call_id,
                        model=model
                    )

                    # Sanitize error result as well
                    try:
                        # Check if error result contains binary data
                        if isinstance(error_result, str) and any(ord(c) < 32 and c not in '\t\n\r' for c in error_result):
                            sanitized_error = f"Error: Tool execution failed and returned binary data. {function_name}: {str(e)}"
                        else:
                            sanitized_error = error_result
                    except Exception:
                        sanitized_error = f"Error: Tool execution failed for {function_name}"

                    # Add error to context - CRITICAL: This must include tool_call_id for the model to understand
                    tool_result_message = {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", "unknown"),  # Ensure we have the tool_call_id
                        "content": sanitized_error
                    }
                    logger.debug(f"Adding tool error to context: {tool_result_message}")
                    await context_plugin.add_message(thread_id, user_id, tool_result_message)

            # Loop continues - go back with updated context that includes tool results
