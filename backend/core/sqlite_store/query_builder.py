"""
Stateless helper functions for building SQL query components.

This module provides functions to construct SQL query fragments:
- Pagination clauses (LIMIT/OFFSET)
- WHERE clauses with various operators
- Parameter validation and serialization
"""
import json
from typing import Dict, Any, List, Tuple, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite
from .helpers import serialize_value, validate_field_name

async def build_pagination_clause(limit: Optional[int], offset: int) -> Tuple[str, List[int]]:
    """
    Builds SQL LIMIT/OFFSET clause and parameters, with validation.
    
    This function creates pagination clauses for SQL queries:
    - Can handle LIMIT only, OFFSET only, or both
    - Validates that values are non-negative integers
    - Returns empty strings when no pagination is needed
    
    Args:
        limit: Maximum number of records to return (None = no limit)
        offset: Number of records to skip before returning
        
    Returns:
        A tuple containing:
        - SQL string fragment (e.g., "LIMIT ? OFFSET ?" or "")
        - List of parameters (e.g., [10, 20] or [])
        
    Raises:
        ValueError: If limit or offset are invalid types or negative
    """
    sql_parts = []
    params = []

    # Handle LIMIT clause
    if limit is not None:
        if not isinstance(limit, int) or limit < 0:
            raise ValueError("Limit must be a non-negative integer.")
        sql_parts.append("LIMIT ?")
        params.append(limit)

    # Handle OFFSET clause (only if non-zero)
    if offset:
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("Offset must be a non-negative integer.")
        sql_parts.append("OFFSET ?")
        params.append(offset)

    # Join parts with space, handles cases with only LIMIT, only OFFSET, both, or neither
    sql_string = " ".join(sql_parts)

    return sql_string, params

async def build_where_clause(db: "aiosqlite.Connection", store_name: str, user_id: str, 
                           filters: Optional[Dict[str, Any]]) -> tuple[str, list]:
    """
    Validates filters against the store schema and builds WHERE clause.
    
    This function:
    1. Fetches the store schema for validation
    2. Validates filter field names against schema
    3. Serializes filter values according to field types
    4. Builds WHERE clause with proper parameterization
    5. Supports various operators: =, $like, $gt, $gte, $lt, $lte
    
    Args:
        db: Database connection for fetching schema
        store_name: Name of the store being queried
        user_id: User ID for multi-tenancy (always included)
        filters: Dictionary of field filters
        
    Returns:
        A tuple containing:
        - WHERE clause SQL string (including "WHERE" if conditions exist)
        - List of parameters for the query
        
    Raises:
        ValueError: If store doesn't exist or filter fields are invalid
        
    Examples:
        filters = {
            "name": "John",  # Exact match
            "email": {"$like": "%@example.com"},  # LIKE query
            "created_at": {"$gt": "2023-01-01", "$lt": "2023-12-31"}  # Range
        }
    """
    if not filters:
        # Always include user_id filter even if no other filters
        return "WHERE user_id = ?", [user_id]

    # Get Schema for validation and type information
    schema_cursor = await db.execute(
        "SELECT schema_json FROM _stores WHERE name = ?", 
        (store_name,)
    )
    schema_row = await schema_cursor.fetchone()
    if not schema_row:
        raise ValueError(f"Store '{store_name}' does not exist")
    schema = json.loads(schema_row[0])
    
    # Add meta fields to the schema map for validation
    schema_with_meta = schema.copy()
    schema_with_meta.update({
         "id": "str",
         "created_at": "str",  # ISO8601 timestamps compare correctly as strings
         "updated_at": "str"
    })
    allowed_fields = set(schema_with_meta.keys())

    # Start with user_id filter for multi-tenancy
    where_clauses = ['user_id = ?']
    params = [user_id]

    # Process each filter condition
    for field_name, filter_condition in filters.items():
        # Validate field name exists in schema
        if field_name not in allowed_fields:
            raise ValueError(
                f"Invalid filter field '{field_name}' for store '{store_name}'. "
                f"Allowed fields: {', '.join(allowed_fields)}"
            )

        validated_field_name = validate_field_name(field_name)
        field_type = schema_with_meta.get(field_name, "str")

        # Handle different filter types
        if isinstance(filter_condition, dict):
            # Complex filters with operators
            for operator, value in filter_condition.items():
                serialized_value = serialize_value(
                    value=value, 
                    field_type=field_type, 
                    field_name=field_name, 
                    store_name=store_name
                )
                
                if operator == "$like":
                    # LIKE operator for pattern matching
                    if not isinstance(serialized_value, str):
                        serialized_value = str(value)  # Fallback
                    where_clauses.append(f'"{validated_field_name}" LIKE ?')
                    params.append(serialized_value)
                    
                elif operator in ("$gt", "$gte", "$lt", "$lte"):
                    # Range operators for numeric and date comparisons
                    # Note: ISO8601 string timestamps compare correctly
                    sql_operator_map = {
                        "$gt": ">", 
                        "$gte": ">=", 
                        "$lt": "<", 
                        "$lte": "<="
                    }
                    sql_operator = sql_operator_map[operator]
                    where_clauses.append(f'"{validated_field_name}" {sql_operator} ?')
                    params.append(serialized_value)
                    
                else:
                    raise ValueError(
                        f"Unsupported filter operator '{operator}' for field '{field_name}'"
                    )
        else:
            # Simple exact match
            serialized_value = serialize_value(
                value=filter_condition, 
                field_type=field_type, 
                field_name=field_name, 
                store_name=store_name
            )
            where_clauses.append(f'"{validated_field_name}" = ?')
            params.append(serialized_value)

    # Combine all clauses with AND
    where_sql = f"WHERE {' AND '.join(where_clauses)}"
    return where_sql, params
