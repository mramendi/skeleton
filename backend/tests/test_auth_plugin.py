"""
Test Auth Plugin - Provides hardcoded test users for testing without real auth.

This plugin provides deterministic authentication for tests with known users
and tokens. Passwords are simple and tokens are predictable for easy testing.
"""
from typing import Dict, Any, Optional
import hashlib


class TestAuthPlugin:
    """Mock authentication plugin for testing - uses hardcoded test users"""

    def __init__(self):
        """Initialize with test users"""
        # Hardcoded test users (username -> password hash)
        # Passwords: testuser1 -> "password1", testuser2 -> "password2", admin -> "admin123"
        self.users = {
            "testuser1": {
                "username": "testuser1",
                "password_hash": self._hash_password("password1"),
                "role": "user"
            },
            "testuser2": {
                "username": "testuser2",
                "password_hash": self._hash_password("password2"),
                "role": "user"
            },
            "admin": {
                "username": "admin",
                "password_hash": self._hash_password("admin123"),
                "role": "admin"
            }
        }

        # Hardcoded tokens for easy testing (username -> token)
        # In real implementation these would be JWTs, but for tests we use simple deterministic tokens
        self.test_tokens = {
            "testuser1": "test-token-user1",
            "testuser2": "test-token-user2",
            "admin": "test-token-admin"
        }

        # Reverse lookup (token -> username)
        self.token_to_user = {token: username for username, token in self.test_tokens.items()}

    def get_priority(self) -> int:
        """High priority to override default auth in tests"""
        return 1000

    def _hash_password(self, password: str) -> str:
        """Simple password hashing for tests (not cryptographically secure)"""
        return hashlib.sha256(password.encode()).hexdigest()

    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Authenticate user with hardcoded credentials"""
        user = self.users.get(username)
        if not user:
            return None

        password_hash = self._hash_password(password)
        if password_hash != user["password_hash"]:
            return None

        return {
            "username": user["username"],
            "role": user["role"]
        }

    def create_token(self, user: Dict[str, Any]) -> str:
        """Create deterministic token for testing"""
        username = user["username"]
        return self.test_tokens.get(username, f"test-token-{username}")

    def verify_token(self, token: str) -> Optional[str]:
        """Verify token and return username"""
        return self.token_to_user.get(token)

    def get_test_credentials(self, username: str = "testuser1") -> Dict[str, str]:
        """Helper method to get test credentials (useful for test setup)"""
        credentials = {
            "testuser1": {"username": "testuser1", "password": "password1", "token": "test-token-user1"},
            "testuser2": {"username": "testuser2", "password": "password2", "token": "test-token-user2"},
            "admin": {"username": "admin", "password": "admin123", "token": "test-token-admin"}
        }
        return credentials.get(username, credentials["testuser1"])
