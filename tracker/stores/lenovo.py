"""Lenovo India's own store (lenovo.com/in): everything comes from its product search API."""
import re
from urllib.parse import quote, urlsplit

from .base import Hit, ListingGone, Product, StoreError, clean, fetch_json, rupees, without_brand

KEY = "lenovo"
LABEL = "Lenovo Store"
BRANDS = ("Lenovo",)
HOME = "https://www.lenovo.com/in/en"
SEARCH_API = "https://openapi.lenovo.com/in/en/ofp/search/global/cache/products/get/_tsc"
# Lenovo marks "build your own" models with this type; their price is for the starting configuration.
CUSTOMISABLE = "4"


def prepare(page, pincode):
    page.goto(f"{HOME}/laptops/", wait_until="domcontentloaded", timeout=45000)
    return True


def _products(page, text, rows):
    data = fetch_json(page, f"{SEARCH_API}?text={quote(text)}&rows={rows}&sort=Recommended", LABEL)
    try:
        groups = data["data"]["data"]
    except (KeyError, TypeError):
        return []
    return [item for group in groups or [] for item in group.get("products") or []]


def _title(item):
    name = re.sub(r"<[^>]+>", "", item.get("summary") or item.get("productName") or "")
    title = name if name.lower().startswith("lenovo") else f"Lenovo {name}"
    return clean(f"{title} (customisable, {item['productCode']})" if item.get("productType") == CUSTOMISABLE
                 else f"{title} ({item['productCode']})")


def search(page, query, limit, kind="laptop"):
    # The store only sells Lenovo, and searching for the brand name itself brings up accessories.
    text = re.sub(r"\blenovo\b|\blaptops?\b|\bdesktops?\b", " ", query, flags=re.I).strip() or kind
    found = _products(page, text, 40)
    if kind == "tower" and text != kind:
        # Laptops crowd a search out ("RTX 5070" alone brings back no desktops), so the towers are listed as well.
        found += _products(page, kind, 40)
    # Lenovo lists its build-your-own models first, and their names give no GPU. Only the first few results of
    # a search are read, so ready-made models that name the searched GPU ("RTX 5070", not "RTX 5070 Ti") go first.
    gpu = re.search(r"\b(?:rtx|gtx|rx|arc)\s*[a-z]?\d{3,4}[a-z]?(?:\s*(?:ti|super|xt)\b)?", text, re.I)
    if gpu:
        named = re.compile(rf"(?<![a-z0-9]){re.escape(gpu.group(0)).replace(r'\ ', r'\s*')}(?!\s*(?:ti|super|xt)\b)(?![a-z0-9])", re.I)
        found.sort(key=lambda item: not named.search(_title(item)))
    hits, seen = [], set()
    for item in found:
        if not item.get("url") or not item.get("productCode") or item["productCode"] in seen:
            continue
        # Its desktops include all-in-ones and palm-sized PCs; the towers say so in their address.
        if item.get("flCode") != "laptops" if kind == "laptop" else (item.get("flCode") != "desktops" or "tower" not in item["url"]):
            continue
        seen.add(item["productCode"])
        hits.append(Hit(url=HOME + item["url"], title=_title(item)))
        if len(hits) >= limit:
            break
    return hits


def read(page, url, pincode):
    line, code = urlsplit(url).path.rstrip("/").rsplit("/", 2)[-2:]
    code = code.upper()
    # Asking for the part number finds a build-your-own model; a ready-made one only turns up under its model's name.
    name = " ".join(word for word in re.findall(r"[a-z0-9]+", line.lower()) if word != "lenovo")
    item = None
    for text in (code, name, f"{name} {code}"):
        item = next((p for p in _products(page, text, 40) if str(p.get("productCode", "")).upper() == code), None)
        if item:
            break
    if not item:
        raise ListingGone("Lenovo no longer lists this model.")
    specs = {"Brand": "Lenovo", "Part Number": item["productCode"]}
    for row in item.get("classification") or []:
        name, value = clean(row.get("a")), clean(re.sub(r"<[^>]+>", " ", str(row.get("b") or "")))
        if name and value and name not in specs:
            specs[name] = value.replace("®", "").replace("™", "")
    price, mrp = rupees(item.get("finalPrice")), rupees(item.get("webPrice"))
    image = ((item.get("media") or {}).get("heroImage") or {}).get("imageAddress")
    return Product(
        url=url, title=_title(item), image_url=(f"https:{image}" if image and image.startswith("//") else image) or None,
        price_inr=price, mrp_inr=mrp if mrp and price and mrp >= price else None,
        in_stock=bool(price) and item.get("marketingStatus") == "Available" and bool(item.get("purchaseFlag", True)),
        raw_specs=specs,
    )
