"""
Handles standard CRUD operations and complex queries.

This module provides the core database operations:
- Create (add new records)
- Read (get single records, find multiple, count)
- Update (modify existing records)
- Delete (remove records)
- Full-text search using FTS5
- Complex querying with filters, pagination, and sorting
"""
import json
import logging
import uuid
import aiosqlite
from datetime import datetime
from typing import Dict, Any, Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from .connection_manager import SQLiteConnectionManager
from .schema_manager import SQLiteSchemaManager
from .query_builder import build_pagination_clause, build_where_clause
from .helpers import (
    serialize_value,
    deserialize_value,
    validate_store_name,
    validate_field_name,
)

logger = logging.getLogger("skeleton.sqlite_store")

class SQLiteCrudOperations:
    """
    Handles Create, Read, Update, Delete and query operations.
    
    This class provides the fundamental CRUD operations for stores:
    - Adding new records with validation
    - Retrieving records by ID or with filters
    - Updating records with partial or full updates
    - Deleting records with cascade support
    - Counting and finding records with complex queries
    - Full-text search across indexed fields
    """

    def __init__(self, conn_manager: SQLiteConnectionManager, 
                 schema_manager: SQLiteSchemaManager):
        """
        Initialize CRUD operations.
        
        Args:
            conn_manager: Database connection manager
            schema_manager: Schema manager for validation
        """
        self._conn_manager = conn_manager
        self._schema_manager = schema_manager

    async def add(self, user_id: str, store_name: str, data: Dict[str, Any], 
                  record_id: Optional[str] = None,
                  _db: Optional["aiosqlite.Connection"] = None) -> str:
        """
        Add a new record to a store.
        
        This method:
        1. Validates the store exists
        2. Checks for duplicate ID
        3. Validates and serializes all field values
        4. Inserts the record with timestamps
        5. Handles json_collection field initialization
        
        Args:
            user_id: ID of the user owning this record
            store_name: Name of the store to add to
            data: Dictionary of field values
            record_id: Optional record ID (generated if not provided)
            _db: Optional database connection (for transaction use)
            
        Returns:
            The ID of the created record
            
        Raises:
            ValueError: If store doesn't exist or ID already exists
            TypeError: If data values cannot be converted to field types
        """
        async def add_logic(db: "aiosqlite.Connection", user_id: str, store_name: str, 
                          data: Dict[str, Any], record_id: str) -> str:
            """Inner function containing the core add logic."""
            # Check if ID already exists (primary key constraint)
            cursor = await db.execute(
                f'SELECT 1 FROM "{store_name}" WHERE id = ?', 
                (record_id,)
            )
            if await cursor.fetchone():
                raise ValueError(f"Record ID '{record_id}' already exists in store '{store_name}'")

            # Get store schema for validation
            schema = await self._schema_manager.find_store(store_name)
            if not schema:
                raise ValueError(f"Store '{store_name}' does not exist")

            # Build INSERT query components
            fields = ["user_id"]
            placeholders = ['?']
            values = [user_id]

            # Process each field in the schema
            for field_name, field_type in schema.items():
                validated_field_name = validate_field_name(field_name)
                fields.append(f'"{validated_field_name}"')
                placeholders.append('?')
                
                # Get and serialize the value
                raw_value = data.get(field_name)
                serialized_value = serialize_value(
                    value=raw_value, 
                    field_type=field_type, 
                    field_name=field_name, 
                    store_name=store_name
                )
                values.append(serialized_value)

            # Execute the INSERT with timestamps
            insert_sql = f'''
                INSERT INTO "{store_name}" (id, {', '.join(fields)}, created_at, updated_at)
                VALUES (?, {', '.join(placeholders)}, ?, ?)
            '''
            now = datetime.now().isoformat()
            await db.execute(
                insert_sql, 
                [record_id] + values + [now, now]
            )
            logger.debug(
                f"Successfully added record '{record_id}' to store '{store_name}' for user '{user_id}'"
            )
            return record_id

        # Generate UUID if not provided
        if record_id is None:
            record_id = str(uuid.uuid4())
            
        # Validate inputs
        store_name = validate_store_name(store_name)
        logger.debug(f"Adding record to store '{store_name}' with ID '{record_id}'")

        # Execute within provided transaction or create new one
        if _db:
            return await add_logic(_db, user_id, store_name, data, record_id)
        else:
            async with self._conn_manager.get_write_db() as db:
                return await add_logic(db, user_id, store_name, data, record_id)

    async def get(self, user_id: str, store_name: str, record_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a single record by ID, returning raw collection metadata.
        
        This method:
        1. Validates the store exists and gets its schema
        2. Queries for the specific record with user_id filter
        3. Deserializes all fields according to their types
        4. Returns collection metadata (not the actual collection items)
        
        Args:
            user_id: ID of the user owning the record
            store_name: Name of the store containing the record
            record_id: ID of the record to retrieve
            
        Returns:
            Dictionary containing the record data, or None if not found
            
        Note:
            For json_collection fields, this returns metadata like:
            {"collection_store": "table_name", "count": N}
            Use the main facade's get() with load_collections=True to get actual items
        """
        # Get read connection for the query
        db = await self._conn_manager.get_read_db()
        logger.debug(f"Getting record '{record_id}' from store '{store_name}'")
        
        # Validate store name
        store_name = validate_store_name(store_name)

        # Get store schema for field type information
        schema = await self._schema_manager.find_store(store_name)
        if not schema:
            logger.error(f"Schema not found for store {store_name}. Does the store exist?")
            return None

        # Add system fields to schema for deserialization
        schema_with_meta = schema.copy()
        schema_with_meta.update({
            "id": "str", 
            "user_id": "str", 
            "created_at": "str", 
            "updated_at": "str"
        })

        # Query for the specific record with user isolation
        cursor = await db.execute(
            f'SELECT * FROM "{store_name}" WHERE id = ? AND user_id = ?', 
            (record_id, user_id)
        )
        row = await cursor.fetchone()
        if not row:
            logger.warning(f"Record '{record_id}' not found in store '{store_name}'")
            return None

        # Get column names from cursor description
        columns = [desc[0] for desc in cursor.description]
        
        # Deserialize each field according to its type
        result = {}
        for i, col_name in enumerate(columns):
            raw_value = row[i]
            field_type = schema_with_meta.get(col_name, "str")
            result[col_name] = deserialize_value(raw_value, field_type)

        logger.debug(f"Found record '{record_id}' in store '{store_name}'")
        return result

    async def update(self, user_id: str, store_name: str, record_id: str,
                     updates: Dict[str, Any], partial: bool = True) -> bool:
        """
        Update a record by ID.
        
        This method:
        1. Validates the record exists and belongs to the user
        2. Validates all update fields against the store schema
        3. Prevents updates to json_collection fields (append-only)
        4. Serializes all values according to field types
        5. Updates the record with a new timestamp
        
        Args:
            user_id: ID of the user owning the record
            store_name: Name of the store containing the record
            record_id: ID of the record to update
            updates: Dictionary of field values to update
            partial: If True, only updates specified fields (always True)
            
        Returns:
            bool: True if record was updated, False if no changes made
            
        Raises:
            ValueError: If record doesn't exist, belongs to wrong user, or invalid fields
            TypeError: If values cannot be converted to field types
        """
        logger.debug(f"Updating record '{record_id}' in store '{store_name}' with: {updates}")
        
        # Validate store name
        store_name = validate_store_name(store_name)
        
        # Return early if no updates provided
        if not updates:
            return True

        # First verify record exists and belongs to user (using read connection)
        read_db = await self._conn_manager.get_read_db()
        cursor = await read_db.execute(
            f'SELECT id FROM "{store_name}" WHERE id = ? AND user_id = ?', 
            (record_id, user_id)
        )
        if not await cursor.fetchone():
            raise ValueError(
                f"Record '{record_id}' does not exist or does not belong to user '{user_id}' "
                f"in store '{store_name}'"
            )

        # Perform the update within a write transaction
        async with self._conn_manager.get_write_db() as db:
            # Get store schema for validation
            schema = await self._schema_manager.find_store(store_name)
            if not schema:
                raise ValueError(f"Store '{store_name}' does not exist")

            # Validate all update fields exist in schema
            schema_fields = set(schema.keys())
            update_fields = set(updates.keys())
            invalid_fields = update_fields - schema_fields
            if invalid_fields:
                raise ValueError(
                    f"Invalid field(s) provided for update in store '{store_name}': "
                    f"{', '.join(invalid_fields)}. Allowed fields: {', '.join(schema_fields)}"
                )

            # Prevent updates to json_collection fields (they are append-only)
            collection_fields_in_update = [
                f for f in updates.keys() 
                if f in schema and schema[f] == "json_collection"
            ]
            if collection_fields_in_update:
                raise ValueError(
                    f"Cannot update json_collection fields via update(): {collection_fields_in_update}. "
                    f"Use collection_append() to add items."
                )

            # Build SET clauses for the UPDATE statement
            set_clauses = []
            params = []
            for field_name, value in updates.items():
                validated_field_name = validate_field_name(field_name)
                field_type = schema[field_name]
                serialized_value = serialize_value(
                    value=value, 
                    field_type=field_type, 
                    field_name=field_name, 
                    store_name=store_name
                )
                set_clauses.append(f'"{validated_field_name}" = ?')
                params.append(serialized_value)

            # Combine all SET clauses
            set_sql = ', '.join(set_clauses)
            if not set_sql:
                logger.warning("Update called with empty valid updates after filtering.")
                return True

            # Execute the UPDATE with timestamp
            query = f'UPDATE "{store_name}" SET {set_sql}, updated_at = ? WHERE id = ? AND user_id = ?'
            params.extend([datetime.now().isoformat(), record_id, user_id])
            cursor = await db.execute(query, params)
            updated = cursor.rowcount > 0
            logger.debug(f"Update result for record '{record_id}': {updated}")
            return updated

    async def delete(self, user_id: str, store_name: str, record_id: str) -> bool:
        """
        Delete a record by ID and associated collection/FTS entries.
        
        This method:
        1. Validates the store exists
        2. Deletes the record with user_id filter for security
        3. Relies on foreign key constraints to cascade delete:
           - Collection items in child tables
           - FTS entries via triggers
        
        Args:
            user_id: ID of the user owning the record
            store_name: Name of the store containing the record
            record_id: ID of the record to delete
            
        Returns:
            bool: True if record was deleted, False if it didn't exist
            
        Raises:
            ValueError: If store doesn't exist
            aiosqlite.Error: If database operation fails
        """
        logger.debug(f"Deleting record '{record_id}' from store '{store_name}'")
        
        # Validate store name
        store_name = validate_store_name(store_name)
        deleted = False
        
        try:
            # Perform deletion within a write transaction
            async with self._conn_manager.get_write_db() as db:
                # Validate store exists
                schema = await self._schema_manager.find_store(store_name)
                if not schema:
                    raise ValueError(f"Store '{store_name}' does not exist")
                
                # Delete from FTS table first to maintain consistency
                cursor = await db.execute(
                    f'DELETE FROM "fts_{store_name}" WHERE parent_id = ? AND user_id = ?',
                    (record_id, user_id)
                )
                
                # Delete the record (cascade handles child tables)
                cursor = await db.execute(
                    f'DELETE FROM "{store_name}" WHERE id = ? AND user_id = ?', 
                    (record_id, user_id)
                )
                deleted = cursor.rowcount > 0
                
            logger.debug(f"Delete result for record '{record_id}': {deleted}")
            return deleted
            
        except (aiosqlite.Error, ValueError) as e:
            logger.error(f"Error deleting record '{record_id}' from store '{store_name}': {e}")
            raise

    async def count(self, user_id: str, store_name: str, filters: Dict[str, Any] = None) -> int:
        """
        Count records matching filters.
        
        This method:
        1. Builds WHERE clause from filters with proper validation
        2. Always includes user_id filter for multi-tenancy
        3. Returns the count of matching records
        
        Args:
            user_id: ID of the user whose records to count
            store_name: Name of the store to query
            filters: Optional dictionary of field filters
            
        Returns:
            int: Number of records matching the criteria
            
        Raises:
            ValueError: If store doesn't exist or filters are invalid
            aiosqlite.Error: If database operation fails
        """
        # Get read connection for counting
        db = await self._conn_manager.get_read_db()
        logger.debug(f"Counting records in store '{store_name}' with filters: {filters}")
        
        # Validate store name
        store_name = validate_store_name(store_name)
        
        try:
            # Build WHERE clause with filters and user_id
            where_sql, params = await build_where_clause(
                db=db, 
                store_name=store_name, 
                user_id=user_id, 
                filters=filters
            )
            
            # Execute COUNT query
            query = f'SELECT COUNT(*) FROM "{store_name}" {where_sql}'
            cursor = await db.execute(query, params)
            result = await cursor.fetchone()
            count = result[0] if result else 0
            
            logger.debug(f"Count result for store '{store_name}' with filters {filters}: {count}")
            return count
            
        except (aiosqlite.Error, ValueError) as e:
            logger.error(f"Error during count for store '{store_name}': {e}")
            raise

    async def find(self, user_id: str, store_name: str, filters: Dict[str, Any] = None,
                   limit: Optional[int] = None, offset: int = 0,
                   order_by: str = None, order_desc: bool = False) -> List[Dict[str, Any]]:
        """
        Find records with optional filters, pagination, and sorting.
        
        This method:
        1. Builds WHERE clause from filters with validation
        2. Validates order_by field against schema
        3. Applies pagination with LIMIT/OFFSET
        4. Deserializes all fields according to their types
        5. Returns collection metadata (not actual items)
        
        Args:
            user_id: ID of the user whose records to find
            store_name: Name of the store to query
            filters: Optional dictionary of field filters
            limit: Maximum number of records to return
            offset: Number of records to skip
            order_by: Field name to sort by
            order_desc: If True, sort in descending order
            
        Returns:
            List of dictionaries containing matching records
            
        Raises:
            ValueError: If store doesn't exist or filters/order_by are invalid
            aiosqlite.Error: If database operation fails
        """
        # Get read connection for querying
        db = await self._conn_manager.get_read_db()
        logger.debug(
            f"Finding records in store '{store_name}' with filters: {filters}, "
            f"limit: {limit}, offset: {offset}"
        )
        
        # Validate store name
        store_name = validate_store_name(store_name)
        
        try:
            # Build WHERE clause with filters and user_id
            where_sql, params = await build_where_clause(
                db=db, 
                store_name=store_name, 
                user_id=user_id, 
                filters=filters
            )
            
            # Get schema for field validation and deserialization
            schema = await self._schema_manager.find_store(store_name)
            if not schema: 
                raise ValueError(f"Store '{store_name}' not found for ordering.")
            
            # Add system fields to schema for deserialization
            schema_with_meta = schema.copy()
            schema_with_meta.update({
                "id": "str", 
                "created_at": "str", 
                "updated_at": "str"
            })

            # Build ORDER BY clause if specified
            order_sql = ""
            if order_by:
                # Validate order_by field exists in schema
                if order_by not in schema_with_meta:
                     raise ValueError(
                        f"Invalid order_by field '{order_by}'. "
                        f"Allowed fields: {', '.join(schema.keys())}"
                     )
                # Whitelist order_by against schema to prevent SQL injection
                validated_order_by = validate_field_name(order_by)
                direction = "DESC" if order_desc else "ASC"
                order_sql = f'ORDER BY "{validated_order_by}" {direction}'

            # Build pagination clause
            pagination_sql, pagination_params = await build_pagination_clause(
                limit=limit, 
                offset=offset
            )
            params.extend(pagination_params)

            # Execute the complete query
            query = f'SELECT * FROM "{store_name}" {where_sql} {order_sql} {pagination_sql}'
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            # Return empty list if no results
            if not rows: 
                return []
                
            # Get column names from cursor description
            columns = [desc[0] for desc in cursor.description]
            
            # Deserialize each row into a dictionary
            results = []
            for row in rows:
                record_dict = {}
                for i, col_name in enumerate(columns):
                    raw_value = row[i]
                    field_type = schema_with_meta.get(col_name, "str")
                    record_dict[col_name] = deserialize_value(raw_value, field_type)
                results.append(record_dict)
                
            logger.debug(f"Found {len(results)} records in store '{store_name}'")
            return results
            
        except (aiosqlite.Error, ValueError) as e:
            logger.error(f"Error during find for store '{store_name}': {e}")
            raise

    async def full_text_search(self, user_id: str, store_name: str, query: str,
                               limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Full-text search across all indexable fields in a store.
        
        This method:
        1. Searches the FTS virtual table for matching records
        2. Returns parent records ordered by relevance (rank)
        3. Applies pagination to the results
        4. Deserializes all fields according to their types
        
        Args:
            user_id: ID of the user whose records to search
            store_name: Name of the store to search
            query: Full-text search query string
            limit: Maximum number of records to return
            offset: Number of records to skip
            
        Returns:
            List of dictionaries containing matching records
            
        Raises:
            ValueError: If store doesn't exist
            aiosqlite.Error: If database operation fails
        """
        # Get read connection for searching
        db = await self._conn_manager.get_read_db()
        
        # Validate store name and build FTS table name
        store_name = validate_store_name(store_name)
        fts_table_name = f"fts_{store_name}"
        
        logger.debug(
            f"Full-text search in store '{store_name}' for user '{user_id}' with query '{query}'"
        )
        
        try:
            # Validate store exists
            schema = await self._schema_manager.find_store(store_name)
            if not schema:
                raise ValueError(f"Store '{store_name}' does not exist")

            # Build FTS match query with wildcard for partial matches
            match_string = f'"{query}"*'
            
            # Build pagination clause
            pagination_sql, pagination_params = await build_pagination_clause(
                limit=limit, 
                offset=offset
            )

            # Step 1: Find matching parent IDs from FTS table
            parent_id_query = f'''
                SELECT DISTINCT parent_id
                FROM "{fts_table_name}"
                WHERE "{fts_table_name}" MATCH ? AND user_id = ?
                ORDER BY rank
                {pagination_sql}
            '''
            cursor = await db.execute(
                parent_id_query, 
                [match_string, user_id] + pagination_params
            )
            parent_rows = await cursor.fetchall()
            parent_ids = [row[0] for row in parent_rows]
            
            # Return empty list if no matches
            if not parent_ids: 
                return []

            # Step 2: Fetch full records from main table
            placeholders = ','.join('?' * len(parent_ids))
            main_query = f'SELECT * FROM "{store_name}" WHERE id IN ({placeholders}) AND user_id = ?'
            cursor = await db.execute(main_query, parent_ids + [user_id])
            rows = await cursor.fetchall()
            
            # Return empty list if no records found
            if not rows: 
                return []

            # Get column names for deserialization
            columns = [desc[0] for desc in cursor.description]
            
            # Add system fields to schema for deserialization
            schema_with_meta = schema.copy()
            schema_with_meta.update({
                "id": "str", 
                "created_at": "str", 
                "updated_at": "str", 
                "user_id": "str"
            })

            # Deserialize each row into a dictionary
            results = []
            for row in rows:
                record_dict = {}
                for i, col_name in enumerate(columns):
                    raw_value = row[i]
                    field_type = schema_with_meta.get(col_name, "str")
                    record_dict[col_name] = deserialize_value(raw_value, field_type)
                results.append(record_dict)

            logger.debug(
                f"Found {len(results)} records matching '{query}' in store '{store_name}'"
            )
            return results
            
        except (aiosqlite.Error, ValueError) as e:
            logger.error(f"Error during full-text search for store '{store_name}': {e}")
            raise
