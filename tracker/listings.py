"""Listings: product pages found for a hunt. Storage and the grouping the results page shows."""
import json

from . import history, hunts, matching
from .db import get_db, now
from .specs import squash

STATES = ("new", "tracked", "ignored")


def save(conn, hunt_id, retailer, product, specs, pincode):
    """Insert or refresh one listing and record this check's price. Returns True if it is new."""
    stamp = now()
    known = conn.execute("SELECT id FROM listings WHERE hunt_id = ? AND url = ?", (hunt_id, product.url)).fetchone()
    deliverable = None if product.deliverable is None else int(product.deliverable)
    values = (
        product.title, product.image_url, specs.get("model_number"),
        json.dumps(specs, ensure_ascii=False), json.dumps(product.raw_specs, ensure_ascii=False),
        stamp, deliverable, product.delivery_note, pincode if deliverable is not None else None,
    )
    if known:
        listing_id = known["id"]
        conn.execute(
            "UPDATE listings SET title = ?, image_url = ?, model_number = ?, specs = ?, raw_specs = ?, "
            "last_seen_at = ?, deliverable = ?, delivery_note = ?, delivery_pincode = ?, gone_checks = 0, gone_at = NULL "
            "WHERE id = ?",  # a search found the page again, so it is not gone
            values + (listing_id,),
        )
    else:
        listing_id = conn.execute(
            "INSERT INTO listings (title, image_url, model_number, specs, raw_specs, last_seen_at, deliverable, "
            "delivery_note, delivery_pincode, hunt_id, retailer, url, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values + (hunt_id, retailer, product.url, stamp),
        ).lastrowid
    conn.execute(
        "INSERT INTO price_points (listing_id, price_inr, mrp_inr, in_stock, checked_at) VALUES (?, ?, ?, ?, ?)",
        (listing_id, product.price_inr, product.mrp_inr, int(product.in_stock), stamp),
    )
    conn.commit()
    return not known


def record(conn, listing_id, product, specs, pincode):
    """Refresh a tracked listing from a scheduled re-check and add its price point."""
    stamp = now()
    before = conn.execute("SELECT specs FROM listings WHERE id = ?", (listing_id,)).fetchone()
    kind = json.loads((before["specs"] if before else None) or "{}").get("kind")
    if kind and "kind" not in specs:
        specs = {**specs, "kind": kind}  # the store page does not say laptop or tower; the search did
    deliverable = None if product.deliverable is None else int(product.deliverable)
    conn.execute(
        "UPDATE listings SET title = ?, image_url = COALESCE(?, image_url), model_number = COALESCE(?, model_number), "
        "specs = ?, raw_specs = ?, last_seen_at = ?, deliverable = ?, delivery_note = ?, delivery_pincode = ?, gone_checks = 0 "
        "WHERE id = ?",
        (product.title, product.image_url, specs.get("model_number"), json.dumps(specs, ensure_ascii=False),
         json.dumps(product.raw_specs, ensure_ascii=False), stamp, deliverable, product.delivery_note,
         pincode if deliverable is not None else None, listing_id),
    )
    conn.execute(
        "INSERT INTO price_points (listing_id, price_inr, mrp_inr, in_stock, checked_at) VALUES (?, ?, ?, ?, ?)",
        (listing_id, product.price_inr, product.mrp_inr, int(product.in_stock), stamp),
    )
    conn.commit()


def missing(conn, listing_id, limit):
    """Note that the store says this listing's page is gone. Returns True when that moves it to Gone."""
    conn.execute("UPDATE listings SET gone_checks = gone_checks + 1 WHERE id = ?", (listing_id,))
    moved = conn.execute(
        "UPDATE listings SET gone_at = ? WHERE id = ? AND gone_checks >= ? AND gone_at IS NULL", (now(), listing_id, limit)
    ).rowcount
    conn.commit()
    return bool(moved)


def bring_back(listing_id):
    """Put a Gone listing back on the Tracked page, to be checked again."""
    db = get_db()
    changed = db.execute("UPDATE listings SET gone_at = NULL, gone_checks = 0 WHERE id = ? AND gone_at IS NOT NULL",
                         (listing_id,)).rowcount
    db.commit()
    return bool(changed)


def gone():
    """Tracked listings whose store page no longer exists, most recently gone first, with their last price."""
    rows = get_db().execute(
        """
        SELECT l.*, h.name AS hunt_name FROM listings l JOIN hunts h ON h.id = l.hunt_id
        WHERE l.state = 'tracked' AND l.gone_at IS NOT NULL AND h.deleted_at IS NULL
        ORDER BY l.gone_at DESC, l.id
        """
    ).fetchall()
    result = []
    for row in rows:
        listing = dict(row)
        listing.update(history.summary(points(get_db(), listing["id"])))
        result.append(listing)
    return result


def points(conn, listing_id):
    """A listing's price checks, oldest first."""
    return conn.execute(
        "SELECT price_inr, mrp_inr, in_stock, checked_at, source FROM price_points WHERE listing_id = ? ORDER BY checked_at, id",
        (listing_id,),
    ).fetchall()


def tracked():
    """Every tracked listing with its price history, grouped so the same model at several stores sits together.

    Returns [{"title", "model_number", "listings": [...cheapest first]}, ...], cheapest group first.
    """
    db = get_db()
    rows = db.execute(
        """
        SELECT l.*, h.name AS hunt_name, h.status AS hunt_status
        FROM listings l JOIN hunts h ON h.id = l.hunt_id
        WHERE l.state = 'tracked' AND l.gone_at IS NULL AND h.deleted_at IS NULL
        """
    ).fetchall()
    groups, wanted = {}, {}
    for row in rows:
        listing = dict(row)
        listing["specs"] = json.loads(listing["specs"] or "{}")
        if listing["hunt_id"] not in wanted:
            wanted[listing["hunt_id"]] = hunts.get(listing["hunt_id"])
        # The same spec chips as on the hunt page, marked against the hunt that found the listing.
        listing["chips"] = matching.evaluate(wanted[listing["hunt_id"]], listing)[1]
        listing.update(history.summary(points(db, listing["id"])))
        if listing["price_inr"] and listing["mrp_inr"] and listing["mrp_inr"] > listing["price_inr"]:
            listing["discount"] = round(100 * (1 - listing["price_inr"] / listing["mrp_inr"]))
        # A model number shared across stores means the same laptop; without one a listing stands alone.
        model = squash(listing["model_number"])
        groups.setdefault(model if len(model) >= 6 else f"listing-{listing['id']}", []).append(listing)

    def price(listing):
        return (not listing["in_stock"], listing["price_inr"] is None, listing["price_inr"] or 0, listing["id"])

    result = []
    for members in groups.values():
        members.sort(key=price)
        result.append({"title": members[0]["title"], "model_number": members[0]["model_number"], "listings": members})
    result.sort(key=lambda group: price(group["listings"][0]))
    return result


def ignored_urls(conn, hunt_id, retailer):
    rows = conn.execute(
        "SELECT url FROM listings WHERE hunt_id = ? AND retailer = ? AND state = 'ignored'", (hunt_id, retailer)
    )
    return {row["url"] for row in rows}


def for_hunt(hunt):
    """Every listing of a hunt with its latest price, sorted cheapest first and grouped for the tabs."""
    rows = get_db().execute(
        """
        SELECT l.*, p.price_inr, p.mrp_inr, p.in_stock, p.checked_at
        FROM listings l
        LEFT JOIN price_points p ON p.id = (
            SELECT id FROM price_points WHERE listing_id = l.id ORDER BY checked_at DESC, id DESC LIMIT 1
        )
        WHERE l.hunt_id = ?
        """,
        (hunt["id"],),
    ).fetchall()
    groups = {"match": [], "near": [], "miss": [], "tracked": [], "ignored": []}
    for row in rows:
        listing = dict(row)
        listing["specs"] = json.loads(listing["specs"] or "{}")
        listing["verdict"], listing["chips"] = matching.evaluate(hunt, listing)
        if listing["price_inr"] and listing["mrp_inr"] and listing["mrp_inr"] > listing["price_inr"]:
            listing["discount"] = round(100 * (1 - listing["price_inr"] / listing["mrp_inr"]))
        key = listing["state"] if listing["state"] != "new" else listing["verdict"]
        if listing["gone_at"] and key == "tracked":
            listing["chips"] = [("Gone from the store", "miss")] + listing["chips"]
        groups[key].append(listing)
    for items in groups.values():
        items.sort(key=lambda item: (item["price_inr"] is None, item["price_inr"] or 0, item["id"]))
    return groups


def review(hunt_id, shown, ticked):
    """Track the ticked listings; hide the other ones that were on screen. Returns (tracked, hidden)."""
    db = get_db()
    tracked = hidden = 0
    for listing_id in shown:
        state = "tracked" if listing_id in ticked else "ignored"
        changed = db.execute(
            "UPDATE listings SET state = ? WHERE id = ? AND hunt_id = ? AND state = 'new'",
            (state, listing_id, hunt_id),
        ).rowcount
        tracked += changed if state == "tracked" else 0
        hidden += changed if state == "ignored" else 0
    db.commit()
    return tracked, hidden


def set_state(hunt_id, listing_id, state):
    db = get_db()
    changed = db.execute(
        "UPDATE listings SET state = ?, gone_checks = 0, gone_at = NULL WHERE id = ? AND hunt_id = ?",
        (state, listing_id, hunt_id)
    ).rowcount
    db.commit()
    return bool(changed)


def restore_hidden(hunt_id):
    db = get_db()
    restored = db.execute(
        "UPDATE listings SET state = 'new' WHERE hunt_id = ? AND state = 'ignored'", (hunt_id,)
    ).rowcount
    db.commit()
    return restored
