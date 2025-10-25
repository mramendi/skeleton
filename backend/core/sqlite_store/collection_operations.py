"""
Handles operations for json_collection fields, which are append-only.

This module manages collection fields that store ordered lists of JSON objects:
- Appending items to collections with O(1) complexity
- Retrieving collection items with pagination
- Maintaining order and metadata
- Automatic FTS synchronization for collection items
"""
import json
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from .connection_manager import SQLiteConnectionManager
from .schema_manager import SQLiteSchemaManager
from .query_builder import build_pagination_clause
from .helpers import (
    validate_store_name,
    validate_field_name,
    get_collection_table_name,
)

logger = logging.getLogger("skeleton.sqlite_store")

class SQLiteCollectionOperations:
    """
    Handles append-only collection operations.
    
    This class manages json_collection fields which are stored in child tables:
    - Items are appended in O(1) time without write amplification
    - Collections maintain insertion order
    - Metadata tracks item count
    - FTS triggers automatically index collection content
    """

    def __init__(self, conn_manager: SQLiteConnectionManager, 
                 schema_manager: SQLiteSchemaManager):
        """
        Initialize collection operations.
        
        Args:
            conn_manager: Database connection manager
            schema_manager: Schema manager for validation
        """
        self._conn_manager = conn_manager
        self._schema_manager = schema_manager

    async def collection_append(self, user_id: str, store_name: str, record_id: str,
                               field_name: str, item: Any,
                               _db: Optional["aiosqlite.Connection"] = None) -> int:
        """
        Append an item to a json_collection field.
        
        This method:
        1. Validates the store, field, and record exist
        2. Verifies the field is a json_collection type
        3. Gets the next order index for the item
        4. Inserts the item into the child table
        5. Updates the metadata in the parent record
        6. Triggers FTS synchronization automatically
        
        Args:
            user_id: ID of the user owning the record
            store_name: Name of the store containing the record
            record_id: ID of the parent record
            field_name: Name of the json_collection field
            item: JSON-serializable item to append (dict or list)
            _db: Optional database connection (for transaction use)
            
        Returns:
            The order_index of the newly appended item (0-based)
            
        Raises:
            ValueError: If store/record/field doesn't exist or field is not a collection
            TypeError: If item is not JSON-serializable
        """
        async def collection_append_logic(db: "aiosqlite.Connection", user_id: str, store_name: str,
                                        record_id: str, field_name: str, 
                                        item_json: str) -> int:
            """Inner function containing the core append logic."""
            # Validate store exists and get schema
            schema = await self._schema_manager.find_store(store_name)
            if not schema:
                raise ValueError(f"Store '{store_name}' does not exist")

            # Validate field exists and is a collection
            if field_name not in schema:
                raise ValueError(f"Field '{field_name}' does not exist in store '{store_name}'")
            if schema[field_name] != "json_collection":
                raise ValueError(
                    f"Field '{field_name}' is type '{schema[field_name]}', not 'json_collection'. "
                    f"Use update() for non-collection fields."
                )

            # Validate parent record exists and belongs to user
            cursor = await db.execute(
                f'SELECT id FROM "{store_name}" WHERE id = ? AND user_id = ?', 
                (record_id, user_id)
            )
            if not await cursor.fetchone():
                raise ValueError(f"Record '{record_id}' does not exist in store '{store_name}'")

            # Get child table name
            child_table = get_collection_table_name(store_name, field_name)

            # Get current item count for order index
            cursor = await db.execute(
                f'SELECT COUNT(*) FROM "{child_table}" WHERE parent_id = ?', 
                (record_id,)
            )
            count_row = await cursor.fetchone()
            order_index = count_row[0] if count_row else 0

            # Insert the new item
            item_id = str(uuid.uuid4())
            now = datetime.now().isoformat()

            await db.execute(f'''
                INSERT INTO "{child_table}" (id, parent_id, order_index, item_json, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (item_id, record_id, order_index, item_json, now))

            # Update metadata in parent record (user_id already verified above)
            validated_field_name = validate_field_name(field_name)
            metadata_json = json.dumps({
                "collection_store": child_table,
                "count": order_index + 1
            })

            await db.execute(f'''
                UPDATE "{store_name}"
                SET "{validated_field_name}" = ?, updated_at = ?
                WHERE id = ?
            ''', (metadata_json, now, record_id))

            logger.info(
                f"Appended item to '{field_name}' in record '{record_id}' "
                f"at index {order_index} (new count: {order_index + 1})"
            )

            return order_index

        # Validate inputs
        store_name = validate_store_name(store_name)
        field_name = validate_field_name(field_name)

        # Validate item is JSON-serializable
        if not isinstance(item, (dict, list)):
            raise TypeError(
                f"Collection item must be dict or list, got {type(item).__name__}"
            )

        # Serialize item to JSON
        try:
            item_json = json.dumps(item)
        except (TypeError, ValueError) as e:
            raise TypeError(f"Cannot serialize item to JSON: {e}")

        logger.debug(
            f"Appending item to collection '{field_name}' in record '{record_id}' "
            f"of store '{store_name}'"
        )

        # Execute within provided transaction or create new one
        if _db:
            return await collection_append_logic(
                _db, user_id, store_name, record_id, field_name, item_json
            )
        else:
            async with self._conn_manager.get_write_db() as db:
                return await collection_append_logic(
                    db, user_id, store_name, record_id, field_name, item_json
                )


    async def collection_get(self, user_id: str, store_name: str, record_id: str,
                            field_name: str, limit: Optional[int] = None,
                            offset: int = 0) -> List[Any]:
        """
        Get items from a json_collection field with pagination.
        
        This method:
        1. Validates the record exists and belongs to the user
        2. Queries the child table for collection items
        3. Returns items in insertion order with pagination
        4. Deserializes JSON items back to Python objects
        
        Args:
            user_id: ID of the user owning the record
            store_name: Name of the store containing the record
            record_id: ID of the parent record
            field_name: Name of the json_collection field
            limit: Maximum number of items to return
            offset: Number of items to skip
            
        Returns:
            List of deserialized collection items (dicts or lists)
            
        Raises:
            ValueError: If record doesn't exist or belongs to wrong user
            aiosqlite.Error: If database operation fails
        """
        # Get read connection for querying
        db = await self._conn_manager.get_read_db()

        # Validate inputs
        store_name = validate_store_name(store_name)
        field_name = validate_field_name(field_name)

        logger.debug(
            f"Getting items from collection '{field_name}' in record '{record_id}' "
            f"of store '{store_name}' (limit={limit}, offset={offset})"
        )

        # Get child table name for the collection
        child_table = get_collection_table_name(store_name, field_name)

        # Verify parent record exists and belongs to the specified user
        cursor = await db.execute(
            f'SELECT id FROM "{store_name}" WHERE id = ? AND user_id = ?', 
            (record_id, user_id)
        )
        if not await cursor.fetchone():
            raise ValueError(
                f"Record '{record_id}' does not exist or does not belong to user '{user_id}' "
                f"in store '{store_name}'"
            )

        # Build query parameters starting with parent_id
        params = [record_id]

        # Build pagination clause for the query
        pagination_sql, pagination_params = await build_pagination_clause(
            limit=limit, 
            offset=offset
        )
        params.extend(pagination_params)

        # Query for collection items in insertion order
        query = f'''
            SELECT item_json FROM "{child_table}"
            WHERE parent_id = ?
            ORDER BY order_index ASC
            {pagination_sql}
        '''

        # Execute the query
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        # Deserialize JSON items back to Python objects
        items = []
        for row in rows:
            try:
                item = json.loads(row[0])
                items.append(item)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to deserialize collection item: {e}")
                # Skip malformed items but continue processing others
                continue

        logger.debug(
            f"Retrieved {len(items)} items from collection '{field_name}' "
            f"in record '{record_id}'"
        )

        return items
