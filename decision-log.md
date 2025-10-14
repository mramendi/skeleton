# Architectural Decision Log

## Overview
This document captures key architectural decisions made during the development of this LLM-based application framework. The system is designed as a modular, plugin-based platform that can be extended and customized for different deployment scenarios.

## Core Architecture Decisions

### 1. Plugin-Based Architecture
**Date**: Initial design
**Decision**: Adopt a comprehensive plugin-based architecture for all core system components
**Rationale**:
- Enables flexible extension and customization without modifying core code
- Supports both built-in defaults and custom plugins
- Allows swapping implementations based on deployment needs
- Provides clear separation of concerns

**Implementation**:
- Core protocols defined in `backend/core/protocols.py`
- Plugin discovery and loading handled by `backend/core/plugin_loader.py`
- Plugin priority system ensures deterministic override behavior
- Support for multiple plugin types: core, function, and tool plugins

### 2. Authentication System Design
**Date**: Initial design
**Decision**: Pluggable authentication system with JWT-based stateless sessions
**Rationale**:
- Security requirements vary significantly across deployments
- Enables integration with existing enterprise auth systems (LDAP, OAuth, etc.)
- Stateless JWT tokens scale well in distributed environments
- Default implementation provides basic password hashing for development

**Components**:
- `backend/core/default_auth.py` - Basic in-memory auth with bcrypt
- `plugins/example_auth_plugin.py` - Enhanced auth with user roles and JWT
- `scripts/generate_jwt_secret.py` - Cryptographically secure secret generation

### 3. Thread Management Abstraction
**Date**: Initial design
**Decision**: Abstract thread management with pluggable persistence backends
**Rationale**:
- Different storage needs across deployments (memory, SQLite, PostgreSQL, Redis)
- Enables conversation history management and search
- Supports both transient and persistent storage
- User isolation is built into the protocol

**Components**:
- `backend/core/default_thread_manager.py` - Basic in-memory implementation
- `plugins/database_thread_manager_plugin.py` - SQLite-based persistence with search
- `plugins/example_thread_manager_plugin.py` - Enhanced logging and metrics

### 4. Model Client Abstraction
**Date**: Initial design
**Decision**: Abstract model client to support multiple LLM providers
**Rationale**:
- Vendor flexibility (OpenAI, Anthropic, local models, etc.)
- Consistent interface across different providers
- Easy to add new providers without changing application code
- Fallback model system for reliability

**Components**:
- `backend/core/default_model_client.py` - OpenAI SDK implementation
- Supports fallback models when primary is unavailable

### 5. Tool System Architecture
**Date**: Initial design
**Decision**: Dual-mode tool system supporting both class-based and function-based implementations
**Rationale**:
- Function-based tools are simpler for developers (just write a Python function)
- Class-based tools provide more control for complex scenarios
- Automatic schema generation reduces boilerplate and errors
- Type hints enable automatic validation and documentation

**Implementation**:
- `backend/core/protocols.py` - ToolPlugin protocol definition
- `backend/core/plugin_loader.py` - FunctionToolWrapper for auto-conversion
- `plugins/example_tool_plugin.py` - Example weather tool implementation
- Uses llmio.function_parser for automatic schema generation

### 6. Function Plugin System
**Date**: Initial design
**Decision**: Function plugins for request/response context modification
**Rationale**:
- Enables request preprocessing (add context, logging, etc.)
- Allows response post-processing (filtering, formatting, etc.)
- Supports adding user-specific context or system-wide logging
- Priority-based execution order

**Components**:
- `plugins/example_function_plugin.py` - User context and logging examples
- Priority system ensures deterministic execution order

### 7. Security Architecture
**Date**: Initial design
**Decision**: Multi-layered security with pluggable components
**Rationale**:
- Different security requirements for different environments
- Enables integration with enterprise security systems
- Provides secure defaults while allowing customization

**Security Features**:
- JWT tokens with configurable expiration
- bcrypt password hashing
- User isolation in thread management
- Input validation and sanitization
- Secure secret generation

### 8. Development and Deployment Strategy
**Date**: Initial design
**Decision**: Example-first development with working implementations
**Rationale**:
- Provides clear usage patterns for developers
- Reduces onboarding friction for new team members
- Demonstrates best practices and common patterns
- Enables rapid prototyping and testing

**Evidence**:
- All plugin types have comprehensive example implementations
- Examples show both simple and advanced usage patterns
- Working database implementation demonstrates persistence
- Security examples show production-ready patterns

## Plugin Directory Structure

---

## Session 2 Decisions

### 9. Model Client: OpenAI SDK Direct Integration
**Date**: 2025-10-14
**Decision**: Use OpenAI SDK directly instead of llmio wrapper clients
**Context**:
- Initial implementation used llmio's OpenAI and Gemini client wrappers
- Discovery: llmio's GeminiClient just wraps AsyncOpenAI internally
- llmio lacks `list_models()` method - had to access internal `_client` attribute
- Main value of llmio is in `function_parser`, not client wrappers

**Rationale**:
- Direct OpenAI SDK provides full API access (models.list(), better error handling)
- Eliminates unnecessary abstraction layer
- Better documentation and community support for OpenAI SDK
- LiteLLM proxy provides multi-provider support at infrastructure level
- Preserve llmio only for its function_parser capabilities

**Implementation**:
- `backend/core/default_model_client.py` - Refactored to use AsyncOpenAI directly
- Uses `client.models.list()` for dynamic model discovery
- Supports custom base URL via `OPENAI_BASE_URL` for LiteLLM proxy
- Tool calling stubs added with TODO comments for future implementation
- `backend/requirements.txt` - Added `openai` as explicit dependency

**Migration Path**:
- Removed: llmio OpenAIClient and GeminiClient usage
- Kept: llmio for `function_parser.model_from_function()` tool schema generation
- Created: `default_model_client.py.backup` preserving original llmio implementation

### 10. Tool System: Function-Based Plugin Auto-Conversion
**Date**: 2025-10-14
**Decision**: Support plain Python functions as tool plugins with automatic schema generation
**Context**:
- Original llmio research goal: Auto-generate OpenAI function specs from Python functions
- Risk of losing this knowledge when cycling AI assistants
- Need to preserve research findings in working code

**Rationale**:
- Significantly reduces boilerplate for tool development
- Type hints provide automatic validation via Pydantic
- Docstrings become function descriptions in OpenAI schema
- Developers can write simple functions instead of complex classes
- Pydantic validation happens automatically before function execution

**Implementation**:
- `backend/core/plugin_loader.py`:
  - Added `FunctionToolWrapper` class to convert functions to ToolPlugin protocol
  - Uses `llmio.function_parser.model_from_function()` for Pydantic model generation
  - Auto-generates OpenAI function schema from Pydantic model JSON schema
  - `_load_tool_plugins()` detects both class-based and function-based tools
  - Validates function has type hints before attempting conversion
- `backend/core/protocols.py`:
  - Updated `ToolPlugin` protocol documentation with both implementation styles
  - Added example showing function-based tool pattern

**Example**:
```python
# Function-based tool (recommended)
def get_weather(location: str, unit: str = "celsius") -> dict:
    '''Get weather for a location.

    Args:
        location: City name
        unit: Temperature unit
    '''
    return {"temp": 20, "unit": unit}

# Automatically converted to:
# - Pydantic model with validation
# - OpenAI function schema
# - ToolPlugin protocol implementation
```

### 11. Frontend: Markdown Rendering with Streaming Support
**Date**: 2025-10-14
**Decision**: Integrate marked.js and highlight.js for markdown rendering in chat interface
**Context**:
- Original frontend displayed only plain text (using Alpine.js `x-text`)
- AI responses often include formatting (bold, code blocks, lists, etc.)
- Need to preserve formatting while supporting streaming responses

**Rationale**:
- marked.js is lightweight (~30KB), well-maintained, CDN-ready
- highlight.js provides syntax highlighting for code blocks
- Both libraries work well with Alpine.js reactive framework
- Streaming-compatible: markdown re-renders on each content update
- GitHub Flavored Markdown provides familiar formatting

**Implementation**:
- `frontend/index.html`:
  - Added CDN scripts: marked.js, highlight.js, DOMPurify
  - Changed `x-text="message.content"` to `x-html="renderMarkdown(message.content)"`
  - Added `renderMarkdown()` method with DOMPurify sanitization
  - Configured marked.js to use highlight.js for code block syntax highlighting
  - Added comprehensive CSS for markdown elements (code, lists, tables, blockquotes, etc.)

**Security**:
- DOMPurify sanitizes HTML to prevent XSS attacks
- Removes dangerous tags (`<script>`, `<iframe>`, etc.)
- Strips event handlers (`onclick`, `onerror`, etc.)
- Allows safe markdown HTML elements

**Streaming Behavior**:
- Content accumulated in `assistantMessage.content` as tokens arrive
- `renderMarkdown()` called on entire accumulated string each update
- Visual artifacts possible during streaming (incomplete code blocks, visible asterisks)
- Final result always correctly formatted once streaming completes

**Supported Markdown**:
- Bold, italic, inline code
- Code blocks with syntax highlighting (language-specific)
- Ordered and unordered lists
- Tables with headers
- Blockquotes
- Headings (H1-H6)
- Links
- Line breaks (GitHub-style)

### 12. Tool Calling: Stub Implementation Pattern
**Date**: 2025-10-14
**Decision**: Add tool calling stubs with comprehensive TODO comments, avoid yielding tool_call events
**Context**:
- Tool calling infrastructure needed but basic chat flow not yet stable
- Frontend currently expects only `message_tokens` and `stream_end` events
- Risk of breaking existing functionality with premature tool implementation

**Rationale**:
- Preserve research findings in code comments to avoid information loss
- Document exact implementation steps for future development
- Prevent breaking frontend by NOT yielding tool_call events yet
- Enable quick implementation later when basic flow is validated

**Implementation**:
- `backend/core/default_model_client.py`:
  - Added `tools` parameter to `generate_response()` method
  - Pass tools to OpenAI API request when provided
  - TODO comments at lines 172-186 documenting full workflow:
    1. Parse tool call from `delta.tool_calls`
    2. Get tool plugin from PluginManager
    3. Validate arguments with Pydantic model (from llmio.function_parser)
    4. Execute tool function
    5. Add tool result to message history
    6. Continue conversation with tool result
  - Explicitly noted: Don't yield tool_call events to frontend (would break UI)

**Future Work**:
- Implement server-side tool execution loop
- Add tool results to conversation history
- Only stream final assistant response to frontend
- Consider adding tool execution status UI elements

### 13. Protocol Design: Async by Default for I/O Protocols
**Date**: 2025-10-14
**Decision**: Make protocols async when they may involve I/O operations in plugin implementations
**Context**:
- Initial `ModelPlugin.get_available_models()` was sync, causing event loop blocking issues
- `ThreadManagerPlugin` methods were sync, but database-backed plugins need async
- FastAPI runs in async context - sync I/O operations block all concurrent requests
- Need to support both in-memory (fast) and database-backed (I/O) implementations

**Problem with Sync Protocols**:
```python
# Sync protocol forces database plugins to use workarounds
class ThreadManagerPlugin(Protocol):
    def get_threads(self, user: str) -> List[Dict]: ...

# Database implementation stuck with ugly workaround
class DatabaseThreadManager:
    def get_threads(self, user: str):
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(db.query(...))  # Blocks event loop!
```

**Rationale**:
- Protocol defines the **interface contract**, not implementation details
- Async protocols allow both sync (in-memory) and async (I/O) implementations
- In-memory implementations can be async without performance penalty
- Database/network implementations can properly use await
- Prevents event loop blocking in FastAPI context

**Implementation**:
- `backend/core/protocols.py`:
  - `ModelPlugin.get_available_models()` → async
  - All `ThreadManagerPlugin` methods → async
  - Added documentation explaining async requirement
- `backend/core/default_model_client.py`:
  - Changed to `async def get_available_models()`
  - Uses `await self.client.models.list()` directly
  - Removed event loop detection workarounds
- `backend/core/default_thread_manager.py`:
  - All methods converted to async
  - In-memory operations return immediately (no performance impact)
- `backend/main.py`:
  - Added `await` to all protocol method calls
  - Enables concurrent request handling without blocking

**Design Principle**:
> "Design protocols for the most demanding use case (I/O), not the simplest (in-memory)"

**Benefits**:
- Database plugins can use proper async database drivers (asyncpg, motor, etc.)
- Network-backed plugins can use async HTTP clients
- No event loop blocking - requests handled concurrently
- In-memory plugins work fine as async (minimal overhead)
- Clean, consistent async/await patterns throughout

**Alternative Considered**:
- Keep protocols sync, use `run_in_executor()` for I/O
- Rejected: More complex, less performant, inconsistent patterns

### 14. Frontend: Dual Scroll Mode Architecture
**Date**: 2025-10-14
**Decision**: Implement toggleable scroll modes (Fixed Scroll vs Document Scroll) in single HTML file
**Context**:
- Different users have different preferences for chat interface scrolling behavior
- Fixed scroll mode maximizes screen real estate efficiency
- Document scroll mode provides familiar infinite-scroll experience
- Need to support both without maintaining two separate codebases

**Two Modes**:

1. **Fixed Scroll Mode** (default):
   - Chat messages scroll within a constrained container
   - Thread list scrolls independently in sidebar
   - Input field fixed at bottom of chat container
   - Header and sidebar remain stationary
   - Most efficient use of screen space

2. **Document Scroll Mode**:
   - Entire page scrolls naturally like a document
   - Sidebar position: fixed (stays visible while scrolling)
   - Input field position: sticky at bottom of viewport
   - Familiar "infinite scroll" behavior

**Implementation**:

**CSS Architecture**:
```css
/* Base (Fixed Scroll Mode) */
html, body { height: 100%; }
body { display: flex; flex-direction: column; overflow: hidden; }
#app-container { height: 100%; display: flex; flex-direction: column; }
.main-container { flex: 1 1 0; overflow: hidden; }
.chat-container { flex: 1 1 0; display: flex; flex-direction: column; }
.chat-messages { flex: 1 1 0; overflow-y: auto; } /* Scrollable box */
.chat-input-container { flex-shrink: 0; } /* Fixed at bottom */

/* Document Scroll Mode Override */
body.document-scroll { height: auto; overflow: auto !important; }
body.document-scroll .sidebar { position: fixed; }
body.document-scroll .chat-container { margin-left: 300px; }
body.document-scroll .chat-messages { overflow-y: visible; }
body.document-scroll .chat-input-container { position: sticky; bottom: 0; }
```

**Key Flexbox Techniques**:
- `flex: 1 1 0` - Grow to fill space, shrink if needed, 0 base size
- `flex-shrink: 0` - Prevent headers/footers from collapsing
- `min-height: 0` - Allow flex children to shrink below content size
- `overflow: hidden` on containers - Constrain scrollable children
- Complete height chain: `html → body → #app-container → .main-container → .chat-container`

**User Preference Persistence**:
- Toggle button in sidebar shows current mode
- Choice saved to `localStorage`
- Auto-loaded on page initialization
- Seamless switching without page reload

**Smart Scroll Behavior**:
```javascript
scrollToBottom() {
    if (this.documentScrollMode) {
        window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
    } else {
        this.$refs.messagesContainer.scrollTop = this.$refs.messagesContainer.scrollHeight;
    }
}
```

**Critical Flexbox Debugging Lessons**:
1. **Height must propagate**: `html` → `body` → all containers must have explicit height
2. **Flex children collapse without base**: Use `flex: 1 1 0` not just `flex: 1`
3. **Overflow hidden on parent**: Required to constrain scrollable flex children
4. **Prevent shrinking**: Use `flex-shrink: 0` on fixed-height elements
5. **Min-height quirk**: Flex children have implicit `min-height: auto` that prevents scrolling

**Files Modified**:
- `frontend/index.html`:
  - Added dual-mode CSS with `.document-scroll` class overrides
  - Added `#app-container` height constraints
  - Fixed complete height chain from html to chat-messages
  - Added toggle button with mode indicator
  - Implemented `toggleLayout()` and `scrollToBottom()` methods
  - Added localStorage persistence

**Benefits**:
- ✅ Single HTML file - no code duplication
- ✅ User choice respected and persisted
- ✅ Works correctly with streaming markdown rendering
- ✅ Responsive to browser window resizing
- ✅ Each mode optimized for its use case

**UI/UX Details**:
- Toggle button: Gray, labeled "Mode: [Current Mode]"
- Fixed Scroll: Maximizes visible messages, familiar app-like interface
- Document Scroll: Natural reading flow, input always accessible
- Smooth auto-scroll in both modes during AI response streaming

### 15. Test System: Mock-Based Testing Without API Keys
**Date**: 2025-10-14
**Decision**: Implement comprehensive test suite using mock plugins instead of real API services
**Context**:
- Need automated testing before architectural changes
- Cannot hardcode API keys into test framework
- No free/public API endpoints available for LLM testing
- Want fast, deterministic tests that work offline

**Rationale**:
- Plugin architecture naturally supports mock implementations
- Mock plugins provide deterministic, repeatable test results
- No external dependencies means tests run fast and offline
- In-memory thread manager already eliminates need for database mocking
- Test plugins can simulate various scenarios (errors, different models, etc.)

**Implementation**:
- `backend/tests/test_auth_plugin.py`:
  - Hardcoded test users (testuser1, testuser2, admin)
  - Deterministic tokens (test-token-user1, etc.)
  - Simple SHA256 password hashing (not for production)
  - Priority 1000 to override default auth in tests
- `backend/tests/test_model_plugin.py`:
  - Returns fake streaming responses without API calls
  - Simulates different model behaviors (fast, smart, creative)
  - Can simulate errors when message contains "error"
  - Configurable responses based on system prompt
- `backend/tests/conftest.py`:
  - Fixture to swap in mock plugins before each test
  - Provides pre-authenticated clients (synchronous and async)
  - Auto-cleanup of thread storage between tests
  - Uses `httpx.ASGITransport` for async streaming tests
- `backend/pytest.ini` - Configuration with test markers and coverage settings
- `backend/requirements-test.txt` - Test dependencies (pytest, pytest-asyncio, pytest-cov, httpx)
- `backend/run_tests.sh` - Convenient test runner script

**Test Coverage**:
- 37 tests covering auth, models, threads, and plugins
- ~2.4 second runtime for full suite
- 62% overall code coverage
- 100% coverage on protocols, auth tests, plugin tests
- Organized with pytest markers: `@pytest.mark.unit`, `@pytest.mark.api`, etc.

**Test Categories**:
1. Authentication (9 tests): Login, logout, token validation, access control
2. Model endpoints (8 tests): Streaming, different models, input validation
3. Thread management (8 tests): Creation, updates, archiving, search, user isolation
4. Plugin system (12 tests): Priority, functionality, credentials helpers

**Key Benefits**:
- ✅ No API keys required - completely self-contained
- ✅ Fast execution - no network calls or database queries
- ✅ Deterministic results - same output every time
- ✅ Works offline - no external dependencies
- ✅ Easy to extend - clear fixture patterns
- ✅ Isolated tests - auto-cleanup prevents test interference

**Documentation**:
- `backend/tests/README.md` - Comprehensive testing guide with examples
- Includes test user credentials, fixture documentation, debugging tips
- Coverage breakdown by module with improvement suggestions

**Design Pattern**:
> "Use the plugin system's own flexibility to enable testing - mock plugins are just plugins with high priority"

**Future Improvements**:
- Add plugin loader tests (function-based tool conversion, discovery)
- Add default auth tests (bcrypt, JWT generation)
- Add default model client tests (OpenAI SDK integration with mocking)
- Increase coverage target to 80%+

### 16. Use `pluggable` directory for reasy/sample plugins
**Date**: 2025-10-14
**Decision**: Plugin files maintained with the project moved to `pluggable` directory
**Context**:
- Git-ignoring `.plugins` to enable users to keep a repo of their own plugins
- *Directory structure for `plugins` and `pluggable` to be confirmed*
