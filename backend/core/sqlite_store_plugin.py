"""
SQLite/FTS5 implementation of the GenericStorePlugin.
Provides full-text search and exact matching capabilities using SQLite.
"""
import json
import os
import uuid
import re
import random
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import asyncio
import aiosqlite
import logging
from .protocols import JSONStructuredRepresentation, StorePlugin
from contextlib import asynccontextmanager

logger = logging.getLogger("skeleton.sqlite_store")


# ============================================================================
# CLASS DEFINITION AND INITIALIZATION
# ============================================================================

class SQLiteStorePlugin():
    """SQLite implementation with FTS5 full-text search support"""

    def get_role(self) -> str:
        """Return the role string for this plugin"""
        return "store"

    def get_priority(self) -> int:
        """Default priority - can be overridden by other plugins"""
        return 0

    def __init__(self, db_path: Optional[str] = None):
        """Initialize SQLite store with optional custom path"""
        # If a direct db_path is passed, use it (for testing)
        if db_path:
            self.db_path = db_path
        else:                                                                                                                        # Otherwise, construct from environment variables
            data_dir = os.getenv("DATA_PATH", ".") # Default to current dir
            db_filename = os.getenv("SQLITE_DB_FILENAME", "skeleton.db") # Allow customizing filename
            self.db_path = os.path.join(data_dir, db_filename)

        # Ensure the directory for the database exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self._write_conn: Optional[aiosqlite.Connection] = None # the single write aiosqlite connection
        self._read_conn: Optional[aiosqlite.Connection] = None # the single read aiosqlite connection
        self._init_lock = asyncio.Lock() # the lock for initializing the database at the start
        self._write_lock = asyncio.Lock() # the lock for writing to the database - must be strictly serialized

        self._shutting_down = False

    async def _init_db(self) -> None:
        """Initialize the WRITE and READ database connections and create core tables if needed"""

        # prevent database access if the system is shutting down
        if self._shutting_down:
            raise RuntimeError("Database access attempted after shutdown initiated.")

        # Fast path: write connection already exists
        if self._write_conn:
            return

        # Slow path: Initialize connection and core tables
        async with self._init_lock:
            # Double-check after acquiring lock
            if self._write_conn is None:
                logger.info(f"Initializing new shared connection to {self.db_path}")
                try:
                    conn = await aiosqlite.connect(self.db_path)

                    # --- Enable PRAGMAs (once per connection) ---
                    await conn.execute("PRAGMA foreign_keys = ON;")
                    await conn.execute("PRAGMA journal_mode=WAL;")
                    # Commit PRAGMAs immediately
                    await conn.commit()
                    logger.info("Enabled WAL mode and foreign keys.")

                    # --- Perform one-time table creation within a transaction ---
                    await conn.execute("BEGIN")
                    try:
                        # Create stores table if it's not there
                        await conn.execute('''
                            CREATE TABLE IF NOT EXISTS _stores (
                                name TEXT PRIMARY KEY,
                                schema_json TEXT NOT NULL,
                                cacheable INTEGER NOT NULL DEFAULT 0,
                                created_at TEXT NOT NULL
                            )
                        ''')

                        # --- Commit ---
                        await conn.commit()
                        logger.info("Core database tables initialized (_stores).")
                        # Save the connection before releasing the lock
                        self._write_conn = conn
                    except Exception as e:
                        logger.error("Exception in intital transaction - rolling back and closing")
                        await conn.rollback()
                        await conn.close()
                        raise

                    # the transaction has completed and the WRITE connection is set up
                    # now set up the READ connection
                    db_uri = f"file:{self.db_path}?mode=ro"
                    logger.info(f"Initializing new shared READ-ONLY connection using URI: {db_uri}")
                    # Connect using the URI and uri=True
                    self._read_conn = await aiosqlite.connect(db_uri, uri=True)



                except aiosqlite.Error as e:
                    logger.error(
                        f"Failed during initial database setup: {e}"
                        f"If {self.db_path}-wal exists, delete it and retry"
                    )
                    # Clean up connections if setup failed but one or both connections were somehow set
                    if self._write_conn:
                        await self._write_conn.close()
                        self._write_conn = None
                    if self._read_conn:
                        await self._read_conn.close()
                        self._read_conn = None
                    raise RuntimeError("Failed to obtain database connection after initialization attempt.")

    async def _get_read_db(self) -> aiosqlite.Connection:
        """get a read connection"""
        if not self._read_conn:
            await self._init_db() # Ensure connection exists
        return self._read_conn

    @asynccontextmanager
    async def _get_write_db(self) -> aiosqlite.Connection:
        """get a write connection - provides write serialization with retry logic
           and transaction management, with auto commit and auto rollback"""

        if not self._write_conn:
            await self._init_db() # Ensure connection exists - this also fails out if shutting down

        max_retries = 7
        base_delay = 0.02   # 20ms base delay
        max_delay = 2.0     # 2000ms max delay

        # Use 'async with' for automatic acquire/release
        async with self._write_lock:
            logger.debug("Write lock acquired.")

            for attempt in range(max_retries):
                try:
                    # Use BEGIN IMMEDIATE to acquire write lock immediately
                    await self._write_conn.execute("BEGIN IMMEDIATE")
                    logger.debug(f"Write transaction started on attempt {attempt + 1}")
                    yield self._write_conn # Yield to the caller
                    # ... Execution resumes here after caller finishes ...
                    await self._write_conn.commit()
                    logger.debug("Write transaction committed successfully")
                    break  # Success, exit retry loop

                except aiosqlite.OperationalError as e:
                    if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                        # Calculate exponential backoff with jitter
                        delay = min(base_delay * (2 ** attempt) + random.uniform(0, 0.001), max_delay)
                        logger.warning(f"Database locked, retrying in {delay:.3f}s (attempt {attempt + 1}/{max_retries})")
                        try:
                            await self._write_conn.rollback()
                        except Exception:
                            pass  # Ignore rollback errors during retry
                        await asyncio.sleep(delay)
                        continue
                    else:
                        # Re-raise the error if it's not a lock error or we've exhausted retries
                        logger.error(f"Write transaction failed after {attempt + 1} attempts: {e}")
                        try:
                            await self._write_conn.rollback()
                        except Exception as rb_e:
                            logger.error(f"Error during rollback: {rb_e}")
                        raise

                except Exception as e:
                    logger.error(f"Exception during write transaction, rolling back: {e}")
                    try:
                        await self._write_conn.rollback()
                    except Exception as rb_e:
                        logger.error(f"Error during rollback: {rb_e}")
                    raise # Re-raise original exception

                finally:
                    # This finally block is for the try/except, not the lock.
                    # The lock is released *after* this block by 'async with'.
                    logger.debug("Write transaction finished.")

        # The lock is guaranteed to be released here
        logger.debug("Write lock released.")



    async def shutdown(self):
        """Graceful shutdown: closes the shared database connection."""
        logger.info("Starting SQLite shutdown...")
        self._shutting_down = True

        write_conn_to_close = self._write_conn
        read_conn_to_close = self._read_conn

        self._write_conn = None
        self._read_conn = None

        try:
            if write_conn_to_close:
                logger.info("Closing write connection...")
                try:
                    # Perform a final checkpoint on the write connection with timeout
                    await asyncio.wait_for(write_conn_to_close.execute("PRAGMA wal_checkpoint(TRUNCATE)"), timeout=5.0)
                    await asyncio.wait_for(write_conn_to_close.close(), timeout=5.0)
                    logger.info("Write connection closed.")
                except asyncio.TimeoutError:
                    logger.warning("Write connection close timed out")
                    write_conn_to_close.close()  # Force close

            if read_conn_to_close:
                logger.info("Closing read connection...")
                try:
                    await asyncio.wait_for(read_conn_to_close.close(), timeout=5.0)
                    logger.info("Read connection closed.")
                except asyncio.TimeoutError:
                    logger.warning("Read connection close timed out")
                    read_conn_to_close.close()  # Force close

        except Exception as e:
            logger.error(f"Error closing SQLite connection: {e}")

        logger.info("SQLite shutdown completed")
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

    def _deserialize_value(self, value: Any, field_type: str) -> Any:
        """
        Deserialize value from SQLite storage type to Python type.
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
                logger.warning(f"Expected string for json_collection metadata, got {type(value).__name__}")
                return value # Return raw value if it wasn't a string as expected
            elif field_type == "json":
                # 'json' fields are returned as raw strings without parsing.
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
            logger.warning(
                f"Failed to deserialize value '{value}' (type {type(value).__name__}) "
                f"to type '{field_type}': {e}. Returning raw value."
            )
            return value

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

    async def _create_collection_child_table(self, db: aiosqlite.Connection,
        store_name: str, field_name: str):
        """ Helper function to create a child table for a collection
            IMPORTANT: takes the database connection as THIS MUST BE DONE INSIDE A TRANSACTION
        """
        child_table = self._get_collection_table_name(store_name, field_name)
        logger.info(
            f"Creating child table '{child_table}' for new collection field '{field_name}'"
        )

        # Create child table
        child_sql = f'''
            CREATE TABLE IF NOT EXISTS "{child_table}" (
                id TEXT PRIMARY KEY,
                parent_id TEXT NOT NULL,
                order_index INTEGER NOT NULL,
                item_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (parent_id) REFERENCES "{store_name}"(id) ON DELETE CASCADE,
                UNIQUE(parent_id, order_index)
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

        # Create FTS triggers for this collection table
        await self._create_collection_fts_triggers(db, store_name, field_name, child_table)

    async def _create_collection_fts_triggers(self, db: aiosqlite.Connection, store_name: str, field_name: str, child_table: str) -> None:
        """Create FTS triggers for collection child tables"""
        fts_table = f"fts_{store_name}"

        # Trigger for INSERT into collection - add child item to FTS
        insert_trigger = f'''
            CREATE TRIGGER IF NOT EXISTS "fts_{child_table}_insert"
            AFTER INSERT ON "{child_table}"
            BEGIN
                INSERT INTO "{fts_table}" (user_id, parent_id, child_id, "{field_name}")
                SELECT parent.user_id, NEW.parent_id, '{field_name}_' || NEW.id, NEW.item_json
                FROM "{store_name}" parent WHERE parent.id = NEW.parent_id;
            END
        '''
        await db.execute(insert_trigger)

        # Trigger for DELETE from collection - remove child item from FTS
        delete_trigger = f'''
            CREATE TRIGGER IF NOT EXISTS "fts_{child_table}_delete"
            AFTER DELETE ON "{child_table}"
            BEGIN
                DELETE FROM "{fts_table}" WHERE child_id = '{field_name}_' || OLD.id;
            END
        '''
        await db.execute(delete_trigger)

        logger.info(f"Created FTS triggers for collection table '{child_table}'")

    async def _create_fts_triggers(self, db: aiosqlite.Connection, store_name: str, indexable_fields: List[str]) -> None:
        """Create triggers for automatic FTS synchronization"""
        fts_table = f"fts_{store_name}"

        # Build the column lists for triggers
        fts_columns = ", ".join(f'"{f}"' for f in indexable_fields)
        new_values = ", ".join(f'NEW."{f}"' for f in indexable_fields)
        old_values = ", ".join(f'OLD."{f}"' for f in indexable_fields)

        # Trigger for INSERT - add parent record to FTS
        insert_trigger = f'''
            CREATE TRIGGER IF NOT EXISTS "fts_{store_name}_insert"
            AFTER INSERT ON "{store_name}"
            BEGIN
                INSERT INTO "{fts_table}" (user_id, parent_id, child_id, {fts_columns})
                VALUES (NEW.user_id, NEW.id, '', {new_values});
            END
        '''
        await db.execute(insert_trigger)

        # Trigger for UPDATE - update FTS entry
        update_trigger = f'''
            CREATE TRIGGER IF NOT EXISTS "fts_{store_name}_update"
            AFTER UPDATE ON "{store_name}"
            BEGIN
                DELETE FROM "{fts_table}" WHERE parent_id = OLD.id AND user_id = OLD.user_id AND child_id = '';
                INSERT INTO "{fts_table}" (user_id, parent_id, child_id, {fts_columns})
                VALUES (NEW.user_id, NEW.id, '', {new_values});
            END
        '''
        await db.execute(update_trigger)

        # Trigger for DELETE - remove from FTS
        delete_trigger = f'''
            CREATE TRIGGER IF NOT EXISTS "fts_{store_name}_delete"
            AFTER DELETE ON "{store_name}"
            BEGIN
                DELETE FROM "{fts_table}" WHERE parent_id = OLD.id AND user_id = OLD.user_id;
            END
        '''
        await db.execute(delete_trigger)

        logger.info(f"Created FTS triggers for store '{store_name}'")

    async def create_store_if_not_exists(self, store_name: str, schema: Dict[str, str],
                                        cacheable: bool = False,
                                        _db: Optional[aiosqlite.Connection] = None) -> bool:
        """
        Create a new store with the given schema if it doesn't already exist.

        Args:
            store_name: Name of the store to create
            schema: Dictionary mapping field names to field types (str, int, float, bool, json)
            cacheable: If True, auto-adds '_version' field for future caching optimization
            _db: database connection; if provided, assume we are in a transaction.

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

        async def create_store_if_not_exists_logic(db: aiosqlite.Connection,
                    store_name: str, schema: Dict[str, str], cacheable: bool = False) -> bool:
            """The core logic of creating a store if it does not exist.
               Separated into an inner procedure so that we can call it
               with or without wrapping in a transaction
               Note: the code uses `self` from the outer scope """

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
                                await self._create_collection_child_table(db, store_name, field_name)

                        # Update schema in metadata
                        updated_schema = {**existing_schema, **{k: v for k, v in schema.items() if k in missing_fields}}
                        await db.execute(
                            "UPDATE _stores SET schema_json = ? WHERE name = ?",
                            (json.dumps(updated_schema), store_name)
                        )
                        logger.info(f"Updated schema for store '{store_name}': {updated_schema}")

                logger.info(f"Store '{store_name}' already exists - returning False")
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

            # Add user_id column to all store tables
            columns.insert(0, "user_id TEXT NOT NULL")

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

            # Create index on user_id for multi-tenancy
            index_name = f"idx_{store_name}_user_id"
            await db.execute(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{store_name}" (user_id)')

            # Create child tables for json_collection fields
            for field_name in collection_fields:
                await self._create_collection_child_table(db, store_name, field_name)

            # Create per-store FTS5 virtual table
            indexable_fields = [
                f for f, t in schema.items()
                if t in ('str', 'json', 'json_collection')
            ]
            if indexable_fields:
                fts_columns = ", ".join(f'"{f}"' for f in indexable_fields)
                fts_sql = f'''
                    CREATE VIRTUAL TABLE IF NOT EXISTS "fts_{store_name}" USING fts5(
                        user_id UNINDEXED,
                        parent_id UNINDEXED,
                        child_id UNINDEXED,
                        {fts_columns},
                        tokenize='porter'
                    )
                '''
                await db.execute(fts_sql)
                logger.info(f"Created FTS table 'fts_{store_name}' with fields: {indexable_fields}")

                # Create triggers for automatic FTS synchronization
                await self._create_fts_triggers(db, store_name, indexable_fields)

            # Insert store metadata
            await db.execute(
                "INSERT INTO _stores (name, schema_json, cacheable, created_at) VALUES (?, ?, ?, ?)",
                (store_name, json.dumps(schema), 1 if cacheable else 0, datetime.now().isoformat())
            )

            logger.info(f"Successfully created store '{store_name}' with {len(collection_fields)} collection fields")
            return True

        # main create_store_if_not_exists() starts here

        # Validate store name to prevent SQL injection
        store_name = self._validate_store_name(store_name)

        # Auto-add _version field if cacheable
        if cacheable and "_version" not in schema:
            schema = {**schema, "_version": "str"}

        logger.info(f"Creating store '{store_name}' with schema: {schema} (cacheable={cacheable})")

        if _db:
            # assume we are in a transaction
            return await create_store_if_not_exists_logic(_db, store_name, schema, cacheable)
        else:
            async with self._get_write_db() as db:
                return await create_store_if_not_exists_logic(db, store_name, schema, cacheable)


    async def list_stores(self) -> List[str]:
        """List all available store names"""
        db = await self._get_read_db()

        cursor = await db.execute("SELECT name FROM _stores ORDER BY name")
        rows = await cursor.fetchall()
        stores = [row[0] for row in rows]
        logger.debug(f"Listed {len(stores)} stores: {stores}")
        return stores

    async def find_store(self, store_name: str) -> Optional[Dict[str, str]]:
        """Find a store and return its schema"""
        db = await self._get_read_db()

        # Validate store name
        store_name = self._validate_store_name(store_name)

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
        db = await self._get_read_db()

        logger.debug(f"Getting stats for store '{store_name}'")

        # Validate store name
        store_name = self._validate_store_name(store_name)

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
        db = await self._get_read_db()

        # Validate store name
        store_name = self._validate_store_name(store_name)

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

    async def add(self, user_id: str, store_name: str, data: Dict[str, Any], record_id: Optional[str] = None,
                  _db: Optional[aiosqlite.Connection] = None) -> str:
        """Add a new record to a store
           If _db is provided we assume we are in a transaction"""

        async def add_logic(db: aiosqlite.Connection, user_id: str, store_name: str, data: Dict[str, Any], record_id: str) -> str:
            """The core logic of adding. Separated into an inner procedure so that
               we can call it with or without wrapping in a transaction
               Note: the code uses `self` from the outer scope """

            # Check if ID already exists (it's a primary key)
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
            fields = ["user_id"]
            placeholders = ['?']
            values = [user_id]

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

            logger.debug(f"Successfully added record '{record_id}' to store '{store_name}' for user '{user_id}'")
            return record_id

        # main add() starts here

        # Create a record ID if one is not provided
        if record_id is None:
            record_id = str(uuid.uuid4())

        # Validate store name
        store_name = self._validate_store_name(store_name)

        logger.debug(f"Adding record to store '{store_name}' with ID '{record_id}'")

        if _db:
            # if a database connection was provided, we are already in a transaction
            return await add_logic(_db, user_id, store_name, data, record_id)
        else:
            async with self._get_write_db() as db:
                return await add_logic(db, user_id, store_name, data, record_id)


    async def get(self, user_id: str, store_name: str, record_id: str,
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
        db = await self._get_read_db()

        logger.debug(
            f"Getting record '{record_id}' from store '{store_name}' "
            f"(load_collections={load_collections})"
        )

        # Validate store name
        store_name = self._validate_store_name(store_name)

        # load store schema
        cursor = await db.execute(
            "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
        )
        schema_row = await cursor.fetchone()
        if schema_row:
            schema = json.loads(schema_row[0])
        else:
            logger.error(f"Schema not found for store {store_name}. Does the store exist?")
            return None

        # --- Add meta-fields to schema map for deserialization ---
        schema_with_meta = schema.copy()
        schema_with_meta.update({
            "id": "str",
            "user_id": "str",
            "created_at": "str",
            "updated_at": "str"
        })

        cursor = await db.execute(
            f'SELECT * FROM "{store_name}" WHERE id = ? AND user_id = ?', (record_id, user_id)
        )
        row = await cursor.fetchone()
        if not row:
            logger.warning(f"Record '{record_id}' not found in store '{store_name}'")
            return None

        # get column names
        columns = [desc[0] for desc in cursor.description] # Get column names from the result

        # --- Build result dict with deserialization ---
        result = {}
        for i, col_name in enumerate(columns):
            raw_value = row[i]
            field_type = schema_with_meta.get(col_name, "str")
            result[col_name] = self._deserialize_value(raw_value, field_type)

        # If load_collections=True, load collection fields
        if load_collections:
                # For each json_collection field, load items
                for field_name, field_type in schema.items():
                    if field_type == "json_collection" and field_name in result:
                        # Load collection items
                        items = await self.collection_get(
                            user_id, store_name, record_id, field_name
                        )
                        # Replace metadata with actual items array
                        result[field_name] = items

        logger.debug(
            f"Found record '{record_id}' in store '{store_name}' "
            f"(loaded {len([f for f, t in schema.items() if t == 'json_collection'])} collections)"
            if load_collections and 'schema' in locals() else ""
        )

        return result

    async def update(self, user_id: str, store_name: str, record_id: str,
                     updates: Dict[str, Any], partial: bool = True) -> bool:
        """
        Update a record by ID.

        Note: json_collection fields cannot be updated via update().
        Use collection_append() to add items to collections.
        Attempting to update a collection field will raise ValueError.
        """

        logger.debug(f"Updating record '{record_id}' in store '{store_name}' with: {updates}")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        # report success if the update is empty
        # TODO: maybe change updated_at in this case?
        if not updates:
            return True

        # First validate that the record exists and belongs to the user (using read connection)
        read_db = await self._get_read_db()
        cursor = await read_db.execute(
            f'SELECT id FROM "{store_name}" WHERE id = ? AND user_id = ?', (record_id, user_id)
        )
        if not await cursor.fetchone():
            raise ValueError(f"Record '{record_id}' does not exist or does not belong to user '{user_id}' in store '{store_name}'")

        async with self._get_write_db() as db:

            # Get schema to check for json_collection fields
            schema_cursor = await db.execute(
                "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
            )
            schema_row = await schema_cursor.fetchone()
            if not schema_row:
                raise ValueError(f"Store '{store_name}' does not exist")

            schema = json.loads(schema_row[0])

            schema_fields = set(schema.keys()) # Get allowed field names

            # check if any fields sent are not in the schema
            update_fields = set(updates.keys())
            invalid_fields = update_fields - schema_fields
            if invalid_fields:
                raise ValueError(f"Invalid field(s) provided for update in store '{store_name}': {', '.join(invalid_fields)}. Allowed fields: {', '.join(schema_fields)}")

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
                # We know field_name is valid now
                validated_field_name = self._validate_field_name(field_name) # Still sanitize
                field_type = schema[field_name]

                # Serialize the value before adding to params
                serialized_value = self._serialize_value(value, field_type, field_name, store_name)

                set_clauses.append(f'"{validated_field_name}" = ?')
                params.append(serialized_value) # Use serialized value

            set_sql = ', '.join(set_clauses)

            if not set_sql: # Should not happen if initial check passed, but good practice
                 logger.warning("Update called with empty valid updates after filtering.")
                 return True # No changes needed

            query = f'''
                UPDATE "{store_name}"
                SET {set_sql}, updated_at = ?
                WHERE id = ? AND user_id = ?
            '''

            params.extend([datetime.now().isoformat(), record_id, user_id])

            cursor = await db.execute(query, params)
            updated = cursor.rowcount > 0

            logger.debug(f"Update result for record '{record_id}': {updated}")
            return updated

    async def delete(self, user_id: str, store_name: str, record_id: str) -> bool:
        """Delete a record by ID and associated collection/FTS entries."""
        logger.debug(f"Deleting record '{record_id}' from store '{store_name}'")
        store_name = self._validate_store_name(store_name)

        deleted = False
        try:
            async with self._get_write_db() as db:
                # --- Get Schema to ensure the store exists ---
                async with db.execute("SELECT schema_json FROM _stores WHERE name = ?", (store_name,)) as cursor:
                     schema_row = await cursor.fetchone()
                if not schema_row:
                    raise ValueError(f"Store '{store_name}' does not exist")
                schema = json.loads(schema_row[0])

                # --- Delete the PARENT record (CASCADE handles child table rows and FTS via triggers) ---
                cursor = await db.execute(
                    f'DELETE FROM "{store_name}" WHERE id = ? AND user_id = ?', (record_id, user_id)
                )
                deleted = cursor.rowcount > 0

            logger.debug(f"Delete result for record '{record_id}': {deleted}")
            return deleted

        except (aiosqlite.Error, ValueError) as e:
             logger.error(f"Error deleting record '{record_id}' from store '{store_name}': {e}")
             raise # Re-raise after rollback (automatic via transaction)

# ============================================================================
# QUERY OPERATIONS
# ============================================================================

    def _build_pagination_clause(self, limit: Optional[int], offset: int) -> Tuple[str, List[int]]:
        """
        Builds SQL LIMIT/OFFSET clause and parameters, with validation.

        Returns:
            A tuple containing the SQL string fragment (e.g., "LIMIT ? OFFSET ?")
            and a list of parameters (e.g., [10, 20]).
            Returns ("", []) if no limit or offset is provided.
        Raises:
            ValueError: If limit or offset are invalid types or negative.
        """
        sql_parts = []
        params = []

        if limit is not None:
            if not isinstance(limit, int) or limit < 0:
                raise ValueError("Limit must be a non-negative integer.")
            sql_parts.append("LIMIT ?")
            params.append(limit)

        if offset:
            if not isinstance(offset, int) or offset < 0:
                raise ValueError("Offset must be a non-negative integer.")
            sql_parts.append("OFFSET ?")
            params.append(offset)

        # Join parts with space, handles cases with only LIMIT, only OFFSET, both, or neither
        sql_string = " ".join(sql_parts)

        return sql_string, params

    async def _build_where_clause(self, db: aiosqlite.Connection, store_name: str, user_id: str, filters: Optional[Dict[str, Any]]) -> tuple[str, list]:
        """
        Validates filters against the store schema (including date ranges),
        serializes values, and builds the WHERE clause SQL string and parameter list.

        Date range filter format: {"created_at": {"$gt": "iso_timestamp", "$lt": "iso_timestamp"}}
        """
        if not filters:
            return "", []

        # --- Get Schema (needed for validation and type info) ---
        schema_cursor = await db.execute(
            "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
        )
        schema_row = await schema_cursor.fetchone()
        if not schema_row:
            raise ValueError(f"Store '{store_name}' does not exist")
        schema = json.loads(schema_row[0])
        # Add meta fields to the schema map for validation
        schema_with_meta = schema.copy()
        schema_with_meta.update({
             "id": "str",
             "created_at": "str", # Treat as string for comparison
             "updated_at": "str"  # Treat as string for comparison
        })
        allowed_fields = set(schema_with_meta.keys())

        where_clauses = ['user_id = ?']
        params = [user_id]

        if filters:
            for field_name, filter_condition in filters.items():
                # --- Validate field name ---
                if field_name not in allowed_fields:
                    raise ValueError(f"Invalid filter field '{field_name}' for store '{store_name}'. Allowed fields: {', '.join(allowed_fields)}")

                validated_field_name = self._validate_field_name(field_name)
                # Default to 'str' if somehow missing (shouldn't happen after validation)
                field_type = schema_with_meta.get(field_name, "str")

                # --- Handle Different Filter Types ---
                if isinstance(filter_condition, dict):
                    # Handle complex filters (LIKE, ranges)
                    for operator, value in filter_condition.items():
                        serialized_value = self._serialize_value(value, field_type, field_name, store_name)
                        if operator == "$like":
                            if not isinstance(serialized_value, str):
                                serialized_value = str(value) # Fallback
                            where_clauses.append(f'"{validated_field_name}" LIKE ?')
                            params.append(serialized_value)
                        # --- Add Date Range Handling ---
                        elif operator in ("$gt", "$gte", "$lt", "$lte"):
                            # Ensure this operator is used on date fields (optional strict check)
                            # if field_name not in ("created_at", "updated_at"):
                            #    raise ValueError(f"Range operator '{operator}' only supported for date fields.")

                            # Note that comparing works not just for int and float nut also for ISO8601 string timestamps

                            sql_operator_map = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<="}
                            sql_operator = sql_operator_map[operator]
                            where_clauses.append(f'"{validated_field_name}" {sql_operator} ?')
                            params.append(serialized_value)
                        else:
                            raise ValueError(f"Unsupported filter operator '{operator}' for field '{field_name}'")
                else:
                    # Handle exact match
                    serialized_value = self._serialize_value(filter_condition, field_type, field_name, store_name)
                    where_clauses.append(f'"{validated_field_name}" = ?')
                    params.append(serialized_value)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        return where_sql, params

    async def count(self, user_id: str, store_name: str, filters: Dict[str, Any] = None) -> int:
        """Count records matching filters"""
        db = await self._get_read_db()

        logger.debug(f"Counting records in store '{store_name}' with filters: {filters}")
        store_name = self._validate_store_name(store_name) # Validate identifier early

        try:
            # --- Use helper to build WHERE clause ---
            where_sql, params = await self._build_where_clause(db, store_name, user_id, filters)

            # --- Execute Query ---
            query = f'SELECT COUNT(*) FROM "{store_name}" {where_sql}'
            cursor = await db.execute(query, params)
            result = await cursor.fetchone()

            count = result[0] if result else 0
            logger.debug(f"Count result for store '{store_name}' with filters {filters}: {count}")
            return count

        except (aiosqlite.Error, ValueError) as e: # Catch DB and validation errors
            logger.error(f"Error during count for store '{store_name}': {e}")
            raise # Re-raise the exception

    async def find(self, user_id: str, store_name: str, filters: Dict[str, Any] = None,
                   limit: Optional[int] = None, offset: int = 0,
                   order_by: str = None, order_desc: bool = False) -> List[Dict[str, Any]]:
        """Find records with optional filters, pagination, and sorting"""
        db = await self._get_read_db()
        logger.debug(f"Finding records in store '{store_name}' with filters: {filters}, limit: {limit}, offset: {offset}")
        store_name = self._validate_store_name(store_name) # Validate identifier early

        try:
            # --- Use helper to build WHERE clause ---
            where_sql, params = await self._build_where_clause(db, store_name, user_id, filters)

            # Fetch schema
            schema_cursor = await db.execute("SELECT schema_json FROM _stores WHERE name = ?", (store_name,))
            schema_row = await schema_cursor.fetchone()
            if not schema_row: raise ValueError(f"Store '{store_name}' not found for ordering.") # Should be caught by helper, but belt-and-suspenders
            schema = json.loads(schema_row[0])
            schema_with_meta = schema.copy()
            schema_with_meta.update({"id": "str", "created_at": "str", "updated_at": "str"})

            # --- Build ORDER BY clause ---
            order_sql = ""
            if order_by:
                if order_by not in schema_with_meta:
                     raise ValueError(f"Invalid order_by field '{order_by}'. Allowed fields: {', '.join(schema.keys())}")

                validated_order_by = self._validate_field_name(order_by) # Sanitize
                direction = "DESC" if order_desc else "ASC"
                order_sql = f'ORDER BY "{validated_order_by}" {direction}'

            # --- Build LIMIT / OFFSET clauses (Parameterized) ---
            pagination_sql, pagination_params = self._build_pagination_clause(limit, offset)
            params.extend(pagination_params) # Add pagination params to main list

            # --- Execute Query ---
            query = f'''
                SELECT * FROM "{store_name}"
                {where_sql}
                {order_sql}
                {pagination_sql}
            '''
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            # --- Deserialize Results ---
            if not rows:
                return []

            columns = [desc[0] for desc in cursor.description]


            results = []
            for row in rows:
                record_dict = {}
                for i, col_name in enumerate(columns):
                    raw_value = row[i]
                    field_type = schema_with_meta.get(col_name, "str")
                    record_dict[col_name] = self._deserialize_value(raw_value, field_type)
                results.append(record_dict)

            logger.debug(f"Found {len(results)} records in store '{store_name}'")
            return results

        except (aiosqlite.Error, ValueError) as e: # Catch DB and validation errors
            logger.error(f"Error during find for store '{store_name}': {e}")
            raise # Re-raise the exception


    async def full_text_search(self,
                               user_id: str,
                               store_name: str,
                               query: str,
                               limit: Optional[int] = None,
                               offset: int = 0) -> List[Dict[str, Any]]:
        """
        Full-text search across all indexable fields in a store.
        Returns matching parent records with full deserialization.
        """
        db = await self._get_read_db()

        # Validate store name
        store_name = self._validate_store_name(store_name)
        fts_table_name = f"fts_{store_name}"

        logger.debug(f"Full-text search in store '{store_name}' for user '{user_id}' with query '{query}'")

        try:
            # Validate store exists
            cursor = await db.execute(
                "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
            )
            schema_row = await cursor.fetchone()
            if not schema_row:
                raise ValueError(f"Store '{store_name}' does not exist")

            schema = json.loads(schema_row[0])

            # Build FTS match string
            match_string = f'"{query}"*'

            # Build pagination
            pagination_sql, pagination_params = self._build_pagination_clause(limit, offset)

            # Step 1: Get matching parent_ids from FTS table
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

            if not parent_ids:
                return []

            # Step 2: Fetch full records from main table
            placeholders = ','.join('?' * len(parent_ids))
            main_query = f'''
                SELECT * FROM "{store_name}"
                WHERE id IN ({placeholders}) AND user_id = ?
            '''

            cursor = await db.execute(main_query, parent_ids + [user_id])
            rows = await cursor.fetchall()

            if not rows:
                return []

            # Deserialize results
            columns = [desc[0] for desc in cursor.description]
            schema_with_meta = schema.copy()
            schema_with_meta.update({
                "id": "str",
                "created_at": "str",
                "updated_at": "str",
                "user_id": "str",
            })

            results = []
            for row in rows:
                record_dict = {}
                for i, col_name in enumerate(columns):
                    raw_value = row[i]
                    field_type = schema_with_meta.get(col_name, "str")
                    record_dict[col_name] = self._deserialize_value(raw_value, field_type)
                results.append(record_dict)

            logger.debug(f"Found {len(results)} records matching '{query}' in store '{store_name}'")
            return results

        except (aiosqlite.Error, ValueError) as e:
            logger.error(f"Error during full-text search for store '{store_name}': {e}")
            raise

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

    async def collection_append(self, user_id: str, store_name: str, record_id: str,
                               field_name: str, item: JSONStructuredRepresentation,
                               _db: Optional[aiosqlite.Connection] = None) -> int:
        """
        Append an item to a json_collection field.

        This is an O(1) operation with no write amplification. Items are stored
        in a separate child table (e.g., "threads_messages" for field "messages"
        in store "threads").

        Args:
            store_name: Name of the parent store
            record_id: ID of the parent record
            field_name: Name of the json_collection field
            item: JSON object to append (dict or list or string)
            _db: database connection to use; if this arg is passed we assume we are in a transaction

        Returns:
            order_index: The index of the newly appended item (0-based)

        Raises:
            ValueError: If store/record doesn't exist or field is not json_collection type
            TypeError: If item is not dict/list or not valid JSON
        """

        async def collection_append_logic(db: aiosqlite.Connection, user_id: str, store_name: str,
                  record_id: str, field_name: str, item_json: str):
            """The core logic of appending. Separated into an inner procedure so that
               we can call it with or without wrapping in a transaction
               Note: the code uses `self` from the outer scope"""

            # Get the store schema
            cursor = await db.execute(
                "SELECT schema_json FROM _stores WHERE name = ?", (store_name,)
            )
            schema_row = await cursor.fetchone()
            if not schema_row:
                raise ValueError(f"Store '{store_name}' does not exist")

            schema = json.loads(schema_row[0])


            # Verify if the field name is in the schema and its type is json_collection
            if field_name not in schema:
                raise ValueError(f"Field '{field_name}' does not exist in store '{store_name}'")
            if schema[field_name] != "json_collection":
                raise ValueError(
                    f"Field '{field_name}' is type '{schema[field_name]}', not 'json_collection'. "
                    f"Use update() for non-collection fields."
                )

            # Verify parent record exists for this user
            cursor = await db.execute(
                f'SELECT id FROM "{store_name}" WHERE id = ? AND user_id = ?', (record_id, user_id)
            )
            if not await cursor.fetchone():
                raise ValueError(f"Record '{record_id}' does not exist in store '{store_name}'")


            # Get child table name
            child_table = self._get_collection_table_name(store_name, field_name)

            # Get current item count for this parent record
            cursor = await db.execute(
                f'SELECT COUNT(*) FROM "{child_table}" WHERE parent_id = ?', (record_id,)
            )
            count_row = await cursor.fetchone()
            order_index = count_row[0] if count_row else 0

            # Insert item into child table
            item_id = str(uuid.uuid4())
            now = datetime.now().isoformat()

            await db.execute(f'''
                INSERT INTO "{child_table}" (id, parent_id, order_index, item_json, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (item_id, record_id, order_index, item_json, now))

            # Update metadata in parent record (count field)
            validated_field_name = self._validate_field_name(field_name)
            metadata_json = json.dumps({
                "collection_store": child_table,
                "count": order_index + 1
            })

            await db.execute(f'''
                UPDATE "{store_name}"
                SET "{validated_field_name}" = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
            ''', (metadata_json, now, record_id, user_id))

            logger.info(
                f"Appended item to '{field_name}' in record '{record_id}' "
                f"at index {order_index} (new count: {order_index + 1})"
            )

            return order_index

        # main collection_append() starts here

        # Validate inputs
        store_name = self._validate_store_name(store_name)
        field_name = self._validate_field_name(field_name)

        # Validate and serialize item
        if not isinstance(item, (dict, list)):
            raise TypeError(
                f"Collection item must be dict or list, got {type(item).__name__}"
            )

        try:
            item_json = json.dumps(item)
        except (TypeError, ValueError) as e:
            raise TypeError(f"Cannot serialize item to JSON: {e}")

        logger.debug(f"Appending item to collection '{field_name}' in record '{record_id}' of store '{store_name}'")

        if _db:
            # database connection was passed, we already are in a transaction
            return await collection_append_logic(_db, user_id, store_name, record_id, field_name, item_json)
        else:
            async with self._get_write_db() as db:
                return await collection_append_logic(db, user_id, store_name, record_id, field_name, item_json)


    async def collection_get(self, user_id: str, store_name: str, record_id: str,
                            field_name: str, limit: Optional[int] = None,
                            offset: int = 0) -> List[JSONStructuredRepresentation]:
        """
        Get items from a json_collection field with pagination.

        This retrieves items from the child table in a single SQL query.

        Args:
            user_id: ID of the user who owns the record
            store_name: Name of the parent store
            record_id: ID of the parent record
            field_name: Name of the json_collection field
            limit: Maximum number of items to return (None = all)
            offset: Number of items to skip (for pagination)

        Returns:
            List of items (dicts/arrays representing JSON), ordered by insertion order (order_index).
            Returns [] if collection is empty or doesn't exist.

        Raises:
            ValueError: If the record exists but belongs to a different user

        Notes:
            - Items are returned as dicts (automatically deserialized from JSON)
            - Order is guaranteed: items returned in insertion order
            - For full thread with collections, use get(load_collections=True) instead
        """
        db = await self._get_read_db()

        # Validate inputs
        store_name = self._validate_store_name(store_name)
        field_name = self._validate_field_name(field_name)

        logger.debug(
            f"Getting items from collection '{field_name}' in record '{record_id}' "
            f"of store '{store_name}' (limit={limit}, offset={offset})"
        )

        # Get child table name
        child_table = self._get_collection_table_name(store_name, field_name)

        # Verify parent record exists and belongs to the specified user
        db = await self._get_read_db()
        cursor = await db.execute(
            f'SELECT id FROM "{store_name}" WHERE id = ? AND user_id = ?', (record_id, user_id)
        )
        if not await cursor.fetchone():
            raise ValueError(f"Record '{record_id}' does not exist or does not belong to user '{user_id}' in store '{store_name}'")

        # initialize query parameters array with just the record ID
        params = [record_id]

        # --- Build LIMIT / OFFSET clauses (Parameterized) ---
        pagination_sql, pagination_params = self._build_pagination_clause(limit, offset)
        params.extend(pagination_params) # Add pagination params to main list

        query = f'''
            SELECT item_json FROM "{child_table}"
            WHERE parent_id = ?
            ORDER BY order_index ASC
            {pagination_sql}
        '''

        cursor = await db.execute(query, params)
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

        logger.info(f"Creating {index_type} index on field '{field_name}' in store '{store_name}'")

        # Validate store name and field name
        store_name = self._validate_store_name(store_name)
        field_name = self._validate_field_name(field_name)

        # Sanitize index name - remove hyphens from store_name for index identifier
        # (store names can have hyphens, but index names cannot)
        safe_store_name = self._sanitize_identifier(store_name)
        index_name = f"idx_{safe_store_name}_{field_name}"

        async with self._get_write_db() as db:
            try:
                if index_type == "unique":
                    sql = f'CREATE UNIQUE INDEX IF NOT EXISTS "{index_name}" ON "{store_name}"("{field_name}")'
                else:
                    sql = f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{store_name}"("{field_name}")'

                logger.debug(f"Creating index with SQL: {sql}")
                await db.execute(sql)

                logger.info(f"Successfully created index on field '{field_name}' in store '{store_name}'")
                return True
            except Exception as e:
                logger.error(f"Failed to create index on field '{field_name}' in store '{store_name}': {e}")
                return False

    async def get_indexes(self, store_name: str) -> List[Dict[str, Any]]:
        """Get information about indexes on a store"""
        db = await self._get_read_db()

        logger.debug(f"Getting indexes for store '{store_name}'")

        # Validate store name
        store_name = self._validate_store_name(store_name)

        cursor = await db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?",
            (store_name,)
        )
        rows = await cursor.fetchall()

        results = [{"name": row[0], "sql": row[1]} for row in rows]
        logger.debug(f"Found {len(results)} indexes for store '{store_name}'")
        return results


# ============================================================================
# DATA MANAGEMENT - ADMIN ONLY FUNCTIONS - NEVER EXPOSE TO USERS
# ============================================================================

    async def export_store(self, store_name: str,
                           format: str = "json") -> Dict[str, Any]:
        """
        Export all records from a store, including json_collection field items.
        Fetches all collection items and groups in Python to avoid SQL parameter limits.

        Note: Runs without a transaction (not a strict snapshot).

        IMPORTANT: THIS METHOD EXPORTS THE CONTENT OF AN ENTIRE STORE FOR ALL USERS.
        USE WITH CAUTION, NEVER EXPOSE TO A NON-ADMIN USER
        """
        db = await self._get_read_db()
        logger.info(f"Exporting store '{store_name}' in format: {format}")
        store_name = self._validate_store_name(store_name)

        try:
            # --- Get Schema ---
            async with db.execute("SELECT schema_json FROM _stores WHERE name = ?", (store_name,)) as cursor:
                schema_row = await cursor.fetchone()
            if not schema_row:
                logger.warning(f"Store '{store_name}' not found for export")
                return {"store": store_name, "records": [], "schema": {}}
            schema = json.loads(schema_row[0])

            # --- Get All Parent Records ---
            async with db.execute(f'SELECT * FROM "{store_name}"') as cursor:
                rows = await cursor.fetchall()
                if not rows:
                     return {"store": store_name, "schema": schema, "records": []}
                columns = [desc[0] for desc in cursor.description]

            # --- Deserialize Parent Records ---
            schema_with_meta = schema.copy()
            schema_with_meta.update({"id": "str", "created_at": "str", "updated_at": "str", "user_id": "str"})
            records = []
            records_by_id = {} # Need map for populating collections later
            for row in rows:
                record_dict = {}
                for i, col_name in enumerate(columns):
                    raw_value = row[i]
                    field_type = schema_with_meta.get(col_name, "str")
                    record_dict[col_name] = self._deserialize_value(raw_value, field_type)
                records.append(record_dict)
                records_by_id[record_dict["id"]] = record_dict # Populate map

            # --- Load All Collection Items (Grouped in Python) ---
            collection_fields = [f for f, t in schema.items() if t == "json_collection"]
            if collection_fields:
                logger.debug(f"Loading {len(collection_fields)} collection field(s): {collection_fields}")

                for field_name in collection_fields:
                    child_table = self._get_collection_table_name(store_name, field_name)
                    logger.debug(f"Fetching all items for '{field_name}' from '{child_table}'")

                    # TODO: Optimize large exports: Use WHERE parent_id IN (...) with batching for parent IDs.
                    # TODO: Current approach fetches all child items, avoiding SQL parameter limits but potentially less efficient.
                    query = f'''
                        SELECT parent_id, item_json FROM "{child_table}"
                        ORDER BY parent_id ASC, order_index ASC
                    ''' # REMOVED: WHERE parent_id IN (...)

                    items_by_parent = {}
                    async with db.execute(query) as item_cursor: # No parameters needed now
                        async for parent_id, item_json_str in item_cursor:
                            # Only process items whose parent was actually fetched initially
                            if parent_id in records_by_id:
                                try:
                                    item = json.loads(item_json_str)
                                    if parent_id not in items_by_parent:
                                        items_by_parent[parent_id] = []
                                    items_by_parent[parent_id].append(item)
                                except json.JSONDecodeError as e:
                                    logger.error(f"Failed to deserialize item for {parent_id} in {field_name}: {e}")

                    # Populate the collection fields in the final records
                    for parent_id, record_dict in records_by_id.items():
                        record_dict[field_name] = items_by_parent.get(parent_id, [])

            # --- Prepare Final Result ---
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
        Uses add() and collection_append() internally for data insertion.
        Runs the import within a single transaction after schema setup.

        NOTE on Duplicate IDs: If an imported record has an 'id' that already
        exists, the add() method will raise a ValueError. This import method
        catches that error, logs a warning, and skips the conflicting record
        (similar to 'INSERT OR IGNORE' behavior).

        NOTE created_at and updated_at are not preserved from the imported data
        currently

        IMPORTANT: THIS METHOD IMPORTA THE CONTENT OF AN ENTIRE STORE FOR ALL USERS.
        WITH replace_existing IT DELETES EXISTING DATA FOR ALL USERS.
        USE WITH CAUTION, NEVER EXPOSE TO A NON-ADMIN USER
        """
        # --- 1. Validate Input Data ---
        if "schema" not in data:
            logger.error(f"Import data for store '{store_name}' is missing the 'schema' key.")
            raise ValueError("Import data must include a 'schema'.")
        # Allow import even if records list is missing/empty (schema update only)
        records = data.get("records", [])
        if not records:
             logger.info(f"Schema provided for '{store_name}', but no records present in import data.")
             # Proceed to schema check/update, but return 0 later if still no records

        # Validate store name early
        store_name = self._validate_store_name(store_name)
        import_schema = data["schema"]
        is_cacheable = data.get("cacheable", False)

        # --- 2. Ensure Store Exists and Schema is Compatible ---
        # This runs its own transaction internally if needed or uses a passed one.
        # Running it *before* the main import transaction simplifies logic,
        # especially around potential ALTER TABLE operations.
        try:
            logger.info(f"Ensuring store '{store_name}' exists and schema is compatible before import.")
            await self.create_store_if_not_exists(store_name, import_schema, cacheable=is_cacheable)
            logger.info(f"Store '{store_name}' is ready for import.")
        except Exception as e:
            logger.error(f"Failed to prepare store '{store_name}' for import: {e}")
            raise # Stop import if schema setup fails

        # Exit if there were truly no records provided
        if not records:
            return 0

        # --- 3. Perform Actual Data Import in a Single Transaction ---
        logger.info(
            f"Importing {len(records)} records into store '{store_name}' "
            f"(replace_existing={replace_existing})"
        )
        imported_count = 0
        skipped_duplicates = 0
        db = await self._init_db() # Get shared connection

        try:
            async with self._get_write_db() as db:
                # Get the potentially updated schema from the database NOW
                async with db.execute("SELECT schema_json FROM _stores WHERE name = ?", (store_name,)) as cursor:
                    schema_row = await cursor.fetchone()
                if not schema_row: # Should exist after step 2, but check again
                     raise RuntimeError(f"Store '{store_name}' disappeared during import setup.")
                current_schema = json.loads(schema_row[0])

                # --- Handle replace_existing ---
                if replace_existing:
                    logger.info(f"Clearing existing data from store '{store_name}'")
                    # Delete parent records (CASCADE handles child table data and FTS via triggers)
                    await db.execute(f'DELETE FROM "{store_name}"')
                    logger.info(f"Cleared existing records from '{store_name}'.")

                # --- Process Records ---
                for record_idx, record in enumerate(records):
                    # Use provided ID or generate if missing
                    record_id = record.get("id") or str(uuid.uuid4())

                    # Separate collection items from parent data based on CURRENT schema
                    parent_data = {}
                    collection_items_to_append = {} # field_name -> list of items

                    for field_name, field_type in current_schema.items():
                        if field_name not in record: # Skip fields missing in import data
                            continue

                        raw_value = record[field_name]

                        if field_type == "json_collection":
                            # Store the list of items to append later
                            if isinstance(raw_value, list):
                                collection_items_to_append[field_name] = raw_value
                            # Ignore non-list values (metadata dict, None etc. for collections during import)
                            # The 'add' method will initialize the metadata correctly.
                        else:
                            # Include regular fields in data for 'add' method
                            parent_data[field_name] = raw_value

                    # --- Add Parent Record ---
                    try:
                        # Extract user_id from record (required field)
                        user_id = record.get("user_id")
                        if not user_id:
                            raise ValueError(f"Record {record_idx+1} missing required 'user_id' field")

                        # Pass the transaction connection '_db=db' and user_id
                        await self.add(user_id, store_name, parent_data, record_id, _db=db)
                        # If add succeeds, proceed to append collection items
                        add_succeeded = True
                    except ValueError as e:
                        # Check if it's the specific "already exists" error from add()
                        if f"Record ID '{record_id}' already exists" in str(e):
                             logger.warning(f"Skipping record {record_idx+1}: ID '{record_id}' already exists in store '{store_name}'.")
                             skipped_duplicates += 1
                             add_succeeded = False # Skip collection append
                        else:
                             # Re-raise other ValueErrors (e.g., store not found, though unlikely here)
                             raise e

                    # --- Append Collection Items (if parent add succeeded) ---
                    if add_succeeded:
                        for field_name, items in collection_items_to_append.items():
                             logger.debug(f"Appending {len(items)} items to collection '{field_name}' for record '{record_id}'...")
                             for item_idx, item in enumerate(items):
                                 try:
                                     # Pass the transaction connection '_db=db' and user_id
                                     await self.collection_append(user_id, store_name, record_id, field_name, item, _db=db)
                                 except Exception as append_err:
                                     # Log specific item error but fail the whole transaction? Yes, safer.
                                     logger.error(f"Error appending item {item_idx+1} to {field_name} for {record_id}: {append_err}")
                                     raise append_err # Fail transaction

                        imported_count += 1 # Count successful parent adds

                # Transaction commits automatically on success

            logger.info(
                f"Import completed for store '{store_name}'. "
                f"Successfully imported: {imported_count}, Skipped duplicates: {skipped_duplicates}."
            )
            return imported_count

        except Exception as e:
            logger.error(f"Import failed for store '{store_name}': {e}. Transaction rolled back.")
            # Transaction rolls back automatically on any exception within the 'async with' block
            raise # Re-raise the exception
