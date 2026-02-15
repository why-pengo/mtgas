"""
Database manager for MTG Arena Statistics Tracker.
Handles SQLite database initialization and connection management.
"""

import sqlite3
from pathlib import Path
from typing import Optional


class DatabaseManager:
    """Manages SQLite database connections and schema initialization."""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the database manager.

        Args:
            db_path: Path to SQLite database file. If None, uses default location.
        """
        if db_path is None:
            # Default to data directory in project root
            project_root = Path(__file__).parent.parent.parent
            db_path = project_root / "data" / "mtga_stats.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: Optional[sqlite3.Connection] = None

    def get_connection(self) -> sqlite3.Connection:
        """Get or create a database connection."""
        if self._connection is None:
            self._connection = sqlite3.connect(
                str(self.db_path), detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
            )
            # Enable foreign keys
            self._connection.execute("PRAGMA foreign_keys = ON")
            # Return rows as dictionaries
            self._connection.row_factory = sqlite3.Row
        return self._connection

    def close(self):
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def initialize_schema(self):
        """Initialize the database schema from schema.sql."""
        schema_path = Path(__file__).parent / "schema.sql"

        with open(schema_path, "r") as f:
            schema_sql = f.read()

        conn = self.get_connection()
        conn.executescript(schema_sql)
        conn.commit()
        print(f"Database initialized at: {self.db_path}")

    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a query and return the cursor."""
        conn = self.get_connection()
        return conn.execute(query, params)

    def executemany(self, query: str, params_list: list) -> sqlite3.Cursor:
        """Execute a query with multiple parameter sets."""
        conn = self.get_connection()
        return conn.executemany(query, params_list)

    def commit(self):
        """Commit the current transaction."""
        if self._connection:
            self._connection.commit()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - commit on success, rollback on error."""
        if exc_type is None:
            self.commit()
        self.close()


# Singleton instance for easy access
_db_manager: Optional[DatabaseManager] = None


def get_db() -> DatabaseManager:
    """Get the global database manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


def init_db(db_path: Optional[str] = None) -> DatabaseManager:
    """Initialize and return a database manager."""
    global _db_manager
    _db_manager = DatabaseManager(db_path)
    _db_manager.initialize_schema()
    return _db_manager
