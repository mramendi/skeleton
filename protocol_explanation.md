# Skeleton Type System - Protocol-Based Design

## Overview

Skeleton uses Python's `typing.Protocol` for its plugin system, which enables **structural subtyping** (duck typing) rather than nominal subtyping (inheritance-based).

## Key Benefits

1. **No Inheritance Required**: Plugins don't need to inherit from abstract base classes
2. **Type Safety**: Mypy verifies compatibility at type-check time
3. **Flexibility**: Plugins can inherit from any base class or mix multiple protocols
4. **Clear Contracts**: Protocols explicitly define what methods are required

## How It Works

### Protocol Definition
```python
# In protocols.py
class ThreadManagerPlugin(Protocol):
    def get_priority(self) -> int: ...
    def create_thread(self, title: str, model: str, system_prompt: str) -> str: ...
    # ... other methods
```

### Plugin Implementation (No Inheritance!)
```python
# In your plugin file
class MyCustomThreadManager:
    def get_priority(self) -> int:
        return 10
    
    def create_thread(self, title: str, model: str, system_prompt: str) -> str:
        # Your implementation
        return "thread_123"
    # ... implement all other required methods
```

### Type Checking
```bash
# Run mypy to verify type compatibility
cd backend
mypy plugins/your_plugin.py

# Or run the demo script
python ../scripts/type_check_demo.py
```

## Verification Process

1. **Static Analysis**: Mypy checks that your plugin implements all required methods
2. **Signature Validation**: Method signatures must match the protocol exactly
3. **Return Type Checking**: Return types must be compatible
4. **Runtime Loading**: PluginLoader verifies the plugin has the required methods

## Example: Creating a Type-Safe Plugin

```python
# plugins/my_thread_manager.py
from typing import List, Dict, Any, Optional
from datetime import datetime

class DatabaseThreadManager:  # Note: No inheritance!
    """Thread manager that uses a real database"""
    
    def get_priority(self) -> int:
        return 20  # Higher priority than default
    
    def create_thread(self, title: str, model: str, system_prompt: str) -> str:
        # Your database implementation
        return self.db.create_thread(title, model, system_prompt)
    
    # ... implement all other required methods
```

## Type Checking Commands

```bash
# Check a specific plugin
mypy plugins/my_plugin.py

# Check all plugins
mypy plugins/

# Check the entire backend
mypy backend/

# Run with verbose output
mypy -v plugins/my_plugin.py
```

## Common Type Errors and Solutions

### Error: "Missing method 'get_priority'"
**Solution**: Add the missing method with correct signature

### Error: "Return type 'str' is not compatible with 'int'"
**Solution**: Fix the return type annotation

### Error: "Argument 1 has incompatible type"
**Solution**: Ensure parameter types match the protocol

## Advantages Over Traditional Inheritance

1. **Multiple Protocols**: A plugin can implement multiple protocols
2. **Existing Classes**: You can adapt existing classes without modification
3. **Clear Separation**: No coupling to base classes
4. **Documentation**: Protocols serve as living documentation

## Best Practices

1. **Use Type Annotations**: Always annotate method parameters and return types
2. **Run Mypy**: Type-check your plugins before deployment
3. **Follow Protocols**: Implement all methods exactly as defined
4. **Document Deviations**: If you extend functionality, document it clearly

## Testing Type Compatibility

```python
# In your plugin tests
from core.protocols import ThreadManagerPlugin
from my_plugin import MyThreadManager

def test_plugin_compatibility():
    # This will fail mypy if MyThreadManager doesn't implement the protocol
    plugin: ThreadManagerPlugin = MyThreadManager()
    
    # Test all methods
    thread_id = plugin.create_thread("Test", "gpt-3.5-turbo", "default")
    assert isinstance(thread_id, str)
```

This approach gives you the flexibility of duck typing with the safety of static type checking!
