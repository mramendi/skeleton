#!/usr/bin/env python3
"""
Generate a secure JWT secret key for Skeleton
Run this once and add the output to your .env file
"""

import secrets
import string

def generate_jwt_secret(length=64):
    """Generate a cryptographically secure random string"""
    # Use URL-safe characters to avoid encoding issues
    return secrets.token_urlsafe(length)

if __name__ == "__main__":
    jwt_secret = generate_jwt_secret()
    print(f"JWT_SECRET_KEY={jwt_secret}")
    print("\nAdd this line to your backend/.env file")
