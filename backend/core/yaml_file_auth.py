"""
Authentication plugin that uses a YAML file for user data.
This allows for simple, file-based user management without requiring a database.
"""
import os
import re
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import bcrypt
import jwt
import yaml
from .protocols import AuthPlugin

logger = logging.getLogger(__name__)

class YamlFileAuthPlugin():
    """
    Authentication plugin using a YAML file for user storage.

    The YAML file should contain user objects keyed by username.
    Each user object must have a 'hash' and 'role', and can optionally have a
    'model_mask' which is a regex to filter available models.

    Example users.yaml:
    admin:
      hash: "$2b$12$..."
      role: "admin"
      model_mask: ".*"
    user:
      hash: "$2b$12$..."
      role: "user"
      model_mask: "^(?!.*gpt).*$"
    """

    def get_role(self) -> str:
        """Return the role string for this plugin"""
        return "auth"

    def __init__(self):
        self.is_ephemeral = (os.getenv("SKELETON_MODE", "").lower() == "ephemeral")
        if self.is_ephemeral:
            logger.warning(
                "🚨 SKELETON IS RUNNING IN EPHEMERAL MODE 🚨\n"                                                                                                             "- A default user 'default' with password 'default' is available.\n"
                "- This mode is intended for short-lived demos ONLY.\n"
                "- DO NOT use this mode for any persistent or production system.\n"
                "- All sessions will be invalidated on restart."
            )
            # Create the in-memory default user
            self.users = {
                "default": {
                    "hash": bcrypt.hashpw("default".encode('utf-8'), bcrypt.gensalt()).decode('utf-8'),
                    "role": "admin"  # Give full power in ephemeral mode
                }
            }
            # create the temporary JWT secret
            self.secret_key = secrets.token_urlsafe(32)
        else:
            # Try multiple methods to get JWT secret
            self.secret_key = None

            # Method 1: JWT_SECRET_KEY environment variable
            jwt_secret_key = os.getenv("JWT_SECRET_KEY")
            if jwt_secret_key and jwt_secret_key.strip():
                self.secret_key = jwt_secret_key.strip()
                logger.info("JWT secret loaded from JWT_SECRET_KEY environment variable")

            # Method 2: JWT_SECRET_FILE environment variable
            if not self.secret_key:
                jwt_secret_file = os.getenv("JWT_SECRET_FILE")
                if jwt_secret_file and os.path.exists(jwt_secret_file):
                    try:
                        with open(jwt_secret_file, 'r', encoding='utf-8') as f:
                            secret = f.read().strip()
                            if secret:
                                self.secret_key = secret
                                logger.info(f"JWT secret loaded from file: {jwt_secret_file}")
                    except Exception as e:
                        logger.error(f"Error reading JWT secret file {jwt_secret_file}: {e}")

            # Method 3: DATA_PATH/jwt.secret file
            if not self.secret_key:
                data_dir = os.getenv("DATA_PATH", ".")
                jwt_secret_path = os.path.join(data_dir, "jwt.secret")
                if os.path.exists(jwt_secret_path):
                    try:
                        with open(jwt_secret_path, 'r', encoding='utf-8') as f:
                            secret = f.read().strip()
                            if secret:
                                self.secret_key = secret
                                logger.info(f"JWT secret loaded from file: {jwt_secret_path}")
                    except Exception as e:
                        logger.error(f"Error reading JWT secret file {jwt_secret_path}: {e}")

            # If no method worked, fail with detailed error
            if not self.secret_key:
                data_dir = os.getenv("DATA_PATH", ".")
                jwt_secret_path = os.path.join(data_dir, "jwt.secret")
                logger.fatal(
                    "JWT secret not found. Tried the following methods:\n"
                    "1. JWT_SECRET_KEY environment variable\n"
                    "2. JWT_SECRET_FILE environment variable (file path)\n"
                    f"3. File at {jwt_secret_path}\n"
                    "Please set one of these methods or run with SKELETON_MODE=ephemeral for testing.\n"
                    "You can generate a JWT secret using: python manage_users.py --generate-jwt <filename>"
                )
                raise RuntimeError("JWT secret must be configured for persistent mode")

            data_dir = os.getenv("DATA_PATH", ".") # Default to current dir
            users_filename = os.getenv("USERS_FILENAME", "users.yaml") # Allow customizing filename
            self.users_file_path = os.path.join(data_dir, users_filename)
            self.users = self._load_users()

        self.algorithm = "HS256"
        self.access_token_expire_minutes = 60 * 24  # 24 hours

    def _validate_user_schema(self, username: str, user_data: Dict[str, Any]) -> Optional[str]:
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
                re.compile(user_data["model_mask"])
            except re.error as e:
                return f"User '{username}' field 'model_mask' contains invalid regex: {e}"

        return None

    def _load_users(self) -> Dict[str, Dict[str, Any]]:
        """Load users from the specified YAML file with schema validation."""
        if not os.path.exists(self.users_file_path):
            logger.error(f"🚨 USERS FILE NOT FOUND 🚨")
            logger.error(f"Expected users file at: {self.users_file_path}")
            logger.error("Please create a users.yaml file with at least one valid user.")
            logger.error("Example users.yaml:")
            logger.error("  admin:")
            logger.error("    hash: '$2b$12$...'")
            logger.error("    role: 'admin'")
            logger.error("You can generate a password hash using: python manage_users.py -u admin -p yourpassword")
            return {}

        try:
            with open(self.users_file_path, 'r', encoding='utf-8') as f:
                users_data = yaml.safe_load(f)

                if not isinstance(users_data, dict):
                    logger.error("🚨 INVALID USERS FILE FORMAT 🚨")
                    logger.error(f"Users file at {self.users_file_path} must contain a dictionary/object")
                    logger.error("Expected format:")
                    logger.error("  username1:")
                    logger.error("    hash: '...'")
                    logger.error("    role: '...'")
                    logger.error("  username2:")
                    logger.error("    hash: '...'")
                    logger.error("    role: '...'")
                    return {}

                if not users_data:
                    logger.error("🚨 EMPTY USERS FILE 🚨")
                    logger.error(f"Users file at {self.users_file_path} contains no users")
                    logger.error("Please add at least one user to the file")
                    return {}

                # Validate each user
                valid_users = {}
                validation_errors = []

                for username, user_data in users_data.items():
                    error = self._validate_user_schema(username, user_data)
                    if error:
                        validation_errors.append(error)
                    else:
                        valid_users[username] = user_data

                if validation_errors:
                    logger.error("🚨 USERS FILE VALIDATION FAILED 🚨")
                    logger.error(f"Found {len(validation_errors)} validation error(s) in {self.users_file_path}:")
                    for error in validation_errors:
                        logger.error(f"  - {error}")

                    if valid_users:
                        logger.warning(f"⚠️  Loaded {len(valid_users)} valid user(s) but {len(validation_errors)} user(s) had errors")
                    else:
                        logger.error("❌ NO VALID USERS FOUND - AUTHENTICATION WILL FAIL")
                        logger.error("Please fix the validation errors above")
                        return {}

                if valid_users:
                    logger.info(f"✅ Successfully loaded {len(valid_users)} valid user(s) from {self.users_file_path}")
                    return valid_users
                else:
                    return {}

        except yaml.YAMLError as e:
            logger.error("🚨 YAML SYNTAX ERROR 🚨")
            logger.error(f"Error parsing users file at {self.users_file_path}: {e}")
            logger.error("Please check the YAML syntax and fix any formatting issues")
            return {}
        except Exception as e:
            logger.error("🚨 UNEXPECTED ERROR LOADING USERS 🚨")
            logger.error(f"Unexpected error loading users file at {self.users_file_path}: {e}")
            logger.error("Please check file permissions and ensure the file is accessible")
            return {}

    def get_priority(self) -> int:
        """Return default priority."""
        return 0

    async def shutdown(self) -> None:
        """Graceful shutdown. Can be a no-op for this plugin."""
        logger.info("YamlFileAuthPlugin shutting down.")

    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Authenticate a user against the YAML file."""
        user_data = self.users.get(username)
        if not user_data:
            return None

        password_hash = user_data.get("hash")
        if not password_hash:
            logger.warning(f"User '{username}' found in {self.users_file_path} but has no password hash.")
            return None

        if not bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8')):
            return None

        return {"username": username, "role": user_data.get("role", "user")}

    def create_token(self, user: Dict[str, Any]) -> str:
        """Create a JWT token for a user."""
        expire = datetime.utcnow() + timedelta(minutes=self.access_token_expire_minutes)
        payload = {
            "sub": user["username"],
            "role": user["role"],
            "exp": expire
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def verify_token(self, token: str) -> Optional[str]:
        """Verify a JWT token and return the username."""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            username = payload.get("sub")
            return username
        except jwt.ExpiredSignatureError:
            logger.warning("Token verification failed: token has expired.")
            return None
        except Exception as e:  # TODO consider something more specific; jwt.JWTError does not exist
            logger.warning(f"Token verification failed: {e}")
            return None

    def request_allowed(self, username: str, model_name: str) -> bool:
        """
        Check if a user is allowed to request a specific model based on their regex mask.
        If no mask is set for the user, all models are allowed by default.
        """
        user_data = self.users.get(username)
        if not user_data:
            logger.warning(f"Authorization check failed: user '{username}' not found.")
            return False

        # Default to allowing all models if no mask is set
        mask = user_data.get("model_mask", ".*")

        try:
            pattern = re.compile(mask)
            if pattern.fullmatch(model_name):
                return True
        except re.error as e:
            logger.error(f"Invalid regex in model_mask for user '{username}': '{mask}'. Error: {e}. Denying access.")
            return False

        return False
