"""The parts of the store modules that work without a browser, and the spec sheets the newer stores publish."""
from tracker import hunts, matching, search, specs
from tracker.stores import STORES, acer, apple, asus, sells, vijaysales
from werkzeug.datastructures import MultiDict

from tracker.stores.base import is_tower, ld_offer, relevant, without_brand

CROMA_TABLE = {
    "Brand": "Lenovo", "Model Series": "Legion 5 15IAX10", "Model Number": "83F000BSIN", "Product Weight": "1.9 Kg",
    "Display Size (In Inches)": "15.1 Inches", "Display Type": "OLED", "Screen Resolution": "2560 x 1600 pixels",
    "Refresh Rate": "165 Hz", "Processor Brand": "Intel", "Processor Type": "Core Ultra 9", "Processor Variant": "275HX",
    "Type of RAM": "DDR5", "RAM": "16GB",
}
LENOVO_TABLE = {
    "Brand": "Lenovo", "Part Number": "83F5CTO1WWIN2",
    "Processor": "Intel Core Ultra 9 275HX Processor (E-cores up to 4.60 GHz P-cores up to 5.40 GHz)",
    "Graphic Card": "NVIDIA GeForce RTX 5070 Ti Laptop GPU 12GB GDDR7", "Memory": "32 GB DDR5-6400MT/s (CSODIMM) - (2 x 16 GB)",
    "Storage": "1 TB SSD M.2 2280 PCIe Gen4 TLC",
    "Display": "40.64cms (16) WQXGA (2560 x 1600), OLED, Glare, Non-Touch, HDR 1000 True Black, 500 nits, 240Hz",
}
DELL_TABLE = {
    "Brand": "Dell", "Processor": "Intel® Core™ 9 processor 270H (24MB cache, 14 cores, 20 threads, up to 5.8 GHz)",
    "Graphics Card": "NVIDIA® GeForce RTX™ 5070, 8 GB GDDR7", "Memory": "16GB DDR5, 2x8GB, 5600MT/s",
    "Storage": "1TB M.2 PCIe NVMe Solid State Drive",
    "Display": "16.0-inch 16:10 2.5K(2560x1600) Anti-Glare Non-Touch 300nits WVA/IPS Display with ComfortView Plus",
}
RELIANCE_TABLE = {
    "Brand": "Lenovo", "Series": "Legion 5 Pro 16IRX10", "Screen Resolution": "2560 x 1600 px",
    "Screen Size (Diagonal)": "40.64 cm - 16 inch", "Graphic Card Memory": "8 GB", "Memory (RAM)": "32 GB",
    "Memory Details": "2x 16GB SO-DIMM DDR5-5600", "Processor": "Intel Core i7-14650HX, 16C (8P + 8E) / 24T",
    "Storage Type Capacity": "1TB SSD M.2 2242 PCIe 4.0x4 NVMe", "Storage Capacity": "1 TB",
    "Graphics Card Model": "NVIDIA GeForce RTX 5060 8GB GDDR7, Boost Clock 2497MHz, TGP 115W",
}


def test_croma_sheet():
    found = specs.normalise("Lenovo Legion 5 15IAX10 Intel Core Ultra 9 Gaming Laptop (16GB, 512GB SSD, 8GB Graphics, "
                            "NVIDIA GeForce RTX 5070)", CROMA_TABLE)
    assert (found["cpu_family"], found["cpu_model"], found["gpu_model"]) == ("Core Ultra 9", "275HX", "RTX 5070")
    assert (found["ram_gb"], found["ram_type"], found["storage_gb"], found["weight_kg"]) == (16, "DDR5", 512, 1.9)
    assert (found["screen_in"], found["resolution"], found["refresh_hz"], found["panel"]) == (15.1, "QHD+", 165, "OLED")
    assert (found["series"], found["model_number"]) == ("Legion 5 15IAX10", "83F000BSIN")


def test_a_brand_store_sheet_with_one_row_per_part():
    found = specs.normalise("Lenovo Legion Pro 7 Intel, 40.64cms - Core Ultra (customisable, 83F5CTO1WWIN2)", LENOVO_TABLE)
    assert (found["cpu_family"], found["cpu_model"], found["gpu_model"], found["vram_gb"]) == ("Core Ultra 9", "275HX", "RTX 5070 Ti", 12)
    assert (found["ram_gb"], found["ram_type"], found["storage_gb"], found["storage_interface"]) == (32, "DDR5", 1024, "NVMe PCIe 4.0")
    assert (found["screen_in"], found["resolution"], found["refresh_hz"], found["panel"]) == (16.0, "QHD+", 240, "OLED")


def test_trademark_signs_do_not_hide_a_model():
    found = specs.normalise("Dell Alienware 16 Aurora Gaming Laptop (OAN1625001301MINO)", DELL_TABLE)
    assert (found["cpu_brand"], found["cpu_family"], found["cpu_model"]) == ("Intel", "Core 9", "270H")
    assert (found["gpu_brand"], found["gpu_model"], found["vram_gb"]) == ("NVIDIA", "RTX 5070", 8)
    assert (found["ram_gb"], found["storage_gb"], found["resolution"], found["panel"]) == (16, 1024, "QHD+", "IPS")


def test_reliance_sheet():
    found = specs.normalise("Lenovo Legion 5 Pro 16IRX10 83NN0046IN Gaming Laptop", RELIANCE_TABLE)
    assert (found["cpu_family"], found["cpu_model"], found["gpu_model"], found["vram_gb"]) == ("Core i7", "14650HX", "RTX 5060", 8)
    assert (found["ram_gb"], found["ram_type"], found["storage_gb"], found["screen_in"]) == (32, "DDR5", 1024, 16.0)


def test_vijay_sales_description_becomes_a_spec_sheet():
    markup = ("<b>Processor:</b> Intel Core Ultra 7 255HX (20 Cores) – Flagship-level performance.<br>\r\n "
              "<b>Graphics:</b> NVIDIA GeForce RTX 5060 (8GB GDDR7, 115W TGP)<br><strong>Memory</strong>: 32GB DDR5-5600")
    sheet = vijaysales.description_specs(markup)
    assert sheet["Processor"].startswith("Intel Core Ultra 7 255HX") and sheet["Memory"] == "32GB DDR5-5600"
    found = specs.normalise("Lenovo Legion Pro 5 AI Powered Gaming Laptop", sheet)
    assert (found["cpu_model"], found["gpu_model"], found["vram_gb"], found["ram_gb"]) == ("255HX", "RTX 5060", 8, 32)


def test_unrelated_results_are_dropped():
    assert relevant("Lenovo Legion Pro 5 Gaming Laptop (RTX 5070)", "Lenovo Legion RTX 5080 laptop")
    assert not relevant("HP 15 Laptop Backpack", "Lenovo Legion RTX 5080 laptop")


def test_brand_stores_are_not_asked_for_their_own_name():
    assert without_brand("Dell Alienware RTX 5080 laptop", "Dell") == "Alienware RTX 5080"
    assert without_brand("ASUS laptop", "ASUS") == "laptop"


def test_a_brand_store_is_skipped_for_other_brands():
    assert sells(STORES["lenovo"], ["Lenovo", "ASUS"]) and sells(STORES["lenovo"], [])
    assert not sells(STORES["lenovo"], ["ASUS"])
    assert sells(STORES["croma"], ["ASUS"])


def test_a_brand_store_only_gets_phrases_for_its_brand():
    hunt = hunts.blank()
    hunt.update(brands=["Lenovo", "ASUS"], gpu_models=["RTX 5080"])
    assert search.queries(hunt) == ["Lenovo RTX 5080 laptop", "ASUS RTX 5080 laptop"]
    assert search.queries(hunt, ("ASUS",)) == ["ASUS RTX 5080 laptop"]
    hunt.update(brands=[])
    assert search.queries(hunt, ("Dell",)) == ["Dell RTX 5080 laptop"]


def test_every_store_follows_the_interface():
    for key, store in STORES.items():
        assert store.KEY == key and store.LABEL
        assert callable(store.prepare) and callable(store.search) and callable(store.read)


def test_names_made_from_page_addresses():
    assert asus._name("https://in.store.asus.com/gaming-laptop-rog-strix-g16-g614p.html?config=90NR0KW7-M00B60") == "ASUS ROG Strix G16 G614P"
    assert apple._name("https://www.apple.com/in/shop/buy-mac/macbook-pro/14-inch-silver-standard-display-apple-m5-chip-"
                       "10-core-cpu-10-core-gpu-16gb-memory-1tb-storage").startswith("Apple MacBook Pro 14 inch, silver")
    assert acer._phrase("https://store.acer.com/en-in/acer-predator-helios-neo-16-gaming-laptop-windows-11-home-"
                        "nvidia-geforce-rtx-5070-laptop-gpu") == "predator helios neo 16 rtx 5070"


def test_price_from_a_schema_block():
    block = {"offers": [{"price": 239900.00, "priceCurrency": "INR"}]}
    assert ld_offer(block) == (239900, True)
    assert ld_offer({"offers": {"price": "219990", "availability": "https://schema.org/OutOfStock"}}) == (219990, False)


def test_a_tower_hunt_searches_for_desktops_at_the_stores_that_sell_them():
    hunt = hunts.blank()
    hunt.update(kind="tower", brands=["Lenovo"], gpu_models=["RTX 5070"])
    assert search.queries(hunt) == ["Lenovo RTX 5070 desktop"]
    assert sells(STORES["amazon"], [], "tower") and sells(STORES["lenovo"], ["Lenovo"], "tower")
    assert not sells(STORES["asus"], ["ASUS"], "tower") and not sells(STORES["samsung"], [], "tower")
    assert not sells(STORES["lenovo"], ["Dell"], "tower")
    assert sells(STORES["asus"], ["ASUS"])  # laptops are unaffected


def test_all_in_ones_are_not_towers():
    assert is_tower("HP OMEN 16L Gaming Desktop TG03-0005in AMD Ryzen 5 8400F")
    assert not is_tower("HP 27-cr1026in 68.58 cm (27 inch) All-In-One Desktop")
    assert not is_tower("ASUS AiO V470 27 Inch Desktop") and not is_tower("Acer Aspire C27 All in One (AIO) Desktop")
    assert relevant("HP OMEN 16L Gaming Desktop (RTX 5060)", "HP OMEN desktop")


def test_a_tower_hunt_drops_screen_and_weight_filters_and_stores_without_towers():
    form = MultiDict([("name", "Tower"), ("kinds", "tower"), ("screen_min_in", "16"), ("weight_max_kg", "abc"), ("panels", "OLED"),
                      ("retailers", "amazon"), ("retailers", "asus"), ("retailers", "dell")])
    hunt, errors = hunts.parse_form(form)
    assert not errors
    assert (hunt["kind"], hunt["screen_min_in"], hunt["weight_max_kg"], hunt["panels"], hunt["retailers"]) == ("tower", None, None, [], ["amazon", "dell"])
    assert hunts.chips(hunt) == ["Tower PC"]
    _, errors = hunts.parse_form(MultiDict([("name", "Tower"), ("kinds", "tower"), ("retailers", "asus")]))
    assert "tower" in errors["retailers"]
    assert hunts.parse_form(MultiDict([("name", "Old form"), ("retailers", "asus")]))[0]["kind"] == "laptop"


def test_desktop_processors_and_graphics_cards_are_read():
    found = specs.normalise("HP OMEN 16L Gaming Desktop TG03-0012in Intel Core i7-14700F (32 GB RAM/NVIDIA GeForce RTX 5060 Ti 16GB "
                            "Graphics/1 TB SSD/Windows 11 Home)", {})
    assert (found["cpu_family"], found["cpu_model"], found["gpu_model"], found["ram_gb"], found["storage_gb"]) == ("Core i7", "14700F", "RTX 5060 Ti", 32, 1024)
    found = specs.normalise("CyberPowerPC Gaming Desktop PC, AMD Ryzen 7 9800X3D, Radeon RX 9070 XT 16GB, 32GB DDR5 RAM, 2TB NVMe SSD", {})
    assert (found["cpu_family"], found["cpu_model"], found["gpu_brand"], found["gpu_model"], found["ram_type"]) == ("Ryzen 7", "9800X3D", "AMD", "RX 9070 XT", "DDR5")
    assert specs.normalise("Lenovo Legion T5 - Intel Core Ultra 7 265KF, 16GB RAM, 1TB SSD, RTX 5070 12GB", {})["cpu_model"] == "265KF"
    hunt = hunts.blank()
    hunt.update(kind="tower", gpu_models=["RTX 5060 Ti"])
    listing = {"title": "t", "price_inr": 150000, "specs": specs.normalise("HP OMEN Desktop Ryzen 5 8400F RTX 5060 Ti 16GB", {})}
    assert matching.evaluate(hunt, listing)[0] == "match"
    listing["specs"] = specs.normalise("HP OMEN Desktop Ryzen 5 8400F RTX 5060 8GB", {})
    assert ("RTX 5060", "miss") in matching.evaluate(hunt, listing)[1]


def test_a_hunt_can_look_for_laptops_and_towers_together():
    form = MultiDict([("name", "Either"), ("kinds", "laptop"), ("kinds", "tower"), ("screen_min_in", "16"),
                      ("gpu_models", "RTX 5070"), ("retailers", "amazon"), ("retailers", "asus")])
    hunt, errors = hunts.parse_form(form)
    assert not errors
    # Both kinds keep the laptop filters and every store; each kind gets its own search phrases.
    assert (hunt["kind"], hunt["screen_min_in"], hunt["retailers"]) == ("both", 16, ["amazon", "asus"])
    assert hunts.kinds(hunt) == ["laptop", "tower"] and "Laptop or Tower PC" in hunts.chips(hunt)
    assert search.queries(hunt, kind="laptop") == ["RTX 5070 laptop"] and search.queries(hunt, kind="tower") == ["RTX 5070 desktop"]
    # A tower is not judged on the screen filter; a laptop still is.
    found = specs.normalise("HP OMEN 16L Gaming Desktop Ryzen 5 8400F RTX 5070 12GB", {})
    tower = {"title": "t", "price_inr": 150000, "specs": {**found, "kind": "tower"}}
    verdict, chips = matching.evaluate(hunt, tower)
    assert verdict == "match" and ("Tower PC", "plain") in chips and ("Screen?", "unknown") not in chips
    laptop = {"title": "t", "price_inr": 150000, "specs": {**found, "kind": "laptop"}}
    assert ("Screen?", "unknown") in matching.evaluate(hunt, laptop)[1]


def test_a_recheck_keeps_what_kind_a_listing_is():
    import json
    import sqlite3

    from tracker import listings
    from tracker.stores.base import Product

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE listings (id INTEGER PRIMARY KEY, title, image_url, model_number, specs, raw_specs, last_seen_at, "
                 "deliverable, delivery_note, delivery_pincode, gone_checks DEFAULT 0, gone_at)")
    conn.execute("CREATE TABLE price_points (id INTEGER PRIMARY KEY, listing_id, price_inr, mrp_inr, in_stock, checked_at, source)")
    conn.execute("INSERT INTO listings (id, specs) VALUES (1, ?)", (json.dumps({"kind": "tower", "ram_gb": 16}),))
    listings.record(conn, 1, Product(url="https://x.test/a", title="Tower", price_inr=150000), {"ram_gb": 32}, None)
    assert json.loads(conn.execute("SELECT specs FROM listings").fetchone()[0]) == {"ram_gb": 32, "kind": "tower"}


def test_mac_listings_give_their_chip_memory_and_display():
    found = specs.normalise("Apple 2025 MacBook Pro Laptop with M4 Max chip with 16‑core CPU and 40‑core GPU: 16.2-inch "
                            "Liquid Retina XDR Display, 48GB Unified Memory, 1TB SSD Storage; Space Black", {})
    assert (found["cpu_brand"], found["cpu_family"], found["cpu_cores"], found["gpu_cores"]) == ("Apple", "M4 Max", 16, 40)
    assert (found["gpu_brand"], found["gpu_model"]) == ("Apple", "Apple integrated")
    assert (found["ram_gb"], found["ram_type"], found["storage_gb"], found["storage_type"]) == (48, "Unified", 1024, "SSD")
    assert (found["screen_in"], found["resolution_px"], found["refresh_hz"], found["panel"]) == (16.2, "3456 × 2234", 120, "Mini-LED")
    # What a listing leaves out about a MacBook's screen is filled in from the line it belongs to.
    air = specs.normalise("Apple MacBook Air (13-inch, Apple M4 chip with 10-core CPU and 8-core GPU, 16GB Unified Memory, 256GB) - Midnight", {})
    assert (air["ram_gb"], air["storage_gb"], air["resolution"], air["refresh_hz"], air["panel"], air["weight_kg"]) == (16, 256, "QHD+", 60, "IPS", 1.24)
    # A chip newer than the catalog is still named.
    assert specs.normalise("Apple Mac mini, M9 Pro Chip, 12-core CPU, 32GB memory, 1TB storage", {})["cpu_family"] == "M9 Pro"
    # Other brands are left alone.
    other = specs.normalise("Lenovo Legion 5 Ryzen 7 RTX 5070 16GB RAM 1TB SSD 16 inch", {})
    assert (other["ram_type"], other["gpu_model"], other["cpu_cores"], other["refresh_hz"]) == (None, "RTX 5070", None, None)


def test_apples_store_sells_the_mac_mini_and_mac_studio_as_towers():
    assert sells(STORES["apple"], ["Apple"], "tower") and apple.LINES["tower"] == ("mac-mini", "mac-studio")
    assert apple._name("https://www.apple.com/in/shop/buy-mac/mac-mini/m6-chip-12-core-cpu-12-core-gpu-16gb-memory-"
                       "256gb-storage") == "Apple Mac mini M6 chip 12 core CPU 12 core GPU 16GB memory 256GB storage"
    hunt = hunts.blank()
    hunt.update(kind="tower", brands=["Apple"], cpu_family=["M6"], ram_min_gb=24, ram_type=["Unified"])
    found = specs.normalise("Apple Mac mini, M6 Chip, 12-core CPU, 12-core GPU, 32GB memory, 1TB storage",
                            {"Brand": "Apple", "Processor": "Apple M6", "RAM": "32GB", "Storage": "1TB SSD"})
    verdict, chips = matching.evaluate(hunt, {"title": "t", "price_inr": 207900, "specs": {**found, "kind": "tower"}})
    assert verdict == "match" and ("M6 12-core", "match") in chips and ("12-core GPU", "plain") in chips


def test_a_filter_with_several_choices_is_met_by_any_one_of_them():
    form = MultiDict([("name", "Mixed"), ("kinds", "laptop"), ("retailers", "amazon"),
                      ("cpu_brand", "AMD"), ("cpu_brand", "Intel"), ("cpu_family", "Ryzen 9"), ("cpu_family", "Core Ultra 9"),
                      ("cpu_model", "275HX, 9955HX3D"), ("gpu_brand", "NVIDIA"), ("gpu_brand", "AMD"),
                      ("ram_type", "DDR5"), ("ram_type", "LPDDR5X"), ("storage_type", "SSD"), ("series", "Legion Pro 7, ROG Strix")])
    hunt, errors = hunts.parse_form(form)
    assert not errors
    assert (hunt["cpu_brand"], hunt["cpu_family"], hunt["cpu_model"]) == (["AMD", "Intel"], ["Ryzen 9", "Core Ultra 9"], ["275HX", "9955HX3D"])
    assert (hunt["gpu_brand"], hunt["ram_type"], hunt["series"]) == (["NVIDIA", "AMD"], ["DDR5", "LPDDR5X"], ["Legion Pro 7", "ROG Strix"])
    assert {"Ryzen 9", "Core Ultra 9", "NVIDIA GPU", "AMD GPU", "LPDDR5X", "ROG Strix"} <= set(hunts.chips(hunt))

    def verdict(title):
        return matching.evaluate(hunt, {"title": title, "price_inr": 250000, "specs": specs.normalise(title, {})})[0]

    assert verdict("Lenovo Legion Pro 7 Intel Core Ultra 9 275HX RTX 5080 32GB DDR5 RAM 1TB SSD") == "match"
    assert verdict("ASUS ROG Strix G16 AMD Ryzen 9 9955HX3D Radeon RX 9070 XT 32GB DDR5 RAM 1TB SSD") == "match"
    # Neither of the two CPU families.
    assert verdict("Lenovo Legion Pro 7 Intel Core Ultra 7 255HX RTX 5080 32GB DDR5 RAM 1TB SSD") != "match"
    # A series that was not asked for.
    assert verdict("MSI Raider 18 Intel Core Ultra 9 275HX RTX 5080 32GB DDR5 RAM 1TB SSD") != "match"


def test_an_exact_chip_is_wanted_and_search_phrases_cover_each_choice():
    hunt = hunts.blank()
    hunt.update(brands=["Apple"], cpu_family=["M5", "M6"])
    title = "Apple MacBook Pro 14 inch M5 Pro chip 24GB memory 1TB storage"
    assert matching.evaluate(hunt, {"title": title, "price_inr": 1, "specs": specs.normalise(title, {})})[0] != "match"
    assert search.queries(hunt) == ["Apple M5 laptop", "Apple M6 laptop"]
    hunt.update(brands=["Lenovo", "ASUS"], series=[], cpu_family=["Ryzen 9", "Core Ultra 9"], gpu_models=["RTX 5080", "RTX 5070 Ti"])
    assert search.queries(hunt) == ["Lenovo RTX 5080 laptop", "Lenovo RTX 5070 Ti laptop", "ASUS RTX 5080 laptop", "ASUS RTX 5070 Ti laptop"]


def test_a_hunt_saved_with_single_values_still_opens():
    assert hunts.as_list("Intel") == ["Intel"] and hunts.as_list(None) == [] and hunts.as_list('["AMD", "Intel"]') == ["AMD", "Intel"]
    old = {field: None for field in hunts.COLUMNS}
    old.update(name="Old", brands="[]", gpu_models='["RTX 5080"]', panels="[]", retailers='["amazon"]', cpu_brand="Intel", ram_type="DDR5")
    hunt = hunts._decode(old)
    assert (hunt["cpu_brand"], hunt["ram_type"], hunt["series"], hunt["gpu_models"]) == (["Intel"], ["DDR5"], [], ["RTX 5080"])
    assert "Intel CPU" in hunts.chips(hunt)
