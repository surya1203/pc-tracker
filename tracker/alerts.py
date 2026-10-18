"""Alerts: decides when a price check is worth telling you about, and sends it.

Rules are set per hunt. Every alert is written to the in-app feed; Discord (a channel
webhook), email (Gmail) and Telegram (your own bot) are sent as well when they are set up in Settings,
outside quiet hours.
A rule does not fire twice for the same listing within the cooldown.
"""
import html
import json
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from . import catalog
from .db import now

RULES = {
    "new_low": "New all-time low",
    "target": "Target price reached",
    "drop": "Price drop",
    "back_in_stock": "Back in stock",
    "new_match": "New match",
    "gone": "No longer listed",
    "test": "Test alert",
}
DEFAULT_COOLDOWN_HOURS = 24
# The stripe down the side of a Discord alert.
COLOURS = {"new_low": 0x16A34A, "target": 0x2563EB, "drop": 0xD97706, "back_in_stock": 0x7C3AED, "new_match": 0x0891B2, "gone": 0x64748B}


def rupees(amount):
    """189990 -> '₹1,89,990'."""
    digits = str(int(amount))
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return "₹" + ",".join(([head] if head else []) + groups + [tail])


def price_text(amount):
    return rupees(amount) if amount else "Price not shown"


def triggered(hunt, points):
    """The rules a listing's newest price check sets off: [(rule, detail), ...]. `points` are oldest first."""
    if len(points) < 2:
        return []
    latest, before = points[-1], points[:-1]
    price = latest["price_inr"]
    if not price or not latest["in_stock"]:
        return []
    priced = [p["price_inr"] for p in before if p["price_inr"] and p["in_stock"]]
    previous = priced[-1] if priced else None
    found = []
    if hunt["alert_new_low"] and priced and price < min(priced):
        found.append(("new_low", f"{rupees(price)}, down from the earlier low of {rupees(min(priced))}"))
    target = hunt["alert_target_inr"]
    if target and price <= target and (previous is None or previous > target):
        found.append(("target", f"{rupees(price)}, at or under your target of {rupees(target)}"))
    drop = hunt["alert_drop_pct"]
    if drop and previous and price <= previous * (1 - drop / 100):
        found.append(("drop", f"{rupees(price)}, {round(100 * (1 - price / previous))}% below the last check ({rupees(previous)})"))
    if hunt["alert_back_in_stock"] and not before[-1]["in_stock"]:
        found.append(("back_in_stock", f"buyable again at {rupees(price)}"))
    return found


def _setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] else default


def in_quiet_hours(start, end, moment=None):
    """Whether the local time falls in the quiet window, e.g. '22:00' to '07:00' (which wraps past midnight)."""
    if not start or not end or start == end:
        return False
    clock = (moment or datetime.now()).strftime("%H:%M")
    return start <= clock < end if start < end else clock >= start or clock < end


def credentials(conn, fallback=None):
    """What the channels sign in with: the values saved in Settings, else the ones in .env (`fallback`)."""
    fallback = fallback or {}
    return {
        "sender": _setting(conn, "sender_email") or fallback.get("sender") or "",
        "password": (_setting(conn, "gmail_app_password") or fallback.get("password") or "").replace(" ", ""),
        "telegram_token": _setting(conn, "telegram_bot_token") or fallback.get("telegram_token") or "",
        "discord_webhook": _setting(conn, "discord_webhook_url") or fallback.get("discord_webhook") or "",
    }


def send_email(mail, to, subject, body):
    if not (mail.get("sender") and mail.get("password")):
        raise RuntimeError("Enter the Gmail address to send from and its app password in Settings first.")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = mail["sender"], to, subject
    message.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(mail["sender"], mail["password"])
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as error:
        raise RuntimeError("Gmail refused the sign-in. It needs a 16-letter app password, not your normal password.") from error
    except (smtplib.SMTPServerDisconnected, OSError):
        # Some networks block the secure port; Gmail also accepts mail on 587.
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(mail["sender"], mail["password"])
            server.send_message(message)


def send_discord(webhook, body, card=None):
    """Post to a Discord channel. With a `card` (see `after_check`) the alert is laid out as an embed."""
    if not webhook:
        raise RuntimeError("Paste a Discord webhook link in Settings first.")
    payload = {"content": body[:2000]}
    if card and "lines" in card:
        shown, length = [], 0
        for line in card["lines"]:
            entry = f"**[{_link_text(line['title'])}]({line['url']})**\n{line['store']} · {line['text']}"
            if length + len(entry) > 3800:  # Discord allows 4096 characters
                shown.append(f"…and {len(card['lines']) - len(shown)} more on the Tracked page.")
                break
            shown.append(entry)
            length += len(entry) + 2
        payload = {"content": f"**{card['headline'][:1900]}**", "embeds": [{
            "author": {"name": "Tracked listings"}, "description": "\n\n".join(shown),
            "color": COLOURS["new_low"] if card["news"] else 0x64748B, "footer": {"text": "PC Tracker"},
        }]}
    elif card:
        embed = {
            "author": {"name": RULES[card["rule"]]},
            "title": card["title"][:250], "url": card["url"],
            "description": f"**{price_text(card['price'])}**\n{card['detail']}",
            "color": COLOURS.get(card["rule"], 0x64748B),
            "fields": [{"name": "Store", "value": card["store"], "inline": True},
                       {"name": "Hunt", "value": card["hunt"][:200], "inline": True}],
            "footer": {"text": "PC Tracker"},
        }
        if (card.get("image") or "").startswith("http"):
            embed["thumbnail"] = {"url": card["image"]}
        # The one-line content is what a phone notification shows; the embed carries the rest.
        payload = {"content": f"**{RULES[card['rule']]}**: {price_text(card['price'])}", "embeds": [embed]}
    request = urllib.request.Request(
        webhook, data=json.dumps(payload).encode("utf-8"),
        # Discord turns away Python's default User-Agent.
        headers={"Content-Type": "application/json", "User-Agent": "PCTracker/1.0"},
    )
    try:
        urllib.request.urlopen(request, timeout=30).close()
    except urllib.error.HTTPError as error:
        gone = error.code in (401, 404)
        raise RuntimeError("Discord no longer knows this webhook. Create a new one and paste its link in Settings."
                           if gone else f"Discord answered {error.code}.") from error


def _link_text(title):
    """A listing title that is safe inside a Discord [link](…)."""
    return title[:80].replace("[", "(").replace("]", ")")


def _telegram(token, method, data=None):
    """Call Telegram's own Bot API and return its result."""
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}",
                                     data=urllib.parse.urlencode(data or {}).encode("utf-8"))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)["result"]
    except urllib.error.HTTPError as error:
        try:
            reason = json.load(error)["description"]
        except Exception:
            reason = f"error {error.code}"
        if error.code in (401, 404):
            reason = "it does not know this bot token. Copy it again from BotFather."
        raise RuntimeError(f"Telegram answered: {reason}") from error


def telegram_chat(token):
    """The chat that last messaged the bot, which is where alerts should go. None if nobody has yet."""
    for update in reversed(_telegram(token, "getUpdates")):
        chat = (update.get("message") or {}).get("chat")
        if chat:
            return str(chat["id"])
    return None


def send_telegram(token, chat, body, card=None):
    if not token:
        raise RuntimeError("Paste your Telegram bot's token in Settings first.")
    if not chat:
        raise RuntimeError("Send your bot a message in Telegram, then press Save so the tracker can find the chat.")
    message = {"chat_id": chat, "text": body[:4096], "disable_web_page_preview": "true"}
    e = html.escape
    if card and "lines" in card:
        lines = [f"<a href=\"{e(line['url'])}\">{e(line['title'][:80])}</a>\n{e(line['store'])} · {e(line['text'])}"
                 for line in card["lines"][:20]]
        message.update(parse_mode="HTML", text=f"<b>{e(card['headline'])}</b>\n\n" + "\n\n".join(lines))
    elif card:
        message.update(parse_mode="HTML", text=(
            f"<b>{e(RULES[card['rule']])}: {price_text(card['price'])}</b>\n{e(card['detail'])}\n\n"
            f"<a href=\"{e(card['url'])}\">{e(card['title'][:250])}</a>\n{e(card['store'])} · hunt “{e(card['hunt'])}”"))
    _telegram(token, "sendMessage", message)


def deliver(conn, mail, subject, body, quiet_ok=False, card=None):
    """Send to every channel that is set up. Returns (channels reached, problems)."""
    reached, problems = ["feed"], []
    mail = credentials(conn, mail)
    if not quiet_ok and in_quiet_hours(_setting(conn, "quiet_start"), _setting(conn, "quiet_end")):
        return reached, ["quiet hours: shown here only"]
    for channel, address, send in (
        ("discord", mail["discord_webhook"], lambda to: send_discord(to, body, card)),
        ("email", _setting(conn, "notify_email") or (mail["sender"] if mail["password"] else None),
         lambda to: send_email(mail, to, subject, body)),
        ("telegram", _setting(conn, "telegram_chat_id") if mail["telegram_token"] else None,
         lambda to: send_telegram(mail["telegram_token"], to, body, card)),
    ):
        if not address:
            continue
        try:
            send(address)
            reached.append(channel)
        except Exception as error:
            problems.append(f"{channel} failed: {str(error).splitlines()[0][:160]}")
    return reached, problems


def after_check(conn, mail, hunt, listing, points):
    """Raise whatever alerts the newest check of a tracked listing calls for. Returns how many were raised."""
    cooldown = float(_setting(conn, "alert_cooldown_hours", DEFAULT_COOLDOWN_HOURS))
    since = (datetime.now(timezone.utc) - timedelta(hours=cooldown)).isoformat(timespec="seconds")
    raised = 0
    for rule, detail in triggered(hunt, points):
        repeat = conn.execute(
            "SELECT 1 FROM alerts WHERE listing_id = ? AND rule = ? AND sent_at >= ?", (listing["id"], rule, since)
        ).fetchone()
        if repeat:
            continue
        store = catalog.RETAILER_LABELS.get(listing["retailer"], listing["retailer"])
        subject = f"{RULES[rule]}: {listing['title'][:70]}"
        body = f"{RULES[rule]}: {detail}\n{listing['title']}\n{store} · hunt “{hunt['name']}”\n{listing['url']}"
        # The same alert in parts, for the channels that can lay it out (Discord, Telegram).
        card = {"rule": rule, "detail": detail, "price": points[-1]["price_inr"], "title": listing["title"],
                "url": listing["url"], "image": listing["image_url"], "store": store, "hunt": hunt["name"]}
        reached, problems = deliver(conn, mail, subject, body, card=card)
        conn.execute(
            "INSERT INTO alerts (hunt_id, listing_id, rule, channel, price_inr, message, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (hunt["id"], listing["id"], rule, ", ".join(reached), points[-1]["price_inr"],
             json.dumps({"detail": detail, "problems": problems}, ensure_ascii=False), now()),
        )
        conn.commit()
        raised += 1
    return raised


def listing_gone(conn, mail, hunt, listing):
    """Say once that a tracked listing's store page no longer exists, so it has stopped being checked."""
    store = catalog.RETAILER_LABELS.get(listing["retailer"], listing["retailer"])
    last = conn.execute("SELECT price_inr FROM price_points WHERE listing_id = ? AND price_inr IS NOT NULL "
                        "ORDER BY checked_at DESC, id DESC LIMIT 1", (listing["id"],)).fetchone()
    price = last["price_inr"] if last else None  # the last price it had, for the card
    detail = f"{store} no longer has this product page, so it has stopped being checked. It is under Gone on the Tracked page."
    subject = f"{RULES['gone']}: {listing['title'][:70]}"
    body = f"{detail}\n{listing['title']}\n{store} · hunt “{hunt['name']}”\n{listing['url']}"
    card = {"rule": "gone", "detail": detail, "price": price, "title": listing["title"],
            "url": listing["url"], "image": listing["image_url"], "store": store, "hunt": hunt["name"]}
    reached, problems = deliver(conn, mail, subject, body, card=card)
    conn.execute(
        "INSERT INTO alerts (hunt_id, listing_id, rule, channel, price_inr, message, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (hunt["id"], listing["id"], "gone", ", ".join(reached), price,
         json.dumps({"detail": detail, "problems": problems}, ensure_ascii=False), now()),
    )
    conn.commit()


def new_matches(conn, mail, hunt, found):
    """Raise one alert for the listings a scheduled search added that meet the hunt. `found` is cheapest first."""
    best, count = found[0], len(found)
    store = catalog.RETAILER_LABELS.get(best["retailer"], best["retailer"])
    detail = ("A new listing that meets this hunt." if count == 1 else
              f"The cheapest of {count} new listings that meet this hunt. Open the hunt to review them all.")
    subject = f"{count} new listing{'' if count == 1 else 's'} for “{hunt['name']}”"
    lines = [f"{price_text(item['price_inr'])} · {catalog.RETAILER_LABELS.get(item['retailer'], item['retailer'])}\n"
             f"{item['title']}\n{item['url']}" for item in found[:10]]
    card = {"rule": "new_match", "detail": detail, "price": best["price_inr"], "title": best["title"],
            "url": best["url"], "image": best["image_url"], "store": store, "hunt": hunt["name"]}
    reached, problems = deliver(conn, mail, subject, subject + "\n\n" + "\n\n".join(lines), card=card)
    conn.execute(
        "INSERT INTO alerts (hunt_id, listing_id, rule, channel, price_inr, message, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (hunt["id"], best["id"], "new_match", ", ".join(reached), best["price_inr"],
         json.dumps({"detail": detail, "problems": problems}, ensure_ascii=False), now()),
    )
    conn.commit()


def update_headline(news, items, since):
    """The title of a regular update: what was found if anything was, else how many prices moved."""
    if len(news) == 1:
        return f"{RULES[news[0]['rule']]}: {price_text(news[0]['price_inr'])} · {(news[0]['title'] or '')[:60]}"
    if news:
        counts = {}
        for alert in news:
            counts[RULES[alert["rule"]]] = counts.get(RULES[alert["rule"]], 0) + 1
        return ", ".join(label if count == 1 else f"{label} ×{count}" for label, count in counts.items())
    moved = sum(1 for item in items if item["change_inr"] and (item["checked_at"] or "") >= since)
    total = f"{len(items)} tracked listing{'' if len(items) == 1 else 's'}"
    return f"{total}: {moved} price{'' if moved == 1 else 's'} changed" if moved else f"{total}: no price change"


def update_line(item, since):
    """One tracked listing in a regular update: its price, how that moved at the last check, and its low."""
    parts = [price_text(item["price_inr"])]
    if (item["checked_at"] or "") < since:
        parts.append("not read this time")
    elif not item["in_stock"]:
        parts.append("out of stock")
    elif item["change_inr"]:
        parts.append(f"{'▼' if item['change_inr'] < 0 else '▲'} {rupees(abs(item['change_inr']))} since the last check")
    else:
        parts.append("no change")
    if item["at_low"]:
        parts.append("its lowest so far")
    elif item["low_inr"] and item["low_inr"] != item["price_inr"]:
        parts.append(f"lowest {rupees(item['low_inr'])}")
    return " · ".join(parts)


def status_update(conn, mail, items, since, news_since=None):
    """Send where every tracked listing stands after a scheduled check that began at `since`.

    The title leads with any alert raised since `news_since` (the last update, so a new match found
    between two checks is not missed); otherwise it is a plain update. It is sent, not written to the
    feed. Returns (headline, channels reached, problems).
    """
    marks = ", ".join("?" * len(COLOURS))
    news = conn.execute(
        f"SELECT a.rule, a.price_inr, l.title FROM alerts a LEFT JOIN listings l ON l.id = a.listing_id "
        f"WHERE a.sent_at >= ? AND a.rule IN ({marks}) ORDER BY a.id", (min(since, news_since or since), *COLOURS),
    ).fetchall()
    headline = update_headline(news, items, since)
    lines = [{"title": item["title"], "url": item["url"], "text": update_line(item, since),
              "store": catalog.RETAILER_LABELS.get(item["retailer"], item["retailer"])} for item in items]
    body = headline + "\n\n" + "\n\n".join(f"{line['title']}\n{line['store']} · {line['text']}\n{line['url']}" for line in lines)
    reached, problems = deliver(conn, mail, headline, body, card={"headline": headline, "lines": lines, "news": bool(news)})
    return headline, reached, problems


def recent(conn, limit=30):
    """The alert feed, newest first."""
    rows = conn.execute(
        """
        SELECT a.*, l.title, l.url, l.retailer, h.name AS hunt_name
        FROM alerts a
        LEFT JOIN listings l ON l.id = a.listing_id
        LEFT JOIN hunts h ON h.id = a.hunt_id
        ORDER BY a.sent_at DESC, a.id DESC LIMIT ?
        """, (limit,),
    ).fetchall()
    feed = []
    for row in rows:
        alert = dict(row)
        try:
            extra = json.loads(alert["message"] or "{}")
        except ValueError:
            extra = {"detail": alert["message"]}
        alert.update(label=RULES.get(alert["rule"], alert["rule"]), detail=extra.get("detail"), problems=extra.get("problems") or [])
        feed.append(alert)
    return feed
