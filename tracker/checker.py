"""Re-checks every tracked listing's price, in the background.

One thread per store, each with its own browser, a few at a time. Each check adds
a price point and may raise alerts. Listings of paused hunts are left alone.
A listing whose store says its page no longer exists, GONE_AFTER checks in a row, is moved to Gone.
"""
import json
import threading

from . import alerts, listings, specs
from .browser import open_page
from .db import connect, now
from .search import MAX_BROWSERS, MAX_FAILURES_IN_A_ROW, _pause
from .stores import STORES
from .stores.base import ListingGone, StoreError

GONE_AFTER = 2
RUN = {"running": False, "stores": {}, "started_at": None}
_lock = threading.Lock()


def _setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] else default


def _save_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value)
    )


def due_listings(conn):
    """Tracked listings of active hunts, as {store key: [listing row, ...]}."""
    rows = conn.execute(
        """
        SELECT l.* FROM listings l JOIN hunts h ON h.id = l.hunt_id
        WHERE l.state = 'tracked' AND l.gone_at IS NULL AND h.status = 'active' AND h.deleted_at IS NULL
        ORDER BY l.id
        """
    ).fetchall()
    by_store = {}
    for row in rows:
        if row["retailer"] in STORES:
            by_store.setdefault(row["retailer"], []).append(row)
    return by_store


def _check_store(store, rows, state, options, turn):
    conn = connect(options["database"])
    try:
        with turn, open_page(options["channel"], options["headless"]) as page:
            state["phase"] = "checking"
            pincode = _setting(conn, "delivery_pincode")
            if not store.prepare(page, pincode):
                pincode = None
            failures = 0
            for row in rows:
                try:
                    product = store.read(page, row["url"], pincode)
                except ListingGone:
                    failures = 0  # the store answered; this one product is what is missing
                    state["failed"] += 1
                    if listings.missing(conn, row["id"], GONE_AFTER):
                        state["gone"] += 1
                        hunt = conn.execute("SELECT * FROM hunts WHERE id = ?", (row["hunt_id"],)).fetchone()
                        alerts.listing_gone(conn, options["mail"], hunt, row)
                except Exception as error:
                    failures += 1
                    state["failed"] += 1
                    if failures >= MAX_FAILURES_IN_A_ROW:
                        raise StoreError(str(error) if isinstance(error, StoreError) else
                                         f"{store.LABEL} stopped answering part-way through.") from error
                else:
                    failures = 0
                    listings.record(conn, row["id"], product, specs.normalise(product.title, product.raw_specs), pincode)
                    hunt = conn.execute("SELECT * FROM hunts WHERE id = ?", (row["hunt_id"],)).fetchone()
                    state["alerts"] += alerts.after_check(conn, options["mail"], hunt, row, listings.points(conn, row["id"]))
                state["checked"] += 1
                _pause()
        state["phase"] = "done"
    except StoreError as error:
        state.update(phase="failed", error=str(error))
    except Exception as error:
        message = str(error).splitlines()[0] if str(error) else type(error).__name__
        state.update(phase="failed", error=message[:200])
    finally:
        conn.close()


def run(options):
    """Check everything now and wait for it to finish. Returns False if a check was already running."""
    with _lock:
        if RUN["running"]:
            return False
        conn = connect(options["database"])
        try:
            by_store = due_listings(conn)
        finally:
            conn.close()
        RUN.update(running=True, started_at=now(), stores={
            key: {"label": STORES[key].LABEL, "phase": "queued", "total": len(rows), "checked": 0,
                  "failed": 0, "gone": 0, "alerts": 0, "error": None}
            for key, rows in by_store.items()
        })
    try:
        turn = threading.BoundedSemaphore(MAX_BROWSERS)
        threads = [
            threading.Thread(target=_check_store, args=(STORES[key], rows, RUN["stores"][key], options, turn), daemon=True)
            for key, rows in by_store.items()
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        states = RUN["stores"].values()
        result = {
            "checked": sum(s["checked"] - s["failed"] for s in states),
            "failed": sum(s["failed"] for s in states),
            "alerts": sum(s["alerts"] for s in states),
            "gone": sum(s["gone"] for s in states),
            "stores_failed": [s["label"] for s in states if s["phase"] == "failed"],
        }
        conn = connect(options["database"])
        try:
            _save_setting(conn, "last_check_at", now())
            _save_setting(conn, "last_check_result", json.dumps(result))
            conn.commit()
        finally:
            conn.close()
    finally:
        RUN["running"] = False
    return True


def start(options):
    """Begin a check in the background unless one is running."""
    if RUN["running"]:
        return False
    threading.Thread(target=run, args=(options,), daemon=True).start()
    return True
