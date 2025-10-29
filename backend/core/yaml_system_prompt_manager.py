"""
System prompt manager plugin that uses a YAML file for prompt definitions.
This allows for simple, file-based prompt management without requiring a database.
"""
import os
import logging
from typing import Optional, Dict, Any

import yaml
from .protocols import SystemPromptPlugin

logger = logging.getLogger(__name__)

class YamlSystemPromptManager():
    """
    System prompt manager plugin using a YAML file for prompt storage.

    The YAML file should contain prompt objects keyed by prompt name.
    Each prompt object must have a 'content' field and can optionally have a
    'description' field.

    Example system_prompts.yaml:
    default:
      content: "You are a helpful assistant."
      description: "General purpose assistant"
    code-assistant:
      content: "You are an expert programming assistant..."
      description: "Expert programming help"
    """

    def get_role(self) -> str:
        """Return the role string for this plugin"""
        return "system_prompt"

    def get_priority(self) -> int:
        """Return default priority."""
        return 0

    async def shutdown(self) -> None:
        """Graceful shutdown. Can be a no-op for this plugin."""
        logger.info("YamlSystemPromptManager shutting down.")

    def __init__(self):
        # Try multiple methods to get the system prompts file
        self.prompts_file_path = None

        # Method 1: SYSTEM_PROMPTS_FILE environment variable
        prompts_file = os.getenv("SYSTEM_PROMPTS_FILE")
        if prompts_file and os.path.exists(prompts_file):
            self.prompts_file_path = prompts_file
            logger.info(f"System prompts loaded from SYSTEM_PROMPTS_FILE: {prompts_file}")

        # Method 2: DATA_PATH/system_prompts.yaml file
        if not self.prompts_file_path:
            data_dir = os.getenv("DATA_PATH", ".")
            prompts_path = os.path.join(data_dir, "system_prompts.yaml")
            if os.path.exists(prompts_path):
                self.prompts_file_path = prompts_path
                logger.info(f"System prompts loaded from file: {prompts_path}")

        # Method 3: Try current working directory (for containerized setup)
        if not self.prompts_file_path:
            # In containerized setup, main.py and system_prompts.yaml are in the same directory
            # where the application is run (typically /app)
            prompts_path = os.path.join(".", "system_prompts.yaml")
            if os.path.exists(prompts_path):
                self.prompts_file_path = prompts_path
                logger.info(f"System prompts loaded from current directory: {prompts_path}")

        # If no method worked, use default path and warn
        if not self.prompts_file_path:
            data_dir = os.getenv("DATA_PATH", ".")
            self.prompts_file_path = os.path.join(data_dir, "system_prompts.yaml")
            logger.warning(
                f"System prompts file not found at any expected location.\n"
                f"Expected locations:\n"
                f"  1. SYSTEM_PROMPTS_FILE environment variable\n"
                f"  2. {os.path.join(data_dir, 'system_prompts.yaml')} (DATA_PATH)\n"
                f"  3. {os.path.join(os.getcwd(), 'system_prompts.yaml')} (current working directory)\n"
                f"Using built-in default prompts. You can create a custom file at any of the above locations."
            )

        self.prompts = self._load_prompts()

    def _validate_prompt_schema(self, prompt_name: str, prompt_data: Dict[str, Any]) -> Optional[str]:
        """Validate a single prompt's data against the expected schema.

        Returns None if valid, otherwise returns an error message.
        """
        if not isinstance(prompt_data, dict):
            return f"Prompt '{prompt_name}' must be a dictionary/object"

        # Check required fields
        if "content" not in prompt_data:
            return f"Prompt '{prompt_name}' missing required field 'content'"

        # Validate field types
        if not isinstance(prompt_data["content"], str):
            return f"Prompt '{prompt_name}' field 'content' must be a string"

        # Validate optional description field
        if "description" in prompt_data:
            if not isinstance(prompt_data["description"], str):
                return f"Prompt '{prompt_name}' field 'description' must be a string"

        return None

    def _load_prompts(self) -> Dict[str, Dict[str, Any]]:
        """Load prompts from the specified YAML file with schema validation."""

        DEFAULT_PROMPTS = {
            "zero": {"content": "", "description": "No system prompt - use model's default behavior"},
            "default": {"content": "You are a helpful assistant.", "description": "General purpose assistant"}
        }

        if not os.path.exists(self.prompts_file_path):
            logger.warning(f"🚨 SYSTEM PROMPTS FILE NOT FOUND 🚨")
            logger.warning(f"Expected prompts file at: {self.prompts_file_path}")
            logger.warning("Creating default prompts dictionary with only 'zero' and 'default' prompts")
            return DEFAULT_PROMPTS

        try:
            with open(self.prompts_file_path, 'r', encoding='utf-8') as f:
                prompts_data = yaml.safe_load(f)

                if not isinstance(prompts_data, dict):
                    logger.error("🚨 INVALID PROMPTS FILE FORMAT 🚨")
                    logger.error(f"Prompts file at {self.prompts_file_path} must contain a dictionary/object")
                    return DEFAULT_PROMPTS

                if not prompts_data:
                    logger.error("🚨 EMPTY PROMPTS FILE 🚨")
                    logger.error(f"Prompts file at {self.prompts_file_path} contains no prompts")
                    return DEFAULT_PROMPTS

                # Validate each prompt
                valid_prompts = {}
                validation_errors = []

                for prompt_name, prompt_data in prompts_data.items():
                    error = self._validate_prompt_schema(prompt_name, prompt_data)
                    if error:
                        validation_errors.append(error)
                    else:
                        valid_prompts[prompt_name] = prompt_data

                if validation_errors:
                    logger.error("🚨 PROMPTS FILE VALIDATION FAILED 🚨")
                    logger.error(f"Found {len(validation_errors)} validation error(s) in {self.prompts_file_path}:")
                    for error in validation_errors:
                        logger.error(f"  - {error}")

                    if valid_prompts:
                        logger.warning(f"⚠️  Loaded {len(valid_prompts)} valid prompt(s) but {len(validation_errors)} prompt(s) had errors")
                    else:
                        logger.error("❌ NO VALID PROMPTS FOUND - USING FALLBACK")
                        return DEFAULT_PROMPTS

                if valid_prompts:
                    logger.info(f"✅ Successfully loaded {len(valid_prompts)} valid prompt(s) from {self.prompts_file_path}")
                    return valid_prompts
                else:
                    return {}

        except yaml.YAMLError as e:
            logger.error("🚨 YAML SYNTAX ERROR 🚨")
            logger.error(f"Error parsing prompts file at {self.prompts_file_path}: {e}")
            logger.error("Please check the YAML syntax and fix any formatting issues")
            return {}
        except Exception as e:
            logger.error("🚨 UNEXPECTED ERROR LOADING PROMPTS 🚨")
            logger.error(f"Unexpected error loading prompts file at {self.prompts_file_path}: {e}")
            logger.error("Please check file permissions and ensure the file is accessible")
            return {}

    async def get_prompt(self, key: str) -> Optional[str]:
        """Get system prompt content by key. Returns None if not found."""
        if not key:
            return None

        prompt_data = self.prompts.get(key)
        if not prompt_data:
            logger.warning(f"System prompt '{key}' not found. Available prompts: {list(self.prompts.keys())}")
            return None

        return prompt_data.get("content")

    async def list_prompts(self) -> Dict[str, str]:
        """List all available prompt keys and descriptions."""
        result = {}
        for key, data in self.prompts.items():
            result[key] = data.get("description", "")
        return result

    async def get_all_prompts(self) -> Dict[str, Dict[str, str]]:
        """Get all prompts with full metadata."""
        return self.prompts.copy()
