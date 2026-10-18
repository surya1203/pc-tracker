"""Vijay Sales: search, product details and delivery checks, all from the APIs its own pages call."""
import html
import re
from urllib.parse import quote

from .base import Hit, ListingGone, Product, StoreError, clean, fetch_json, is_tower, relevant, rupees

KEY = "vijaysales"
LABEL = "Vijay Sales"
HOME = "https://www.vijaysales.com"
SEARCH_API = "https://mdm.vijaysales.com/web/api/search/unbxd-search/v1"
PRODUCT_API = "https://vsprod.vijaysales.com/graphql"
DELIVERY_API = "https://oms.vijaysales.systems/v1/servicability"

_PRODUCT_QUERY = ('{products(search:"",filter:{sku:{in:["%s"]}}){items{sku name vsp mrp stock_status '
                  'image{url} description{html} short_description{html} price_range{maximum_price{final_price{value}}}}}}')


def prepare(page, pincode):
    page.goto(HOME, wait_until="domcontentloaded", timeout=45000)
    return True


# How the site names the two categories.
CATEGORIES = {"laptop": "Laptops", "tower": "Desktops"}


def search(page, query, limit, kind="laptop"):
    # The site packs the paging into the search phrase itself.
    data = fetch_json(page, f"{SEARCH_API}?q={quote(f'{query}&page=1&rows=40', safe='')}&fq=", LABEL)
    hits = []
    for item in (data.get("response") or {}).get("products") or []:
        title = clean(item.get("title"))
        if item.get("l3") != CATEGORIES[kind] or not item.get("productUrl") or not relevant(title, query):
            continue
        if kind == "tower" and not is_tower(title):
            continue
        hits.append(Hit(url=item["productUrl"], title=title))
        if len(hits) >= limit:
            break
    return hits


def description_specs(markup):
    """'<b>Processor:</b> Intel Core Ultra 7 255HX ...<br>' lines -> {'Processor': 'Intel Core Ultra 7 255HX ...'}."""
    specs = {}
    for name, value in re.findall(r"<(?:b|strong)>\s*([^<:]{2,40}):?\s*</(?:b|strong)>\s*:?\s*([^<]+)", markup or ""):
        name, value = clean(html.unescape(name)), clean(html.unescape(value)).lstrip(":–- ")
        if name and value and name not in specs:
            specs[name] = value
    return specs


def read(page, url, pincode):
    sku = re.search(r"/p/(?:[A-Za-z]\w*/)?(\d+)", url)  # some addresses carry a group code before the number
    if not sku:
        raise StoreError("That is not a Vijay Sales product page.")
    sku = sku.group(1)
    data = fetch_json(page, f"{PRODUCT_API}?query={quote(_PRODUCT_QUERY % sku, safe='')}", LABEL, headers={"store": "vijay_sales"})
    items = ((data.get("data") or {}).get("products") or {}).get("items") or []
    if not items:
        raise ListingGone("Vijay Sales no longer lists this product.")
    item = items[0]
    price = rupees(item.get("vsp")) or rupees(item["price_range"]["maximum_price"]["final_price"]["value"])
    mrp = rupees(item.get("mrp"))
    specs = description_specs((item.get("description") or {}).get("html"))
    for name, value in description_specs((item.get("short_description") or {}).get("html")).items():
        specs.setdefault(name, value)

    in_stock = bool(price) and item.get("stock_status", "IN_STOCK") == "IN_STOCK"
    deliverable = note = None
    if pincode:
        try:
            answer = fetch_json(page, f"{DELIVERY_API}?pincode={pincode}&vanNo={sku}&storeList=true", LABEL)
            answer = (answer.get("data") or {}).get(sku)
        except StoreError:
            answer = None
        if answer is not None:
            deliverable = bool(answer.get("isServiceable"))
            modes = answer.get("shippingModes") or []
            note = clean(modes[0].get("label")) or None if deliverable and modes else None
    return Product(
        url=url, title=clean(item.get("name")), image_url=(item.get("image") or {}).get("url") or None,
        price_inr=price, mrp_inr=mrp if mrp and price and mrp >= price else None, in_stock=in_stock,
        raw_specs=specs, deliverable=deliverable, delivery_note=note,
    )
