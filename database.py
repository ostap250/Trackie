"""
database.py — SQLite operations for Trackie bot.
All DB access goes through this module.
"""

import sqlite3
from datetime import datetime, timedelta


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_tables()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # access columns by name
        return conn

    def _init_tables(self):
        """Create tables if they don't exist yet."""
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id   INTEGER PRIMARY KEY,
                    username  TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS food_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    calories    REAL,
                    protein     REAL,
                    timestamp   TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS weight_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id   INTEGER NOT NULL,
                    weight    REAL NOT NULL,
                    timestamp TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL,
                    reminder_type   TEXT NOT NULL,
                    time            TEXT NOT NULL,
                    UNIQUE(user_id, reminder_type),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );
            """)

    # ── Users ──────────────────────────────────────────────────────────────

    def upsert_user(self, user_id: int, username: str):
        """Create user if not exists (ignore if already present)."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
                (user_id, username),
            )

    # ── Food log ───────────────────────────────────────────────────────────

    def add_food(self, user_id: int, description: str, calories: float, protein: float):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO food_log (user_id, description, calories, protein) VALUES (?, ?, ?, ?)",
                (user_id, description, calories, protein),
            )

    def get_today_food(self, user_id: int) -> list[dict]:
        """Return all food entries logged today (local date, stored as UTC)."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT description, calories, protein, timestamp FROM food_log "
                "WHERE user_id = ? AND timestamp LIKE ? ORDER BY timestamp",
                (user_id, f"{today}%"),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_week_food(self, user_id: int) -> list[dict]:
        """Return food entries from the last 7 days."""
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT description, calories, protein, timestamp FROM food_log "
                "WHERE user_id = ? AND timestamp >= ? ORDER BY timestamp",
                (user_id, week_ago),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Weight log ─────────────────────────────────────────────────────────

    def add_weight(self, user_id: int, weight: float):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO weight_log (user_id, weight) VALUES (?, ?)",
                (user_id, weight),
            )

    def get_week_weights(self, user_id: int) -> list[dict]:
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT weight, timestamp FROM weight_log "
                "WHERE user_id = ? AND timestamp >= ? ORDER BY timestamp",
                (user_id, week_ago),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Reminders ──────────────────────────────────────────────────────────

    def set_reminder(self, user_id: int, reminder_type: str, time_str: str):
        """Insert or update a reminder for this user + type."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO reminders (user_id, reminder_type, time) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, reminder_type) DO UPDATE SET time = excluded.time",
                (user_id, reminder_type, time_str),
            )

    def delete_reminder(self, user_id: int, reminder_type: str):
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM reminders WHERE user_id = ? AND reminder_type = ?",
                (user_id, reminder_type),
            )

    def get_user_reminders(self, user_id: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT reminder_type, time FROM reminders WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_reminders(self) -> list[dict]:
        """Load every reminder from DB — used at startup to reschedule jobs."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT user_id, reminder_type, time FROM reminders"
            ).fetchall()
        return [dict(r) for r in rows]
