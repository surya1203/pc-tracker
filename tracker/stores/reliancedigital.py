"""Reliance Digital: search and product details from the catalogue API its own pages call.

Its delivery check needs requests signed by the site's own scripts, so delivery is not checked here.
"""
from urllib.parse import quote, urlsplit

from .base import Hit, Product, StoreError, clean, fetch_json, is_tower, relevant, rupees

KEY = "reliancedigital"
LABEL = "Reliance Digital"
HOME = "https://www.reliancedigital.in"
# The app token Reliance Digital's own pages send with every price request (the same for all visitors).
APP_TOKEN = "NjQ1YTA1Nzg3NWQ4YzQ4ODJiMDk2ZjdlOl9fLU80NC00aQ=="


def prepare(page, pincode):
    page.goto(HOME, wait_until="domcontentloaded", timeout=45000)
    return True


def _is_kind(item, kind):
    names = " ".join(str(category.get("name", "")) for category in item.get("categories") or []).lower()
    if kind == "tower":  # filed under "Desktops & All-In-Ones"
        return "desktop" in names and is_tower(str(item.get("name")))
    return "laptop" in names and "bag" not in names and "accessor" not in names


def search(page, query, limit, kind="laptop"):
    data = fetch_json(page, f"/ext/raven-api/catalog/v1.0/products?q={quote(query)}&page_no=1&page_size=24"
                            "&page_type=number&page_id=%2A&f=page_type%3Anumber", LABEL)
    hits = []
    for item in data.get("items") or []:
        title = clean(item.get("name"))
        if not item.get("slug") or not _is_kind(item, kind) or not relevant(title, query):
            continue
        hits.append(Hit(url=f"{HOME}/product/{item['slug']}", title=title))
        if len(hits) >= limit:
            break
    return hits


def read(page, url, pincode):
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    if "/product/" not in url or not slug:
        raise StoreError("That is not a Reliance Digital product page.")
    item = fetch_json(page, f"/ext/raven-api/catalog/v1.0/products/{slug}", LABEL, gone_on_404=True)
    specs = {}
    for group in item.get("grouped_attributes") or []:
        for detail in group.get("details") or []:
            name, value = clean(detail.get("key")), clean(detail.get("value"))
            if name and value and name not in specs:
                specs[name] = value

    price = mrp = None
    sellable = True
    try:
        offer = fetch_json(page, f"/api/service/application/catalog/v1.0/products/{slug}/sizes/", LABEL,
                           headers={"authorization": f"Bearer {APP_TOKEN}"})
        price = rupees(offer["price"]["effective"]["min"])
        mrp = rupees(offer["price"]["marked"]["min"])
        sellable = bool(offer.get("sellable")) and any(size.get("is_available") for size in offer.get("sizes") or [])
    except (StoreError, KeyError, TypeError):
        pass
    images = [media.get("url") for media in item.get("medias") or [] if media.get("type") == "image"]
    return Product(
        url=url, title=clean(item.get("name")), image_url=images[0] if images else None,
        price_inr=price, mrp_inr=mrp if mrp and price and mrp >= price else None,
        in_stock=bool(price) and sellable, raw_specs=specs,
    )
