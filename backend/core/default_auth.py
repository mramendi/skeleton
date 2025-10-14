"""
Default authentication implementation.
Can be overridden by plugins.
"""
from typing import Optional, Dict, Any
import jwt
import bcrypt
from datetime import datetime, timedelta
import os
import secrets

class DefaultAuthHandler:
    """Default authentication handler - can be overridden by plugins"""
    
    def get_priority(self) -> int:
        """Default priority - plugins can override with higher priority"""
        return 0
    
    def __init__(self):
        self.secret_key = os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(32))
        self.algorithm = "HS256"
        self.access_token_expire_minutes = 60 * 24  # 24 hours
        
        # Simple in-memory user store (replace with database in production)
        self.users = {
            "admin": {
                "username": "admin",
                "password_hash": self._hash_password("admin123"),  # Change in production!
                "role": "admin"
            }
        }
    
    def _hash_password(self, password: str) -> str:
        """Hash a password using bcrypt"""
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    
    def _verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash"""
        return bcrypt.checkpw(
            plain_password.encode('utf-8'), 
            hashed_password.encode('utf-8')
        )
    
    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Authenticate a user"""
        user = self.users.get(username)
        if not user:
            return None
        
        if not self._verify_password(password, user["password_hash"]):
            return None
        
        return {"username": username, "role": user["role"]}
    
    def create_token(self, user: Dict[str, Any]) -> str:
        """Create JWT token for user"""
        expire = datetime.utcnow() + timedelta(minutes=self.access_token_expire_minutes)
        
        payload = {
            "sub": user["username"],
            "role": user["role"],
            "exp": expire
        }
        
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
    
    def verify_token(self, token: str) -> Optional[str]:
        """Verify JWT token and return username"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            username = payload.get("sub")
            return username
        except jwt.ExpiredSignatureError:
            return None
        except jwt.JWTError:
            return None
