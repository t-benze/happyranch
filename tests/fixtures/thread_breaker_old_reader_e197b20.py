"""Pinned pre-breaker application read path from e197b20acca7498907518ff3a3593affe9bc39e4.

The SQL is copied from Database.get_thread() and
Database.list_thread_messages() at that commit.  This deliberately models the
old binary's application reads rather than treating raw sqlite access as the
compatibility contract.
"""

import sqlite3


class HistoricalApplicationReader:
    def __init__(self, path):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row

    def close(self):
        self._conn.close()

    def get_thread(self, thread_id):
        row = self._conn.execute(
            "SELECT * FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_thread_messages(self, thread_id, *, since_seq=0, limit=1000):
        rows = self._conn.execute(
            "SELECT * FROM thread_messages "
            "WHERE thread_id = ? AND seq > ? ORDER BY seq LIMIT ?",
            (thread_id, since_seq, limit),
        ).fetchall()
        return [dict(row) for row in rows]
