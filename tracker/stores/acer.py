"""Acer India's own store (store.acer.com/en-in).

Acer turns automated browsers away from its product pages but not from its search,
and a search result already carries the full name, price and stock. So a listing
is read by searching for its model line again and picking it out by its reference
number, which is kept after the "#" in its address.
"""
import base64
import json
import re
from urllib.parse import quote_plus

from .base import Hit, ListingGone, Product, StoreError, clean, relevant, rupees, without_brand

KEY = "acer"
LABEL = "Acer Store"
BRANDS = ("Acer",)
HOME = "https://store.acer.com/en-in"

_CARDS_JS = """() => [...document.querySelectorAll('.product-item-info[data-eec-product-id]')].map((card) => {
    const link = card.querySelector('a.product-item-link[href]');
    const amount = (type) => { const el = card.querySelector(`[data-price-type="${type}"]`); return el ? el.getAttribute('data-price-amount') : ''; };
    const image = card.querySelector('img[data-src], img[src]');
    return {
        reference: card.getAttribute('data-eec-product-id'),
        info: card.getAttribute('data-new-product-info') || '',
        href: link ? link.href : '',
        title: link ? link.textContent : '',
        price: amount('finalPrice'),
        mrp: amount('oldPrice'),
        image: image ? (image.getAttribute('data-src') || image.src) : '',
        unavailable: !!card.querySelector('.stock.unavailable'),
    };
})"""


def prepare(page, pincode):
    page.goto(HOME, wait_until="domcontentloaded", timeout=45000)
    return True


def _cards(page, phrase):
    page.goto(f"{HOME}/catalogsearch/result/?q={quote_plus(phrase)}&product_list_limit=36", wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_selector(".product-item-info, .message.notice", timeout=15000)
    except Exception:
        if "access denied" in page.title().lower():
            raise StoreError("Acer refused the request (it thinks this is a bot). Try again later.")
        return []
    return [card for card in page.evaluate(_CARDS_JS) if card["href"] and card["reference"]]


def search(page, query, limit, kind="laptop"):
    query = without_brand(query, "Acer")
    hits, seen = [], set()
    for card in _cards(page, query):
        title = clean(card["title"])
        if card["reference"] in seen or not relevant(title, query):
            continue
        seen.add(card["reference"])
        hits.append(Hit(url=f"{card['href'].split('#')[0]}#{card['reference']}", title=title))
        if len(hits) >= limit:
            break
    return hits


def _phrase(address):
    """'.../acer-predator-helios-neo-16-gaming-laptop-...-rtx-5070-...' -> 'predator helios neo 16 rtx 5070'."""
    slug = address.rstrip("/").rsplit("/", 1)[-1]
    line = re.split(r"-(?:gaming|laptop|intel|amd|windows|nvidia|ai|thin|oled)\b", slug, maxsplit=1)[0]
    chip = re.search(r"\b(rtx|gtx|rx)-(\d{4})", slug)
    return " ".join(word for word in line.split("-") if word != "acer") + (f" {chip.group(1)} {chip.group(2)}" if chip else "")


def read(page, url, pincode):
    address, _, reference = url.partition("#")
    if not reference:
        raise StoreError("Acer's product pages cannot be opened by the tracker; find this laptop through a search instead.")
    # Acer also turns away searches for the reference number itself.
    card = next((c for c in _cards(page, _phrase(address)) if c["reference"] == reference), None)
    if not card:
        raise ListingGone("Acer no longer lists this model.")
    price = rupees(card["price"])
    if not price:
        try:  # the card also carries its details as packed text
            price = rupees(json.loads(base64.b64decode(card["info"])).get("price"))
        except (ValueError, AttributeError):
            pass
    mrp = rupees(card["mrp"])
    title = clean(card["title"]).replace("Acer,", "Acer", 1)
    return Product(
        url=url, title=title if title.lower().startswith("acer") else f"Acer {title}",
        image_url=card["image"] or None, price_inr=price, mrp_inr=mrp if mrp and price and mrp >= price else None,
        in_stock=bool(price) and not card["unavailable"],
        raw_specs={"Brand": "Acer", "Part Number": reference},
    )
