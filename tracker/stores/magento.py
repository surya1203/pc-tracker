"""Shared reading code for brand stores built on Magento (Dell, Acer and ASUS in India).

They share a results page (product links with one class name) and a product page
(a schema.org block for name and price, and a "Tech Specs" table).
"""
from urllib.parse import quote_plus

from .base import Hit, Product, StoreError, clean, ld_offer, ld_product, open_product, relevant, rupees

_SEARCH_JS = """() => [...document.querySelectorAll('.product-item-info')].map((card) => {
    const link = card.querySelector('a.product-item-link[href], a.product-item-photo[href]');
    const name = card.querySelector('.product-item-name, .product-name');
    const image = card.querySelector('img[alt]');
    return {
        href: link ? link.href : '',
        title: (name && name.textContent.trim()) || (link && link.textContent.trim()) || (image ? image.alt : ''),
        text: card.textContent,
    };
})"""

_PRODUCT_JS = """() => {
    const text = (selector) => { const el = document.querySelector(selector); return el ? el.textContent.trim() : ''; };
    const specs = {};
    document.querySelectorAll('#product-attribute-specs-table tr, table.additional-attributes tr').forEach((row) => {
        const cells = row.querySelectorAll('th, td');
        if (cells.length >= 2 && !(cells[0].textContent.trim() in specs)) specs[cells[0].textContent.trim()] = cells[1].textContent.trim();
    });
    document.querySelectorAll('.tech-label').forEach((label) => {
        const value = label.parentElement.querySelector('.tech-value');
        const name = label.textContent.replace(/[:：]\\s*$/, '').trim();
        if (value && name && !(name in specs)) specs[name] = value.textContent.trim();
    });
    document.querySelectorAll('.ux-module-title').forEach((label) => {
        const value = label.parentElement.querySelector('.ux-module-content');
        const name = label.textContent.trim();
        if (value && name && !(name in specs)) specs[name] = value.textContent.trim();
    });
    const amount = (...selectors) => {
        for (const selector of selectors) {
            const el = document.querySelector(selector);
            if (el && Number(el.getAttribute('data-price-amount')) > 0) return el.getAttribute('data-price-amount');
        }
        return '';
    };
    const main = '.product-info-main ';
    const image = document.querySelector('meta[property="og:image"]');
    return {
        title: text('h1.page-title') || text('h1'),
        price: amount('.price-as-configured [data-price-amount]', main + '[data-price-type="finalPrice"]', main + '[data-price-type="minPrice"]',
                      '[data-price-type="finalPrice"]', '[data-price-type="minPrice"]'),
        mrp: amount(main + '[data-price-type="oldPrice"]', '[data-price-type="oldPrice"]'),
        buyable: !!document.querySelector('#product-addtocart-button, button.tocart'),
        unavailable: !!document.querySelector('.product-info-main .stock.unavailable'),
        image: image ? image.content : '',
        specs,
    };
}"""


def search(page, home, query, limit, label):
    page.goto(f"{home}/catalogsearch/result/?q={quote_plus(query)}&product_list_limit=36", wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_selector(".product-item-info, .message.notice", timeout=15000)
    except Exception:
        if "access denied" in page.title().lower():
            raise StoreError(f"{label} refused the request (it thinks this is a bot). Try again later.")
        return []
    hits, seen = [], set()
    for item in page.evaluate(_SEARCH_JS):
        url, title = item["href"].split("#")[0], clean(item["title"])
        if not url or url in seen or not relevant(f"{title} {item['text']}", query):
            continue
        seen.add(url)
        hits.append(Hit(url=url, title=title))
        if len(hits) >= limit:
            break
    return hits


def read(page, url, label, brand, settle_ms=0):
    open_product(page, url, label)
    try:
        page.wait_for_selector("h1", state="attached", timeout=20000)
    except Exception as error:
        raise StoreError(f"{label}'s product page did not load as expected.") from error
    if "access denied" in page.title().lower():
        raise StoreError(f"{label} refused the request (it thinks this is a bot). Try again later.")
    if settle_ms:  # a page that offers several configurations fills in the chosen one after loading
        page.wait_for_timeout(settle_ms)
    data = page.evaluate(_PRODUCT_JS)
    block = ld_product(page)
    title = clean(data["title"]) or clean(block.get("name"))
    if not title:
        raise StoreError(f"{label}'s product page did not load as expected.")
    listed_price, listed_stock = ld_offer(block)
    price = rupees(data["price"]) or listed_price
    mrp = rupees(data["mrp"])
    specs = {clean(k): clean(v) for k, v in data["specs"].items() if clean(k) and clean(v)}
    specs.setdefault("Brand", brand)
    image = block.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    return Product(
        url=url, title=title if brand.lower() in title.lower() else f"{brand} {title}",
        image_url=image or data["image"] or None, price_inr=price,
        mrp_inr=mrp if mrp and price and mrp >= price else None,
        in_stock=bool(price) and not data["unavailable"] and (data["buyable"] or listed_stock), raw_specs=specs,
    )
