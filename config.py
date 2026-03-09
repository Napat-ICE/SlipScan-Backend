"""
backend_flask/config.py
Database connection (Supabase PostgreSQL via psycopg2)
"""
import os
import psycopg2
import psycopg2.extras
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

_conn = None


def get_db():
    """Return a singleton psycopg2 connection with RealDictCursor."""
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(
            os.environ["DATABASE_URL"],
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        _conn.autocommit = False
    return _conn
