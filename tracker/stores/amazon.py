"""Amazon.in: search results and product pages, read from the rendered page."""
import re
from urllib.parse import quote_plus

from .base import Hit, ListingGone, Product, StoreError, clean, rupees

KEY = "amazon"
LABEL = "Amazon"
HOME = "https://www.amazon.in"
# Amazon's "Laptops" and "Tower PCs" categories, to keep accessories (bags, RAM sticks, skins) out of the results.
NODES = {"laptop": "1375424031", "tower": "10384421031"}

_SEARCH_JS = """() => [...document.querySelectorAll('div[data-component-type="s-search-result"][data-asin]')].map((item) => {
    // Some result layouts put the brand in a heading of its own, above the title.
    const heading = [...item.querySelectorAll('h2')].sort((a, b) => b.textContent.length - a.textContent.length)[0];
    return {
        asin: item.dataset.asin,
        title: heading ? (heading.getAttribute('aria-label') || heading.textContent) : '',
        sponsored: !!item.querySelector('.puis-sponsored-label-text, .s-sponsored-label-info-icon, [aria-label="View Sponsored information or leave ad feedback"]'),
    };
})"""

_PRODUCT_JS = """() => {
    const text = (selector) => { const el = document.querySelector(selector); return el ? el.textContent : ''; };
    const first = (...selectors) => { for (const s of selectors) { const t = text(s).trim(); if (t) return t; } return ''; };
    const specs = {};
    const rows = '#productOverview_feature_div tr, #poExpander tr, table.prodDetTable tr, ' +
        '#productDetails_techSpec_section_1 tr, #productDetails_detailBullets_sections1 tr, #technicalSpecifications_section_1 tr';
    document.querySelectorAll(rows).forEach((row) => {
        const cells = row.querySelectorAll('th, td');
        if (cells.length >= 2 && !(cells[0].textContent.trim() in specs)) specs[cells[0].textContent.trim()] = cells[1].textContent.trim();
    });
    const image = document.querySelector('#landingImage, #imgTagWrapperId img');
    return {
        title: text('#productTitle'),
        price: first('#corePriceDisplay_desktop_feature_div .priceToPay', '#corePrice_feature_div .a-price .a-offscreen',
                     '#apex_desktop .priceToPay', '#apex_desktop .a-price .a-offscreen', '#price_inside_buybox'),
        mrp: first('#corePriceDisplay_desktop_feature_div .basisPrice .a-offscreen', '#apex_desktop .basisPrice .a-offscreen',
                   '#corePriceDisplay_desktop_feature_div .a-text-price .a-offscreen'),
        availability: text('#availability'),
        delivery: first('#mir-layout-DELIVERY_BLOCK', '#deliveryBlockMessage', '#ddmDeliveryMessage'),
        buyable: !!document.querySelector('#add-to-cart-button, #buy-now-button'),
        image: image ? (image.getAttribute('data-old-hires') || image.src) : '',
        specs,
    };
}"""


def _goto(page, url):
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as error:
        # Amazon reloads the page by itself after a location change, which cancels a navigation in flight.
        if "ERR_ABORTED" not in str(error):
            raise
        page.wait_for_timeout(2000)
        response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
    # Amazon sometimes shows a "continue shopping" interstitial before the real page.
    button = page.query_selector('form[action*="/errors/"] button, button:has-text("Continue shopping")')
    if button and not page.query_selector("#captchacharacters"):
        button.click()
        page.wait_for_load_state("domcontentloaded")
    if page.query_selector('#captchacharacters, form[action*="validateCaptcha"]'):
        raise StoreError("Amazon asked for a CAPTCHA (it thinks this is a bot). Try again later.")
    return response


def prepare(page, pincode):
    """Returns False when a PIN code was given but Amazon would not take it."""
    _goto(page, HOME)
    if not pincode:
        return True
    for _attempt in range(2):
        try:
            # The location box ignores clicks until the page's scripts have settled.
            page.wait_for_timeout(2000)
            page.click("#nav-global-location-popover-link", timeout=10000)
            page.wait_for_selector("#GLUXZipUpdateInput", state="visible", timeout=10000)
            page.fill("#GLUXZipUpdateInput", pincode)
            change = None
            for _click in range(4):  # Apply does nothing until its handler is attached, so press it until it answers
                page.wait_for_timeout(700)
                try:
                    with page.expect_response(lambda response: "address-change" in response.url, timeout=3500) as change:
                        page.click("#GLUXZipUpdate input[type=submit], #GLUXZipUpdate .a-button-input", timeout=3000)
                    break
                except Exception:
                    change = None
            if change is not None:
                # Amazon answered: either it took the PIN code or it is not a real one.
                return change.value.ok and f'"zipCode":"{pincode}"' in change.value.text()
        except Exception:  # delivery info is a nice-to-have; search still works without it
            pass
        _goto(page, HOME)
    return False


def search(page, query, limit, kind="laptop"):
    _goto(page, f"{HOME}/s?k={quote_plus(query)}&rh=n%3A{NODES[kind]}")
    try:
        page.wait_for_selector('div[data-component-type="s-search-result"], .s-no-outline, #noResultsTitle', timeout=15000)
    except Exception:
        pass
    hits, seen = [], set()
    for item in page.evaluate(_SEARCH_JS):
        asin = item["asin"]
        if not asin or asin in seen or item["sponsored"]:
            continue
        seen.add(asin)
        hits.append(Hit(url=f"{HOME}/dp/{asin}", title=clean(item["title"])))
        if len(hits) >= limit:
            break
    return hits


def _delivery(data, pincode):
    if not pincode:
        return None, None
    # "FREE delivery Tuesday, 13 October. Details Or fastest delivery ..." -> the first sentence.
    note = clean(re.split(r"\bDetails\b|\.\s*Order within", data["delivery"])[0])
    text = f"{data['delivery']} {data['availability']}".lower()
    if re.search(r"cannot be (shipped|delivered)|can.t be (shipped|delivered)|does not deliver|not deliverable|doesn.t deliver", text):
        return False, None
    if re.search(r"\bdelivery\b", note.lower()) and re.search(r"\d|today|tomorrow", note.lower()):
        return True, note.rstrip(" .")[:120]
    return None, None


def read(page, url, pincode):
    response = _goto(page, url)
    if response is not None and response.status in (404, 410):
        raise ListingGone(f"Amazon no longer has this product page (HTTP {response.status}).")
    try:
        page.wait_for_selector("#productTitle", timeout=15000)
    except Exception as error:
        raise StoreError("Amazon's product page did not load as expected.") from error
    data = page.evaluate(_PRODUCT_JS)
    availability = clean(data["availability"]).lower()
    price = rupees(data["price"])
    unavailable = bool(re.search(r"unavailable|out of stock", availability))
    deliverable, note = _delivery(data, pincode)
    return Product(
        url=url,
        title=clean(data["title"]),
        image_url=data["image"] or None,
        price_inr=price,
        mrp_inr=rupees(data["mrp"]),
        in_stock=bool(price) and not unavailable and data["buyable"],
        raw_specs={clean(k): clean(v) for k, v in data["specs"].items() if clean(k) and clean(v)},
        deliverable=deliverable,
        delivery_note=note,
    )
