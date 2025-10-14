# Skeleton API Test Suite

Comprehensive test suite for the Skeleton API with **no API keys required**.

## Overview

This test suite uses **mock plugins** instead of real API services:
- `TestAuthPlugin` - Provides hardcoded test users (testuser1, testuser2, admin)
- `TestModelPlugin` - Returns fake LLM responses without calling any external APIs
- Uses existing `DefaultThreadManager` (already in-memory)

**No API keys, no external services, fast tests.**

## Key Features

✅ **No API keys required** - All tests use mock plugins
✅ **Fast execution** - ~2.4 seconds for full suite
✅ **Isolated tests** - Auto-cleanup between tests
✅ **Comprehensive** - Covers auth, models, threads, plugins
✅ **Easy to extend** - Clear fixture patterns
✅ **No external dependencies** - Works offline

## Installation

```bash
# Install test dependencies
cd backend
pip install -r requirements-test.txt
```

## Running Tests

### Using the test runner script (recommended)

```bash
# From the kimi directory (with venv)
source venv/bin/activate
cd backend
./run_tests.sh all

# Or run specific categories
./run_tests.sh fast      # Unit tests only
./run_tests.sh auth      # Auth tests
./run_tests.sh api       # API tests
./run_tests.sh coverage  # Generate coverage report
```

### Using pytest directly

```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=. --cov-report=html
# Open htmlcov/index.html in browser to see coverage

# Run specific test categories
pytest -m unit          # Only unit tests (fast)
pytest -m integration   # Only integration tests
pytest -m auth          # Only auth tests
pytest -m api           # Only API endpoint tests
pytest -m streaming     # Only streaming tests

# Run specific test files
pytest tests/test_auth_endpoints.py
pytest tests/test_model_endpoints.py
pytest tests/test_thread_endpoints.py
pytest tests/test_plugins.py

# Run specific tests
pytest tests/test_auth_endpoints.py::test_login_success
pytest tests/test_model_endpoints.py::test_get_models

# Verbose output
pytest -v
pytest -vv  # Extra verbose
```

## Test Categories

### Authentication Tests (9 tests)
- Login with valid/invalid credentials
- Logout functionality
- Token validation
- Protected endpoint access control

### Model Tests (8 tests)
- Model listing
- System prompts
- Message streaming
- Different model types
- Input validation
- Error handling

### Thread Management Tests (8 tests)
- Thread creation
- Message retrieval
- Thread updates
- Thread archiving
- Search functionality
- User isolation

### Plugin System Tests (12 tests)
- Plugin priority
- Auth plugin functionality
- Model plugin functionality
- Test credentials helpers

## Test Structure

```
tests/
├── __init__.py                  # Package marker
├── conftest.py                  # Pytest fixtures and configuration
├── test_auth_plugin.py          # Mock authentication plugin
├── test_model_plugin.py         # Mock LLM plugin
├── test_auth_endpoints.py       # Tests for login/logout
├── test_model_endpoints.py      # Tests for model and message endpoints
├── test_thread_endpoints.py     # Tests for thread management
├── test_plugins.py              # Tests for plugin system
└── README.md                    # This file
```

## Test Infrastructure

### Mock Plugins (No API Keys Needed!)

**TestAuthPlugin**:
- Hardcoded test users (testuser1, testuser2, admin)
- Deterministic tokens for easy testing
- Simple password hashing (SHA256 for tests only)

**TestModelPlugin**:
- Returns fake streaming responses
- Simulates different model behaviors
- No external API calls
- Configurable responses based on content

**DefaultThreadManager**:
- Already in-memory (no database needed)
- Auto-cleanup between tests
- Fast and isolated

### Test Users

The `TestAuthPlugin` provides these test users:

| Username   | Password  | Token              | Role  |
|------------|-----------|-------------------|-------|
| testuser1  | password1 | test-token-user1  | user  |
| testuser2  | password2 | test-token-user2  | user  |
| admin      | admin123  | test-token-admin  | admin |

### Test Fixtures

Available in all tests via `conftest.py`:

- `client` - Synchronous test client
- `async_client` - Async test client for streaming tests
- `test_plugins` - Registers mock plugins for the test
- `auth_headers` - Pre-configured auth headers for testuser1
- `auth_headers_user2` - Pre-configured auth headers for testuser2
- `auth_headers_admin` - Pre-configured auth headers for admin
- `test_credentials` - Credentials dict for testuser1
- `authenticated_client` - Pre-authenticated client
- `reset_thread_storage` - Auto-cleanup between tests

## Example Usage

```python
import pytest

@pytest.mark.unit
def test_example(authenticated_client):
    """Test with pre-authenticated client"""
    response = authenticated_client.get("/api/v1/threads")
    assert response.status_code == 200

@pytest.mark.asyncio
async def test_streaming(async_client, auth_headers):
    """Test streaming endpoint"""
    response = await async_client.post(
        "/api/v1/message",
        headers=auth_headers,
        data={"content": "Hello", "model": "test-model-fast"}
    )
    assert response.status_code == 200
```

## Coverage

### Coverage by Module

| Module | Coverage | Status |
|--------|----------|--------|
| core/protocols.py | 100% | ✅ Excellent |
| tests/test_auth_endpoints.py | 100% | ✅ Excellent |
| tests/test_auth_plugin.py | 100% | ✅ Excellent |
| tests/test_plugins.py | 100% | ✅ Excellent |
| tests/conftest.py | 88% | ✅ Good |
| main.py | 78% | ✅ Good |
| core/default_thread_manager.py | 70% | ✅ Good |
| core/plugin_manager.py | 69% | ✅ Good |
| tests/test_model_endpoints.py | 53% | ⚠️ Could improve |
| core/default_auth.py | 47% | ⚠️ Could improve |
| tests/test_thread_endpoints.py | 44% | ⚠️ Could improve |
| core/plugin_loader.py | 23% | ⚠️ Needs work |
| core/default_model_client.py | 17% | ⚠️ Needs work |

### Coverage Goals

Target coverage: **>80%** across all modules

Current coverage areas:
- ✅ Authentication (login, logout, token validation)
- ✅ Model listing and response generation
- ✅ Thread creation, listing, updating, archiving
- ✅ Message sending and streaming
- ✅ Search functionality
- ✅ User isolation
- ✅ Input validation
- ✅ Error handling

### Next Steps for Improved Coverage

To reach 80%+ coverage:

1. **Add plugin loader tests** - Test function-based tool conversion, plugin discovery
2. **Add default auth tests** - Test bcrypt hashing, JWT generation
3. **Add default model client tests** - Test OpenAI SDK integration (may need mocking)
4. **Add more integration tests** - Test complete user flows
5. **Add error scenario tests** - Test edge cases and error handling

## Writing New Tests

1. Create test file: `test_<feature>.py`
2. Import fixtures from conftest
3. Mark tests with appropriate markers (`@pytest.mark.unit`, etc.)
4. Use `authenticated_client` for simple tests
5. Use `async_client` for streaming/async tests

Example:

```python
import pytest

@pytest.mark.unit
@pytest.mark.api
def test_my_feature(authenticated_client):
    response = authenticated_client.get("/api/v1/my-endpoint")
    assert response.status_code == 200
    assert response.json()["key"] == "expected_value"
```

## Debugging Tests

```bash
# Run with stdout/stderr output
pytest -s

# Run with pdb on failure
pytest --pdb

# Run last failed tests
pytest --lf

# Stop on first failure
pytest -x
```

## Performance

All tests use mock plugins, so they run **very fast**:
- No network calls
- No external API dependencies
- No database queries (using in-memory storage)
- Typical test suite runtime: **< 5 seconds**

## Continuous Integration

To integrate with CI/CD:

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: |
    cd backend
    pip install -r requirements-test.txt
    pytest --cov=. --cov-report=xml
```

## Troubleshooting

### Tests fail with "No module named 'core'"
```bash
# Make sure you're in the backend directory
cd backend
pytest
```

### Tests fail with import errors
```bash
# Install test dependencies
pip install -r requirements-test.txt
```

### Coverage report not generating
```bash
# Install coverage plugin
pip install pytest-cov
pytest --cov=. --cov-report=html
```

## Notes

- One warning about `TestAuthPlugin` class name - pytest thinks it's a test class. This is harmless.
- Coverage can be viewed in detail: Open `htmlcov/index.html` after running `./run_tests.sh coverage`
- All async tests work correctly with pytest-asyncio
- SSE streaming tests parse events correctly
