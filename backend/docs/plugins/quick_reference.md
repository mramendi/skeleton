# Plugin Quick Reference

Quick reference for creating and managing Skeleton plugins.

## Plugin Types Overview

| Type | Purpose | Directory | Protocol | Priority Range |
|------|---------|-----------|----------|----------------|
| **Core** | Replace system components | `plugins/core/` | Role-specific | 0-1000+ |
| **Function** | Modify request context | `plugins/functions/` | FunctionPlugin | 0-100+ |
| **Tool** | Add AI capabilities | `plugins/tools/` | ToolPlugin | N/A |

## Quick Start Checklist

1. **Choose plugin type** based on what you want to customize
2. **Create plugin file** in correct directory
3. **Implement required methods** from protocol
4. **Set appropriate priority** (higher = more likely to be used)
5. **Test your plugin** before deployment
6. **Monitor logs** for plugin loading and execution

## Core Plugin Protocols

### Authentication Plugin
```python
def get_role(self) -> str: return "auth"
def get_priority(self) -> int: return 10  # Higher than default (0)
def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]
def create_token(self, user: Dict[str, Any]) -> str
def verify_token(self, token: str) -> Optional[str]
def request_allowed(self, username: str, model_name: str) -> bool
```

### Model Plugin
```python
def get_role(self) -> str: return "model"
def get_priority(self) -> int: return 20  # Higher than default (0)
async def get_available_models(self) -> List[str]
async def generate_response(self, messages: List[Dict[str, Any]], model: str, system_prompt: str) -> AsyncGenerator[Dict[str, Any], None]
```

### Thread Manager Plugin
```python
def get_role(self) -> str: return "thread"
def get_priority(self) -> int: return 15  # Higher than default (0)
async def create_thread(self, title: str, model: str, system_prompt: str, user: str) -> str
async def get_threads(self, user: str, query: Optional[str] = None) -> List[Dict[str, Any]]
async def get_thread_messages(self, thread_id: str, user: str) -> Optional[List[Dict[str, Any]]]
async def add_message(self, thread_id: str, user: str, role: str, type: str, content: str, model: Optional[str] = None) -> bool
async def update_thread(self, thread_id: str, user: str, title: Optional[str] = None) -> bool
async def archive_thread(self, thread_id: str, user: str) -> bool
async def search_threads(self, query: str, user: str) -> List[Dict[str, Any]]
```

### Store Plugin
```python
def get_role(self) -> str: return "store"
def get_priority(self) -> int: return 25  # Higher than default (0)
async def create_store_if_not_exists(self, store_name: str, schema: Dict[str, str], cacheable: bool = False) -> bool
async def add(self, user_id: str, store_name: str, data: Dict[str, Any], record_id: Optional[str] = None) -> str
async def get(self, user_id: str, store_name: str, record_id: str, load_collections: bool = False) -> Optional[Dict[str, Any]]
async def update(self, user_id: str, store_name: str, record_id: str, updates: Dict[str, Any], partial: bool = True) -> bool
async def delete(self, user_id: str, store_name: str, record_id: str) -> bool
async def find(self, user_id: str, store_name: str, filters: Dict[str, Any] = None, limit: Optional[int] = None, offset: int = 0, order_by: str = None, order_desc: bool = False) -> List[Dict[str, Any]]
async def collection_append(self, user_id: str, store_name: str, record_id: str, field_name: str, item: Any) -> int
async def collection_get(self, user_id: str, store_name: str, record_id: str, field_name: str, limit: Optional[int] = None, offset: int = 0) -> List[Any]
```

## Function Plugin Protocol


**NOT WORKING, NOT FINAL**

```python
def get_name(self) -> str: return "your_plugin_name"
def get_priority(self) -> int: return 50  # 0-100 range
async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
    # Modify context and return updates
    return {"new_field": "value"}
```

## Tool Plugin Protocol

**NOT WORKING YET BUT (at least function-based) UNLIKELY TO CHANGE**

```python
# Function-based (recommended)
def your_tool(param1: str, param2: int = 42) -> Dict[str, Any]:
    """Tool description for AI.

    Args:
        param1: Description of parameter 1
        param2: Description of parameter 2 (default: 42)

    Returns:
        Dictionary with tool results
    """
    return {"result": f"Processed {param1} with {param2}"}

# Class-based (legacy)
def get_schema(self) -> Dict[str, Any]:
    return {"type": "function", "function": {"name": "tool_name", ...}}
async def execute(self, arguments: Dict[str, Any]) -> Any:
    return {"result": "tool output"}
```

## Common Patterns

### Environment Configuration
```python
def __init__(self):
    self.api_key = os.getenv("YOUR_API_KEY")
    if not self.api_key:
        raise RuntimeError("YOUR_API_KEY not set")
```

### Error Handling
```python
try:
    # Your logic
    return {"success": True, "data": result}
except Exception as e:
    return {"error": f"Operation failed: {str(e)}"}
```

### Fallback Implementation
```python
def get_available_models(self) -> List[str]:
    try:
        return await self._fetch_from_api()
    except Exception:
        return ["gpt-3.5-turbo", "gpt-4"]  # Fallback
```

### Request Context Access
```python
async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
    user = context.get("user", "unknown")
    message = context.get("user_message", "")
    model = context.get("model", "default")
    # Your logic here
```

## Priority Guidelines

| Priority | Use Case | Examples |
|----------|----------|----------|
| 0-10 | Default plugins | Core system defaults |
| 10-30 | Simple overrides | Basic auth, simple models |
| 30-60 | Enhanced features | Advanced auth, multi-provider |
| 60-90 | Security/Critical | Rate limiting, content filtering |
| 90-100 | Preprocessing | User context, request modification |
| 100+ | Test/Override | High-priority test plugins
