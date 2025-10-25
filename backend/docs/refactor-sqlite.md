## **Project: Refactor `SQLiteStorePlugin` for Multi-Tenancy and FTS5**

### 1. Overview

This task is to refactor the `SQLiteStorePlugin` to enforce strict multi-tenancy using a `user_id` and to replace the global FTS system with a new per-store FTS5 virtual table. This involves API changes, schema changes, and updates to all data-handling methods. We will use **Model A**: a denormalized FTS table that duplicates text content for fast, secure searching.

---

### 2. Pillar 1: Multi-Tenancy (`user_id`) Implementation

The plugin must be refactored to treat `user_id` as a mandatory, system-managed metadata field, just like `id` and `created_at`.

* **Method Signatures:** All public-facing data methods must be modified to accept a `user_id: str` as their first argument after `self` (or `store_name`).
    * `create_store_if_not_exists(self, store_name, ...)` -> **No change**. Store schemas are global.
    * `add(self, store_name, ...)` -> `add(self, user_id: str, store_name: str, ...)`
    * `get(self, store_name, ...)` -> `get(self, user_id: str, store_name: str, ...)`
    * `update(self, store_name, ...)` -> `update(self, user_id: str, store_name: str, ...)`
    * `delete(self, store_name, ...)` -> `delete(self, user_id: str, store_name: str, ...)`
    * `find(self, store_name, ...)` -> `find(self, user_id: str, store_name: str, ...)`
    * `count(self, store_name, ...)` -> `count(self, user_id: str, store_name: str, ...)`
    * `collection_append(self, store_name, ...)` -> `collection_append(self, user_id: str, store_name: str, ...)`
    * ...and so on for all data-handling methods.

* **Main Table Schema:**
    * Modify `create_store_if_not_exists` to automatically add a `user_id TEXT NOT NULL` column to the main table's `CREATE TABLE` statement.
    * This field should **not** be part of the user-provided `schema` dict.
    * After creating the table, an index must be created:
        ```sql
        CREATE INDEX IF NOT EXISTS "idx_{store_name}_user_id" ON "{store_name}" (user_id);
        ```

* **Query Enforcement:**
    * Every single SQL query that reads, writes, or deletes data **must** be scoped by `user_id`.
    * `get`: `SELECT * FROM "{store_name}" WHERE id = ? AND user_id = ?`
    * `update`: `UPDATE "{store_name}" SET ... WHERE id = ? AND user_id = ?`
    * `delete`: `DELETE FROM "{store_name}" WHERE id = ? AND user_id = ?`
    * `find`/`count`: The `_build_where_clause` helper must be modified to *always* inject `user_id = ?` into the `WHERE` clause, in addition to any user-provided filters.
    * `collection_append`: The check for the parent record's existence must be: `SELECT 1 FROM "{store_name}" WHERE id = ? AND user_id = ?`

---

### 3. Pillar 2: FTS Refactor (Per-Store, Denormalized)

The global `_fts_content` table is deprecated.

* **Remove Old Logic:** All references to `_fts_content` (creation in `_init_db`, `INSERT`s, `DELETE`s) must be removed.

* **New FTS Table Schema:**
    * For each store, a corresponding FTS virtual table must exist, named `fts_{store_name}`.
    * This table must have the following schema:
        1.  `user_id TEXT UNINDEXED`: To filter searches by user.
        2.  `parent_id TEXT UNINDEXED`: The `id` of the main record.
        3.  `child_id TEXT UNINDEXED`: An ID for a collection item (e.g., `messages_item-uuid`), or an empty string (`''`) if this row represents the parent record.
        4.  **Content Columns:** One column for *each* field in the store's schema of type `str`, `json`, or `json_collection`. (e.g., `title`, `content`, `messages`).

* **`create_store_if_not_exists`:**
    * This method must now generate and execute *two* `CREATE` statements.
    * First, the main table (with the new `user_id` column).
    * Second, the `fts_{store_name}` table.
    * *Example logic:*
        ```python
        # ... inside create_store_if_not_exists ...
        indexable_fields = [
            f for f, t in schema.items()
            if t in ('str', 'json', 'json_collection')
        ]
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
        ```

* **`add` Method Logic:**
    * Must now accept `user_id`.
    * After inserting the parent record into `"{store_name}"`, it must insert a row into `"{fts_{store_name}}"` representing the parent.
    * `child_id` for this row is `''`.
    * `parent_id` is the `record_id`.
    * The `user_id` is included.
    * Text/JSON content from `data` is duplicated into the corresponding columns.

* **`collection_append` Method Logic:**
    * Must now accept `user_id`.
    * After inserting the item into the child table (e.g., `threads_messages`), it must insert a *new row* into `"{fts_{store_name}}"` representing this child item.
    * `parent_id` is the parent's `record_id`.
    * `child_id` is a unique identifier for this item (e.g., `f"{field_name}_{item_id}"`).
    * The `user_id` is included.
    * The `item_json` text is inserted into the FTS column corresponding to the collection (e.g., the `messages` column).

* **`delete` Method Logic:**
    * Must now accept `user_id`.
    * The `DELETE` from `"{store_name}"` will cascade to child tables.
    * A *second* `DELETE` must be executed to clear all FTS entries (parent and children) for this record:
        ```sql
        DELETE FROM "fts_{store_name}" WHERE parent_id = ? AND user_id = ?
        ```
        (Pass `record_id` and `user_id` as parameters).

* **`update` Method Logic:**
    * Must now accept `user_id`.
    * It must update the main table `"{store_name}"`.
    * It must also re-synchronize the *parent* FTS row. The simplest way is to:
        1.  `DELETE FROM "fts_{store_name}" WHERE parent_id = ? AND user_id = ? AND child_id = ''`
        2.  Fetch the newly updated data from the main table.
        3.  `INSERT` a new FTS parent row (with `child_id = ''`) containing all the updated text fields.
    * **Note:** This logic does not update FTS for collections. This is an acceptable limitation for this refactor.

---

### 4. Pillar 3: API/Protocol Changes

* **Remove `search()`:** The existing `search` method and its complex logic must be completely removed.
* **Remove `full_text_search()`:** The *old* simple `full_text_search` must also be removed.

* **Create New `full_text_search()`:**
    * **Signature:**
        ```python
        async def full_text_search(self,
                                   user_id: str,
                                   store_name: str,
                                   query: str,
                                   limit: Optional[int] = None,
                                   offset: int = 0) -> List[Dict[str, Any]]:
        ```
    * **Implementation:** This method searches *only* the FTS table and returns the full, deserialized *parent records* that match.
        1.  Validate `store_name` and get the `fts_table_name`.
        2.  Build the FTS match string (e.g., `f'"{query}"*'`).
        3.  Build pagination SQL (`_build_pagination_clause`).
        4.  **Step 1: Get matching `parent_id`s.** Execute a query to get the `parent_id`s of matching records, scoped by the user.
            ```sql
            parent_id_query = f'''
                SELECT DISTINCT parent_id
                FROM "{fts_table_name}"
                WHERE "{fts_table_name}" MATCH ? AND user_id = ?
                ORDER BY rank -- FTS ranking
                {pagination_sql}
            '''
            ```
            (Execute with `[match_string, user_id] + pagination_params`).
        5.  Fetch all `parent_id`s from this query. If none, return `[]`.
        6.  **Step 2: Fetch and deserialize full records.** Use the list of `parent_id`s to fetch the full records from the main table.
            ```sql
            placeholders = ','.join('?' * len(parent_ids))
            main_query = f'''
                SELECT * FROM "{store_name}"
                WHERE id IN ({placeholders}) AND user_id = ?
            '''
            ```
        7.  Deserialize these rows (using logic similar to `find`) and return them.
            * *Note:* The final list may not be in `rank` order unless you re-order it in Python based on the `parent_ids` list. This is acceptable.

---

### 5. Summary of Affected Methods

* `_init_db`: Remove `_fts_content` creation.
* `create_store_if_not_exists`: Add `user_id` column/index to main table. Add `fts_{store_name}` virtual table creation.
* `add`: Add `user_id` param. Add `user_id` to `INSERT`. Add FTS parent row `INSERT`.
* `get`: Add `user_id` param. Add `user_id` to `WHERE`.
* `update`: Add `user_id` param. Add `user_id` to `WHERE`. Add FTS parent row re-sync logic.
* `delete`: Add `user_id` param. Add `user_id` to `WHERE`. Add FTS `DELETE` for `parent_id`.
* `find`: Add `user_id` param. Modify `_build_where_clause` to inject `user_id`.
* `count`: Add `user_id` param. Modify `_build_where_clause` to inject `user_id`.
* `collection_append`: Add `user_id` param. Add `user_id` to parent check. Add FTS child row `INSERT`.
* `search`: **REMOVE**.
* `full_text_search`: **REPLACE** with new multi-tenant implementation specified above.

### 6. Out of Scope

* You do not need to implement any offline, command-line FTS rebuild utility.
* You do not need to implement surgical FTS updates for `collection_update`
