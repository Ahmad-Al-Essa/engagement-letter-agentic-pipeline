"""Rebuild the SQLite database from scratch.

Run from the repo root:
    python3 data/build_db.py

It deletes data/sbp.db (if it exists), creates the tables from schema.sql,
fills them from seed.sql, and prints how many rows each table has.
All client and prospect data is invented.
"""

import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).parent
DB_PATH = DATA_DIR / "sbp.db"
TABLES = ["services", "clients", "prospects", "drafts", "escalations"]


def build():
    # Start clean every time, so the database always matches the two .sql files.
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")  # SQLite only checks foreign keys when asked

    schema_sql = (DATA_DIR / "schema.sql").read_text(encoding="utf-8")
    seed_sql = (DATA_DIR / "seed.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.executescript(seed_sql)
    conn.commit()

    print(f"Built {DB_PATH}")
    for table in TABLES:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count} rows")

    conn.close()


if __name__ == "__main__":
    build()
