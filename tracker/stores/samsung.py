"""Samsung India's own store (samsung.com/in): its laptop finder API lists every Galaxy Book with price and stock."""
import re

from .base import Hit, ListingGone, Product, StoreError, clean, fetch_json, rupees, without_brand

KEY = "samsung"
LABEL = "Samsung Store"
BRANDS = ("Samsung",)
SITE = "https://www.samsung.com"
HOME = f"{SITE}/in/computers/all-computers/"
# type=03010000 is Samsung's code for laptops.
FINDER_API = ("https://searchapi.samsung.com/v6/front/b2c/product/finder/newhybris?type=03010000&siteCode=in"
              "&start=1&num=60&sort=recommended&onlyFilterInfoYN=N&keySummaryYN=Y")


def prepare(page, pincode):
    page.goto(HOME, wait_until="domcontentloaded", timeout=45000)
    return True


def _models(page):
    data = fetch_json(page, FINDER_API, LABEL)
    try:
        families = data["response"]["resultData"]["productList"]
    except (KeyError, TypeError) as error:
        raise StoreError("Samsung's laptop list did not load as expected.") from error
    return [model for family in families or [] for model in family.get("modelList") or [] if model.get("pdpUrl")]


def _title(model):
    return clean(f"Samsung {model.get('displayName') or ''} ({model.get('modelCode') or ''})")


def search(page, query, limit, kind="laptop"):
    # The range is small enough to list whole; models closest to the phrase come first.
    words = re.findall(r"[a-z0-9]+", without_brand(query, "Samsung").lower())
    def closeness(model):
        text = re.sub(r"[^a-z0-9]", "", _title(model).lower())
        return -sum(word in text for word in words)
    return [Hit(url=SITE + model["pdpUrl"], title=_title(model)) for model in sorted(_models(page), key=closeness)[:limit]]


def read(page, url, pincode):
    path = url.replace(SITE, "").split("?")[0].rstrip("/")
    model = next((m for m in _models(page) if m["pdpUrl"].rstrip("/") == path), None)
    if not model:
        raise ListingGone("Samsung no longer lists this model.")
    specs = {"Brand": "Samsung", "Model Number": clean(model.get("modelCode"))}
    # The finder gives no spec sheet; the name and page address state the processor, memory and storage.
    chip = re.search(r"\bUltra\s+X?([3579])\b", model.get("displayName") or "")
    if chip:
        specs["Processor"] = f"Intel Core Ultra {chip.group(1)}"
    sizes = re.search(r"-(\d+)gb-(\d+)(gb|tb)-", path.rsplit("/", 1)[-1])
    if sizes:
        specs["RAM"] = f"{sizes.group(1)} GB"
        specs["Storage"] = f"{sizes.group(2)} {sizes.group(3).upper()} SSD"
    listed, offer = rupees(model.get("price")), rupees(model.get("promotionPrice"))
    price = offer or listed
    image = model.get("largeUrl") or model.get("thumbUrl")
    return Product(
        url=url, title=_title(model), image_url=(f"https:{image}" if image and image.startswith("//") else image) or None,
        price_inr=price, mrp_inr=listed if listed and price and listed > price else None,
        in_stock=bool(price) and model.get("stockStatusText") == "inStock", raw_specs=specs,
    )
