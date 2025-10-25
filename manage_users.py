#!/usr/bin/env python3
"""
User management script for Skeleton authentication.

This script creates or updates users in the YAML authentication file.
It can create the file if it doesn't exist, or add/update users if it does.
"""
import argparse
import os
import sys
import bcrypt
import yaml
import secrets
from typing import Dict, Any, Optional


def load_users(file_path: str) -> Dict[str, Dict[str, Any]]:
    """Load users from YAML file, return empty dict if file doesn't exist."""
    if not os.path.exists(file_path):
        return {}

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file {file_path}: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file {file_path}: {e}", file=sys.stderr)
        sys.exit(1)


def validate_user_data(username: str, user_data: Dict[str, Any]) -> Optional[str]:
    """Validate a single user's data against the expected schema.

    Returns None if valid, otherwise returns an error message.
    """
    if not isinstance(user_data, dict):
        return f"User '{username}' must be a dictionary/object"

    # Check required fields
    if "hash" not in user_data:
        return f"User '{username}' missing required field 'hash'"

    if "role" not in user_data:
        return f"User '{username}' missing required field 'role'"

    # Validate field types
    if not isinstance(user_data["hash"], str) or not user_data["hash"].strip():
        return f"User '{username}' field 'hash' must be a non-empty string"

    if not isinstance(user_data["role"], str) or not user_data["role"].strip():
        return f"User '{username}' field 'role' must be a non-empty string"

    # Validate optional model_mask field
    if "model_mask" in user_data:
        if not isinstance(user_data["model_mask"], str):
            return f"User '{username}' field 'model_mask' must be a string"
        try:
            import re
            re.compile(user_data["model_mask"])
        except re.error as e:
            return f"User '{username}' field 'model_mask' contains invalid regex: {e}"

    return None


def save_users(file_path: str, users: Dict[str, Dict[str, Any]]) -> None:
    """Save users to YAML file with validation."""
    # Validate all users before saving
    validation_errors = []
    for username, user_data in users.items():
        error = validate_user_data(username, user_data)
        if error:
            validation_errors.append(error)

    if validation_errors:
        print("❌ VALIDATION ERRORS FOUND:", file=sys.stderr)
        for error in validation_errors:
            print(f"  - {error}", file=sys.stderr)
        print("Please fix these errors before saving.", file=sys.stderr)
        sys.exit(1)

    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            yaml.dump(users, f, default_flow_style=False, sort_keys=False)
        print(f"✅ Users saved to {file_path}")
        print(f"📝 Saved {len(users)} user(s)")
    except Exception as e:
        print(f"Error writing to file {file_path}: {e}", file=sys.stderr)
        sys.exit(1)


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def add_or_update_user(
    users: Dict[str, Dict[str, Any]],
    username: str,
    password: str,
    role: str = "user",
    model_mask: Optional[str] = None
) -> bool:
    """Add or update a user in the users dictionary. Returns True if user was added/updated."""
    user_exists = username in users

    # Create or update user entry
    if not users.get(username):
        users[username] = {}

    # Update password (always update when provided)
    users[username]["hash"] = hash_password(password)

    # Update role if provided
    if role is not None:
        users[username]["role"] = role

    # Update model mask if provided
    if model_mask is not None:
        users[username]["model_mask"] = model_mask

    return True


def generate_jwt_secret(filename: str) -> None:
    """Generate a JWT secret and save it to the specified file."""
    try:
        secret = secrets.token_urlsafe(32)
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(secret)
    except Exception as e:
        print(f"Error generating JWT secret: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"JWT secret generated and saved to {filename}")
    try:
        # Set restrictive permissions (owner read/write only)
        os.chmod(filename, 0o600)
        print(f"File permissions set to 600 (owner read/write only)")
    except Exception as e:
        print(f"Error setting file permissions: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Manage users for Skeleton authentication",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -u admin -p secret123 -r admin
  %(prog)s -u user1 -p password123 -r user -m "^(?!.*gpt).*$"
  %(prog)s -f custom_users.yaml -u test -p test123
  %(prog)s --generate-jwt jwt.secret
  %(prog)s --validate -f users.yaml
        """
    )

    parser.add_argument(
        "-f", "--file",
        default="users.yaml",
        help="YAML file path (default: users.yaml)"
    )

    parser.add_argument(
        "-u", "--username",
        required=False,
        help="Username (required unless using --generate-jwt or --validate)"
    )

    parser.add_argument(
        "-p", "--password",
        required=False,
        help="Password (required unless using --generate-jwt or --validate)"
    )

    parser.add_argument(
        "-r", "--role",
        default="user",
        help="User role (default: user)"
    )

    parser.add_argument(
        "-m", "--model-mask",
        help="Model mask regex (default: not set)"
    )

    parser.add_argument(
        "--generate-jwt",
        metavar="FILENAME",
        help="Generate a JWT secret and save it to the specified file"
    )

    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the users file and report any schema errors"
    )

    args = parser.parse_args()

    # Handle JWT generation
    if args.generate_jwt:
        generate_jwt_secret(args.generate_jwt)

    # Handle validation
    if args.validate:
        users = load_users(args.file)
        if not users:
            print(f"❌ No users found in {args.file} or file doesn't exist", file=sys.stderr)
            sys.exit(1)

        validation_errors = []
        for username, user_data in users.items():
            error = validate_user_data(username, user_data)
            if error:
                validation_errors.append(error)

        if validation_errors:
            print(f"❌ VALIDATION FAILED for {args.file}:", file=sys.stderr)
            for error in validation_errors:
                print(f"  - {error}", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"✅ VALIDATION PASSED for {args.file}")
            print(f"📝 Found {len(users)} valid user(s)")
            for username, user_data in users.items():
                role = user_data.get('role', 'unknown')
                mask = user_data.get('model_mask', 'none')
                print(f"  - {username} (role: {role}, model_mask: {mask})")
        return

    if args.username:
        # add a user
        if not args.password:
            print (f"Password not provided for user {args.username}", file=sys.stderr)
            exit(1)

        # Load existing users
        users = load_users(args.file)

        # Add or update user
        user_existed = args.username in users
        add_or_update_user(users, args.username, args.password, args.role, args.model_mask)

        # Save users
        save_users(args.file, users)

        # Report result
        if user_existed:
            print(f"Updated user '{args.username}' in {args.file}")
        else:
            print(f"Added user '{args.username}' to {args.file}")

        # Show current user info
        user_info = users[args.username]
        print(f"  Role: {user_info['role']}")
        if 'model_mask' in user_info:
            print(f"  Model mask: {user_info['model_mask']}")
        else:
            print(f"  Model mask: not set (all models allowed)")


if __name__ == "__main__":
    main()
