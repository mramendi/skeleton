# Tools Support Documentation

## Overview

Skeleton supports OpenAI-compatible function calling through a flexible tool plugin system. Tools can extend the AI's capabilities by allowing it to execute external functions, call APIs, manipulate files, and perform other actions.

## Tool Types

### 1. Class-based Tools

Class-based tools provide full control over schema definition and execution logic. They implement the `ToolPlugin` protocol directly.

**Structure:**
```python
from backend.core.protocols import ToolPlugin
from typing import Dict, Any

class WeatherToolPlugin:
    """Weather information tool using external API"""
    
    def get_role(self) -> str:
        return "tool"
    
    def get_priority(self) -> int:
        return 0
    
    async def shutdown(self) -> None:
        pass
    
    def get_schema(self) -> Dict[str, Any]:
        """Return OpenAI function schema"""
        return {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather information for a location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "City name or coordinates"
                        },
                        "units": {
                            "type": "string",
                            "enum": ["celsius", "fahrenheit"],
                            "description": "Temperature units"
                        }
                    },
                    "required": ["location"]
                }
            }
        }
    
    async def execute(self, arguments: Dict[str, Any]) -> Any:
        """Execute the tool with validated arguments"""
        location = arguments.get("location")
        units = arguments.get("units", "celsius")
        
        # Call weather API
        weather_data = await self._call_weather_api(location, units)
        
        return {
            "location": location,
            "temperature": weather_data["temp"],
            "conditions": weather_data["conditions"],
            "units": units
        }
    
    async def _call_weather_api(self, location: str, units: str) -> Dict[str, Any]:
        """Internal method to call external weather API"""
        # Implementation here
        pass
```

**Advantages:**
- Full control over schema definition
- Custom validation logic
- Complex execution flows
- Error handling customization
- Async/await support throughout

### 2. Function-based Tools

Function-based tools are simpler to implement - just a plain Python function with type hints and a docstring. The system automatically generates the schema and handles validation.

**Structure:**
```python
from typing import Dict, Any

async def calculate_expression(expression: str) -> Dict[str, Any]:
    """
    Evaluate a mathematical expression safely.
    
    Args:
        expression: Mathematical expression to evaluate (e.g., "2 + 3 * 4")
    
    Returns:
        Dictionary with the result and any error information
    """
    import ast
    import operator
    
    try:
        # Safe expression evaluation
        node = ast.parse(expression, mode='eval')
        
        # Define allowed operators
        operators = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Pow: operator.pow,
            ast.Mod: operator.mod,
        }
        
        def eval_node(node):
            if isinstance(node, ast.Constant):  # Updated for Python 3.8+
                return node.value
            elif isinstance(node, ast.Num):  # Fallback for older Python
                return node.n
            elif isinstance(node, ast.BinOp):
                left = eval_node(node.left)
                right = eval_node(node.right)
                op_type = type(node.op)
                if op_type in operators:
                    return operators[op_type](left, right)
                else:
                    raise ValueError(f"Operator {op_type} not allowed")
            else:
                raise ValueError(f"Expression type {type(node)} not allowed")
        
        result = eval_node(node.body)
        
        return {
            "expression": expression,
            "result": result,
            "success": True
        }
        
    except Exception as e:
        return {
            "expression": expression,
            "error": str(e),
            "success": False
        }
```

**Advantages:**
- Minimal boilerplate
- Automatic schema generation
- Type hint validation
- Pydantic model generation
- Quick to implement

## Tool Installation

### Directory Structure

Place tool files in the `plugins/tools/` directory:

```
plugins/tools/
├── weather.py          # Class-based weather tool
├── calculator.py       # Function-based calculator
├── file_operations.py  # File manipulation tools
└── api_tools.py        # External API integration tools
```

### Loading Rules

1. **File-based Discovery**: The system scans all `.py` files in `plugins/tools/`
2. **Class Detection**: Classes with `get_schema()` and `execute()` methods are loaded as class-based tools
3. **Function Detection**: Functions with type hints are auto-wrapped as function-based tools
4. **Mutual Exclusion**: If a file contains class-based tools, function-based detection is skipped for that file
5. **Name Deduplication**: Tool names must be unique; duplicates are skipped with warnings

## Tool Execution Flow

### 1. Schema Registration

```python
# Tool manager collects schemas from all loaded tools
schemas = plugin_manager.tool.get_tool_schemas()
# Returns: [
#     {"type": "function", "function": {"name": "get_weather", ...}},
#     {"type": "function", "function": {"name": "calculate_expression", ...}}
# ]
```

### 2. Model Integration

```python
# Schemas passed to model during response generation
response = await model_plugin.generate_response(
    messages=context,
    model=model,
    system_prompt=system_prompt,
    tools=schemas  # Tool schemas included here
)
```

### 3. Tool Call Detection

The model returns tool calls in the streaming response. Tool calls may be streamed in chunks:

```python
{
    "event": "tool_calls",
    "data": {
        "tool_call": {
            "id": "call_123",
            "index": 0,
            "type": "function",
            "function": {
                "name": "get_weather",
                "arguments": '{"location": "New York", "units": "celsius"}'
            }
        }
    }
}
```

### 4. Tool Execution

```python
# Backend executes the tool
result = await plugin_manager.tool.execute_tool(
    "get_weather",
    {"location": "New York", "units": "celsius"}
)

# Result added to context for continued conversation
await context_plugin.add_message(thread_id, user_id, {
    "role": "tool",
    "tool_call_id": "call_123",
    "content": result
})
```

### 5. User Feedback

The user sees tool execution updates:

```
🔧 Calling get_weather({"location": "New York", "units": "celsius"})
✅ get_weather: {"location": "New York", "temperature": 22, "conditions": "sunny"}
```

## Error Handling

### Tool Execution Errors

The system automatically handles tool execution errors:

1. **Error Detection**: Exceptions during tool execution are caught
2. **User Notification**: Error messages are streamed to the user with ❌ prefix
3. **Context Storage**: Errors are stored in conversation history for the model
4. **Result Sanitization**: Binary data and non-serializable objects are handled gracefully

```python
# Example error handling in the message processor
try:
    result = await plugin_manager.tool.execute_tool(function_name, args_dict)
    # Success case - result streamed to user
except Exception as e:
    # Error case - error message streamed and stored
    error_result = f"Error executing tool {function_name}: {str(e)}"
    # Both streamed to user and stored in context
```

### Data Validation

- **Function-based tools**: Automatic Pydantic validation before execution using type hints
- **Class-based tools**: Manual validation in `execute()` method
- **Binary data**: Automatically detected and converted to error messages
- **JSON serialization**: Handled automatically, with fallback for non-serializable data
- **Argument parsing**: JSON arguments are parsed, with error handling for malformed JSON

## Best Practices

### 1. Input Validation

```python
# Function-based: Use type hints
async def safe_divide(a: float, b: float) -> Dict[str, Any]:
    """Divide two numbers safely"""
    if b == 0:
        return {"error": "Division by zero", "success": False}
    return {"result": a / b, "success": True}

# Class-based: Manual validation
async def execute(self, arguments: Dict[str, Any]) -> Any:
    a = arguments.get("a")
    b = arguments.get("b")
    
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        raise ValueError("Both arguments must be numbers")
    
    if b == 0:
        raise ValueError("Division by zero")
    
    return a / b
```

### 2. Error Messages

```python
# Provide clear, actionable error messages
return {
    "error": "Invalid location format. Expected city name or lat,lon coordinates.",
    "example": "Valid formats: 'New York' or '40.7128,-74.0060'"
}
```

### 3. Async Operations

```python
# Always use async for I/O operations
async def fetch_url(url: str) -> Dict[str, Any]:
    """Fetch content from a URL"""
    import aiohttp
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                return {
                    "url": url,
                    "status": response.status,
                    "content": await response.text()
                }
    except Exception as e:
        return {"error": f"Failed to fetch {url}: {str(e)}"}
```

### 4. Resource Management

```python
# Clean up resources properly
class FileToolPlugin:
    async def execute(self, arguments: Dict[str, Any]) -> Any:
        filepath = arguments.get("filepath")
        
        try:
            # Use context managers for file operations
            async with aiofiles.open(filepath, 'r') as f:
                content = await f.read()
            
            return {"filepath": filepath, "content": content}
            
        except FileNotFoundError:
            return {"error": f"File not found: {filepath}"}
        except PermissionError:
            return {"error": f"Permission denied: {filepath}"}
```

## Testing Tools

### Unit Testing

```python
import pytest
from plugins.tools.calculator import calculate_expression

@pytest.mark.asyncio
async def test_calculator():
    # Test successful calculation
    result = await calculate_expression("2 + 3 * 4")
    assert result["success"] is True
    assert result["result"] == 14
    
    # Test error handling
    result = await calculate_expression("2 / 0")
    assert result["success"] is False
    assert "error" in result
```

### Integration Testing

```python
# Test tool execution through the plugin manager
async def test_tool_execution():
    result = await plugin_manager.tool.execute_tool(
        "calculate_expression",
        {"expression": "10 + 5"}
    )
    
    assert result["success"] is True
    assert result["result"] == 15
```

## Security Considerations

1. **Input Validation**: Always validate and sanitize user inputs
2. **Resource Limits**: Implement timeouts and size limits for external operations
3. **Permission Checks**: Verify tool execution is allowed for the user
4. **Error Information**: Don't expose sensitive system information in error messages
5. **File Access**: Restrict file system access to safe directories
6. **Network Calls**: Use allowlists for external API endpoints

## Advanced Features

### Tool Composition

Tools can call other tools through the plugin manager:

```python
from backend.core.plugin_manager import plugin_manager

async def complex_analysis(data: str) -> Dict[str, Any]:
    """Perform complex analysis using multiple tools"""
    
    # Call calculator tool
    calc_result = await plugin_manager.tool.execute_tool(
        "calculate_expression",
        {"expression": f"len('{data}') * 2"}
    )
    
    # Call weather tool if data contains location
    if "location" in data.lower():
        weather_result = await plugin_manager.tool.execute_tool(
            "get_weather",
            {"location": extract_location(data)}
        )
    
    return {
        "analysis": data,
        "calculated_value": calc_result.get("result"),
        "weather": weather_result if "weather_result" in locals() else None
    }
```


This tool system provides a flexible foundation for extending Skeleton's capabilities while maintaining security and reliability.
