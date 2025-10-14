"""
Example database-backed thread manager plugin.
This shows how to implement a real persistence layer.
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
import sqlite3
import json
import os

class DatabaseThreadManagerPlugin:
    """Thread manager plugin using SQLite for persistence"""
    
    def get_priority(self) -> int:
        """Higher priority than default (which is 0)"""
        return 20  # Even higher than the example plugin
    
    def __init__(self):
        # Use a database file in the project directory
        self.db_path = os.getenv("THREAD_DB_PATH", "./threads.db")
        self._init_database()
        print(f"[DatabaseThreadManagerPlugin] Initialized with database at {self.db_path}")
    
    def _init_database(self):
        """Initialize SQLite database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create threads table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created TEXT NOT NULL,
                model TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                is_archived BOOLEAN DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            )
        ''')
        
        # Create messages table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                role TEXT NOT NULL,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                model TEXT,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (thread_id) REFERENCES threads (id)
            )
        ''')
        
        # Create index for better search performance
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_messages_content ON messages (content)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_messages_thread_id ON messages (thread_id)
        ''')
        
        conn.commit()
        conn.close()
    
    def create_thread(self, title: str, model: str, system_prompt: str) -> str:
        """Create a new thread in database"""
        thread_id = f"thread_{datetime.now().timestamp()}"
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO threads (id, title, created, model, system_prompt, is_archived, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (thread_id, title, datetime.now().isoformat(), model, system_prompt, False, '{}'))
        
        conn.commit()
        conn.close()
        
        print(f"[DatabaseThreadManagerPlugin] Created thread {thread_id}")
        return thread_id
    
    def get_threads(self, query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get threads from database with optional search"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if query:
            # Search in both title and content
            cursor.execute('''
                SELECT DISTINCT t.id, t.title, t.created, t.model, t.system_prompt, t.metadata
                FROM threads t
                LEFT JOIN messages m ON t.id = m.thread_id
                WHERE t.is_archived = 0 
                AND (LOWER(t.title) LIKE LOWER(?) OR LOWER(m.content) LIKE LOWER(?))
                ORDER BY t.created DESC
            ''', (f'%{query}%', f'%{query}%'))
        else:
            cursor.execute('''
                SELECT id, title, created, model, system_prompt, metadata
                FROM threads
                WHERE is_archived = 0
                ORDER BY created DESC
            ''')
        
        threads = []
        for row in cursor.fetchall():
            threads.append({
                "id": row[0],
                "title": row[1],
                "created": row[2],
                "model": row[3],
                "system_prompt": row[4],
                "metadata": json.loads(row[5]) if row[5] else {}
            })
        
        conn.close()
        return threads
    
    def get_thread_messages(self, thread_id: str) -> Optional[List[Dict[str, Any]]]:
        """Get messages for a thread from database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Verify thread exists
        cursor.execute('SELECT 1 FROM threads WHERE id = ?', (thread_id,))
        if not cursor.fetchone():
            conn.close()
            return None
        
        cursor.execute('''
            SELECT role, type, content, timestamp, model, metadata
            FROM messages
            WHERE thread_id = ?
            ORDER BY timestamp
        ''', (thread_id,))
        
        messages = []
        for row in cursor.fetchall():
            message = {
                "role": row[0],
                "type": row[1],
                "content": row[2],
                "timestamp": row[3],
                "metadata": json.loads(row[5]) if row[5] else {}
            }
            if row[4]:  # model
                message["model"] = row[4]
            messages.append(message)
        
        conn.close()
        return messages
    
    def add_message(self, thread_id: str, role: str, type: str, content: str, model: Optional[str] = None) -> bool:
        """Add a message to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Verify thread exists
        cursor.execute('SELECT 1 FROM threads WHERE id = ?', (thread_id,))
        if not cursor.fetchone():
            conn.close()
            return False
        
        cursor.execute('''
            INSERT INTO messages (thread_id, role, type, content, timestamp, model, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (thread_id, role, type, content, datetime.now().isoformat(), model, '{}'))
        
        conn.commit()
        conn.close()
        return True
    
    def update_thread(self, thread_id: str, title: Optional[str] = None) -> bool:
        """Update thread in database"""
        if not title:
            return True
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE threads 
            SET title = ?, metadata = json_set(metadata, '$.updated_at', ?)
            WHERE id = ?
        ''', (title, datetime.now().isoformat(), thread_id))
        
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        return success
    
    def archive_thread(self, thread_id: str) -> bool:
        """Archive thread in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE threads 
            SET is_archived = 1, metadata = json_set(metadata, '$.archived_at', ?)
            WHERE id = ?
        ''', (datetime.now().isoformat(), thread_id))
        
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        return success
    
    def search_threads(self, query: str) -> List[Dict[str, Any]]:
        """Search threads in database with proper full-text search"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Use SQLite's LIKE for simple search (could be enhanced with FTS)
        cursor.execute('''
            SELECT DISTINCT t.id, t.title, 
                   substr(m.content, max(1, instr(lower(m.content), lower(?)) - 30), 100) as snippet,
                   count(m.id) as message_count,
                   max(m.timestamp) as last_activity
            FROM threads t
            JOIN messages m ON t.id = m.thread_id
            WHERE t.is_archived = 0 
            AND (LOWER(t.title) LIKE LOWER(?) OR LOWER(m.content) LIKE LOWER(?))
            GROUP BY t.id, t.title
            ORDER BY last_activity DESC
        ''', (query, f'%{query}%', f'%{query}%'))
        
        results = []
        for row in cursor.fetchall():
            snippet = row[2]
            if len(snippet) > 0:
                # Clean up snippet
                if not snippet.startswith(query.lower()) and len(snippet) > 30:
                    snippet = "..." + snippet
                if len(snippet) > 100:
                    snippet = snippet[:97] + "..."
                
                results.append({
                    "id": row[0],
                    "title": row[1],
                    "snippet": snippet,
                    "metadata": {
                        "message_count": row[3],
                        "last_activity": row[4]
                    }
                })
        
        conn.close()
        return results
