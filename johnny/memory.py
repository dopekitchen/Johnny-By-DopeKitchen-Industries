"""Long-term memory: conversation history, remembered facts, and observed activity."""
import json
import sqlite3
import threading
import time

from . import config


class Memory:
    def __init__(self, path=None):
        config.ensure_dirs()
        self._lock = threading.Lock()
        self.db = sqlite3.connect(str(path or config.DB_PATH), check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, ts REAL, role TEXT, content TEXT);
            CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY, ts REAL, fact TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY, ts REAL, app TEXT, title TEXT, duration REAL);
            CREATE INDEX IF NOT EXISTS activity_ts ON activity(ts);
        """)
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(messages)")]
        if "extra" not in cols:
            # v1.3: tool calls are stored too. History saved before that only holds the assistant's words,
            # which taught the model to *say* it opened apps without calling tools - so start it fresh.
            self.db.execute("ALTER TABLE messages ADD COLUMN extra TEXT")
            self.db.execute("DELETE FROM messages")
        self.db.commit()

    def _exec(self, sql, args=()):
        with self._lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def _query(self, sql, args=()):
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    # conversation
    def add_message(self, role, content, extra=None):
        """extra: dict merged into the message, e.g. {"tool_calls": [...]} or {"tool_name": "open_app"}."""
        self._exec("INSERT INTO messages(ts, role, content, extra) VALUES(?,?,?,?)",
                   (time.time(), role, content, json.dumps(extra) if extra else None))

    def recent_messages(self, n=20):
        rows = self._query("SELECT role, content, extra FROM messages ORDER BY id DESC LIMIT ?", (n,))
        out = []
        for r, c, x in reversed(rows):
            m = {"role": r, "content": c}
            if x:
                try:
                    m.update(json.loads(x))
                except ValueError:
                    pass
            out.append(m)
        while out and out[0]["role"] != "user":  # never start mid tool-exchange
            out.pop(0)
        return out

    def clear_messages(self):
        self._exec("DELETE FROM messages")

    # facts
    def remember(self, fact: str):
        self._exec("INSERT OR IGNORE INTO facts(ts, fact) VALUES(?,?)", (time.time(), fact.strip()))

    def forget(self, text: str) -> int:
        return self._exec("DELETE FROM facts WHERE fact LIKE ?", (f"%{text}%",)).rowcount

    def facts(self, limit=100):
        return [r[0] for r in self._query("SELECT fact FROM facts ORDER BY id DESC LIMIT ?", (limit,))]

    # activity (passive observation)
    def log_activity(self, app, title, duration):
        self._exec("INSERT INTO activity(ts, app, title, duration) VALUES(?,?,?,?)",
                   (time.time(), app, title[:200], duration))

    def activity_since(self, seconds):
        return self._query("SELECT ts, app, title, duration FROM activity WHERE ts > ? ORDER BY ts",
                           (time.time() - seconds,))

    def top_apps(self, seconds=86400 * 7, limit=10):
        return self._query(
            "SELECT app, SUM(duration) d, COUNT(*) FROM activity WHERE ts > ? GROUP BY app ORDER BY d DESC LIMIT ?",
            (time.time() - seconds, limit))

    def clear_activity(self):
        self._exec("DELETE FROM activity")
