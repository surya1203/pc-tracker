"""Croma: search and prices from Croma's own APIs, specs from the product page."""
import re
from urllib.parse import quote

from .base import Hit, Product, StoreError, clean, fetch_json, is_tower, ld_product, open_product, relevant, rupees

KEY = "croma"
LABEL = "Croma"
HOME = "https://www.croma.com"
API = "https://api.croma.com"
# Croma's "Laptops" and "Desktops" categories, to keep accessories out of the results.
CATEGORIES = {"laptop": "20", "tower": "25"}
# Croma prices by PIN code; this is the one its site uses before a visitor picks a location.
DEFAULT_PINCODE = "400049"
# The key Croma's own pages send with a delivery check.
DELIVERY_KEY = "1131858141634e2abe2efb2b3a2a2a5d"

_PRODUCT_JS = """() => {
    const specs = {};
    document.querySelectorAll('ul.cp-specification-spec-info').forEach((row) => {
        const name = row.querySelector('.cp-specification-spec-title');
        const value = row.querySelector('.cp-specification-spec-details');
        if (name && value && !(name.textContent.trim() in specs)) specs[name.textContent.trim()] = value.textContent.trim();
    });
    const heading = document.querySelector('h1');
    const image = document.querySelector('meta[property="og:image"]');
    return {title: heading ? heading.textContent : '', image: image ? image.content : '', specs};
}"""


def prepare(page, pincode):
    page.goto(HOME, wait_until="domcontentloaded", timeout=45000)
    return True


def search(page, query, limit, kind="laptop"):
    data = fetch_json(page, f"{API}/searchservices/v1/search?currentPage=0&query={quote(query)}%3Arelevance"
                            f"&fields=FULL&channel=WEB&channelCode={DEFAULT_PINCODE}&spellOpt=DEFAULT", LABEL)
    hits = []
    for item in data.get("products") or []:
        title = clean(item.get("name"))
        if str(item.get("categoryL1")) != CATEGORIES[kind] or not item.get("url") or not relevant(title, query):
            continue
        # An all-in-one's title leads with its screen ("27 Inch Full HD IPS Display ...") rather than saying so.
        if kind == "tower" and (not is_tower(title) or re.search(r"\binch\b.*\bdisplay\b", title, re.I)):
            continue
        hits.append(Hit(url=HOME + item["url"], title=title))
        if len(hits) >= limit:
            break
    return hits


def _delivery(page, code, pincode):
    """Ask Croma's stock system whether home delivery to this PIN code is possible."""
    line = {
        "fulfillmentType": "HDEL", "mch": "", "itemID": code, "lineId": "1", "categoryType": "nonMobile",
        "reqEndDate": "2500-01-01", "reqStartDate": "", "requiredQty": "1", "extn": {"widerStoreFlag": "N"},
        "shipToAddress": {"company": "", "country": "", "city": "", "mobilePhone": "", "state": "", "zipCode": pincode,
                          "extn": {"irlAddressLine1": "", "irlAddressLine2": ""}},
    }
    body = {"promise": {"allocationRuleID": "SYSTEM", "checkInventory": "Y", "organizationCode": "CROMA",
                        "sourcingClassification": "EC", "promiseLines": {"promiseLine": [line]}}}
    try:
        data = fetch_json(page, f"{API}/inventory/oms/v2/tms/details-pwa/", LABEL, method="POST",
                          headers={"oms-apim-subscription-key": DELIVERY_KEY}, body=body)
        promise = data["promise"]
    except (StoreError, KeyError, TypeError):
        return None, None
    try:
        for found in promise["suggestedOption"]["option"]["promiseLines"]["promiseLine"]:
            if found.get("fulfillmentType") == "HDEL":
                dates = [a.get("deliveryDate") for a in found["assignments"]["assignment"] if a.get("deliveryDate")]
                return True, f"Delivery by {dates[0][:10]}" if dates else None
    except (KeyError, TypeError):
        pass
    return False, None


def read(page, url, pincode):
    code = re.search(r"/p/(\d+)", url)
    if not code:
        raise StoreError("That is not a Croma product page.")
    code = code.group(1)
    open_product(page, url, LABEL)
    try:
        page.wait_for_selector("h1", timeout=15000)
        # The spec sheet is drawn a moment after the heading.
        page.wait_for_selector("ul.cp-specification-spec-info", state="attached", timeout=8000)
    except Exception:
        pass
    data = page.evaluate(_PRODUCT_JS)
    title = clean(data["title"]) or clean(ld_product(page).get("name"))
    if not title:
        raise StoreError("Croma's product page did not load as expected.")

    price = mrp = None
    try:
        prices = fetch_json(page, f"{API}/pricing-services/v2/price/national?itemIds={code}&pincode={pincode or DEFAULT_PINCODE}",
                            LABEL, headers={"channel": "EC"})
        entry = (prices.get("pricelist") or [{}])[0]
        price, mrp = rupees(entry.get("sellingPriceValue")), rupees(entry.get("mrpValue"))
    except StoreError:
        pass
    deliverable, note = _delivery(page, code, pincode) if pincode else (None, None)
    return Product(
        url=url, title=title, image_url=data["image"] or None, price_inr=price,
        mrp_inr=mrp if mrp and price and mrp >= price else None,
        in_stock=bool(price),
        raw_specs={clean(k): clean(v) for k, v in data["specs"].items() if clean(k) and clean(v)},
        deliverable=deliverable, delivery_note=note,
    )
