"""
SQLite/FTS5 implementation of the GenericStorePlugin.
Provides full-text search and exact matching capabilities using SQLite.

This class acts as a facade, delegating responsibilities to specialized
components in the `sqlite_store` subdirectory.
"""
import os
import logging
from typing import Dict, Any, Optional, List

from .protocols import StorePlugin, JSONStructuredRepresentation
from .sqlite_store.connection_manager import SQLiteConnectionManager
from .sqlite_store.schema_manager import SQLiteSchemaManager
from .sqlite_store.crud_operations import SQLiteCrudOperations
from .sqlite_store.collection_operations import SQLiteCollectionOperations
from .sqlite_store.admin_operations import SQLiteAdminOperations

logger = logging.getLogger("skeleton.sqlite_store")

class SQLiteStorePlugin(StorePlugin):
    """SQLite implementation with FTS5 full-text search support"""

    def __init__(self, db_path: Optional[str] = None):
        """Initialize SQLite store with optional custom path"""
        # If a direct db_path is passed, use it (for testing)
        if db_path:
            self.db_path = db_path
        else:
            # Otherwise, construct from environment variables
            data_dir = os.getenv("DATA_PATH", ".") # Default to current dir
            db_filename = os.getenv("SQLITE_DB_FILENAME", "skeleton.db") # Allow customizing filename
            self.db_path = os.path.join(data_dir, db_filename)

        # Initialize component managers with named parameters for clarity
        self._conn_manager = SQLiteConnectionManager(db_path=self.db_path)
        self._schema_manager = SQLiteSchemaManager(conn_manager=self._conn_manager)
        self._crud_ops = SQLiteCrudOperations(
            conn_manager=self._conn_manager, 
            schema_manager=self._schema_manager
        )
        self._collection_ops = SQLiteCollectionOperations(
            conn_manager=self._conn_manager, 
            schema_manager=self._schema_manager
        )
        self._admin_ops = SQLiteAdminOperations(
            conn_manager=self._conn_manager, 
            schema_manager=self._schema_manager, 
            crud_ops=self._crud_ops, 
            collection_ops=self._collection_ops
        )

    # -------------------------------------------------------------------------
    # Core Plugin Protocol Methods
    # -------------------------------------------------------------------------

    def get_role(self) -> str:
        """Return the role string for this plugin"""
        return "store"

    def get_priority(self) -> int:
        """Default priority - can be overridden by other plugins"""
        return 0

    async def shutdown(self):
        """Graceful shutdown: closes the shared database connection."""
        await self._conn_manager.shutdown()

    # -------------------------------------------------------------------------
    # Store Management Methods (Delegated to SchemaManager)
    # -------------------------------------------------------------------------

    async def create_store_if_not_exists(self, store_name: str, schema: Dict[str, str],
                                        cacheable: bool = False) -> bool:
        """Create a new store with the given schema if it doesn't already exist."""
        return await self._schema_manager.create_store_if_not_exists(store_name, schema, cacheable)

    async def list_stores(self) -> List[str]:
        """List all available store names."""
        return await self._schema_manager.list_stores()

    async def find_store(self, store_name: str) -> Optional[Dict[str, str]]:
        """Find a store and return its schema."""
        return await self._schema_manager.find_store(store_name)

    async def get_store_stats(self, store_name: str) -> Dict[str, Any]:
        """Get statistics about a store."""
        return await self._schema_manager.get_store_stats(store_name)

    async def is_cacheable(self, store_name: str) -> bool:
        """Check if a store is marked as cacheable."""
        return await self._schema_manager.is_cacheable(store_name)

    # -------------------------------------------------------------------------
    # CRUD Methods (Delegated to CrudOperations)
    # -------------------------------------------------------------------------

    async def add(self, user_id: str, store_name: str, data: Dict[str, Any], record_id: Optional[str] = None) -> str:
        """Add a new record to a store."""
        return await self._crud_ops.add(user_id, store_name, data, record_id)

    async def get(self, user_id: str, store_name: str, record_id: str,
                  load_collections: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get a single record by ID, optionally loading json_collection fields.

        This method orchestrates fetching the parent record and then fetching
        any collection items if requested.
        """
        record = await self._crud_ops.get(user_id, store_name, record_id)

        if not record or not load_collections:
            return record

        # If load_collections is True, fetch collection items
        schema = await self._schema_manager.find_store(store_name)
        if not schema:
            # This case should ideally not happen if crud_ops.get succeeded
            return record

        for field_name, field_type in schema.items():
            if field_type == "json_collection" and field_name in record:
                # The record contains metadata, replace it with actual items
                items = await self._collection_ops.collection_get(user_id, store_name, record_id, field_name)
                record[field_name] = items

        return record

    async def update(self, user_id: str, store_name: str, record_id: str,
                     updates: Dict[str, Any], partial: bool = True) -> bool:
        """Update a record by ID."""
        return await self._crud_ops.update(user_id, store_name, record_id, updates, partial)

    async def delete(self, user_id: str, store_name: str, record_id: str) -> bool:
        """Delete a record by ID."""
        return await self._crud_ops.delete(user_id, store_name, record_id)

    async def count(self, user_id: str, store_name: str, filters: Dict[str, Any] = None) -> int:
        """Count records matching filters."""
        return await self._crud_ops.count(user_id, store_name, filters)

    async def find(self, user_id: str, store_name: str, filters: Dict[str, Any] = None,
                   limit: Optional[int] = None, offset: int = 0,
                   order_by: str = None, order_desc: bool = False) -> List[Dict[str, Any]]:
        """Find records with optional filters, pagination, and sorting."""
        return await self._crud_ops.find(user_id, store_name, filters, limit, offset, order_by, order_desc)

    async def full_text_search(self,
                               user_id: str,
                               store_name: str,
                               query: str,
                               limit: Optional[int] = None,
                               offset: int = 0) -> List[Dict[str, Any]]:
        """Full-text search across all indexable fields in a store."""
        return await self._crud_ops.full_text_search(user_id, store_name, query, limit, offset)

    # -------------------------------------------------------------------------
    # Collection Methods (Delegated to CollectionOperations)
    # -------------------------------------------------------------------------

    async def collection_append(self, user_id: str, store_name: str, record_id: str,
                               field_name: str, item: JSONStructuredRepresentation) -> int:
        """Append an item to a json_collection field."""
        return await self._collection_ops.collection_append(user_id, store_name, record_id, field_name, item)

    async def collection_get(self, user_id: str, store_name: str, record_id: str,
                            field_name: str, limit: Optional[int] = None,
                            offset: int = 0) -> List[JSONStructuredRepresentation]:
        """Get items from a json_collection field with pagination."""
        return await self._collection_ops.collection_get(user_id, store_name, record_id, field_name, limit, offset)

    # -------------------------------------------------------------------------
    # Admin/Index Methods (Delegated to AdminOperations)
    # -------------------------------------------------------------------------

    async def create_index(self, store_name: str, field_name: str,
                          index_type: str = "default") -> bool:
        """Create an index on a field."""
        return await self._admin_ops.create_index(store_name, field_name, index_type)

    async def get_indexes(self, store_name: str) -> List[Dict[str, Any]]:
        """Get information about indexes on a store."""
        return await self._admin_ops.get_indexes(store_name)

    async def export_store(self, store_name: str,
                           format: str = "json") -> Dict[str, Any]:
        """Export all records from a store, including json_collection field items."""
        return await self._admin_ops.export_store(store_name, format)

    async def import_store(self, store_name: str,
                           data: Dict[str, Any],
                           replace_existing: bool = False) -> int:
        """Import records into a store. Creates/updates the store schema if needed."""
        return await self._admin_ops.import_store(store_name, data, replace_existing)
