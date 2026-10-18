"""Price history, alert rules, quiet hours and the check schedule."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from werkzeug.datastructures import MultiDict

from tracker import alerts, backfill, history, hunts, scheduler

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def _point(days_ago, price, in_stock=1, mrp=None):
    stamp = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    return {"price_inr": price, "mrp_inr": mrp, "in_stock": in_stock, "checked_at": stamp}


def _rules(**changes):
    hunt = {"alert_new_low": 1, "alert_back_in_stock": 1, "alert_target_inr": None, "alert_drop_pct": None}
    hunt.update(changes)
    return hunt


def test_lows_and_change_come_from_the_history():
    found = history.summary([_point(60, 180000), _point(20, 200000), _point(1, 210000), _point(0, 195000)], NOW)
    assert (found["price_inr"], found["previous_inr"], found["change_inr"]) == (195000, 210000, -15000)
    assert (found["low_inr"], found["low_30_inr"], found["above_low_inr"]) == (180000, 195000, 15000)
    assert not found["at_low"] and found["checks"] == 4 and found["spark"]


def test_an_out_of_stock_check_never_counts_as_a_low():
    found = history.summary([_point(3, 200000), _point(2, 150000, in_stock=0), _point(0, 190000)], NOW)
    assert (found["low_inr"], found["previous_inr"], found["at_low"]) == (190000, 200000, True)


def test_the_small_chart_draws_the_last_30_days_with_each_source_apart():
    imported = [dict(_point(90, 150000), source="buyhatke"), dict(_point(20, 210000), source="buyhatke")]
    lines = history.sparkline(imported + [_point(10, 200000), _point(5, 190000)], NOW)
    # BuyHatke: flat from the left edge at the price from before the window, a step up, then on to where tracking began.
    assert lines["imported"] == "2.0,25.0 40.7,25.0 40.7,3.0 79.3,3.0"
    # The tracker: steps down where its checks found a change, then holds to today at the right edge.
    assert lines["own"] == "79.3,6.7 98.7,6.7 98.7,10.3 118.0,10.3"
    assert history.sparkline([_point(0, 200000)], NOW) is None


def test_one_check_is_not_yet_a_low_or_a_chart():
    found = history.summary([_point(0, 200000)], NOW)
    assert (found["low_inr"], found["at_low"], found["change_inr"], found["spark"]) == (200000, False, None, None)
    assert history.summary([], NOW)["price_inr"] is None


def test_new_low_alert():
    assert [rule for rule, _ in alerts.triggered(_rules(), [_point(2, 200000), _point(1, 205000), _point(0, 199000)])] == ["new_low"]
    assert alerts.triggered(_rules(), [_point(1, 200000), _point(0, 200000)]) == []
    assert alerts.triggered(_rules(alert_new_low=0), [_point(1, 200000), _point(0, 190000)]) == []


def test_the_first_check_of_a_listing_raises_nothing():
    assert alerts.triggered(_rules(alert_target_inr=250000), [_point(0, 200000)]) == []


def test_target_alert_fires_on_crossing_not_while_staying_under():
    rules = _rules(alert_new_low=0, alert_target_inr=190000)
    assert [rule for rule, _ in alerts.triggered(rules, [_point(1, 200000), _point(0, 189990)])] == ["target"]
    assert alerts.triggered(rules, [_point(2, 200000), _point(1, 189990), _point(0, 189990)]) == []


def test_drop_alert_compares_with_the_previous_check():
    rules = _rules(alert_new_low=0, alert_drop_pct=5)
    assert [rule for rule, _ in alerts.triggered(rules, [_point(2, 150000), _point(1, 200000), _point(0, 190000)])] == ["drop"]
    assert alerts.triggered(rules, [_point(1, 200000), _point(0, 195000)]) == []


def test_back_in_stock_alert():
    points = [_point(2, 200000), _point(1, 200000, in_stock=0), _point(0, 200000)]
    assert [rule for rule, _ in alerts.triggered(_rules(), points)] == ["back_in_stock"]
    assert alerts.triggered(_rules(), points[:2]) == []  # going out of stock is not news


def test_quiet_hours_can_wrap_past_midnight():
    assert alerts.in_quiet_hours("22:00", "07:00", datetime(2026, 10, 11, 23, 30))
    assert alerts.in_quiet_hours("22:00", "07:00", datetime(2026, 10, 12, 6, 59))
    assert not alerts.in_quiet_hours("22:00", "07:00", datetime(2026, 10, 12, 7, 0))
    assert alerts.in_quiet_hours("13:00", "15:00", datetime(2026, 10, 12, 14, 0))
    assert not alerts.in_quiet_hours("", "", datetime(2026, 10, 12, 14, 0))


def test_rupees_use_indian_grouping():
    assert (alerts.rupees(189990), alerts.rupees(999), alerts.rupees(12345678)) == ("₹1,89,990", "₹999", "₹1,23,45,678")


def _settings(**values):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.executemany("INSERT INTO settings VALUES (?, ?)", values.items())
    return conn


def test_discord_is_a_channel_once_a_webhook_is_saved(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send_discord", lambda webhook, body, card=None: sent.append((webhook, body, card)))
    assert alerts.deliver(_settings(), {}, "subject", "body") == (["feed"], [])
    hook = "https://discord.com/api/webhooks/1/abc"
    assert alerts.deliver(_settings(discord_webhook_url=hook), {}, "subject", "body") == (["feed", "discord"], [])
    assert sent == [(hook, "body", None)]


def test_telegram_needs_both_a_token_and_a_chat(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_telegram", lambda token, method, data=None: sent.append((token, method, data)))
    assert alerts.deliver(_settings(telegram_bot_token="1:abc"), {}, "subject", "body") == (["feed"], [])
    conn = _settings(telegram_bot_token="1:abc", telegram_chat_id="42")
    card = {"rule": "new_low", "detail": "d", "price": 189990, "title": "A <B> laptop", "url": "https://x.test/p?a=1&b=2",
            "image": None, "store": "Croma", "hunt": "Work"}
    assert alerts.deliver(conn, {}, "subject", "body", card=card) == (["feed", "telegram"], [])
    (token, method, message), = sent
    assert (token, method, message["chat_id"], message["parse_mode"]) == ("1:abc", "sendMessage", "42", "HTML")
    assert "A &lt;B&gt; laptop" in message["text"] and "₹1,89,990" in message["text"] and "a=1&amp;b=2" in message["text"]


def test_hunts_are_searched_again_on_their_own_schedule():
    conn = _settings(last_search_at="2026-10-11T06:00:00+00:00")
    conn.execute("CREATE TABLE hunts (last_searched_at TEXT, deleted_at TEXT)")
    assert scheduler.next_search(conn) == datetime(2026, 10, 12, 6, 0, tzinfo=timezone.utc)  # once a day by default
    conn.execute("INSERT INTO settings VALUES ('search_interval_hours', '0')")
    assert scheduler.next_search(conn) is None  # switched off
    # Before the first scheduled search, the clock starts from the last time any hunt was searched by hand.
    conn = _settings(search_interval_hours="12")
    conn.execute("CREATE TABLE hunts (last_searched_at TEXT, deleted_at TEXT)")
    conn.execute("INSERT INTO hunts VALUES ('2026-10-11T08:00:00+00:00', NULL)")
    assert scheduler.next_search(conn) == datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)


def test_new_matches_raise_one_alert_led_by_the_cheapest(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send_discord", lambda webhook, body, card=None: sent.append((body, card)))
    conn = _settings(discord_webhook_url="https://discord.com/api/webhooks/1/abc")
    conn.execute("CREATE TABLE alerts (hunt_id, listing_id, rule, channel, price_inr, message, sent_at)")
    found = [{"id": 7, "retailer": "croma", "title": "Laptop A", "url": "https://x.test/a", "image_url": None, "price_inr": 150000},
             {"id": 8, "retailer": "amazon", "title": "Laptop B", "url": "https://x.test/b", "image_url": None, "price_inr": None}]
    alerts.new_matches(conn, {}, {"id": 3, "name": "Work"}, found)
    (body, card), = sent
    assert card["rule"] == "new_match" and card["price"] == 150000 and "2 new listings" in card["detail"]
    assert "Laptop B" in body and "Price not shown" in body
    row = conn.execute("SELECT * FROM alerts").fetchone()
    assert (row["listing_id"], row["rule"], row["channel"]) == (7, "new_match", "feed, discord")


def _item(**changes):
    item = {"title": "Laptop [A]", "url": "https://x.test/a", "retailer": "croma", "price_inr": 200000, "in_stock": True,
            "checked_at": "2026-10-12T09:01:00+00:00", "change_inr": 0, "at_low": False, "low_inr": 190000}
    item.update(changes)
    return item


def test_a_regular_update_lists_every_tracked_listing_and_its_title_leads_with_any_news(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send_discord", lambda webhook, body, card=None: sent.append((body, card)))
    conn = _settings(discord_webhook_url="https://discord.com/api/webhooks/1/abc")
    conn.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY, hunt_id, listing_id, rule, channel, price_inr, message, sent_at)")
    conn.execute("CREATE TABLE listings (id INTEGER PRIMARY KEY, title)")
    conn.execute("INSERT INTO listings VALUES (7, 'Laptop B')")
    since = "2026-10-12T09:00:00+00:00"
    items = [_item(), _item(title="Laptop B", change_inr=-5000, price_inr=185000, at_low=True),
             _item(title="Laptop C", checked_at="2026-10-12T06:00:00+00:00", change_inr=-900)]
    headline, reached, problems = alerts.status_update(conn, {}, items, since)
    assert (headline, reached, problems) == ("3 tracked listings: 1 price changed", ["feed", "discord"], [])
    texts = [line["text"] for line in sent[0][1]["lines"]]
    assert texts == ["₹2,00,000 · no change · lowest ₹1,90,000", "₹1,85,000 · ▼ ₹5,000 since the last check · its lowest so far",
                     "₹2,00,000 · not read this time · lowest ₹1,90,000"]
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0  # an update is not an alert
    # An alert raised during the round takes over the title; one from before it does not.
    conn.execute("INSERT INTO alerts (listing_id, rule, price_inr, sent_at) VALUES (7, 'new_low', 185000, '2026-10-12T09:02:00+00:00')")
    conn.execute("INSERT INTO alerts (listing_id, rule, price_inr, sent_at) VALUES (7, 'drop', 190000, '2026-10-12T03:00:00+00:00')")
    assert alerts.status_update(conn, {}, items, since)[0] == "New all-time low: ₹1,85,000 · Laptop B"
    conn.execute("INSERT INTO alerts (listing_id, rule, price_inr, sent_at) VALUES (7, 'new_match', 150000, '2026-10-12T09:20:00+00:00')")
    assert alerts.status_update(conn, {}, items, since)[0] == "New all-time low, New match"
    assert alerts.update_headline([], [_item()], since) == "1 tracked listing: no price change"
    assert alerts.update_line(_item(in_stock=False, low_inr=200000), since) == "₹2,00,000 · out of stock"


def test_discord_lays_an_update_out_as_one_linked_entry_per_listing(monkeypatch):
    posted = []

    class Answer:
        def close(self):
            pass
    monkeypatch.setattr(alerts.urllib.request, "urlopen", lambda request, timeout: posted.append(json.loads(request.data)) or Answer())
    lines = [{"title": "Laptop [A]", "url": "https://x.test/a", "store": "Croma", "text": "₹2,00,000 · no change"}] * 60
    alerts.send_discord("https://discord.com/api/webhooks/1/abc", "plain", {"headline": "60 tracked laptops: no price change", "lines": lines, "news": False})
    embed = posted[0]["embeds"][0]
    assert posted[0]["content"] == "**60 tracked laptops: no price change**"
    assert embed["description"].startswith("**[Laptop (A)](https://x.test/a)**\nCroma · ₹2,00,000 · no change")
    assert len(embed["description"]) <= 4096 and "more on the Tracked page" in embed["description"]


BUYHATKE_STORES = {"www.croma": [{".com": [{"pos": 71, "config": {"pidRegex": ["\\/p\\/(\\d+)"]}}]}],
                   "www.flipkart": [{".com": [{"pos": 2, "config": {"pidRegex": ["pid=([A-Z0-9]+)"]}}]}]}


def test_a_store_link_is_matched_to_buyhatkes_store_and_product():
    assert backfill.locate("https://www.croma.com/some-laptop/p/322280", BUYHATKE_STORES) == (71, "322280")
    assert backfill.locate("https://www.flipkart.com/x/p/itm1?pid=COMGMX1", BUYHATKE_STORES) == (2, "COMGMX1")
    assert backfill.locate("https://store.acer.com/en-in/laptop", BUYHATKE_STORES) is None


def test_buyhatke_stretches_become_points_before_tracking_began():
    page = ('history:[{from:"2026-06-08 15:38:48",to:"2026-06-08 15:38:48",price:324990},'
            '{from:"2026-06-10 17:06:49",to:"2026-06-14 07:50:44",price:331490.5},'
            '{from:"2026-10-11 09:00:00",to:"2026-10-12 09:00:00",price:336490}]')
    found = backfill.points_from(page, datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc))
    # Indian time becomes UTC; a stretch gives a point at each end; anything after tracking began is left out.
    assert found == [(datetime(2026, 6, 8, 10, 8, 48, tzinfo=timezone.utc), 324990),
                     (datetime(2026, 6, 10, 11, 36, 49, tzinfo=timezone.utc), 331490),
                     (datetime(2026, 6, 14, 2, 20, 44, tzinfo=timezone.utc), 331490),
                     (datetime(2026, 10, 11, 3, 30, tzinfo=timezone.utc), 336490)]


def _tracked_listing(price=200000):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE listings (id INTEGER PRIMARY KEY, url TEXT, history_checked_at TEXT, history_note TEXT, history_url TEXT)")
    conn.execute("CREATE TABLE price_points (id INTEGER PRIMARY KEY, listing_id, price_inr, mrp_inr, in_stock, checked_at, source)")
    conn.execute("INSERT INTO listings (id, url) VALUES (1, 'https://www.croma.com/x/p/1')")
    conn.execute("INSERT INTO price_points (listing_id, price_inr, in_stock, checked_at) VALUES (1, ?, 1, '2026-10-11T06:00:00+00:00')", (price,))
    return conn, conn.execute("SELECT * FROM listings").fetchone()


def test_earlier_prices_are_imported_once_and_count_towards_the_low():
    conn, listing = _tracked_listing()
    earlier = [(datetime(2026, 3, 1, tzinfo=timezone.utc), 180000), (datetime(2026, 8, 1, tzinfo=timezone.utc), 210000)]
    asked = []
    note = backfill.import_listing(conn, listing, lambda url, before: asked.append(before) or {"points": earlier, "current": 205000, "link": "https://buyhatke.com/x-1"})
    assert asked == [datetime(2026, 10, 11, 6, 0, tzinfo=timezone.utc)] and "back to 1 Mar 2026" in note
    from tracker import listings
    points = listings.points(conn, 1)
    assert [(p["price_inr"], p["source"]) for p in points] == [(180000, "buyhatke"), (210000, "buyhatke"), (200000, None)]
    found = history.summary(points, NOW)
    assert (found["low_inr"], found["checks"], found["imported"], found["since_at"][:10]) == (180000, 1, True, "2026-03-01")
    # The small chart covers the last 30 days: the imported price it entered them at, then the tracker's check.
    assert found["spark"]["imported"].split()[0] == "2.0,3.0" and found["spark"]["own"].endswith("118.0,25.0")
    # A price above the imported low is not a new low, so the first check after an import stays quiet.
    assert alerts.triggered(_rules(), points) == []
    assert conn.execute("SELECT history_checked_at, history_url FROM listings").fetchone()[1] == "https://buyhatke.com/x-1"


def test_nothing_is_imported_when_buyhatke_seems_to_mean_another_configuration():
    conn, listing = _tracked_listing(price=200000)
    earlier = [(datetime(2026, 3, 1, tzinfo=timezone.utc), 120000)]
    note = backfill.import_listing(conn, listing, lambda url, before: {"points": earlier, "current": 125000})
    assert "another configuration" in note
    assert conn.execute("SELECT COUNT(*) FROM price_points WHERE source IS NOT NULL").fetchone()[0] == 0

    def unknown(url, before):
        raise backfill.Unavailable("BuyHatke has not recorded this listing.")
    assert backfill.import_listing(conn, listing, unknown) == "BuyHatke has not recorded this listing."
    assert conn.execute("SELECT history_checked_at FROM listings").fetchone()[0]


def test_next_check_follows_the_chosen_interval():
    last = "2026-10-11T06:00:00+00:00"
    assert scheduler.next_check(_settings(check_interval_hours="3", last_check_at=last)) == datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
    assert scheduler.next_check(_settings(check_interval_hours="1", last_check_at=last)) == datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)
    # An interval that is not offered falls back to the default.
    assert scheduler.interval_hours(_settings(check_interval_hours="5")) == scheduler.DEFAULT_INTERVAL
    assert scheduler.next_check(_settings()) <= datetime.now(timezone.utc)
    assert {1, 3, 6, 12} <= set(scheduler.INTERVALS)


def test_hunt_form_reads_alert_rules():
    form = MultiDict([("name", "Test"), ("retailers", "amazon"), ("alert_new_low", "1"),
                      ("alert_target_inr", "2,25,000"), ("alert_drop_pct", "5")])
    hunt, errors = hunts.parse_form(form)
    assert not errors
    assert (hunt["alert_new_low"], hunt["alert_back_in_stock"], hunt["alert_target_inr"], hunt["alert_drop_pct"]) == (1, 0, 225000, 5)
    assert hunts.blank()["alert_new_low"] == 1


def test_two_points_from_one_round_do_not_hide_a_price_change():
    from tracker import history

    def point(price, when):
        return {"price_inr": price, "mrp_inr": None, "in_stock": 1, "checked_at": when, "source": None}

    # Two copies of the app both checked at 20:44; the price had fallen since 18:42.
    points = [point(326607, "2026-10-12T18:42:35+00:00"), point(323608, "2026-10-12T20:44:21+00:00"),
              point(323608, "2026-10-12T20:44:33+00:00")]
    found = history.summary(points)
    assert (found["price_inr"], found["previous_inr"], found["change_inr"]) == (323608, 326607, -2999)


def test_only_one_copy_of_the_app_takes_a_round_of_checks():
    import sqlite3

    from tracker import scheduler

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    assert scheduler.claim_round(conn) and not scheduler.claim_round(conn)
    assert scheduler.claim_round(conn, minutes=-1) is True  # a later round is free again


def test_a_listing_whose_page_is_gone_twice_in_a_row_moves_to_gone(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from pathlib import Path

    from tracker import checker, db, listings
    from tracker.stores.base import ListingGone, Product

    path = str(tmp_path / "t.db")
    conn = db.connect(path)
    conn.executescript((Path(db.__file__).parent / "schema.sql").read_text(encoding="utf-8"))
    for table, column, kind in db.ADDED_COLUMNS:
        if column not in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    conn.execute("INSERT INTO hunts (id, name, status, created_at, updated_at) VALUES (1, 'Hunt', 'active', '2026-10-01', '2026-10-01')")
    for listing_id in (1, 2):
        conn.execute("INSERT INTO listings (id, hunt_id, retailer, url, title, state, first_seen_at, last_seen_at) "
                     "VALUES (?, 1, 'croma', ?, 'A laptop', 'tracked', '2026-10-01', '2026-10-01')", (listing_id, f"https://x.test/{listing_id}"))
    conn.commit()

    class Store:
        LABEL = "Croma"

        @staticmethod
        def prepare(page, pincode):
            return True

        @staticmethod
        def read(page, url, pincode):
            if url.endswith("/1"):
                raise ListingGone("Croma no longer has this product page (HTTP 404).")
            return Product(url=url, title="A laptop", price_inr=90000)

    told = []
    monkeypatch.setattr(checker, "STORES", {"croma": Store})
    monkeypatch.setattr(checker, "open_page", lambda *args: nullcontext())
    monkeypatch.setattr(checker, "_pause", lambda: None)
    monkeypatch.setattr(checker.alerts, "after_check", lambda *args: 0)
    monkeypatch.setattr(checker.alerts, "listing_gone", lambda conn, mail, hunt, row: told.append(row["id"]))
    options = {"database": path, "channel": None, "headless": True, "mail": {}}

    checker.run(options)  # one "gone" answer could be a hiccup: still checked
    assert told == [] and [row["id"] for row in checker.due_listings(conn)["croma"]] == [1, 2]
    checker.run(options)  # the second in a row moves it to Gone, once, and the store does not count as failed
    assert told == [1] and [row["id"] for row in checker.due_listings(conn)["croma"]] == [2]
    assert json.loads(conn.execute("SELECT value FROM settings WHERE key = 'last_check_result'").fetchone()[0])["gone"] == 1
    assert checker.RUN["stores"]["croma"]["phase"] == "done"
    # Price points are kept, and a search that finds the page again brings it back.
    listings.save(conn, 1, "croma", Product(url="https://x.test/1", title="A laptop", price_inr=88000), {}, None)
    assert [row["id"] for row in checker.due_listings(conn)["croma"]] == [1, 2]
    conn.close()
