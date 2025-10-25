"""
Stateless helper functions for validation, serialization, and naming.
"""
import json
import os
import re
import uuid
from typing import Any, Dict

# ============================================================================
# VALIDATION AND SECURITY HELPERS
# ============================================================================

def sanitize_identifier(identifier: str) -> str:
    """
    Sanitize SQL identifiers (table names, column names) to prevent injection.
    Only allows alphanumeric characters and underscores.
    """
    if not identifier:
        raise ValueError("Identifier cannot be empty")

    # Remove any characters that aren't alphanumeric or underscore
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '', identifier)

    # Must start with letter or underscore
    if sanitized and not re.match(r'^[a-zA-Z_]', sanitized):
        sanitized = '_' + sanitized

    if not sanitized:
        raise ValueError("Identifier contains no valid characters")

    # SQLite has a 64 character limit for identifiers
    if len(sanitized) > 64:
        sanitized = sanitized[:64]

    return sanitized

def validate_store_name(store_name: str) -> str:
    """Validate and sanitize store name"""
    if not store_name:
        raise ValueError("Store name cannot be empty")

    # Basic validation - alphanumeric, underscore, hyphen
    if not re.match(r'^[a-zA-Z0-9_-]+$', store_name):
        raise ValueError("Store name can only contain letters, numbers, underscore, and hyphen")

    if len(store_name) > 64:
        raise ValueError("Store name too long (max 64 characters)")

    return store_name

def validate_field_name(field_name: str) -> str:
    """Validate and sanitize field name"""
    if not field_name:
        raise ValueError("Field name cannot be empty")

    # Basic validation - alphanumeric, underscore
    if not re.match(r'^[a-zA-Z0-9_]+$', field_name):
        raise ValueError("Field name can only contain letters, numbers, and underscore")

    if len(field_name) > 64:
        raise ValueError("Field name too long (max 64 characters)")

    return field_name


# ============================================================================
# TYPE MAPPING AND SERIALIZATION
# ============================================================================

def map_type(generic_type: str) -> str:
    """Map generic types to SQLite types"""
    type_map = {
        "str": "TEXT",
        "int": "INTEGER",
        "float": "REAL",
        "bool": "INTEGER",
        "json": "TEXT",
        "json_collection": "TEXT"  # Stores metadata, items in child table
    }
    return type_map.get(generic_type, "TEXT")

def serialize_value(value: Any, field_type: str, field_name: str = "field",
                    store_name: str = None) -> Any:
    """
    Validate and serialize value based on field type for storage in SQLite.

    Returns the value in the correct Python type for SQLite storage:
    - str fields: str (TEXT)
    - int fields: int (INTEGER)
    - float fields: float (REAL)
    - bool fields: int 0/1 (INTEGER)
    - json fields: str (TEXT, validated JSON)
    - json_collection fields: str (TEXT, metadata JSON)

    Raises TypeError with clear message if value cannot be converted to field type.
    """
    if value is None:
        # Special handling for json_collection: None means initialize empty collection
        if field_type == "json_collection":
            if store_name is None:
                raise ValueError(
                    f"Field '{field_name}' (json_collection): store_name required for initialization"
                )
            collection_store = get_collection_table_name(store_name, field_name)
            return json.dumps({"collection_store": collection_store, "count": 0})
        return None

    if field_type == "str":
        # Accept strings, convert other types to string
        if not isinstance(value, str):
            try:
                return str(value)
            except Exception as e:
                raise TypeError(
                    f"Field '{field_name}' expects str, got {type(value).__name__} "
                    f"that cannot be converted to string: {e}"
                )
        return value

    elif field_type == "int":
        # Validate and convert to int
        if isinstance(value, bool):
            # Reject bool (True/False) for int fields to avoid confusion
            raise TypeError(
                f"Field '{field_name}' expects int, got bool. "
                f"Use 1/0 or convert explicitly."
            )
        try:
            return int(value)
        except (ValueError, TypeError) as e:
            raise TypeError(
                f"Field '{field_name}' expects int, got {type(value).__name__} "
                f"with value '{value}' that cannot be converted to int: {e}"
            )

    elif field_type == "float":
        # Validate and convert to float
        try:
            return float(value)
        except (ValueError, TypeError) as e:
            raise TypeError(
                f"Field '{field_name}' expects float, got {type(value).__name__} "
                f"with value '{value}' that cannot be converted to float: {e}"
            )

    elif field_type == "bool":
        # Convert to 0/1 integer for SQLite storage
        # Accept bool, int, or truthy/falsy values
        try:
            return 1 if bool(value) else 0
        except Exception as e:
            raise TypeError(
                f"Field '{field_name}' expects bool, got {type(value).__name__} "
                f"that cannot be converted to bool: {e}"
            )

    elif field_type == "json":
        # Accept dict/list/str, return validated JSON string
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value)
            except (TypeError, ValueError) as e:
                raise TypeError(
                    f"Field '{field_name}' (json): Cannot serialize {type(value).__name__} "
                    f"to JSON: {e}"
                )

        if isinstance(value, str):
            if value == "":
                raise ValueError(
                    f"Field '{field_name}' (json): Empty string is not valid JSON. "
                    f"Use '{{}}' for empty object or '[]' for empty array."
                )
            try:
                # Validate by parsing (store original to preserve formatting/order)
                json.loads(value)
                return value
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Field '{field_name}' (json): Invalid JSON string: {e}"
                )

        raise TypeError(
            f"Field '{field_name}' (json) expects dict, list, or JSON string, "
            f"got {type(value).__name__}"
        )

    elif field_type == "json_collection":
        # json_collection fields cannot be set directly - must use collection_append()
        raise TypeError(
            f"Field '{field_name}' (json_collection): Cannot set collection directly. "
            f"Use collection_append() to add items. Leave field unset or None during add()."
        )

    else:
        # Unknown field type - treat as string
        # Note: Logger is not available here, so we can't log a warning.
        # This should be handled by the caller if needed.
        return str(value) if value is not None else None

def deserialize_value(value: Any, field_type: str) -> Any:
    """
    Deserialize value from SQLite storage type to Python type.
    
    This function converts SQLite values back to appropriate Python types:
    - bool: Convert INTEGER 0/1 to bool
    - int: Keep as INTEGER
    - float: Keep as REAL
    - json: Return as raw string (validated)
    - json_collection: Deserialize metadata to dict
    - str: Keep as TEXT
    
    Args:
        value: The value from SQLite storage
        field_type: The expected field type
        
    Returns:
        The deserialized Python value
        
    Note:
        On deserialization errors, returns the raw value to avoid data loss
    """
    if value is None:
        return None

    try:
        if field_type == "bool":
            # Stored as INTEGER 0/1
            return bool(value)

        elif field_type == "int":
            # Stored as INTEGER
            return int(value)

        elif field_type == "float":
            # Stored as REAL
            return float(value)

        elif field_type == "json_collection":
            # Metadata is stored as JSON string '{"collection_store": "...", "count": N}'
            # Deserialize it back to a dict for internal use (e.g., in get())
            if isinstance(value, str):
               return json.loads(value)
            # Note: Logger is not available here.
            # This should be handled by the caller if needed.
            return value # Return raw value if it wasn't a string as expected
            
        elif field_type == "json":
            # 'json' fields are returned as raw strings without parsing.
            # Validate that it's valid JSON before returning
            json_candidate = str(value)
            # Validate by parsing; the exception is caught in the big wrapping try
            json.loads(json_candidate)
            return json_candidate

        elif field_type == "str":
            return str(value)

        else:
            # Default for unknown types
            return value

    except (ValueError, TypeError, json.JSONDecodeError) as e:
        # Note: Logger is not available here.
        # This should be handled by the caller if needed.
        # Return raw value to avoid data loss
        return value

def get_collection_table_name(store_name: str, field_name: str) -> str:
    """
    Generate the child table name for a json_collection field.
    
    The naming convention is:
    {store_name}_{field_name}
    
    Examples:
    - "threads" store with "messages" field -> "threads_messages"
    - "documents" store with "attachments" field -> "documents_attachments"
    
    Args:
        store_name: Name of the parent store
        field_name: Name of the json_collection field
        
    Returns:
        The child table name
    """
    return f"{store_name}_{field_name}"
