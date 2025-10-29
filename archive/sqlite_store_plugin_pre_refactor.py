"""
THIS IS A DEVELOPMENT BACKUP of a pre-refactor version
This backup includes batch and transaction processing and query_raw(),
any of which may or may not be added to the production implementation

DO NOT LOAD THIS PLUGIN

SQLite/FTS5 implementation of the GenericStorePlugin.
Provides full-text search and exact matching capabilities using SQLite.
"""
import json
import os
import uuid
import re
from typing import Dict, Any, Optional, List
from datetime import datetime
import aiosqlite
import logging

raise Exception("DO NOT LOAD THE DEVELOPMENT BACKUP")

logger = logging.getLogger("skeleton.sqlite_store")


# ============================================================================
# CLASS DEFINITION AND INITIALIZATION
# ============================================================================

class SQLiteStorePlugin:
    """SQLite implementation with FTS5 full-text search support"""

    def get_priority(self) -> int:
        """Default priority - can be overridden by other plugins"""
        return 0

    def __init__(self, db_path: Optional[str] = None):
        """Initialize SQLite store with optional custom path"""
        self.db_path = db_path or os.getenv("SQLITE_PATH", "./skeleton.db")
        self._initialized = False

    async def _init_db(self):
        """Initialize database connection and create tables if needed"""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            # Enable foreign keys
            await db.execute("PRAGMA foreign_keys = ON")

            # Create stores table to track all stores and their schemas
            await db.execute('''
                CREATE TABLE IF NOT EXISTS _stores (
                    name TEXT PRIMARY KEY,
                    schema_json TEXT NOT NULL,
                    cacheable INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            ''')

            # Add cacheable column if it doesn't exist (for existing databases)
            try:
                await db.execute('ALTER TABLE _stores ADD COLUMN cacheable INTEGER NOT NULL DEFAULT 0')
                logger.info("Added cacheable column to existing _stores table")
            except Exception:
                # Column already exists, ignore
                pass

            # Create FTS5 virtual table for full-text search across all text fields
            await db.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS _fts_content USING fts5(
                    store_name,
                    record_id,
                    field_name,
                    content,
                    tokenize='porter'
                )
            ''')

            await db.commit()
            self._initialized = True


# ============================================================================
# VALIDATION AND SECURITY HELPERS
# ============================================================================

    def _sanitize_identifier(self, identifier: str) -> str:
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

    def _validate_store_name(self, store_name: str) -> str:
        """Validate and sanitize store name"""
        if not store_name:
            raise ValueError("Store name cannot be empty")

        # Basic validation - alphanumeric, underscore, hyphen
        if not re.match(r'^[a-zA-Z0-9_-]+$', store_name):
            raise ValueError("Store name can only contain letters, numbers, underscore, and hyphen")

        if len(store_name) > 64:
            raise ValueError("Store name too long (max 64 characters)")

        return store_name

    def _validate_field_name(self, field_name: str) -> str:
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

    def _serialize_value(self, value: Any, field_type: str, field_name: str = "field",
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
                collection_store = self._get_collection_table_name(store_name, field_name)
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
            logger.warning(f"Unknown field type '{field_type}' for field '{field_name}', treating as string")
            return str(value) if value is not None else None

    def _map_type(self, generic_type: str) -> str:
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


# ============================================================================
# STORE MANAGEMENT
# ============================================================================

    async def create_store_if_not_exists(self, store_name: str, schema: Dict[str, str],
                                        cacheable: bool = False) -> bool:
        """
        Create a new store with the given schema if it doesn't already exist.

        Args:
            store_name: Name of the store to create
            schema: Dictionary mapping field names to field types (str, int, float, bool, json)
            cacheable: If True, auto-adds '_version' field for future caching optimization

        Returns:
            bool: True if store was created, False if store already exists

        Behavior:
            - If store doesn't exist: Creates it with the provided schema
            - If store exists but schema doesn't match:
                - Logs INFO warning about schema differences
                - Adds missing fields from new schema
                - Ignores extra fields in existing store
                - Returns False (store already existed)
            - If store exists and schema matches: Returns False (store already existed)

        Logging:
            - INFO: Store creation attempts, schema modifications
            - DEBUG: Schema comparison details
        """
        await self._init_db()

        # Validate store name to prevent SQL injection
        store_name = self._validate_store_name(store_name)

        # Auto-add _version field if cacheable
        if cacheable and "_version" not in schema:
            schema = {**schema, "_version": "str"}

        logger.info(f"Creating store '{store_name}' with schema: {schema} (cacheable={cacheable})")

        async with aiosqlite.connect(self.db_path) as db:
            # Check if store already exists
            cursor = await db.execute(
                "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
            )
            existing_row = await cursor.fetchone()

            if existing_row:
                # Store exists - check schema compatibility
                existing_schema = json.loads(existing_row[0])
                logger.debug(f"Store '{store_name}' exists with schema: {existing_schema}")

                # Compare schemas
                missing_fields = set(schema.keys()) - set(existing_schema.keys())
                extra_fields = set(existing_schema.keys()) - set(schema.keys())

                if missing_fields or extra_fields:
                    logger.info(f"Schema differences detected for store '{store_name}':")
                    if missing_fields:
                        logger.info(f"  Missing fields to add: {missing_fields}")
                    if extra_fields:
                        logger.info(f"  Extra fields to ignore: {extra_fields}")

                    # Add missing fields to existing store
                    if missing_fields:
                        for field_name in missing_fields:
                            field_type = schema[field_name]
                            sql_type = self._map_type(field_type)

                            # Validate field name before using in SQL
                            validated_field_name = self._validate_field_name(field_name)

                            # Add column to existing table
                            alter_sql = f'ALTER TABLE "{store_name}" ADD COLUMN "{validated_field_name}" {sql_type}'
                            logger.info(f"Adding column '{validated_field_name}' to store '{store_name}'")
                            await db.execute(alter_sql)

                            # If it's a json_collection field, create child table
                            if field_type == "json_collection":
                                child_table = self._get_collection_table_name(store_name, field_name)
                                logger.info(
                                    f"Creating child table '{child_table}' "
                                    f"for new collection field '{field_name}'"
                                )

                                # Create child table
                                child_sql = f'''
                                    CREATE TABLE IF NOT EXISTS "{child_table}" (
                                        id TEXT PRIMARY KEY,
                                        parent_id TEXT NOT NULL,
                                        order_index INTEGER NOT NULL,
                                        item_json TEXT NOT NULL,
                                        created_at TEXT NOT NULL,
                                        FOREIGN KEY (parent_id) REFERENCES "{store_name}"(id) ON DELETE CASCADE
                                    )
                                '''
                                await db.execute(child_sql)

                                # Create index on parent_id
                                index_name = f"idx_{self._sanitize_identifier(child_table)}_parent"
                                await db.execute(
                                    f'CREATE INDEX IF NOT EXISTS "{index_name}" '
                                    f'ON "{child_table}"(parent_id, order_index)'
                                )

                                # Register child table in _stores metadata
                                child_schema = {
                                    "parent_id": "str",
                                    "order_index": "int",
                                    "item_json": "json"
                                }
                                await db.execute(
                                    "INSERT OR IGNORE INTO _stores (name, schema_json, cacheable, created_at) VALUES (?, ?, ?, ?)",
                                    (child_table, json.dumps(child_schema), 0, datetime.now().isoformat())
                                )

                                logger.info(f"Successfully created child table '{child_table}'")

                        # Update schema in metadata
                        updated_schema = {**existing_schema, **{k: v for k, v in schema.items() if k in missing_fields}}
                        await db.execute(
                            "UPDATE _stores SET schema_json = ? WHERE name = ?",
                            (json.dumps(updated_schema), store_name)
                        )
                        logger.info(f"Updated schema for store '{store_name}': {updated_schema}")

                logger.info(f"Store '{store_name}' already exists - returning False")
                await db.commit()
                return False

            # Store doesn't exist - create it
            logger.info(f"Store '{store_name}' does not exist - creating new store")

            # Create the store table
            columns = []
            collection_fields = []  # Track json_collection fields for child table creation

            for field_name, field_type in schema.items():
                # Validate field names to prevent SQL injection
                validated_field_name = self._validate_field_name(field_name)
                sql_type = self._map_type(field_type)
                columns.append(f"{validated_field_name} {sql_type}")

                # Track collection fields for child table creation
                if field_type == "json_collection":
                    collection_fields.append(field_name)

            create_sql = f'''
                CREATE TABLE IF NOT EXISTS "{store_name}" (
                    id TEXT PRIMARY KEY,
                    {', '.join(columns)},
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            '''
            logger.debug(f"Creating table with SQL: {create_sql}")
            await db.execute(create_sql)

            # Create child tables for json_collection fields
            for field_name in collection_fields:
                child_table = self._get_collection_table_name(store_name, field_name)
                logger.info(f"Creating child table '{child_table}' for collection field '{field_name}'")

                # Create child table with parent_id, order_index, and item storage
                child_sql = f'''
                    CREATE TABLE IF NOT EXISTS "{child_table}" (
                        id TEXT PRIMARY KEY,
                        parent_id TEXT NOT NULL,
                        order_index INTEGER NOT NULL,
                        item_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (parent_id) REFERENCES "{store_name}"(id) ON DELETE CASCADE
                    )
                '''
                await db.execute(child_sql)

                # Create index on parent_id for fast lookups
                index_name = f"idx_{self._sanitize_identifier(child_table)}_parent"
                await db.execute(
                    f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{child_table}"(parent_id, order_index)'
                )

                # Register child table in _stores metadata
                child_schema = {
                    "parent_id": "str",
                    "order_index": "int",
                    "item_json": "json"
                }
                await db.execute(
                    "INSERT INTO _stores (name, schema_json, cacheable, created_at) VALUES (?, ?, ?, ?)",
                    (child_table, json.dumps(child_schema), 0, datetime.now().isoformat())
                )

                logger.info(f"Successfully created child table '{child_table}'")

            # Insert store metadata
            await db.execute(
                "INSERT INTO _stores (name, schema_json, cacheable, created_at) VALUES (?, ?, ?, ?)",
                (store_name, json.dumps(schema), 1 if cacheable else 0, datetime.now().isoformat())
            )

            await db.commit()
            logger.info(f"Successfully created store '{store_name}' with {len(collection_fields)} collection fields")
            return True

    async def list_stores(self) -> List[str]:
        """List all available store names"""
        await self._init_db()

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT name FROM _stores ORDER BY name")
            rows = await cursor.fetchall()
            stores = [row[0] for row in rows]
            logger.debug(f"Listed {len(stores)} stores: {stores}")
            return stores

    async def find_store(self, store_name: str) -> Optional[Dict[str, str]]:
        """Find a store and return its schema"""
        await self._init_db()

        # Validate store name
        store_name = self._validate_store_name(store_name)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
            )
            row = await cursor.fetchone()
            if row:
                schema = json.loads(row[0])
                logger.debug(f"Found store '{store_name}' with schema: {schema}")
                return schema
            logger.debug(f"Store '{store_name}' not found")
            return None

    async def get_store_stats(self, store_name: str) -> Dict[str, Any]:
        """Get statistics about a store"""
        await self._init_db()

        logger.debug(f"Getting stats for store '{store_name}'")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        async with aiosqlite.connect(self.db_path) as db:
            # Count records
            cursor = await db.execute(f'SELECT COUNT(*) FROM "{store_name}"')
            count = (await cursor.fetchone())[0]

            # Get size info
            cursor = await db.execute(
                "SELECT SUM(pgsize) FROM dbstat WHERE name = ?", (store_name,)
            )
            size = (await cursor.fetchone())[0] or 0

            # Get oldest and newest records
            cursor = await db.execute(
                f'SELECT MIN(created_at), MAX(created_at) FROM "{store_name}"'
            )
            min_max = await cursor.fetchone()

            result = {
                "store": store_name,
                "record_count": count,
                "size_bytes": size,
                "oldest_record": min_max[0],
                "newest_record": min_max[1]
            }
            logger.debug(f"Store stats for '{store_name}': {result}")
            return result

    async def is_cacheable(self, store_name: str) -> bool:
        """Check if a store is marked as cacheable"""
        await self._init_db()

        # Validate store name
        store_name = self._validate_store_name(store_name)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT cacheable FROM _stores WHERE name = ?", (store_name,)
            )
            row = await cursor.fetchone()
            if row:
                return bool(row[0])
            logger.warning(f"Store '{store_name}' not found when checking cacheable status")
            return False


# ============================================================================
# BASIC CRUD OPERATIONS
# ============================================================================

    async def add(self, store_name: str, data: Dict[str, Any], record_id: Optional[str] = None) -> str:
        """Add a new record to a store"""
        await self._init_db()

        if record_id is None:
            record_id = str(uuid.uuid4())

        logger.debug(f"Adding record to store '{store_name}' with ID '{record_id}'")

        async with aiosqlite.connect(self.db_path) as db:
            # Validate store name
            store_name = self._validate_store_name(store_name)

            # Check if ID already exists
            cursor = await db.execute(
                f'SELECT 1 FROM "{store_name}" WHERE id = ?', (record_id,)
            )
            if await cursor.fetchone():
                raise ValueError(f"Record ID '{record_id}' already exists in store '{store_name}'")

            # Get store schema
            schema_cursor = await db.execute(
                "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
            )
            schema_row = await schema_cursor.fetchone()
            if not schema_row:
                raise ValueError(f"Store '{store_name}' does not exist")

            schema = json.loads(schema_row[0])

            # Build insert query with validated field names
            fields = []
            placeholders = []
            values = []

            for field_name, field_type in schema.items():
                # Validate field names
                validated_field_name = self._validate_field_name(field_name)
                fields.append(f'"{validated_field_name}"')
                placeholders.append('?')

                # Serialize value according to field type (pass store_name for json_collection init)
                raw_value = data.get(field_name)
                serialized_value = self._serialize_value(raw_value, field_type, field_name, store_name)
                values.append(serialized_value)

            insert_sql = f'''
                INSERT INTO "{store_name}" (id, {', '.join(fields)}, created_at, updated_at)
                VALUES (?, {', '.join(placeholders)}, ?, ?)
            '''

            now = datetime.now().isoformat()
            await db.execute(insert_sql, [record_id] + values + [now, now])

            # Add to FTS index for full-text search
            text_fields = [f for f, t in schema.items() if t == 'str']
            for field in text_fields:
                if field in data and data[field]:
                    await db.execute('''
                        INSERT INTO _fts_content (store_name, record_id, field_name, content)
                        VALUES (?, ?, ?, ?)
                    ''', (store_name, record_id, field, str(data[field])))

            await db.commit()
            logger.debug(f"Successfully added record '{record_id}' to store '{store_name}'")
            return record_id

    async def get(self, store_name: str, record_id: str,
                  load_collections: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get a single record by ID.

        Args:
            store_name: Name of the store
            record_id: ID of the record to retrieve
            load_collections: If True, loads json_collection fields as arrays.
                            If False, returns collection metadata only.

        Returns:
            Record dict, or None if not found.

        Collection field behavior:
            - load_collections=False: {"field": {"collection_store": "...", "count": N}}
            - load_collections=True: {"field": [{item1}, {item2}, ...]}
        """
        await self._init_db()

        logger.debug(
            f"Getting record '{record_id}' from store '{store_name}' "
            f"(load_collections={load_collections})"
        )

        # Validate store name
        store_name = self._validate_store_name(store_name)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f'SELECT * FROM "{store_name}" WHERE id = ?', (record_id,)
            )
            row = await cursor.fetchone()
            if not row:
                logger.debug(f"Record '{record_id}' not found in store '{store_name}'")
                return None

            # Get column names
            cursor = await db.execute(f'PRAGMA table_info("{store_name}")')
            columns = [col[1] for col in await cursor.fetchall()]

            result = dict(zip(columns, row))

            # If load_collections=True, load collection fields
            if load_collections:
                # Get store schema to find json_collection fields
                cursor = await db.execute(
                    "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
                )
                schema_row = await cursor.fetchone()
                if schema_row:
                    schema = json.loads(schema_row[0])

                    # For each json_collection field, load items
                    for field_name, field_type in schema.items():
                        if field_type == "json_collection" and field_name in result:
                            # Load collection items
                            items = await self.collection_get(
                                store_name, record_id, field_name
                            )
                            # Replace metadata with actual items array
                            result[field_name] = items

            logger.debug(
                f"Found record '{record_id}' in store '{store_name}' "
                f"(loaded {len([f for f, t in schema.items() if t == 'json_collection'])} collections)"
                if load_collections and 'schema' in locals() else ""
            )

            return result

    async def update(self, store_name: str, record_id: str,
                     updates: Dict[str, Any], partial: bool = True) -> bool:
        """
        Update a record by ID.

        Note: json_collection fields cannot be updated via update().
        Use collection_append() to add items to collections.
        Attempting to update a collection field will raise ValueError.
        """
        await self._init_db()

        logger.debug(f"Updating record '{record_id}' in store '{store_name}' with: {updates}")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        async with aiosqlite.connect(self.db_path) as db:
            if not updates:
                return True

            # Get schema to check for json_collection fields
            schema_cursor = await db.execute(
                "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
            )
            schema_row = await schema_cursor.fetchone()
            if not schema_row:
                raise ValueError(f"Store '{store_name}' does not exist")

            schema = json.loads(schema_row[0])

            # Check if any update keys are json_collection fields
            collection_fields_in_update = [
                field for field in updates.keys()
                if field in schema and schema[field] == "json_collection"
            ]

            if collection_fields_in_update:
                raise ValueError(
                    f"Cannot update json_collection fields via update(): {collection_fields_in_update}. "
                    f"json_collection fields are append-only. Use collection_append() to add items."
                )

            # Build update query with validated field names
            set_clauses = []
            params = []

            for field_name, value in updates.items():
                # Validate field names
                field_name = self._validate_field_name(field_name)
                set_clauses.append(f'"{field_name}" = ?')
                params.append(value)

            set_sql = ', '.join(set_clauses)

            query = f'''
                UPDATE "{store_name}"
                SET {set_sql}, updated_at = ?
                WHERE id = ?
            '''

            params.extend([datetime.now().isoformat(), record_id])

            cursor = await db.execute(query, params)
            updated = cursor.rowcount > 0

            if updated:
                # Update FTS index for text fields
                text_fields = [f for f, t in schema.items() if t == 'str']

                # Remove old FTS entries
                await db.execute(
                    "DELETE FROM _fts_content WHERE store_name = ? AND record_id = ?",
                    (store_name, record_id)
                )

                # Add new FTS entries
                record = await self.get(store_name, record_id)
                if record:
                    for field in text_fields:
                        if field in record and record[field]:
                            await db.execute('''
                                INSERT INTO _fts_content (store_name, record_id, field_name, content)
                                VALUES (?, ?, ?, ?)
                            ''', (store_name, record_id, field, str(record[field])))

            await db.commit()
            logger.debug(f"Update result for record '{record_id}': {updated}")
            return updated

    async def delete(self, store_name: str, record_id: str) -> bool:
        """Delete a record by ID"""
        await self._init_db()

        logger.debug(f"Deleting record '{record_id}' from store '{store_name}'")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        async with aiosqlite.connect(self.db_path) as db:
            # Remove from FTS index
            await db.execute(
                "DELETE FROM _fts_content WHERE store_name = ? AND record_id = ?",
                (store_name, record_id)
            )

            # Remove from store
            cursor = await db.execute(
                f'DELETE FROM "{store_name}" WHERE id = ?', (record_id,)
            )
            deleted = cursor.rowcount > 0

            await db.commit()
            logger.debug(f"Delete result for record '{record_id}': {deleted}")
            return deleted

    async def count(self, store_name: str, filters: Dict[str, Any] = None) -> int:
        """Count records matching filters"""
        await self._init_db()

        logger.debug(f"Counting records in store '{store_name}' with filters: {filters}")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        async with aiosqlite.connect(self.db_path) as db:
            where_clauses = []
            params = []

            if filters:
                for field_name, value in filters.items():
                    # Validate field names
                    field_name = self._validate_field_name(field_name)

                    if isinstance(value, dict) and "$like" in value:
                        where_clauses.append(f'"{field_name}" LIKE ?')
                        params.append(value["$like"])
                    else:
                        where_clauses.append(f'"{field_name}" = ?')
                        params.append(value)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            cursor = await db.execute(
                f'SELECT COUNT(*) FROM "{store_name}" {where_sql}',
                params
            )
            result = await cursor.fetchone()
            count = result[0] if result else 0
            logger.debug(f"Count result for store '{store_name}': {count}")
            return count


# ============================================================================
# QUERY OPERATIONS
# ============================================================================

    async def find(self, store_name: str, filters: Dict[str, Any] = None,
                   limit: Optional[int] = None, offset: int = 0,
                   order_by: str = None, order_desc: bool = False) -> List[Dict[str, Any]]:
        """Find records with optional filters, pagination, and sorting"""
        await self._init_db()

        logger.debug(f"Finding records in store '{store_name}' with filters: {filters}")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        async with aiosqlite.connect(self.db_path) as db:
            where_clauses = []
            params = []

            if filters:
                for field, value in filters.items():
                    # Validate field names
                    field = self._validate_field_name(field)

                    if isinstance(value, dict) and "$like" in value:
                        where_clauses.append(f'"{field}" LIKE ?')
                        params.append(value["$like"])
                    else:
                        where_clauses.append(f'"{field}" = ?')
                        params.append(value)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            order_sql = ""
            if order_by:
                # Validate order_by field
                order_by = self._validate_field_name(order_by)
                direction = "DESC" if order_desc else "ASC"
                order_sql = f'ORDER BY "{order_by}" {direction}'

            limit_sql = f"LIMIT {limit}" if limit else ""
            offset_sql = f"OFFSET {offset}" if offset else ""

            query = f'''
                SELECT * FROM "{store_name}"
                {where_sql}
                {order_sql}
                {limit_sql} {offset_sql}
            '''

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            # Get column names
            cursor = await db.execute(f'PRAGMA table_info("{store_name}")')
            columns = [col[1] for col in await cursor.fetchall()]

            results = [dict(zip(columns, row)) for row in rows]
            logger.debug(f"Found {len(results)} records in store '{store_name}'")
            return results

    async def search(self, store_name: str, query: str,
                     search_fields: List[str] = None,
                     filters: Dict[str, Any] = None,
                     limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Full-text search across specified fields with FTS5"""
        await self._init_db()

        logger.debug(f"Searching store '{store_name}' with query '{query}' in fields: {search_fields}")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        async with aiosqlite.connect(self.db_path) as db:
            # Build FTS query
            fts_query = f'"{query}"*'

            # Build base query
            base_sql = f'''
                SELECT DISTINCT s.* FROM "{store_name}" s
                JOIN _fts_content f ON s.id = f.record_id AND f.store_name = ?
            '''
            params = [store_name]

            # Add field filter if specified
            if search_fields:
                # Validate search field names
                validated_fields = []
                for field in search_fields:
                    validated_fields.append(self._validate_field_name(field))

                field_placeholders = ', '.join(['?' for _ in validated_fields])
                base_sql += f' AND f.field_name IN ({field_placeholders})'
                params.extend(validated_fields)

            # Add regular filters
            if filters:
                for field, value in filters.items():
                    # Validate field names
                    field = self._validate_field_name(field)

                    if isinstance(value, dict) and "$like" in value:
                        base_sql += f' AND s."{field}" LIKE ?'
                        params.append(value["$like"])
                    else:
                        base_sql += f' AND s."{field}" = ?'
                        params.append(value)

            # Add FTS search
            base_sql += ' AND _fts_content MATCH ?'
            params.append(fts_query)

            # Add pagination
            base_sql += f' ORDER BY rank LIMIT {limit or -1} OFFSET {offset}'

            cursor = await db.execute(base_sql, params)
            rows = await cursor.fetchall()

            # Get column names
            cursor = await db.execute(f'PRAGMA table_info("{store_name}")')
            columns = [col[1] for col in await cursor.fetchall()]

            results = [dict(zip(columns, row)) for row in rows]
            logger.debug(f"Found {len(results)} search results in store '{store_name}'")
            return results

    async def full_text_search(self, store_name: str, query: str,
                              limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Simple full-text search across all text fields"""
        return await self.search(store_name, query, limit=limit, offset=offset)


# ============================================================================
# BATCH OPERATIONS
# ============================================================================

    async def batch_add(self, store_name: str, records: List[Dict[str, Any]]) -> List[str]:
        """Add multiple records in a single operation"""
        await self._init_db()

        logger.debug(f"Batch adding {len(records)} records to store '{store_name}'")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        ids = []
        async with aiosqlite.connect(self.db_path) as db:
            for record in records:
                record_id = str(uuid.uuid4())
                ids.append(record_id)

                # Get store schema
                cursor = await db.execute(
                    "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
                )
                schema_row = await cursor.fetchone()
                if not schema_row:
                    raise ValueError(f"Store '{store_name}' does not exist")

                schema = json.loads(schema_row[0])

                # Build insert query with validated field names
                fields = []
                placeholders = []
                values = []

                for field_name, field_type in schema.items():
                    # Validate field names
                    validated_field_name = self._validate_field_name(field_name)
                    fields.append(f'"{validated_field_name}"')
                    placeholders.append('?')

                    # Serialize value according to field type (pass store_name for json_collection init)
                    raw_value = record.get(field_name)
                    serialized_value = self._serialize_value(raw_value, field_type, field_name, store_name)
                    values.append(serialized_value)

                insert_sql = f'''
                    INSERT INTO "{store_name}" (id, {', '.join(fields)}, created_at, updated_at)
                    VALUES (?, {', '.join(placeholders)}, ?, ?)
                '''

                now = datetime.now().isoformat()
                await db.execute(insert_sql, [record_id] + values + [now, now])

                # Add to FTS index
                text_fields = [f for f, t in schema.items() if t == 'str']
                for field in text_fields:
                    if field in record and record[field]:
                        await db.execute('''
                            INSERT INTO _fts_content (store_name, record_id, field_name, content)
                            VALUES (?, ?, ?, ?)
                        ''', (store_name, record_id, field, str(record[field])))

            await db.commit()
            logger.debug(f"Successfully batch added {len(ids)} records to store '{store_name}'")
        return ids

    async def batch_update(self, store_name: str,
                          updates: List[Dict[str, Any]]) -> int:
        """
        Update multiple records.

        Note: json_collection fields cannot be updated via batch_update().
        Use collection_append() to add items to collections.
        Attempting to update a collection field will raise ValueError.
        """
        await self._init_db()

        logger.debug(f"Batch updating {len(updates)} records in store '{store_name}'")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        async with aiosqlite.connect(self.db_path) as db:
            # Get schema once for all updates
            schema_cursor = await db.execute(
                "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
            )
            schema_row = await schema_cursor.fetchone()
            if not schema_row:
                raise ValueError(f"Store '{store_name}' does not exist")

            schema = json.loads(schema_row[0])

            updated_count = 0
            for update in updates:
                record_id = update.pop('id', None)
                if not record_id:
                    continue

                if not update:
                    continue

                # Check if any update keys are json_collection fields
                collection_fields_in_update = [
                    field for field in update.keys()
                    if field in schema and schema[field] == "json_collection"
                ]

                if collection_fields_in_update:
                    raise ValueError(
                        f"Cannot update json_collection fields via batch_update(): {collection_fields_in_update}. "
                        f"json_collection fields are append-only. Use collection_append() to add items."
                    )

                # Build update query with validated field names
                set_clauses = []
                params = []

                for field_name, value in update.items():
                    # Validate field names
                    field_name = self._validate_field_name(field_name)
                    set_clauses.append(f'"{field_name}" = ?')
                    params.append(value)

                set_sql = ', '.join(set_clauses)

                query = f'''
                    UPDATE "{store_name}"
                    SET {set_sql}, updated_at = ?
                    WHERE id = ?
                '''

                params.extend([datetime.now().isoformat(), record_id])

                cursor = await db.execute(query, params)
                updated_count += cursor.rowcount

                # Update FTS index
                if cursor.rowcount > 0:
                    text_fields = [f for f, t in schema.items() if t == 'str']

                    # Remove old FTS entries
                    await db.execute(
                        "DELETE FROM _fts_content WHERE store_name = ? AND record_id = ?",
                        (store_name, record_id)
                    )

                    # Add new FTS entries
                    record = await self.get(store_name, record_id)
                    if record:
                        for field in text_fields:
                            if field in record and record[field]:
                                await db.execute('''
                                    INSERT INTO _fts_content (store_name, record_id, field_name, content)
                                    VALUES (?, ?, ?, ?)
                                ''', (store_name, record_id, field, str(record[field])))

            await db.commit()
            logger.debug(f"Successfully batch updated {updated_count} records in store '{store_name}'")
        return updated_count

    async def batch_delete(self, store_name: str, record_ids: List[str]) -> int:
        """Delete multiple records by ID"""
        await self._init_db()

        logger.debug(f"Batch deleting {len(record_ids)} records from store '{store_name}'")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        deleted_count = 0
        async with aiosqlite.connect(self.db_path) as db:
            # Remove from FTS index
            placeholders = ', '.join(['?' for _ in record_ids])
            await db.execute(
                f"DELETE FROM _fts_content WHERE store_name = ? AND record_id IN ({placeholders})",
                [store_name] + record_ids
            )

            # Remove from store
            placeholders = ', '.join(['?' for _ in record_ids])
            cursor = await db.execute(
                f'DELETE FROM "{store_name}" WHERE id IN ({placeholders})',
                record_ids
            )
            deleted_count = cursor.rowcount

            await db.commit()
            logger.debug(f"Successfully batch deleted {deleted_count} records from store '{store_name}'")
        return deleted_count


# ============================================================================
# COLLECTION OPERATIONS
# ============================================================================

    def _get_collection_table_name(self, store_name: str, field_name: str) -> str:
        """
        Generate the child table name for a json_collection field.

        Format: {store_name}_{field_name}
        Example: threads_messages for "messages" field in "threads" store
        """
        return f"{store_name}_{field_name}"

    async def collection_append(self, store_name: str, record_id: str,
                               field_name: str, item: Dict[str, Any]) -> int:
        """
        Append an item to a json_collection field.

        This is an O(1) operation with no write amplification. Items are stored
        in a separate child table (e.g., "threads_messages" for field "messages"
        in store "threads").

        Args:
            store_name: Name of the parent store
            record_id: ID of the parent record
            field_name: Name of the json_collection field
            item: JSON object to append (dict or list)

        Returns:
            order_index: The index of the newly appended item (0-based)

        Raises:
            ValueError: If store/record doesn't exist or field is not json_collection type
            TypeError: If item is not dict/list or not valid JSON
        """
        await self._init_db()

        # Validate inputs
        store_name = self._validate_store_name(store_name)
        field_name = self._validate_field_name(field_name)

        logger.debug(f"Appending item to collection '{field_name}' in record '{record_id}' of store '{store_name}'")

        async with aiosqlite.connect(self.db_path) as db:
            # 1. Verify store exists and field is json_collection type
            cursor = await db.execute(
                "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
            )
            schema_row = await cursor.fetchone()
            if not schema_row:
                raise ValueError(f"Store '{store_name}' does not exist")

            schema = json.loads(schema_row[0])
            if field_name not in schema:
                raise ValueError(f"Field '{field_name}' does not exist in store '{store_name}'")
            if schema[field_name] != "json_collection":
                raise ValueError(
                    f"Field '{field_name}' is type '{schema[field_name]}', not 'json_collection'. "
                    f"Use update() for non-collection fields."
                )

            # 2. Verify parent record exists
            cursor = await db.execute(
                f'SELECT id FROM "{store_name}" WHERE id = ?', (record_id,)
            )
            if not await cursor.fetchone():
                raise ValueError(f"Record '{record_id}' does not exist in store '{store_name}'")

            # 3. Validate and serialize item
            if not isinstance(item, (dict, list)):
                raise TypeError(
                    f"Collection item must be dict or list, got {type(item).__name__}"
                )

            try:
                item_json = json.dumps(item)
            except (TypeError, ValueError) as e:
                raise TypeError(f"Cannot serialize item to JSON: {e}")

            # 4. Get child table name and current count
            child_table = self._get_collection_table_name(store_name, field_name)

            # Get current item count for this parent record
            cursor = await db.execute(
                f'SELECT COUNT(*) FROM "{child_table}" WHERE parent_id = ?', (record_id,)
            )
            count_row = await cursor.fetchone()
            order_index = count_row[0] if count_row else 0

            # 5. Insert item into child table
            item_id = str(uuid.uuid4())
            now = datetime.now().isoformat()

            await db.execute(f'''
                INSERT INTO "{child_table}" (id, parent_id, order_index, item_json, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (item_id, record_id, order_index, item_json, now))

            # 6. Update metadata in parent record (count field)
            validated_field_name = self._validate_field_name(field_name)
            metadata_json = json.dumps({
                "collection_store": child_table,
                "count": order_index + 1
            })

            await db.execute(f'''
                UPDATE "{store_name}"
                SET "{validated_field_name}" = ?, updated_at = ?
                WHERE id = ?
            ''', (metadata_json, now, record_id))

            # 7. Add to FTS index for searchable text content
            # For now, we'll add the entire item JSON to FTS
            # This allows full-text search across all collection items
            await db.execute('''
                INSERT INTO _fts_content (store_name, record_id, field_name, content)
                VALUES (?, ?, ?, ?)
            ''', (child_table, item_id, field_name, item_json))

            await db.commit()

            logger.info(
                f"Appended item to '{field_name}' in record '{record_id}' "
                f"at index {order_index} (new count: {order_index + 1})"
            )

            return order_index

    async def collection_get(self, store_name: str, record_id: str,
                            field_name: str, limit: Optional[int] = None,
                            offset: int = 0) -> List[Dict[str, Any]]:
        """
        Get items from a json_collection field with pagination.

        This retrieves items from the child table in a single SQL query.

        Args:
            store_name: Name of the parent store
            record_id: ID of the parent record
            field_name: Name of the json_collection field
            limit: Maximum number of items to return (None = all)
            offset: Number of items to skip (for pagination)

        Returns:
            List of items (dicts), ordered by insertion order (order_index).
            Returns [] if collection is empty or doesn't exist.

        Notes:
            - Items are returned as dicts (automatically deserialized from JSON)
            - Order is guaranteed: items returned in insertion order
            - For full thread with collections, use get(load_collections=True) instead
        """
        await self._init_db()

        # Validate inputs
        store_name = self._validate_store_name(store_name)
        field_name = self._validate_field_name(field_name)

        logger.debug(
            f"Getting items from collection '{field_name}' in record '{record_id}' "
            f"of store '{store_name}' (limit={limit}, offset={offset})"
        )

        async with aiosqlite.connect(self.db_path) as db:
            # Get child table name
            child_table = self._get_collection_table_name(store_name, field_name)

            # Build query with pagination
            limit_sql = f"LIMIT {limit}" if limit is not None else ""
            offset_sql = f"OFFSET {offset}" if offset > 0 else ""

            query = f'''
                SELECT item_json FROM "{child_table}"
                WHERE parent_id = ?
                ORDER BY order_index ASC
                {limit_sql} {offset_sql}
            '''

            cursor = await db.execute(query, (record_id,))
            rows = await cursor.fetchall()

            # Deserialize JSON items
            items = []
            for row in rows:
                try:
                    item = json.loads(row[0])
                    items.append(item)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to deserialize collection item: {e}")
                    # Skip malformed items
                    continue

            logger.debug(
                f"Retrieved {len(items)} items from collection '{field_name}' "
                f"in record '{record_id}'"
            )

            return items


# ============================================================================
# INDEX OPERATIONS
# ============================================================================

    async def create_index(self, store_name: str, field_name: str,
                          index_type: str = "default") -> bool:
        """Create an index on a field"""
        await self._init_db()

        logger.info(f"Creating {index_type} index on field '{field_name}' in store '{store_name}'")

        # Validate store name and field name
        store_name = self._validate_store_name(store_name)
        field_name = self._validate_field_name(field_name)

        # Sanitize index name - remove hyphens from store_name for index identifier
        # (store names can have hyphens, but index names cannot)
        safe_store_name = self._sanitize_identifier(store_name)
        index_name = f"idx_{safe_store_name}_{field_name}"

        async with aiosqlite.connect(self.db_path) as db:
            try:
                if index_type == "unique":
                    sql = f'CREATE UNIQUE INDEX IF NOT EXISTS "{index_name}" ON "{store_name}"("{field_name}")'
                else:
                    sql = f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{store_name}"("{field_name}")'

                logger.debug(f"Creating index with SQL: {sql}")
                await db.execute(sql)
                await db.commit()
                logger.info(f"Successfully created index on field '{field_name}' in store '{store_name}'")
                return True
            except Exception as e:
                logger.error(f"Failed to create index on field '{field_name}' in store '{store_name}': {e}")
                return False

    async def get_indexes(self, store_name: str) -> List[Dict[str, Any]]:
        """Get information about indexes on a store"""
        await self._init_db()

        logger.debug(f"Getting indexes for store '{store_name}'")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?",
                (store_name,)
            )
            rows = await cursor.fetchall()

            results = [{"name": row[0], "sql": row[1]} for row in rows]
            logger.debug(f"Found {len(results)} indexes for store '{store_name}'")
            return results


# ============================================================================
# DATA MANAGEMENT
# ============================================================================

    async def export_store(self, store_name: str,
                          format: str = "json") -> Dict[str, Any]:
        """
        Export all records from a store, including json_collection field items.

        For json_collection fields, exports the full arrays of items rather than metadata.
        This ensures a complete, human-readable export that can be re-imported.
        """
        await self._init_db()

        logger.info(f"Exporting store '{store_name}' in format: {format}")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        async with aiosqlite.connect(self.db_path) as db:
            # Get store info
            cursor = await db.execute(
                "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
            )
            schema_row = await cursor.fetchone()
            if not schema_row:
                logger.warning(f"Store '{store_name}' not found for export")
                return {"store": store_name, "records": [], "schema": {}}

            schema = json.loads(schema_row[0])

            # Get all records
            cursor = await db.execute(f'SELECT * FROM "{store_name}"')
            rows = await cursor.fetchall()

            # Get column names
            cursor = await db.execute(f'PRAGMA table_info("{store_name}")')
            columns = [col[1] for col in await cursor.fetchall()]

            records = [dict(zip(columns, row)) for row in rows]

            # Load json_collection fields as arrays for complete export
            collection_fields = [f for f, t in schema.items() if t == "json_collection"]
            if collection_fields:
                logger.debug(
                    f"Loading {len(collection_fields)} collection fields "
                    f"for export: {collection_fields}"
                )
                for record in records:
                    record_id = record["id"]
                    for field_name in collection_fields:
                        # Load collection items
                        items = await self.collection_get(
                            store_name, record_id, field_name
                        )
                        # Replace metadata with actual items array
                        record[field_name] = items

            result = {
                "store": store_name,
                "schema": schema,
                "records": records
            }
            logger.info(
                f"Exported {len(records)} records from store '{store_name}' "
                f"({len(collection_fields)} collection fields loaded)"
            )
            return result

    async def import_store(self, store_name: str,
                          data: Dict[str, Any],
                          replace_existing: bool = False) -> int:
        """
        Import records into a store, including json_collection field items.

        Handles json_collection fields specially:
        - If field value is an array: inserts parent record, then uses collection_append() for each item
        - If field value is None or missing: initializes empty collection
        - If field value is metadata dict: initializes empty collection (backward compat)
        """
        await self._init_db()

        if "records" not in data:
            logger.warning(f"No records found in import data for store '{store_name}'")
            return 0

        records = data["records"]
        logger.info(
            f"Importing {len(records)} records into store '{store_name}' "
            f"(replace_existing={replace_existing})"
        )

        imported_count = 0

        # Validate store name
        store_name = self._validate_store_name(store_name)

        async with aiosqlite.connect(self.db_path) as db:
            if replace_existing:
                # Clear existing data (including child tables for collections)
                logger.info(f"Clearing existing data from store '{store_name}'")
                await db.execute(f'DELETE FROM "{store_name}"')
                await db.execute(
                    "DELETE FROM _fts_content WHERE store_name = ?", (store_name,)
                )

            for record in records:
                if "id" not in record:
                    record["id"] = str(uuid.uuid4())

                record_id = record["id"]

                # Get store schema
                cursor = await db.execute(
                    "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
                )
                schema_row = await cursor.fetchone()
                if not schema_row:
                    logger.warning(f"Store '{store_name}' not found during import - skipping record")
                    continue

                schema = json.loads(schema_row[0])

                # Separate collection fields from regular fields
                collection_data = {}  # field_name -> array of items
                regular_data = {}

                for field_name, field_type in schema.items():
                    raw_value = record.get(field_name)

                    if field_type == "json_collection":
                        # Handle collection field specially
                        if isinstance(raw_value, list):
                            # Array format (from export) - save for later collection_append
                            collection_data[field_name] = raw_value
                        elif isinstance(raw_value, dict) and "collection_store" in raw_value:
                            # Metadata format (raw export) - treat as empty collection
                            logger.debug(
                                f"Found metadata format for collection '{field_name}', "
                                f"initializing as empty"
                            )
                            collection_data[field_name] = []
                        elif raw_value is None:
                            # None - empty collection
                            collection_data[field_name] = []
                        else:
                            logger.warning(
                                f"Unexpected value type for collection field '{field_name}': "
                                f"{type(raw_value).__name__}, treating as empty"
                            )
                            collection_data[field_name] = []
                        # Don't include in regular data - will be initialized by _serialize_value
                    else:
                        # Regular field - include in insert
                        regular_data[field_name] = raw_value

                # Build insert query for parent record (collections excluded from data)
                fields = []
                placeholders = []
                values = []

                for field_name, field_type in schema.items():
                    validated_field_name = self._validate_field_name(field_name)
                    fields.append(f'"{validated_field_name}"')
                    placeholders.append('?')

                    if field_type == "json_collection":
                        # Initialize with empty collection metadata
                        serialized_value = self._serialize_value(None, field_type, field_name, store_name)
                    else:
                        # Serialize regular field value
                        serialized_value = self._serialize_value(
                            regular_data.get(field_name),
                            field_type,
                            field_name,
                            store_name
                        )
                    values.append(serialized_value)

                insert_sql = f'''
                    INSERT OR IGNORE INTO "{store_name}" (id, {', '.join(fields)}, created_at, updated_at)
                    VALUES (?, {', '.join(placeholders)}, ?, ?)
                '''

                now = datetime.now().isoformat()
                await db.execute(insert_sql, [record_id] + values + [now, now])

                # Add regular text fields to FTS index
                text_fields = [f for f, t in schema.items() if t == 'str']
                for field in text_fields:
                    if field in regular_data and regular_data[field]:
                        await db.execute('''
                            INSERT OR IGNORE INTO _fts_content (store_name, record_id, field_name, content)
                            VALUES (?, ?, ?, ?)
                        ''', (store_name, record_id, field, str(regular_data[field])))

                # Now append collection items using collection_append()
                for field_name, items in collection_data.items():
                    if items:  # Only if there are items to append
                        logger.debug(
                            f"Appending {len(items)} items to collection '{field_name}' "
                            f"for record '{record_id}'"
                        )
                        for item in items:
                            await self.collection_append(store_name, record_id, field_name, item)

                imported_count += 1

            await db.commit()
            logger.info(
                f"Successfully imported {imported_count} records into store '{store_name}'"
            )
        return imported_count

    async def cleanup_old_records(self, store_name: str,
                                 days: int,
                                 date_field: str = "created") -> int:
        """Remove records older than specified days"""
        await self._init_db()

        logger.info(f"Cleaning up records older than {days} days from store '{store_name}' (date_field='{date_field}')")

        # Validate store name and field name
        store_name = self._validate_store_name(store_name)
        date_field = self._validate_field_name(date_field)

        async with aiosqlite.connect(self.db_path) as db:
            # Remove from FTS index
            await db.execute('''
                DELETE FROM _fts_content
                WHERE store_name = ? AND record_id IN (
                    SELECT id FROM "{store_name}"
                    WHERE "{date_field}" < datetime('now', '-{days} days')
                )
            '''.format(store_name=store_name, date_field=date_field, days=days))

            # Remove from store
            cursor = await db.execute('''
                DELETE FROM "{store_name}"
                WHERE "{date_field}" < datetime('now', '-{days} days')
            '''.format(store_name=store_name, date_field=date_field, days=days))

            deleted_count = cursor.rowcount
            await db.commit()
            logger.info(f"Cleaned up {deleted_count} old records from store '{store_name}'")
            return deleted_count


# ============================================================================
# ADVANCED OPERATIONS
# ============================================================================

    async def query_raw(self, store_name: str, query: str,
                        params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Execute raw SQL query for advanced use cases"""
        await self._init_db()

        logger.debug(f"Executing raw query on store '{store_name}': {query}")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        # SECURITY WARNING: This method executes raw SQL and is vulnerable to injection
        # Only use this method if you absolutely trust the query source
        # Consider implementing query validation or using parameterized queries only

        async with aiosqlite.connect(self.db_path) as db:
            try:
                # Basic safety check - reject obviously dangerous queries
                dangerous_keywords = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE', 'ATTACH', 'DETACH']
                query_upper = query.upper()
                for keyword in dangerous_keywords:
                    if keyword in query_upper:
                        logger.warning(f"Potentially dangerous query rejected: contains '{keyword}'")
                        raise ValueError(f"Query contains potentially dangerous keyword: {keyword}")

                cursor = await db.execute(query, list(params.values()) if params else [])
                rows = await cursor.fetchall()

                # Get column names
                columns = [desc[0] for desc in cursor.description]

                results = [dict(zip(columns, row)) for row in rows]
                logger.debug(f"Raw query returned {len(results)} results")
                return results
            except Exception as e:
                logger.error(f"Raw query execution failed: {e}")
                raise

    async def transaction(self, operations: List[Dict[str, Any]]) -> bool:
        """Execute multiple operations in a transaction"""
        await self._init_db()

        logger.info(f"Executing transaction with {len(operations)} operations")

        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute("BEGIN TRANSACTION")
                logger.debug("Transaction started")

                for i, op in enumerate(operations):
                    op_type = op.get("type")
                    store = op.get("store")

                    logger.debug(f"Executing operation {i+1}/{len(operations)}: {op_type} on store '{store}'")

                    if op_type == "add":
                        await self.add(store, op.get("data"), op.get("record_id"), db)
                    elif op_type == "update":
                        await self.update(store, op["record_id"], op.get("updates"), db)
                    elif op_type == "delete":
                        await self.delete(store, op["record_id"], db)
                    else:
                        logger.warning(f"Unknown operation type: {op_type}")

                await db.commit()
                logger.info(f"Transaction completed successfully with {len(operations)} operations")
                return True
            except Exception as e:
                await db.rollback()
                logger.error(f"Transaction failed: {e}")
                return False
