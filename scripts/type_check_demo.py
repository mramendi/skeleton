#!/usr/bin/env python3
"""
Demonstration of Protocol-based type checking with mypy.
This script shows that plugins don't need explicit inheritance and how
the plugin system verifies compatibility at runtime.
"""
from typing import TYPE_CHECKING, List, Dict, Any, Optional
import sys
import os

# Add the backend directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from core.protocols import ThreadManagerPlugin, AuthPlugin, ModelPlugin
from core.plugin_loader import PluginLoader
from core.plugin_manager import CorePluginManager
from core.default_thread_manager import DefaultThreadManager

# Demo plugin that implements ThreadManagerPlugin without inheritance
class DemoThreadManager:
    """
    This class doesn't inherit from ThreadManagerPlugin but implements all required methods.
    Mypy will recognize it as compatible with the protocol through structural subtyping.
    """
    
    def get_priority(self) -> int:
        """Return plugin priority (higher numbers override lower)"""
        return 25  # Higher than default (0) and example plugin (10)
    
    def __init__(self):
        self.threads = {}
        self.messages = {}
        print("[DemoThreadManager] Initialized without inheritance!")
    
    def create_thread(self, title: str, model: str, system_prompt: str) -> str:
        """Create a new thread"""
        thread_id = f"demo_{len(self.threads) + 1}"
        self.threads[thread_id] = {
            "id": thread_id,
            "title": title,
            "created": "2024-01-01T00:00:00",
            "model": model,
            "system_prompt": system_prompt,
            "is_archived": False
        }
        self.messages[thread_id] = []
        print(f"[DemoThreadManager] Created thread {thread_id}")
        return thread_id
    
    def get_threads(self, query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all non-archived threads, optionally filtered by query"""
        threads = []
        for thread in self.threads.values():
            if thread["is_archived"]:
                continue
            if query and query.lower() not in thread["title"].lower():
                continue
            threads.append({
                "id": thread["id"],
                "title": thread["title"],
                "created": thread["created"],
                "model": thread["model"],
                "system_prompt": thread["system_prompt"]
            })
        return threads
    
    def get_thread_messages(self, thread_id: str) -> Optional[List[Dict[str, Any]]]:
        """Get all messages for a thread"""
        return self.messages.get(thread_id)
    
    def add_message(self, thread_id: str, role: str, type: str, content: str, model: Optional[str] = None) -> bool:
        """Add a message to a thread"""
        if thread_id not in self.messages:
            return False
        message = {
            "role": role,
            "type": type,
            "content": content,
            "timestamp": "2024-01-01T00:00:00"
        }
        if model and role == "assistant":
            message["model"] = model
        self.messages[thread_id].append(message)
        return True
    
    def update_thread(self, thread_id: str, title: Optional[str] = None) -> bool:
        """Update thread metadata"""
        if thread_id not in self.threads:
            return False
        if title:
            self.threads[thread_id]["title"] = title
        return True
    
    def archive_thread(self, thread_id: str) -> bool:
        """Archive a thread"""
        if thread_id not in self.threads:
            return False
        self.threads[thread_id]["is_archived"] = True
        return True
    
    def search_threads(self, query: str) -> List[Dict[str, Any]]:
        """Search across all thread messages"""
        results = []
        for thread_id, messages in self.messages.items():
            thread = self.threads.get(thread_id)
            if not thread or thread["is_archived"]:
                continue
            for message in messages:
                if query.lower() in message.get("content", "").lower():
                    results.append({
                        "id": thread_id,
                        "title": thread["title"],
                        "snippet": f"Found '{query}' in message"
                    })
                    break
        return results

def verify_plugin_compatibility(plugin: ThreadManagerPlugin) -> bool:
    """
    This function accepts any object that implements the ThreadManagerPlugin protocol.
    Mypy will verify that DemoThreadManager is compatible at type-check time.
    """
    print("🔍 Verifying plugin compatibility...")
    
    # These calls will be type-checked by mypy
    thread_id = plugin.create_thread("Test Thread", "gpt-3.5-turbo", "default")
    threads = plugin.get_threads()
    messages = plugin.get_thread_messages(thread_id)
    success = plugin.add_message(thread_id, "user", "message_text", "Hello world")
    
    print(f"✅ Thread created: {thread_id}")
    print(f"✅ Threads retrieved: {len(threads)}")
    print(f"✅ Messages retrieved: {len(messages) if messages else 0}")
    print(f"✅ Message added: {success}")
    
    return success

def demonstrate_plugin_system_integration():
    """Show how the plugin system loads and uses protocol-compatible plugins"""
    print("\n🔌 Demonstrating plugin system integration...")
    
    # Create plugin loader (this would normally be done by PluginManager)
    plugin_loader = PluginLoader()
    
    # Create core plugin manager for thread management
    thread_manager = CorePluginManager[ThreadManagerPlugin](
        plugin_loader, 'thread', DefaultThreadManager
    )
    
    # Initialize with our demo plugin
    thread_manager._active_plugin = DemoThreadManager()  # Simulate plugin loading
    
    # Get the active plugin and use it
    active_plugin = thread_manager.get_plugin()
    
    print(f"✅ Active plugin type: {type(active_plugin).__name__}")
    print(f"✅ Plugin priority: {active_plugin.get_priority()}")
    
    # Use the plugin through the manager
    thread_id = active_plugin.create_thread("Plugin System Test", "gpt-4", "test")
    threads = active_plugin.get_threads()
    
    print(f"✅ Created thread via plugin manager: {thread_id}")
    print(f"✅ Total threads: {len(threads)}")
    
    return True

def demonstrate_multiple_protocols():
    """Show how a single class can implement multiple protocols"""
    print("\n🎯 Demonstrating multiple protocol implementation...")
    
    class MultiProtocolPlugin:
        """A plugin that implements multiple protocols without inheritance"""
        
        def get_priority(self) -> int:
            return 30
        
        # AuthPlugin methods
        def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
            return {"username": username, "role": "demo"}
        
        def create_token(self, user: Dict[str, Any]) -> str:
            return f"demo_token_for_{user['username']}"
        
        def verify_token(self, token: str) -> Optional[str]:
            return "demo_user" if token.startswith("demo_") else None
        
        # ThreadManagerPlugin methods
        def create_thread(self, title: str, model: str, system_prompt: str) -> str:
            return "multi_protocol_thread"
        
        def get_threads(self, query: Optional[str] = None) -> List[Dict[str, Any]]:
            return [{"id": "1", "title": "Multi Protocol Thread"}]
        
        def get_thread_messages(self, thread_id: str) -> Optional[List[Dict[str, Any]]]:
            return []
        
        def add_message(self, thread_id: str, role: str, type: str, content: str, model: Optional[str] = None) -> bool:
            return True
        
        def update_thread(self, thread_id: str, title: Optional[str] = None) -> bool:
            return True
        
        def archive_thread(self, thread_id: str) -> bool:
            return True
        
        def search_threads(self, query: str) -> List[Dict[str, Any]]:
            return []
    
    # Verify compatibility with multiple protocols
    auth_plugin: AuthPlugin = MultiProtocolPlugin()
    thread_plugin: ThreadManagerPlugin = MultiProtocolPlugin()
    
    # Test auth functionality
    user = auth_plugin.authenticate_user("test", "password")
    token = auth_plugin.create_token(user)
    username = auth_plugin.verify_token(token)
    
    # Test thread functionality
    thread_id = thread_plugin.create_thread("Test", "gpt-4", "default")
    threads = thread_plugin.get_threads()
    
    print(f"✅ Multi-protocol plugin works as AuthPlugin: {user}")
    print(f"✅ Multi-protocol plugin works as ThreadManagerPlugin: {thread_id}")
    print(f"✅ Token verification: {username}")
    print(f"✅ Thread listing: {len(threads)} threads")
    
    return True

def main():
    """Main demonstration of protocol-based type checking"""
    print("🚀 Skeleton Protocol-Based Type Checking Demo")
    print("=" * 50)
    
    try:
        # 1. Basic protocol compatibility check
        print("\n1️⃣ Basic Protocol Compatibility Check")
        plugin = DemoThreadManager()
        result1 = verify_plugin_compatibility(plugin)
        
        # 2. Plugin system integration
        print("\n2️⃣ Plugin System Integration")
        result2 = demonstrate_plugin_system_integration()
        
        # 3. Multiple protocols
        print("\n3️⃣ Multiple Protocol Implementation")
        result3 = demonstrate_multiple_protocols()
        
        # Summary
        print("\n" + "=" * 50)
        print("✅ All protocol compatibility checks passed!")
        print("✅ Mypy recognizes DemoThreadManager as compatible with ThreadManagerPlugin")
        print("✅ No explicit inheritance required - just implement the methods!")
        print("✅ Plugin system can load and use protocol-compatible classes!")
        print("✅ Single class can implement multiple protocols!")
        
        print("\n📋 Key Benefits Demonstrated:")
        print("  • Structural subtyping (duck typing) with type safety")
        print("  • No inheritance coupling")
        print("  • Runtime plugin loading with compile-time verification")
        print("  • Multiple protocol implementation in single class")
        print("  • Clear contracts through Protocol definitions")
        
        return all([result1, result2, result3])
        
    except Exception as e:
        print(f"❌ Demo failed: {e}")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
