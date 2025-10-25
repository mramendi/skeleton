"""
Manages SQLite database connections, initialization, and lifecycle.

This module provides a robust connection management system for SQLite databases:
- Maintains separate read/write connections for optimal performance
- Implements connection pooling with single instances
- Handles database initialization with retry logic and exponential backoff
- Provides transaction management with automatic rollback on errors
- Supports graceful shutdown with connection cleanup
- Uses WAL mode for better concurrency

Key Design Patterns:
- Singleton pattern for connections (one read, one write)
- Double-checked locking for lazy initialization
- Context managers for transaction safety
- Exponential backoff with jitter for retry logic

This module is designed to be reusable as a standalone component for SQLite
connection management in async Python applications.
"""
import os
import random
import asyncio
import aiosqlite
import logging
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger("skeleton.sqlite_store")

class SQLiteConnectionManager:
    """
    Manages a single read and a single write connection to an SQLite database.

    This class implements a connection management pattern that:
    - Maintains exactly one read connection and one write connection
    - Serializes all write operations through a lock
    - Provides automatic database initialization with schema setup
    - Handles database locking with intelligent retry logic
    - Supports graceful shutdown and connection cleanup

    The design prioritizes:
    - Performance: Separate connections prevent read/write blocking
    - Reliability: Comprehensive error handling and retry logic
    - Safety: Transaction management with automatic rollback
    - Concurrency: WAL mode for better read/write concurrency
    """

    def __init__(self, db_path: str):
        """
        Initialize the connection manager with a specific database path.

        Args:
            db_path: Full path to the SQLite database file

        Side Effects:
            - Creates the database directory if it doesn't exist
            - Initializes connection state variables
            - Sets up synchronization primitives
        """
        self.db_path = db_path
        # Ensure the directory for the database exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        # Connection instances - lazily initialized
        self._write_conn: Optional[aiosqlite.Connection] = None  # Single write connection
        self._read_conn: Optional[aiosqlite.Connection] = None   # Single read connection

        # Synchronization primitives for thread safety
        self._init_lock = asyncio.Lock()    # Prevents concurrent initialization
        self._write_lock = asyncio.Lock()   # Serializes write operations
        self._shutting_down = False         # Flag to prevent new connections during shutdown

    async def _init_db(self) -> None:
        """
        Initialize the WRITE and READ database connections and create core tables if needed.

        This method implements a robust initialization pattern:
        1. Checks shutdown status to prevent new connections during shutdown
        2. Uses double-checked locking with _init_lock for thread safety
        3. Creates write connection with WAL mode and foreign keys
        4. Initializes core _stores table in a transaction
        5. Creates read-only connection using URI for safety
        6. Implements retry logic with exponential backoff for database locks

        The initialization process:
        - Fast path: Return immediately if already initialized
        - Slow path: Acquire lock and perform full initialization
        - Retry logic: Handle database locks with exponential backoff + jitter
        - Cleanup: Properly close connections on any failure

        Raises:
            RuntimeError: If initialization fails or shutdown is in progress
        """
        # Prevent database access if system is shutting down
        if self._shutting_down:
            raise RuntimeError("Database access attempted after shutdown initiated.")

        # Fast path: return immediately if already initialized
        if self._write_conn:
            return

        # Slow path: acquire lock and initialize
        async with self._init_lock:
            # Double-check after acquiring lock (double-checked locking pattern)
            if self._write_conn is not None:
                return

            # Retry configuration for handling database locks
            max_retries = 7
            base_delay = 0.02   # 20ms base delay
            max_delay = 2.0     # 2000ms max delay

            for attempt in range(max_retries):
                try:
                    logger.info(
                        f"Initializing new shared connection to {self.db_path} "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )

                    # Step 1: Create write connection and configure pragmas
                    conn = await aiosqlite.connect(self.db_path)
                    await conn.execute("PRAGMA foreign_keys = ON;")    # Enable foreign key constraints
                    await conn.execute("PRAGMA journal_mode=WAL;")      # Enable Write-Ahead Logging
                    await conn.commit()
                    logger.info("Enabled WAL mode and foreign keys.")

                    # Step 2: Create core tables in a transaction
                    await conn.execute("BEGIN IMMEDIATE")  # Acquire write lock immediately
                    try:
                        # Create the _stores metadata table for schema management
                        await conn.execute('''
                            CREATE TABLE IF NOT EXISTS _stores (
                                name TEXT PRIMARY KEY,           -- Store name
                                schema_json TEXT NOT NULL,       -- JSON schema definition
                                cacheable INTEGER NOT NULL DEFAULT 0,  -- Cacheable flag
                                created_at TEXT NOT NULL         -- Creation timestamp
                            )
                        ''')
                        await conn.commit()
                        logger.info("Core database tables initialized (_stores).")

                        # Store the write connection for future use
                        self._write_conn = conn

                    except Exception as e:
                        logger.error("Exception in initial transaction - rolling back and closing")
                        await conn.rollback()
                        await conn.close()
                        raise

                    # Step 3: Create read-only connection using URI for safety
                    db_uri = f"file:{self.db_path}?mode=ro"
                    logger.info(f"Initializing new shared READ-ONLY connection using URI: {db_uri}")
                    self._read_conn = await aiosqlite.connect(db_uri, uri=True)

                    # Success - break out of retry loop
                    break

                except aiosqlite.OperationalError as e:
                    # Handle database locking with exponential backoff
                    if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                        # Calculate delay with jitter to avoid thundering herd
                        delay = min(base_delay * (2 ** attempt) + random.uniform(0, 0.001), max_delay)
                        logger.warning(
                            f"Database locked during init, retrying in {delay:.3f}s "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        # Clean up any partial connection before retry
                        if 'conn' in locals():
                            try:
                                await conn.close()
                            except Exception:
                                pass
                        await asyncio.sleep(delay)
                        continue
                    else:
                        # Re-raise if not a lock error or retries exhausted
                        error_msg = f"Failed to initialize database after {attempt + 1} attempts"
                        if "database is locked" in str(e).lower():
                            error_msg += " due to database locking"
                        logger.error(f"{error_msg}: {e}")
                        # Clean up any partial connection before raising
                        if 'conn' in locals():
                            try:
                                await conn.close()
                            except Exception:
                                pass
                        raise RuntimeError(f"{error_msg}: {e}")

                except Exception as e:
                    # Handle unexpected errors during initialization
                    logger.error(f"Unexpected error during database initialization: {e}")
                    # Clean up any partial connection
                    if 'conn' in locals():
                        try:
                            await conn.close()
                        except Exception:
                            pass
                    raise

            else:
                # This executes if for loop completes without breaking (all retries failed)
                # Final cleanup if initialization failed after all retries
                error_msg = f"Failed to initialize database after {max_retries} attempts due to database locking"
                logger.error(error_msg)
                if 'conn' in locals():
                    try:
                        await conn.close()
                    except Exception:
                        pass
                if self._write_conn:
                    try:
                        await self._write_conn.close()
                    except Exception:
                        pass
                self._write_conn = None
                if self._read_conn:
                    try:
                        await self._read_conn.close()
                    except Exception:
                        pass
                self._read_conn = None
                raise RuntimeError(error_msg)

    async def get_read_db(self) -> aiosqlite.Connection:
        """
        Get a read connection, initializing it if necessary.

        This method provides lazy initialization of the read connection:
        - Checks if connection exists, initializes if needed
        - Returns the shared read-only connection
        - Multiple callers can use the connection concurrently

        Returns:
            The shared read-only database connection

        Raises:
            RuntimeError: If database access is attempted after shutdown

        Note:
            Read connections can be used concurrently by multiple coroutines
            as SQLite allows multiple simultaneous readers
        """
        if not self._read_conn:
            await self._init_db()  # Ensure connection is initialized
        return self._read_conn

    @asynccontextmanager
    async def get_write_db(self) -> aiosqlite.Connection:
        """
        Get a write connection with serialization, retry logic, and transaction management.

        This context manager provides a complete transaction management system:
        1. Ensures the write connection is initialized
        2. Acquires an exclusive write lock to serialize all write operations
        3. Begins a transaction with BEGIN IMMEDIATE (with retry logic for locks)
        4. Yields the connection for the caller's database operations
        5. Commits the transaction automatically on success
        6. Rolls back automatically on any exception (including client exceptions)
        7. Releases the write lock when done

        Usage Pattern:
            async with conn_manager.get_write_db() as db:
                await db.execute("INSERT INTO table VALUES (?)", (value,))
                # Transaction commits automatically here

        Yields:
            The shared write database connection

        Raises:
            RuntimeError: If database access is attempted after shutdown
             or database remains locked after retries when starting the transaction 
            aiosqlite.Error: If database operations fail on other ways

        Design Notes:
        - Uses BEGIN IMMEDIATE to acquire write lock immediately
        - Implements exponential backoff with jitter to avoid thundering herd
        - All write operations are serialized through the write lock
        - Automatic transaction management prevents resource leaks
        - Retry logic only applies to BEGIN IMMEDIATE, not client operations
        """
        # Ensure connection is initialized before attempting to use it
        if not self._write_conn:
            await self._init_db()

        # Retry configuration for write operations
        max_retries = 7
        base_delay = 0.02   # 20ms base delay
        max_delay = 2.0     # 2000ms max delay

        # Serialize all write operations through the lock
        async with self._write_lock:
            logger.debug("Write lock acquired.")

            # Retry loop only for BEGIN IMMEDIATE (acquiring write lock)
            for attempt in range(max_retries):
                try:
                    # Begin transaction with immediate write lock acquisition
                    await self._write_conn.execute("BEGIN IMMEDIATE")
                    logger.debug(f"Write transaction started on attempt {attempt + 1}")
                    break  # Success, exit retry loop

                except aiosqlite.OperationalError as e:
                    # Handle database locking with retry logic
                    if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                        # Calculate exponential backoff with jitter
                        delay = min(base_delay * (2 ** attempt) + random.uniform(0, 0.001), max_delay)
                        logger.warning(
                            f"Database locked, retrying in {delay:.3f}s "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        # Re-raise if not a lock error or retries exhausted
                        error_msg = f"Failed to begin transaction after {attempt + 1} attempts"
                        if "database is locked" in str(e).lower():
                            error_msg += " due to database locking"
                        logger.error(f"{error_msg}: {e}")
                        raise RuntimeError(f"{error_msg}: {e}")

                except Exception as e:
                    # Handle any other exception during BEGIN
                    logger.error(f"Unexpected error beginning transaction: {e}")
                    raise

            else:
                # This executes if for loop completes without breaking (all retries failed)
                error_msg = f"Failed to begin transaction after {max_retries} attempts due to database locking"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            # At this point, we have successfully begun a transaction
            # Now yield to the caller and handle their transaction
            try:
                # Yield connection to caller for their database operations
                yield self._write_conn

                # If we reach here, no exceptions occurred - commit the transaction
                await self._write_conn.commit()
                logger.debug("Write transaction committed successfully")

            except Exception as e:
                # Handle any exception during the client's transaction
                logger.error(f"Exception during write transaction, rolling back: {e}")
                try:
                    await self._write_conn.rollback()
                except Exception as rb_e:
                    logger.error(f"Error during rollback: {rb_e}")
                raise  # Re-raise original exception

            finally:
                # This finally block executes after client's transaction completes
                logger.debug("Write transaction finished.")

        # Lock is released here after exiting the 'async with' block
        logger.debug("Write lock released.")

    async def shutdown(self):
        """
        Graceful shutdown: closes the shared database connections.

        This method implements a comprehensive shutdown procedure:
        1. Sets the shutdown flag to prevent new connections/operations
        2. Clears connection references to prevent further use
        3. Performs WAL checkpoint on write connection to clean up
        4. Closes both connections with timeout protection
        5. Handles timeouts by force-closing connections
        6. Logs all shutdown steps for debugging

        The shutdown process ensures:
        - No new operations can start after shutdown begins
        - All pending data is flushed to disk via WAL checkpoint
        - Connections are properly closed even if they hang
        - Resources are cleaned up to prevent memory leaks

        Call Pattern:
            await conn_manager.shutdown()
            # After this, all connection methods will raise RuntimeError

        Side Effects:
            - Sets _shutting_down flag to True
            - Sets connection references to None
            - Closes database connections
        """
        logger.info("Starting SQLite shutdown...")

        # Step 1: Prevent new connections/operations by setting shutdown flag
        self._shutting_down = True

        # Step 2: Store references to close after clearing attributes
        # This prevents race conditions where new operations might try to use closing connections
        write_conn_to_close = self._write_conn
        read_conn_to_close = self._read_conn

        # Step 3: Clear references to prevent further use
        self._write_conn = None
        self._read_conn = None

        try:
            # Step 4: Close write connection with WAL checkpoint
            if write_conn_to_close:
                logger.info("Closing write connection...")
                try:
                    # Perform a final WAL checkpoint to clean up the WAL file
                    # This ensures all data is written to the main database file
                    await asyncio.wait_for(
                        write_conn_to_close.execute("PRAGMA wal_checkpoint(TRUNCATE)"),
                        timeout=5.0
                    )
                    # Close the connection gracefully with timeout
                    await asyncio.wait_for(write_conn_to_close.close(), timeout=5.0)
                    logger.info("Write connection closed.")
                except asyncio.TimeoutError:
                    logger.warning("Write connection close timed out, forcing close")
                    # Force close without waiting if graceful close times out
                    write_conn_to_close.close()

            # Step 5: Close read connection
            if read_conn_to_close:
                logger.info("Closing read connection...")
                try:
                    # Close the read connection gracefully with timeout
                    await asyncio.wait_for(read_conn_to_close.close(), timeout=5.0)
                    logger.info("Read connection closed.")
                except asyncio.TimeoutError:
                    logger.warning("Read connection close timed out, forcing close")
                    # Force close without waiting if graceful close times out
                    read_conn_to_close.close()

        except Exception as e:
            logger.error(f"Error closing SQLite connection: {e}")

        logger.info("SQLite shutdown completed")
