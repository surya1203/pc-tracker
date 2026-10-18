"""SQLite connection handling and first-run setup."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, g

DEFAULT_SETTINGS = {
    "check_interval_hours": "6",
}


# Columns added after the first release: (table, column, type). Applied to databases that predate them.
ADDED_COLUMNS = [
    ("hunts", "last_searched_at", "TEXT"),
    ("listings", "deliverable", "INTEGER"),
    ("listings", "delivery_note", "TEXT"),
    ("listings", "delivery_pincode", "TEXT"),
    ("hunts", "alert_new_low", "INTEGER NOT NULL DEFAULT 1"),
    ("hunts", "alert_back_in_stock", "INTEGER NOT NULL DEFAULT 1"),
    ("hunts", "alert_target_inr", "INTEGER"),
    ("hunts", "alert_drop_pct", "INTEGER"),
    ("hunts", "alert_new_match", "INTEGER NOT NULL DEFAULT 1"),
    ("price_points", "source", "TEXT"),
    ("listings", "history_checked_at", "TEXT"),
    ("listings", "history_note", "TEXT"),
    ("listings", "history_url", "TEXT"),
    ("hunts", "kind", "TEXT NOT NULL DEFAULT 'laptop'"),
    ("listings", "gone_checks", "INTEGER NOT NULL DEFAULT 0"),
    ("listings", "gone_at", "TEXT"),
]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE_PATH"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def get_setting(key, default=None):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] else default


def set_setting(key, value):
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    db.commit()


def init_db(app):
    """Create tables, seed default settings and import the old saved searches once."""
    from . import catalog, migrate

    conn = connect(app.config["DATABASE_PATH"])
    try:
        conn.executescript((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
        for table, column, kind in ADDED_COLUMNS:
            if column not in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        conn.executemany(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", DEFAULT_SETTINGS.items()
        )
        conn.commit()
        done = conn.execute("SELECT 1 FROM settings WHERE key = 'legacy_imported'").fetchone()
        if not done:
            for line in migrate.import_legacy(conn, app.config["LEGACY_DATABASE_PATH"]):
                app.logger.warning(line)
            conn.execute("INSERT INTO settings (key, value) VALUES ('legacy_imported', ?)", (now(),))
            conn.commit()
        # Hunts saved while only Amazon and Flipkart could be searched get the stores added since, once.
        if not conn.execute("SELECT 1 FROM settings WHERE key = 'stores_added_oct_2026'").fetchone():
            for row in conn.execute("SELECT id, retailers FROM hunts").fetchall():
                chosen = json.loads(row["retailers"] or "[]")
                chosen += [key for key in catalog.DEFAULT_RETAILERS if key not in chosen]
                conn.execute("UPDATE hunts SET retailers = ? WHERE id = ?", (json.dumps(chosen), row["id"]))
            conn.execute("INSERT INTO settings (key, value) VALUES ('stores_added_oct_2026', ?)", (now(),))
            conn.commit()
    finally:
        conn.close()
