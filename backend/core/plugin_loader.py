"""
PluginLoader discovers and loads plugins from the plugins/ directory.
This is a low-level component that PluginManager uses - other code should
use PluginManager instances rather than calling PluginLoader directly.
"""
import os
import importlib.util
import inspect
import logging
import asyncio
from typing import Dict, List, Any, Optional, Callable
from pathlib import Path
from .protocols import CorePlugin, FunctionPlugin, ToolPlugin

try:
    from llmio.function_parser import model_from_function
    LLMIO_AVAILABLE = True
except ImportError:
    LLMIO_AVAILABLE = False
    logger = logging.getLogger("skeleton.plugin_loader")
    logger.warning("llmio.function_parser not available - function-based tools will not work")

logger = logging.getLogger("skeleton.plugin_loader")


class FunctionToolWrapper:
    """
    Wrapper that converts a plain Python function into a ToolPlugin.

    Uses llmio.function_parser.model_from_function() to:
    1. Auto-generate Pydantic model from function signature
    2. Create OpenAI function schema from the Pydantic model
    3. Validate arguments before calling the function

    Example function:
        def get_weather(location: str, unit: str = "celsius") -> dict:
            '''Get weather for a location.

            Args:
                location: City name
                unit: Temperature unit
            '''
            return {"temp": 20, "unit": unit}
    """

    def __init__(self, func: Callable):
        self.func = func
        self.func_name = func.__name__
        self.is_method = self._is_method(func)

        if not LLMIO_AVAILABLE:
            raise ImportError("llmio.function_parser is required for function-based tools")

        # For methods, store a reference to the class for later instantiation
        if self.is_method:
            # Get the class from the function's __qualname__ when we first create the wrapper
            class_name = self.func.__qualname__.split('.')[0]
            module = inspect.getmodule(self.func)
            if module and hasattr(module, class_name):
                self.method_class = getattr(module, class_name)
                # Store a strong reference to the module to prevent garbage collection
                self._module = module
            else:
                # If we can't get the class now, we'll try to find it later
                self.method_class = None
                self._class_name = class_name
                self._module = module
        else:
            self.method_class = None

        # Generate Pydantic model from function signature
        # For methods, we need to create a wrapper that excludes 'self'
        if self.is_method:
            # Create a wrapper function that excludes 'self' parameter
            self.wrapped_func = self._create_method_wrapper(func)
            self.pydantic_model = model_from_function(self.wrapped_func)
        else:
            self.wrapped_func = func
            self.pydantic_model = model_from_function(func)

        # Generate OpenAI schema from Pydantic model
        # The model has a model_json_schema() method that returns JSON schema
        self.schema = self._generate_openai_schema()

    def _generate_openai_schema(self) -> Dict[str, Any]:
        """Generate OpenAI function schema from Pydantic model"""
        # Get JSON schema from Pydantic model
        json_schema = self.pydantic_model.model_json_schema()

        # Convert to OpenAI function schema format
        openai_schema = {
            "type": "function",
            "function": {
                "name": self.func_name,
                "description": self.func.__doc__ or f"Call {self.func_name}",
                "parameters": {
                    "type": "object",
                    "properties": json_schema.get("properties", {}),
                    "required": json_schema.get("required", [])
                }
            }
        }

        return openai_schema

    def get_schema(self) -> Dict[str, Any]:
        """Return OpenAI function schema (implements ToolPlugin protocol)"""
        return self.schema

    async def execute(self, arguments: Dict[str, Any]) -> Any:
        """
        Execute tool with arguments (implements ToolPlugin protocol).

        Validates arguments with Pydantic model before calling the function.
        For class methods, creates an instance and calls the method on it.
        """
        # Validate arguments with Pydantic model
        validated_args = self.pydantic_model(**arguments)

        if self.is_method:
            # For methods, we need to create an instance of the class
            # and call the method on it
            
            if hasattr(self.func, '__self__'):
                # Bound method - use the existing instance
                instance = self.func.__self__
            else:
                # Unbound method - create a new instance
                # Use the stored class reference if we have it
                if self.method_class is not None:
                    method_class = self.method_class
                    logger.debug(f"Using stored class reference: {method_class}")
                else:
                    # Fallback: try to find the class using stored info
                    class_name = self._class_name
                    module = self._module
                    
                    if module is not None and hasattr(module, class_name):
                        method_class = getattr(module, class_name)
                        logger.debug(f"Found class using stored module: {method_class}")
                    else:
                        # Last resort: search all modules
                        import sys
                        for module_name, module_obj in sys.modules.items():
                            if hasattr(module_obj, class_name):
                                method_class = getattr(module_obj, class_name)
                                logger.debug(f"Found class by searching modules: {method_class}")
                                break
                        else:
                            raise AttributeError(f"Could not find class {class_name} in any loaded module")
                
                instance = method_class()
            
            # Call the method on the instance with validated arguments
            method = getattr(instance, self.func_name)
            result = await method(**validated_args.model_dump())
        else:
            # Call the function with validated arguments
            # Convert Pydantic model to dict for **kwargs unpacking
            result = self.func(**validated_args.model_dump())
            if asyncio.iscoroutine(result):
                result = await result

        return result

    def _is_method(self, func: Callable) -> bool:
        """Check if the function is a method (has 'self' parameter)"""
        try:
            sig = inspect.signature(func)
            params = list(sig.parameters.keys())
            return params and params[0] == 'self'
        except Exception:
            return False

    def _create_method_wrapper(self, func: Callable) -> Callable:
        """Create a wrapper function that excludes 'self' parameter for schema generation"""
        import inspect
        from typing import get_type_hints
        
        def wrapper(*args, **kwargs):
            # This wrapper is only used for schema generation, not execution
            pass
        
        # Get the original signature
        sig = inspect.signature(func)
        params = list(sig.parameters.values())
        
        # Remove 'self' parameter if present
        if params and params[0].name == 'self':
            params = params[1:]
        
        # Create new signature without 'self'
        wrapper.__signature__ = sig.replace(parameters=params)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        
        # Preserve type hints from original function, excluding 'self'
        try:
            original_hints = get_type_hints(func)
            # Remove 'self' from type hints if present
            if 'self' in original_hints:
                original_hints.pop('self')
            # Apply type hints to wrapper
            wrapper.__annotations__ = original_hints
        except Exception as e:
            logger.debug(f"Could not preserve type hints for wrapper: {e}")
        
        return wrapper


class PluginLoader:
    """
    PluginLoader discovers and loads plugins from the plugins/ directory.
    
    This is a low-level plugin discovery system. For actual plugin usage,
    use the PluginManager class which provides clean interfaces to active plugins.
    
    PLUGIN DISCOVERY PROCESS:
    ------------------------
    1. Scan plugins/ subdirectories (core/, functions/, tools/)
    2. Import Python modules and find classes implementing protocols
    3. Sort plugins by priority (highest first)
    4. Return lists of plugin instances for PluginManager to use

    DIRECTORY STRUCTURE:
    -------------------
    plugins/
    ├── core/           # Override core functionality (auth, models, thread, etc.)
    │   ├── my_auth.py     # Must contain class implementing AuthPlugin
    │   ├── my_models.py   # Must contain class implementing ModelPlugin
    │   └── my_threads.py  # Must contain class implementing ThreadManagerPlugin
    ├── functions/      # Add function plugins (modify request context)
    │   └── logging.py     # Must contain class implementing FunctionPlugin
    └── tools/          # Add tool plugins (OpenAI function calling)
        ├── weather.py     # Can be a class implementing ToolPlugin (legacy)
        └── calculator.py  # Or a plain Python function with type hints (recommended)

    TOOL PLUGIN STYLES:
    ------------------
    Tools support two implementation styles:

    1. Class-based (legacy): Classes implementing ToolPlugin protocol
    2. Function-based (recommended): Plain Python functions with type hints

    Function-based tools are auto-converted using llmio.function_parser.
    """
    
    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.core_plugins: Dict[str, List[Any]] = {}
        self.function_plugins: List[FunctionPlugin] = []
        self.tool_plugins: List[ToolPlugin] = []
        self.plugin_filenames: Dict[int, str] = {}  # Map plugin id to filename for tiebreaking

    def load_plugins(self):
        """Load all plugins from plugins directory"""
        logger.info(f"PluginLoader: Loading plugins from {self.plugins_dir}")
        
        if not self.plugins_dir.exists():
            logger.warning(f"PluginLoader: Plugins directory {self.plugins_dir} does not exist")
            return

        logger.info("PluginLoader: Directory exists, starting plugin loading...")

        # Load core plugins
        logger.info("Loading core plugins...")
        self._load_core_plugins()
        logger.info(f"Loaded {len(self.core_plugins)} core plugin types: {list(self.core_plugins.keys())}")

        # Load function plugins
        logger.info("Loading function plugins...")
        self._load_function_plugins()
        logger.info(f"Loaded {len(self.function_plugins)} function plugins")

        # Load tool plugins
        logger.info("Loading tool plugins...")
        self._load_tool_plugins()
        logger.info(f"Loaded {len(self.tool_plugins)} tool plugins")
        
        logger.info("PluginLoader: All plugin loading completed")

    def _load_core_plugins(self):
        """
        Load core plugins that can override default implementations.
        
        This method discovers and loads core plugins from the plugins/core/ directory.
        Core plugins are special plugins that can replace the default implementations
        of core system components (auth, model, thread, store).
        
        The loading process works as follows:
        1. Scan plugins/core/ directory for .py files (excluding __init__.py)
        2. For each file, dynamically import it as a Python module
        3. Inspect the module for classes that implement the CorePlugin interface
        4. Instantiate each plugin class and group them by their declared role
        5. Sort plugins within each role by priority (highest first)
        
        Plugin classes are identified by having both get_role() and get_priority() methods.
        The get_role() method declares which system component the plugin wants to replace.
        The get_priority() method determines which plugin wins when multiple plugins target the same role.
        """
        core_dir = self.plugins_dir / "core"
        if not core_dir.exists():
            logger.debug("Core plugins directory not found - no core plugins to load")
            return

        logger.info(f"Loading core plugins from: {core_dir}")

        # Step 1: Scan for Python files in the core directory
        for plugin_file in core_dir.glob("*.py"):
            # Skip special files like __init__.py
            if plugin_file.name.startswith("__"):
                logger.debug(f"Skipping special file: {plugin_file.name}")
                continue

            logger.debug(f"Processing core plugin file: {plugin_file}")

            # Step 2: Dynamically import the Python file as a module
            # Create a unique module name to avoid conflicts
            module_name = f"plugin_{plugin_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            module = importlib.util.module_from_spec(spec)
            
            try:
                # Execute the module to load its classes into memory
                spec.loader.exec_module(module)
                logger.debug(f"Successfully imported module: {module_name}")
            except Exception as e:
                logger.error(f"Failed to import module {module_name}: {e}", exc_info=True)
                continue

            # Step 3: Inspect the module for plugin classes
            for name, obj in inspect.getmembers(module, inspect.isclass):
                # Skip private classes and classes imported from other modules
                if name.startswith("_") or obj.__module__ != module.__name__:
                    continue

                logger.debug(f"Found class in module: {name}")

                # Step 4: Check if class implements CorePlugin interface
                # Use isinstance() for proper duck typing against the CorePlugin protocol
                try:
                    # Try to instantiate and check if it implements CorePlugin
                    plugin_instance = obj()
                    if isinstance(plugin_instance, CorePlugin):
                        # Get the role this plugin wants to handle
                        role = plugin_instance.get_role()
                        
                        logger.debug(f"Plugin '{name}' declares role: '{role}'")

                        # Step 5: Group plugins by their role
                        # Initialize the list for this role if it doesn't exist
                        if role not in self.core_plugins:
                            self.core_plugins[role] = []

                        # Add the plugin instance to the appropriate role group
                        self.core_plugins[role].append(plugin_instance)
                        
                        # Store the filename for this plugin instance (for tiebreaking)
                        self.plugin_filenames[id(plugin_instance)] = plugin_file.name
                        
                        logger.info(f"Loaded core plugin '{name}' for role '{role}' (priority: {plugin_instance.get_priority()}, file: {plugin_file.name})")

                except Exception as e:
                    logger.error(f"Failed to instantiate or validate plugin class '{name}': {e}", exc_info=True)
                else:
                    logger.debug(f"Class '{name}' does not implement CorePlugin protocol - skipping")

        # Step 6: Sort plugins within each role by priority (descending), then by filename (ascending)
        # This makes plugin selection deterministic: higher priority wins, ties broken alphabetically by filename
        for role in self.core_plugins:
            plugins_for_role = self.core_plugins[role]
            
            # Sort by priority (descending), then by filename (ascending) as tiebreaker
            plugins_for_role.sort(key=lambda p: (-p.get_priority(), self.plugin_filenames.get(id(p), p.__class__.__name__)))
            
            # Log the final priority order for debugging
            plugin_names = [f"{p.__class__.__name__}({p.get_priority()}, {self.plugin_filenames.get(id(p), 'unknown')})" for p in plugins_for_role]
            logger.debug(f"Core plugins for role '{role}' (sorted by priority, then filename): {plugin_names}")
            
            # Check for priority ties and log the tiebreaker
            if len(plugins_for_role) > 1:
                top_priority = plugins_for_role[0].get_priority()
                top_priority_plugins = [p for p in plugins_for_role if p.get_priority() == top_priority]
                if len(top_priority_plugins) > 1:
                    tied_plugin_names = [
                        f"{p.__class__.__name__}({self.plugin_filenames.get(id(p), 'unknown')})" 
                        for p in top_priority_plugins
                    ]
                    selected_plugin = top_priority_plugins[0]
                    selected_filename = self.plugin_filenames.get(id(selected_plugin), 'unknown')
                    logger.info(
                        f"PRIORITY TIE for role '{role}': {len(top_priority_plugins)} plugins share "
                        f"the highest priority ({top_priority}): {tied_plugin_names}. "
                        f"Using filename tiebreaker: '{selected_plugin.__class__.__name__}' from '{selected_filename}' selected."
                    )
            
            # The first plugin in the list will be the active one
            active_plugin = plugins_for_role[0]
            logger.info(f"Selected active plugin for role '{role}': {active_plugin.__class__.__name__} (file: {self.plugin_filenames.get(id(active_plugin), 'unknown')})")

    def _load_function_plugins(self):
        """
        Load function plugins that modify request context.
        """
        functions_dir = self.plugins_dir / "functions"
        if not functions_dir.exists():
            return

        for plugin_file in functions_dir.glob("*.py"):
            if plugin_file.name.startswith("__"):
                continue

            module_name = f"function_{plugin_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find function plugin classes
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if hasattr(obj, 'execute') and hasattr(obj, 'get_name'):
                    plugin_instance = obj()
                    self.function_plugins.append(plugin_instance)

        # Sort by priority
        self.function_plugins.sort(key=lambda p: p.get_priority(), reverse=True)

    def _load_tool_plugins(self):
        """
        Load tool plugins for OpenAI function calling.

        Supports two plugin styles:
        1. Class-based (legacy): Classes implementing ToolPlugin protocol
        2. Function-based (recommended): Plain Python functions with type hints

        Function-based tools are auto-converted using llmio.function_parser.
        Tool names are deduplicated - if duplicate names are found, the first one
        loaded wins and subsequent duplicates are skipped with a warning.
        """
        tools_dir = self.plugins_dir / "tools"
        logger.debug(f"Loading tool plugins from directory: {tools_dir}")
        logger.debug(f"Tools directory exists: {tools_dir.exists()}")
        
        if not tools_dir.exists():
            logger.warning(f"Tools directory {tools_dir} does not exist - no tools will be loaded")
            return
        
        # List all files in tools directory for debugging
        all_files = list(tools_dir.glob("*"))
        logger.debug(f"All files in tools directory: {[f.name for f in all_files]}")

        # Track tool names to prevent duplicates
        loaded_tool_names = set()
        py_files = list(tools_dir.glob("*.py"))
        logger.debug(f"Found Python files in tools directory: {[f.name for f in py_files]}")

        for plugin_file in py_files:
            if plugin_file.name.startswith("__"):
                logger.debug(f"Skipping special file: {plugin_file.name}")
                continue
            
            logger.debug(f"Processing tool file: {plugin_file}")

            module_name = f"tool_{plugin_file.stem}"
            logger.debug(f"Importing module '{module_name}' from {plugin_file}")
            
            try:
                spec = importlib.util.spec_from_file_location(module_name, plugin_file)
                if spec is None or spec.loader is None:
                    logger.error(f"Failed to create spec for module {module_name} from {plugin_file}")
                    continue
                    
                module = importlib.util.module_from_spec(spec)
                
                # IMPORTANT: Add module to sys.modules to prevent garbage collection
                # This ensures the module and its classes remain accessible
                import sys
                sys.modules[module_name] = module
                
                spec.loader.exec_module(module)
                logger.debug(f"Successfully imported module {module_name} and added to sys.modules")
            except Exception as e:
                logger.error(f"Failed to import module {module_name} from {plugin_file}: {e}", exc_info=True)
                continue

            # Find tool plugin classes (legacy class-based approach)
            logger.debug(f"Scanning module {module_name} for classes...")
            all_classes = inspect.getmembers(module, inspect.isclass)
            logger.debug(f"Found classes in {module_name}: {[name for name, obj in all_classes if not name.startswith('_') and obj.__module__ == module.__name__]}")
            
            for name, obj in all_classes:
                # Skip private classes and classes imported from other modules
                if name.startswith("_") or obj.__module__ != module.__name__:
                    logger.debug(f"Skipping class {name} (private or imported from another module)")
                    continue
                
                logger.debug(f"Examining class {name} for tool plugin interface...")
                has_execute = hasattr(obj, 'execute')
                has_get_schema = hasattr(obj, 'get_schema')
                logger.debug(f"Class {name} has execute: {has_execute}, has get_schema: {has_get_schema}")
                
                if has_execute and has_get_schema:
                    logger.debug(f"Class {name} appears to be a tool plugin - attempting to instantiate...")
                    try:
                        plugin_instance = obj()
                        logger.debug(f"Successfully instantiated class {name}")
                        
                        tool_schema = plugin_instance.get_schema()
                        logger.debug(f"Got schema from {name}: {tool_schema}")
                        tool_name = tool_schema.get('name')
                        
                        if not tool_name:
                            logger.warning(f"Class-based tool {name} has no name in schema - skipping")
                            continue
                        
                        if tool_name in loaded_tool_names:
                            logger.warning(f"Duplicate tool name '{tool_name}' found in class {name} from {plugin_file.name} - skipping")
                            continue
                        
                        self.tool_plugins.append(plugin_instance)
                        loaded_tool_names.add(tool_name)
                        logger.info(f"Loaded class-based tool plugin: {name} (name: {tool_name})")
                    except Exception as e:
                        logger.error(f"Error processing class-based tool {name}: {e}", exc_info=True)
                else:
                    logger.debug(f"Class {name} does not implement required tool methods - skipping")

            # Find plain Python functions (function-based approach)
            # Only check for function-based tools if no class-based tools were found in this file
            class_based_tools_found = any(
                hasattr(obj, 'execute') and hasattr(obj, 'get_schema')
                for name, obj in inspect.getmembers(module, inspect.isclass)
                if not name.startswith("_") and obj.__module__ == module.__name__
            )
            
            logger.debug(f"Class-based tools found in {module_name}: {class_based_tools_found}")
            logger.debug(f"LLMIO_AVAILABLE for function-based tools: {LLMIO_AVAILABLE}")
            
            if not class_based_tools_found:
                logger.debug(f"Scanning module {module_name} for functions and methods...")
                
                # First, scan for standalone functions
                all_functions = inspect.getmembers(module, inspect.isfunction)
                logger.debug(f"Found functions in {module_name}: {[name for name, _ in all_functions if not name.startswith('_') and obj.__module__ == module.__name__]}")
                
                # Then, scan for methods within classes (but not classes that implement ToolPlugin)
                all_classes = inspect.getmembers(module, inspect.isclass)
                for class_name, class_obj in all_classes:
                    # Skip private classes and classes imported from other modules
                    if class_name.startswith("_") or class_obj.__module__ != module.__name__:
                        continue
                    
                    # Skip classes that already implement ToolPlugin (these are handled above)
                    if hasattr(class_obj, 'execute') and hasattr(class_obj, 'get_schema'):
                        logger.debug(f"Skipping class {class_name} for method scanning - it's already a ToolPlugin")
                        continue
                    
                    logger.debug(f"Scanning class {class_name} for methods...")
                    class_methods = inspect.getmembers(class_obj, inspect.ismethod)
                    class_functions = inspect.getmembers(class_obj, inspect.isfunction)
                    
                    # Combine both methods and functions (unbound methods appear as functions)
                    all_class_methods = [(name, obj) for name, obj in class_methods + class_functions 
                                        if not name.startswith("_")]
                    
                    if all_class_methods:
                        logger.debug(f"Found methods in class {class_name}: {[name for name, _ in all_class_methods]}")
                        all_functions.extend(all_class_methods)
                
                for name, obj in all_functions:
                    # Skip private functions and imports from other modules
                    if name.startswith("_") or (hasattr(obj, '__module__') and obj.__module__ != module.__name__):
                        logger.debug(f"Skipping function/method {name} (private or imported from another module)")
                        continue

                    logger.debug(f"Examining function/method {name} for tool conversion...")
                    
                    # Check if function has type hints (required for auto-generation)
                    try:
                        signature = inspect.signature(obj)
                        logger.debug(f"Function {name} signature: {signature}")
                        
                        has_type_hints = any(
                            param.annotation != inspect.Parameter.empty
                            for param in signature.parameters.values()
                        )
                        logger.debug(f"Function {name} has type hints: {has_type_hints}")
                        
                        if not has_type_hints:
                            logger.debug(f"Function {name} has no type hints - skipping")
                            continue
                            
                    except Exception as e:
                        logger.error(f"Error inspecting function {name} signature: {e}")
                        continue

                    if has_type_hints and LLMIO_AVAILABLE:
                        logger.debug(f"Attempting to wrap function {name} as tool...")
                        try:
                            # Wrap function with FunctionToolWrapper
                            wrapped_tool = FunctionToolWrapper(obj)
                            tool_schema = wrapped_tool.get_schema()
                            logger.debug(f"Generated schema for function {name}: {tool_schema}")
                            tool_name = tool_schema.get('function', {}).get('name')
                            
                            if not tool_name:
                                logger.warning(f"Function-based tool {name} has no name in schema - skipping")
                                continue
                            
                            if tool_name in loaded_tool_names:
                                logger.warning(f"Duplicate tool name '{tool_name}' found in function {name} from {plugin_file.name} - skipping")
                                continue
                            
                            self.tool_plugins.append(wrapped_tool)
                            loaded_tool_names.add(tool_name)
                            logger.info(f"Loaded function-based tool plugin: {name} (name: {tool_name})")
                        except Exception as e:
                            logger.error(f"Error wrapping function {name} as tool: {e}", exc_info=True)
                    elif has_type_hints:
                        logger.warning(f"Function {name} has type hints but llmio.function_parser is not available - install llmio to use function-based tools")
            else:
                logger.debug(f"Skipping function-based tool detection in {plugin_file.name} because class-based tools were found")
        
        logger.info(f"Tool loading complete. Loaded {len(self.tool_plugins)} unique tools: {sorted(loaded_tool_names)}")

    def get_core_plugin(self, plugin_type: str) -> Optional[Any]:
        """
        Get the highest priority core plugin of given type.
        
        Returns None if no plugins of this type are loaded.
        """
        if plugin_type in self.core_plugins and self.core_plugins[plugin_type]:
            return self.core_plugins[plugin_type][0]  # Highest priority (first in sorted list)
        return None

    def get_function_plugins(self) -> List[FunctionPlugin]:
        """Get all function plugins sorted by priority"""
        return self.function_plugins

    def get_tool_plugins(self) -> List[ToolPlugin]:
        """Get all tool plugins"""
        return self.tool_plugins

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get all tool schemas for OpenAI function calling"""
        schemas = []
        for tool in self.tool_plugins:
            try:
                schema = tool.get_schema()
                schemas.append(schema)
            except Exception as e:
                logger.error(f"Error getting schema from tool plugin: {e}", exc_info=True)
        return schemas
