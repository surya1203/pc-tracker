"""Apple India's own store (apple.com/in): every Mac configuration has its own page with a price."""
import re

from .base import Hit, Product, StoreError, clean, ld_offer, ld_product, open_product, without_brand

KEY = "apple"
LABEL = "Apple Store"
BRANDS = ("Apple",)
HOME = "https://www.apple.com/in/shop/buy-mac"
LINES = {"laptop": ("macbook-air", "macbook-pro"), "tower": ("mac-mini", "mac-studio")}
COLOURS = r"silver|space-black|space-grey|space-gray|midnight|starlight|sky-blue|gold|blue|pink|green|yellow"

_LINKS_JS = """() => [...new Set([...document.querySelectorAll('a[href*="/in/shop/buy-mac/mac"]')].map((a) => a.href.split('?')[0]))]"""


def prepare(page, pincode):
    page.goto(HOME, wait_until="domcontentloaded", timeout=45000)
    return True


def _name(url):
    """'.../macbook-pro/14-inch-space-black-...-apple-m5-chip-...-16gb-memory-1tb-storage' -> a readable title."""
    line, slug = url.rstrip("/").rsplit("/", 2)[-2:]
    words = slug.replace("-inch", "-inch,").replace("-apple-", "-Apple-").split("-")
    text = " ".join(word.upper() if re.fullmatch(r"m\d|\d+(gb|tb)|cpu|gpu", word) else word for word in words)
    return clean(f"Apple {line.replace('-', ' ').title().replace('Macbook', 'MacBook').replace('Mini', 'mini')} {text}")


def search(page, query, limit, kind="laptop"):
    wanted = re.sub(r"\bdesktop\b", " ", without_brand(query, "Apple").lower()).strip()
    lines = [line for line in LINES[kind] if line.split("-")[1] in wanted] or LINES[kind]
    found = {}
    for line in lines:
        page.goto(f"{HOME}/{line}", wait_until="domcontentloaded", timeout=45000)
        for url in page.evaluate(_LINKS_JS):
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            if f"/{line}/" not in url or "memory" not in slug:
                continue
            # The same configuration is listed once per colour, at the same price; keep the first.
            found.setdefault(re.sub(rf"-({COLOURS})(?=-)", "", slug), url)
    words = re.findall(r"[a-z0-9]+", wanted)
    def closeness(url):
        text = re.sub(r"[^a-z0-9]", "", url.lower())
        return -sum(word in text for word in words)
    return [Hit(url=url, title=_name(url)) for url in sorted(found.values(), key=closeness)[:limit]]


def read(page, url, pincode):
    open_product(page, url, LABEL)
    block = ld_product(page)
    if not block.get("name"):
        raise StoreError("Apple's product page did not load as expected.")
    name = clean(block["name"])
    price, in_stock = ld_offer(block)
    specs = {"Brand": "Apple", "Graphics": "Apple integrated"}
    for label, pattern, shape in (
        ("Processor", r"\b(M\d(?: Pro| Max| Ultra)?) chip", "Apple {}"), ("RAM", r"(\d+\s*GB) (?:unified )?memory", "{}"),
        ("Storage", r"(\d+\s*(?:GB|TB)) (?:SSD )?storage", "{} SSD"), ("Screen Size", r"(\d{2}(?:\.\d)?)\s*(?:\"|”|″|-inch| inch)", "{} inch"),
    ):
        match = re.search(pattern, name, re.I)
        if match:
            specs[label] = shape.format(match.group(1))
    offers = block.get("offers") or {}
    offer = offers[0] if isinstance(offers, list) and offers else offers if isinstance(offers, dict) else {}
    if offer.get("sku"):
        specs["Part Number"] = clean(offer["sku"])
    image = block.get("image")
    return Product(
        url=url, title=name if name.lower().startswith("apple") else f"Apple {name}",
        image_url=(image[0] if isinstance(image, list) and image else image) or None,
        price_inr=price, in_stock=bool(price) and in_stock, raw_specs=specs,
    )
