# Store Plugin Protocol Documentation

The Store Plugin Protocol provides a unified interface for data storage with support for both regular JSON fields and append-only JSON collections. This document explains how to use the store system, format schemas, and work with collections.

## Overview

The store system is designed around these key principles:
- **Unified CRUD operations** across any store/table/collection
- **Schema-driven** with automatic table creation and validation
- **Append-only collections** for efficient thread/message patterns
- **Full-text search** using SQLite FTS5
- **Type safety** with automatic serialization/deserialization

## Schema Format

Schemas define the structure of your store using a dictionary mapping field names to types:

```python
schema = {
    "title": "str",
    "content": "str", 
    "metadata": "json",
    "is_archived": "bool",
    "priority": "int",
    "score": "float",
    "messages": "json_collection"  # Special append-only collection
}
```

### Supported Field Types

| Type | Description | Storage | Usage |
|------|-------------|---------|--------|
| `str` | Text strings | TEXT | Any text data |
| `int` | Integers | INTEGER | Whole numbers |
| `float` | Floating point | REAL | Decimal numbers |
| `bool` | Boolean values | INTEGER (0/1) | True/False |
| `json` | JSON objects/arrays | TEXT | Complex data structures |
| `json_collection` | Append-only arrays | TEXT (metadata) + child table | Thread messages, logs |

## Collections: The Append-Only Pattern

Collections are a key optimization for append-only data patterns like chat messages, logs, or activity feeds. They provide:

- **O(1) append operations** - no read-modify-write cycles
- **No write amplification** - appends don't touch parent records
- **Automatic ordering** - items maintain insertion order
- **Efficient pagination** - built-in limit/offset support

### How Collections Work

1. **Parent Table**: Stores regular fields plus collection metadata
2. **Child Table**: Stores individual collection items with order_index
3. **Automatic Triggers**: Keep FTS5 indexes in sync
4. **Lazy Loading**: Collections only load when requested

### Example: Thread with Messages

```python
# Create a thread store with messages collection
schema = {
    "title": "str",
    "model": "str", 
    "system_prompt": "str",
    "messages": "json_collection"  # This will create threads_messages table
}

# Create thread (messages field auto-initialized as empty collection)
thread_id = await store.add("threads", {
    "title": "My Chat",
    "model": "gpt-4",
    "system_prompt": "You are helpful"
})

# Append messages (O(1) operation)
await store.collection_append("threads", thread_id, "messages", {
    "role": "user",
    "content": "Hello!",
    "timestamp": "2024-01-01T10:00:00Z"
})

await store.collection_append("threads", thread_id, "messages", {
    "role": "assistant", 
    "content": "Hi there!",
    "timestamp": "2024-01-01T10:00:01Z"
})

# Get messages with pagination
messages = await store.collection_get("threads", thread_id, "messages", 
                                     limit=10, offset=0)

# Get full thread with collections loaded
thread = await store.get("threads", thread_id, load_collections=True)
# thread["messages"] now contains the actual array of messages
```

### Collection Best Practices

1. **Use for append-only data**: Messages, logs, events, history
2. **Don't use for mutable arrays**: Use json field for arrays you need to update
3. **Consider pagination**: Large collections should be paginated
4. **Index parent_id**: Child tables automatically index parent_id for fast lookups

## Store Operations

### Basic CRUD

```python
# Create store with schema
await store.create_store_if_not_exists("users", {
    "username": "str",
    "email": "str", 
    "profile": "json",
    "is_active": "bool"
})

# Add record
user_id = await store.add("users", {
    "username": "alice",
    "email": "alice@example.com",
    "profile": {"age": 30, "location": "NYC"},
    "is_active": True
})

# Get record
user = await store.get("users", user_id)

# Update record
await store.update("users", user_id, {
    "email": "alice@newdomain.com",
    "profile": {"age": 31, "location": "Boston"}
})

# Delete record  
await store.delete("users", user_id)

# Count records
count = await store.count("users", {"is_active": True})
```

### Querying

```python
# Find with filters
active_users = await store.find("users", 
                               filters={"is_active": True},
                               limit=10, 
                               order_by="username")

# Search with full-text search
results = await store.search("users", "alice", 
                           search_fields=["username", "email"],
                           filters={"is_active": True})

# Complex filters with LIKE
users = await store.find("users", 
                        filters={"email": {"$like": "%@example.com"}})
```

### Batch Operations

```python
# Batch add
ids = await store.batch_add("users", [
    {"username": "bob", "email": "bob@example.com"},
    {"username": "carol", "email": "carol@example.com"}
])

# Batch update
updated = await store.batch_update("users", [
    {"id": id1, "is_active": False},
    {"id": id2, "is_active": False}
])

# Batch delete
deleted = await store.batch_delete("users", [id1, id2, id3])
```

## Advanced Features

### Full-Text Search (FTS5)

The store automatically creates FTS5 virtual tables for searchable fields:

```python
# Schema with searchable fields
schema = {
    "title": "str",           # Will be indexed
    "content": "str",         # Will be indexed  
    "metadata": "json",       # Will be indexed
    "tags": "json_collection" # Child table will be indexed
}

# Search across all indexed fields
results = await store.search("articles", "machine learning")

# Search specific fields
results = await store.search("articles", "python", 
                           search_fields=["title", "content"])

# Search with filters
results = await store.search("articles", "AI",
                           filters={"category": "technology"})
```

### Data Export/Import

```python
# Export store (includes collections as arrays)
export_data = await store.export_store("threads")

# Import store (handles collections correctly)
imported = await store.import_store("threads", export_data, 
                                  replace_existing=True)
```

### Raw Queries (Advanced)

For complex queries, you can execute raw SQL (with safety checks):

```python
# Custom query with safety validation
results = await store.query_raw("users", 
                               "SELECT username, COUNT(*) as count FROM users GROUP BY username")
```

## Error Handling

The store provides detailed error messages for common issues:

```python
try:
    await store.add("users", {"username": None})
except TypeError as e:
    # Field 'username' expects str, got NoneType
    print(f"Validation error: {e}")

try:
    await store.update("users", user_id, {"messages": []})  # Collection field
except ValueError as e:
    # Cannot update json_collection fields via update()
    print(f"Collection error: {e}")
```

## Performance Considerations

1. **Collections are optimized for appends** - use them for append-only data
2. **FTS5 indexes are automatic** - but searches are still fast
3. **Child tables are indexed** - collection lookups are O(log n)
4. **Batch operations** - use batch_add/batch_update for bulk operations
5. **Pagination** - always use limit/offset for large result sets

## Migration and Schema Evolution

The store handles schema changes gracefully:

```python
# Original schema
schema_v1 = {"name": "str", "email": "str"}

# Updated schema with new fields
schema_v2 = {"name": "str", "email": "str", "age": "int", "profile": "json"}

# Store automatically adds missing fields
await store.create_store_if_not_exists("users", schema_v2)
# Existing records keep their data, new fields get NULL/default values
```

This documentation covers the complete Store Plugin Protocol. The implementation in `sqlite_store_plugin.py` provides a production-ready SQLite backend with all these features working together seamlessly.
