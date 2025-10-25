"""
Manages database schema, stores, and related structures like FTS tables.
"""
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from .connection_manager import SQLiteConnectionManager
from .helpers import (
    validate_store_name,
    validate_field_name,
    map_type,
    get_collection_table_name,
    sanitize_identifier,
)

logger = logging.getLogger("skeleton.sqlite_store")

class SQLiteSchemaManager:
    """Handles schema-related operations for the SQLite store."""

    def __init__(self, conn_manager: SQLiteConnectionManager):
        self._conn_manager = conn_manager

    async def create_store_if_not_exists(self, store_name: str, schema: Dict[str, str],
                                        cacheable: bool = False,
                                        _db: Optional["aiosqlite.Connection"] = None) -> bool:
        """Create a new store with the given schema if it doesn't already exist."""
        async def create_store_logic(db: Any, store_name: str, schema: Dict[str, str], cacheable: bool) -> bool:
            cursor = await db.execute("SELECT schema_json FROM _stores WHERE name = ?", (store_name,))
            existing_row = await cursor.fetchone()

            if existing_row:
                existing_schema = json.loads(existing_row[0])
                logger.debug(f"Store '{store_name}' exists with schema: {existing_schema}")

                missing_fields = set(schema.keys()) - set(existing_schema.keys())
                extra_fields = set(existing_schema.keys()) - set(schema.keys())

                if missing_fields or extra_fields:
                    logger.info(f"Schema differences detected for store '{store_name}':")
                    if missing_fields: logger.info(f"  Missing fields to add: {missing_fields}")
                    if extra_fields: logger.info(f"  Extra fields to ignore: {extra_fields}")

                    if missing_fields:
                        for field_name in missing_fields:
                            field_type = schema[field_name]
                            sql_type = map_type(field_type)
                            validated_field_name = validate_field_name(field_name)
                            alter_sql = f'ALTER TABLE "{store_name}" ADD COLUMN "{validated_field_name}" {sql_type}'
                            logger.info(f"Adding column '{validated_field_name}' to store '{store_name}'")
                            await db.execute(alter_sql)
                            if field_type == "json_collection":
                                await self._create_collection_child_table(db, store_name, field_name)

                        updated_schema = {**existing_schema, **{k: v for k, v in schema.items() if k in missing_fields}}
                        await db.execute("UPDATE _stores SET schema_json = ? WHERE name = ?", (json.dumps(updated_schema), store_name))
                        logger.info(f"Updated schema for store '{store_name}': {updated_schema}")
                logger.info(f"Store '{store_name}' already exists - returning False")
                return False

            logger.info(f"Store '{store_name}' does not exist - creating new store")
            
            # Step 1: Process schema and identify collection fields
            columns = []
            collection_fields = []

            for field_name, field_type in schema.items():
                validated_field_name = validate_field_name(field_name)
                sql_type = map_type(field_type)
                columns.append(f"{validated_field_name} {sql_type}")
                if field_type == "json_collection":
                    collection_fields.append(field_name)

            # Step 2: Create main table with user_id as first column
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

            # Step 3: Create user_id index for multi-tenancy
            index_name = f"idx_{store_name}_user_id"
            await db.execute(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{store_name}" (user_id)')

            # Step 4: Create child tables for collection fields
            for field_name in collection_fields:
                await self._create_collection_child_table(db, store_name, field_name)

            # Step 5: Create FTS table for indexable fields
            indexable_fields = [f for f, t in schema.items() if t in ('str', 'json', 'json_collection')]
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
                await self._create_fts_triggers(db, store_name, indexable_fields)

            # Step 6: Register store in metadata table
            await db.execute(
                "INSERT INTO _stores (name, schema_json, cacheable, created_at) VALUES (?, ?, ?, ?)",
                (store_name, json.dumps(schema), 1 if cacheable else 0, datetime.now().isoformat())
            )
            logger.info(f"Successfully created store '{store_name}' with {len(collection_fields)} collection fields")
            return True

        store_name = validate_store_name(store_name)
        if cacheable and "_version" not in schema:
            schema = {**schema, "_version": "str"}
        logger.info(f"Creating store '{store_name}' with schema: {schema} (cacheable={cacheable})")

        if _db:
            return await create_store_logic(_db, store_name, schema, cacheable)
        else:
            async with self._conn_manager.get_write_db() as db:
                return await create_store_logic(db, store_name, schema, cacheable)

    async def list_stores(self) -> List[str]:
        """List all available store names."""
        db = await self._conn_manager.get_read_db()
        cursor = await db.execute("SELECT name FROM _stores ORDER BY name")
        rows = await cursor.fetchall()
        stores = [row[0] for row in rows]
        logger.debug(f"Listed {len(stores)} stores: {stores}")
        return stores

    async def find_store(self, store_name: str) -> Optional[Dict[str, str]]:
        """Find a store and return its schema."""
        db = await self._conn_manager.get_read_db()
        store_name = validate_store_name(store_name)
        cursor = await db.execute("SELECT schema_json FROM _stores WHERE name = ?", (store_name,))
        row = await cursor.fetchone()
        if row:
            schema = json.loads(row[0])
            logger.debug(f"Found store '{store_name}' with schema: {schema}")
            return schema
        logger.debug(f"Store '{store_name}' not found")
        return None

    async def get_store_stats(self, store_name: str) -> Dict[str, Any]:
        """Get statistics about a store."""
        db = await self._conn_manager.get_read_db()
        logger.debug(f"Getting stats for store '{store_name}'")
        store_name = validate_store_name(store_name)
        cursor = await db.execute(f'SELECT COUNT(*) FROM "{store_name}"')
        count = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT SUM(pgsize) FROM dbstat WHERE name = ?", (store_name,))
        size = (await cursor.fetchone())[0] or 0
        cursor = await db.execute(f'SELECT MIN(created_at), MAX(created_at) FROM "{store_name}"')
        min_max = await cursor.fetchone()
        result = {"store": store_name, "record_count": count, "size_bytes": size, "oldest_record": min_max[0], "newest_record": min_max[1]}
        logger.debug(f"Store stats for '{store_name}': {result}")
        return result

    async def is_cacheable(self, store_name: str) -> bool:
        """Check if a store is marked as cacheable."""
        db = await self._conn_manager.get_read_db()
        store_name = validate_store_name(store_name)
        cursor = await db.execute("SELECT cacheable FROM _stores WHERE name = ?", (store_name,))
        row = await cursor.fetchone()
        if row:
            return bool(row[0])
        logger.warning(f"Store '{store_name}' not found when checking cacheable status")
        return False

    async def _create_collection_child_table(self, db: "aiosqlite.Connection", store_name: str, field_name: str):
        """Helper function to create a child table for a collection."""
        child_table = get_collection_table_name(store_name, field_name)
        logger.info(f"Creating child table '{child_table}' for new collection field '{field_name}'")
        
        # Step 1: Create the child table with foreign key constraint
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
        
        # Step 2: Create index for efficient querying by parent_id
        index_name = f"idx_{sanitize_identifier(child_table)}_parent"
        await db.execute(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{child_table}"(parent_id, order_index)')
        
        # Step 3: Register child table in metadata
        child_schema = {"parent_id": "str", "order_index": "int", "item_json": "json"}
        await db.execute("INSERT OR IGNORE INTO _stores (name, schema_json, cacheable, created_at) VALUES (?, ?, ?, ?)",
                         (child_table, json.dumps(child_schema), 0, datetime.now().isoformat()))
        logger.info(f"Successfully created child table '{child_table}'")
        
        # Step 4: Create FTS triggers for the collection
        await self._create_collection_fts_triggers(db, store_name, field_name, child_table)

    async def _create_collection_fts_triggers(self, db: "aiosqlite.Connection", store_name: str, field_name: str, child_table: str) -> None:
        """Create FTS triggers for collection child tables."""
        fts_table = f"fts_{store_name}"
        
        # Step 1: Create INSERT trigger to sync new collection items to FTS
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
        
        # Step 2: Create DELETE trigger to remove deleted items from FTS
        delete_trigger = f'''
            CREATE TRIGGER IF NOT EXISTS "fts_{child_table}_delete"
            AFTER DELETE ON "{child_table}"
            BEGIN
                DELETE FROM "{fts_table}" WHERE child_id = '{field_name}_' || OLD.id;
            END
        '''
        await db.execute(delete_trigger)
        logger.info(f"Created FTS triggers for collection table '{child_table}'")

    async def _create_fts_triggers(self, db: "aiosqlite.Connection", store_name: str, indexable_fields: List[str]) -> None:
        """Create triggers for automatic FTS synchronization."""
        fts_table = f"fts_{store_name}"
        fts_columns = ", ".join(f'"{f}"' for f in indexable_fields)
        new_values = ", ".join(f'NEW."{f}"' for f in indexable_fields)
        
        # Step 1: Create INSERT trigger to sync new records to FTS
        insert_trigger = f'''
            CREATE TRIGGER IF NOT EXISTS "fts_{store_name}_insert"
            AFTER INSERT ON "{store_name}"
            BEGIN
                INSERT INTO "{fts_table}" (user_id, parent_id, child_id, {fts_columns})
                VALUES (NEW.user_id, NEW.id, '', {new_values});
            END
        '''
        await db.execute(insert_trigger)
        
        # Step 2: Create UPDATE trigger to sync modified records to FTS
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
        
        # Step 3: Create DELETE trigger to remove deleted records from FTS
        delete_trigger = f'''
            CREATE TRIGGER IF NOT EXISTS "fts_{store_name}_delete"
            AFTER DELETE ON "{store_name}"
            BEGIN
                DELETE FROM "{fts_table}" WHERE parent_id = OLD.id AND user_id = OLD.user_id;
            END
        '''
        await db.execute(delete_trigger)
        logger.info(f"Created FTS triggers for store '{store_name}'")
