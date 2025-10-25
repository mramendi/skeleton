"""
Handles administrative operations like import/export and index management.

This module provides administrative functions:
- Creating and managing database indexes
- Exporting store data with collections
- Importing data with schema validation
- Bulk operations for data migration
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
from .crud_operations import SQLiteCrudOperations
from .collection_operations import SQLiteCollectionOperations
from .helpers import (
    validate_store_name,
    validate_field_name,
    get_collection_table_name,
    deserialize_value,
    sanitize_identifier,
)

logger = logging.getLogger("skeleton.sqlite_store")

class SQLiteAdminOperations:
    """
    Handles administrative and bulk data operations.
    
    This class provides high-level administrative functions:
    - Index creation and management for performance optimization
    - Data export with full collection support
    - Data import with schema validation and migration
    - Store statistics and metadata management
    """

    def __init__(self, conn_manager: SQLiteConnectionManager, 
                 schema_manager: SQLiteSchemaManager,
                 crud_ops: SQLiteCrudOperations, 
                 collection_ops: SQLiteCollectionOperations):
        """
        Initialize admin operations.
        
        Args:
            conn_manager: Database connection manager
            schema_manager: Schema manager for validation
            crud_ops: CRUD operations for data manipulation
            collection_ops: Collection operations for handling json_collection fields
        """
        self._conn_manager = conn_manager
        self._schema_manager = schema_manager
        self._crud_ops = crud_ops
        self._collection_ops = collection_ops

    async def create_index(self, store_name: str, field_name: str,
                          index_type: str = "default") -> bool:
        """
        Create an index on a field for performance optimization.
        
        This method creates database indexes to improve query performance:
        - Default indexes: Standard B-tree index for fast lookups
        - Unique indexes: Enforce uniqueness constraint on field values
        - Index names follow pattern: idx_{store}_{field}
        
        Args:
            store_name: Name of the store containing the field
            field_name: Name of the field to index
            index_type: Type of index ("default" or "unique")
            
        Returns:
            bool: True if index was created successfully, False otherwise
            
        Raises:
            ValueError: If store or field name is invalid
            
        Note:
            Uses "IF NOT EXISTS" to make the operation idempotent
        """
        logger.info(
            f"Creating {index_type} index on field '{field_name}' in store '{store_name}'"
        )

        # Validate inputs
        store_name = validate_store_name(store_name)
        field_name = validate_field_name(field_name)

        # Generate safe index name
        safe_store_name = sanitize_identifier(store_name)
        index_name = f"idx_{safe_store_name}_{field_name}"

        async with self._conn_manager.get_write_db() as db:
            try:
                # Build CREATE INDEX SQL based on type
                if index_type == "unique":
                    sql = f'CREATE UNIQUE INDEX IF NOT EXISTS "{index_name}" ON "{store_name}"("{field_name}")'
                else:
                    sql = f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{store_name}"("{field_name}")'

                logger.debug(f"Creating index with SQL: {sql}")
                await db.execute(sql)

                logger.info(
                    f"Successfully created index on field '{field_name}' in store '{store_name}'"
                )
                return True
                
            except Exception as e:
                logger.error(
                    f"Failed to create index on field '{field_name}' in store '{store_name}': {e}"
                )
                return False

    async def get_indexes(self, store_name: str) -> List[Dict[str, Any]]:
        """
        Get information about indexes on a store.
        
        This method:
        1. Queries the sqlite_master table for index metadata
        2. Returns both index names and their CREATE SQL statements
        3. Filters indexes to only those for the specified store
        
        Args:
            store_name: Name of the store to get indexes for
            
        Returns:
            List of dictionaries containing:
            - name: Index name
            - sql: CREATE INDEX SQL statement
            
        Raises:
            ValueError: If store name is invalid
            aiosqlite.Error: If database query fails
        """
        # Get read connection for querying metadata
        db = await self._conn_manager.get_read_db()
        logger.debug(f"Getting indexes for store '{store_name}'")
        
        # Validate store name
        store_name = validate_store_name(store_name)

        # Query sqlite_master for index information
        cursor = await db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?",
            (store_name,)
        )
        rows = await cursor.fetchall()

        # Format results as list of dictionaries
        results = [{"name": row[0], "sql": row[1]} for row in rows]
        logger.debug(f"Found {len(results)} indexes for store '{store_name}'")
        return results

    async def export_store(self, store_name: str,
                           format: str = "json") -> Dict[str, Any]:
        """
        Export all records from a store, including json_collection field items.
        
        This method:
        1. Validates the store exists and gets its schema
        2. Exports all parent records with proper deserialization
        3. Loads all collection items for json_collection fields
        4. Groups collection items by parent record in Python
        5. Returns a complete export with schema and data
        
        Args:
            store_name: Name of the store to export
            format: Export format (currently only "json" supported)
            
        Returns:
            Dictionary containing:
            - store: Store name
            - schema: Store schema definition
            - records: List of all records with collections loaded
            
        Raises:
            ValueError: If store doesn't exist
            aiosqlite.Error: If database operations fail
            json.JSONDecodeError: If collection items can't be deserialized
            
        Note:
            This is an admin function that exports ALL data for ALL users.
            Use with caution and never expose to non-admin users.
        """
        # Get read connection for export
        db = await self._conn_manager.get_read_db()
        logger.info(f"Exporting store '{store_name}' in format: {format}")
        
        # Validate store name
        store_name = validate_store_name(store_name)

        try:
            # Get store schema for validation and field type info
            schema = await self._schema_manager.find_store(store_name)
            if not schema:
                logger.warning(f"Store '{store_name}' not found for export")
                return {"store": store_name, "records": [], "schema": {}}

            # Step 1: Export all parent records
            async with db.execute(f'SELECT * FROM "{store_name}"') as cursor:
                rows = await cursor.fetchall()
                if not rows:
                     return {"store": store_name, "schema": schema, "records": []}
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
            
            # Deserialize all parent records
            records = []
            records_by_id = {}  # Map for efficient collection item grouping
            for row in rows:
                record_dict = {}
                for i, col_name in enumerate(columns):
                    raw_value = row[i]
                    field_type = schema_with_meta.get(col_name, "str")
                    record_dict[col_name] = deserialize_value(raw_value, field_type)
                records.append(record_dict)
                records_by_id[record_dict["id"]] = record_dict

            # Step 2: Load collection items for json_collection fields
            collection_fields = [f for f, t in schema.items() if t == "json_collection"]
            if collection_fields:
                logger.debug(f"Loading {len(collection_fields)} collection field(s): {collection_fields}")

                # Process each collection field
                for field_name in collection_fields:
                    child_table = get_collection_table_name(store_name, field_name)
                    logger.debug(f"Fetching all items for '{field_name}' from '{child_table}'")

                    # Query all collection items ordered by parent and position
                    query = f'''
                        SELECT parent_id, item_json FROM "{child_table}"
                        ORDER BY parent_id ASC, order_index ASC
                    '''

                    # Group items by parent_id in Python to avoid SQL parameter limits
                    items_by_parent = {}
                    async with db.execute(query) as item_cursor:
                        async for parent_id, item_json_str in item_cursor:
                            # Only process items whose parent was actually exported
                            if parent_id in records_by_id:
                                try:
                                    item = json.loads(item_json_str)
                                    if parent_id not in items_by_parent:
                                        items_by_parent[parent_id] = []
                                    items_by_parent[parent_id].append(item)
                                except json.JSONDecodeError as e:
                                    logger.error(
                                        f"Failed to deserialize item for {parent_id} in {field_name}: {e}"
                                    )

                    # Replace collection metadata with actual items
                    for parent_id, record_dict in records_by_id.items():
                        record_dict[field_name] = items_by_parent.get(parent_id, [])

            # Step 3: Assemble final export result
            result = {
                "store": store_name,
                "schema": schema,
                "records": records
            }
            logger.info(f"Exported {len(records)} records from store '{store_name}'")
            return result

        except (aiosqlite.Error, ValueError, TypeError, json.JSONDecodeError) as e:
             logger.error(f"Error exporting store '{store_name}': {e}")
             raise

    async def import_store(self, store_name: str,
                           data: Dict[str, Any],
                           replace_existing: bool = False) -> int:
        """
        Import records into a store. Creates/updates the store schema if needed.
        
        This method:
        1. Validates import data contains required schema
        2. Ensures store exists with compatible schema
        3. Optionally clears existing data
        4. Imports parent records with collection handling
        5. Appends collection items for json_collection fields
        6. Handles duplicate IDs gracefully
        
        Args:
            store_name: Name of the store to import into
            data: Dictionary containing:
                  - schema: Store schema definition
                  - records: List of records to import
                  - cacheable: Optional cacheable flag
            replace_existing: If True, clears existing data before import
            
        Returns:
            int: Number of successfully imported records
            
        Raises:
            ValueError: If import data is invalid or missing required fields
            RuntimeError: If store disappears during import
            aiosqlite.Error: If database operations fail
            
        Note:
            This is an admin function that imports data for ALL users.
            With replace_existing it DELETES existing data for ALL users.
            Use with caution and never expose to non-admin users.
        """
        # Step 1: Validate import data structure
        if "schema" not in data:
            logger.error(f"Import data for store '{store_name}' is missing the 'schema' key.")
            raise ValueError("Import data must include a 'schema'.")
            
        records = data.get("records", [])
        if not records:
             logger.info(f"Schema provided for '{store_name}', but no records present in import data.")

        # Validate store name and extract schema
        store_name = validate_store_name(store_name)
        import_schema = data["schema"]
        is_cacheable = data.get("cacheable", False)

        # Step 2: Ensure store exists with compatible schema
        try:
            logger.info(f"Ensuring store '{store_name}' exists and schema is compatible before import.")
            await self._schema_manager.create_store_if_not_exists(
                store_name=store_name, 
                schema=import_schema, 
                cacheable=is_cacheable
            )
            logger.info(f"Store '{store_name}' is ready for import.")
        except Exception as e:
            logger.error(f"Failed to prepare store '{store_name}' for import: {e}")
            raise

        # Exit early if no records to import
        if not records:
            return 0

        # Initialize counters
        logger.info(
            f"Importing {len(records)} records into store '{store_name}' "
            f"(replace_existing={replace_existing})"
        )
        imported_count = 0
        skipped_duplicates = 0

        # Step 3: Perform import within a single transaction
        try:
            async with self._conn_manager.get_write_db() as db:
                # Get the potentially updated schema from the database
                schema = await self._schema_manager.find_store(store_name)
                if not schema:
                     raise RuntimeError(f"Store '{store_name}' disappeared during import setup.")

                # Step 4: Handle replace_existing option
                if replace_existing:
                    logger.info(f"Clearing existing data from store '{store_name}'")
                    # Delete parent records (CASCADE handles child tables and FTS via triggers)
                    await db.execute(f'DELETE FROM "{store_name}"')
                    logger.info(f"Cleared existing records from '{store_name}'.")

                # Step 5: Process each record for import
                for record_idx, record in enumerate(records):
                    # Generate or use provided record ID
                    record_id = record.get("id") or str(uuid.uuid4())

                    # Separate parent data from collection items
                    parent_data = {}
                    collection_items_to_append = {}

                    # Process each field according to its type
                    for field_name, field_type in schema.items():
                        if field_name not in record:
                            continue

                        raw_value = record[field_name]

                        if field_type == "json_collection":
                            # Store collection items for later appending
                            if isinstance(raw_value, list):
                                collection_items_to_append[field_name] = raw_value
                            # Ignore non-list values (metadata dict, None etc.)
                        else:
                            # Include regular fields in parent data
                            parent_data[field_name] = raw_value

                    # Step 6: Add parent record
                    try:
                        # Extract user_id from record (required field)
                        user_id = record.get("user_id")
                        if not user_id:
                            raise ValueError(f"Record {record_idx+1} missing required 'user_id' field")

                        # Add parent record within the transaction
                        await self._crud_ops.add(
                            user_id=user_id, 
                            store_name=store_name, 
                            data=parent_data, 
                            record_id=record_id, 
                            _db=db
                        )
                        add_succeeded = True
                        
                    except ValueError as e:
                        # Handle duplicate IDs gracefully
                        if f"Record ID '{record_id}' already exists" in str(e):
                             logger.warning(
                                f"Skipping record {record_idx+1}: ID '{record_id}' already exists "
                                f"in store '{store_name}'."
                             )
                             skipped_duplicates += 1
                             add_succeeded = False
                        else:
                             raise e

                    # Step 7: Append collection items if parent add succeeded
                    if add_succeeded:
                        for field_name, items in collection_items_to_append.items():
                             logger.debug(
                                f"Appending {len(items)} items to collection '{field_name}' "
                                f"for record '{record_id}'..."
                             )
                             for item_idx, item in enumerate(items):
                                 try:
                                     await self._collection_ops.collection_append(
                                         user_id=user_id, 
                                         store_name=store_name, 
                                         record_id=record_id, 
                                         field_name=field_name, 
                                         item=item, 
                                         _db=db
                                     )
                                 except Exception as append_err:
                                     logger.error(
                                        f"Error appending item {item_idx+1} to {field_name} "
                                        f"for {record_id}: {append_err}"
                                     )
                                     raise append_err

                        imported_count += 1

            # Step 8: Report final results
            logger.info(
                f"Import completed for store '{store_name}'. "
                f"Successfully imported: {imported_count}, Skipped duplicates: {skipped_duplicates}."
            )
            return imported_count

        except (aiosqlite.Error, ValueError) as e:
            logger.error(f"Import failed for store '{store_name}': {e}. Transaction rolled back.")
            raise
