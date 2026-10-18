"""Hunts: saved searches. Form parsing, storage and display text."""
import json
import re
from datetime import datetime, timedelta, timezone

from . import catalog
from .db import get_db, now

# Filters that take several choices; a listing passes when it fits any one of them.
# The seven after "retailers" held a single value before October 2026 and kept their column names.
LIST_FIELDS = ("brands", "gpu_models", "panels", "retailers",
               "series", "cpu_brand", "cpu_family", "cpu_model", "gpu_brand", "ram_type", "storage_type", "storage_interface")
FILTER_FIELDS = (
    "kind", "brands", "series",
    "cpu_brand", "cpu_family", "cpu_model",
    "gpu_brand", "gpu_models", "vram_min_gb",
    "ram_min_gb", "ram_type",
    "storage_type", "storage_interface", "storage_min_gb",
    "screen_min_in", "screen_max_in", "resolution_min", "refresh_min_hz", "panels",
    "weight_max_kg", "budget_max_inr", "retailers",
)
ALERT_FIELDS = ("alert_new_low", "alert_back_in_stock", "alert_target_inr", "alert_drop_pct", "alert_new_match")
COLUMNS = ("name",) + FILTER_FIELDS + ALERT_FIELDS

# Filters that only make sense for something with a screen you carry; a tower hunt leaves them unset.
LAPTOP_ONLY_FIELDS = ("screen_min_in", "screen_max_in", "resolution_min", "refresh_min_hz", "panels", "weight_max_kg")

# How long a deleted hunt stays restorable. The Undo toast shows for 10 seconds;
# the extra time covers a slow click.
UNDO_SECONDS = 10
PURGE_AFTER_SECONDS = 30


# ---------- display text ----------

def inr(amount):
    """189990 -> '₹1,89,990' (Indian digit grouping)."""
    digits = str(int(amount))
    if len(digits) > 3:
        head = re.sub(r"\B(?=(\d{2})+$)", ",", digits[:-3])
        digits = f"{head},{digits[-3:]}"
    return f"₹{digits}"


def num(value):
    """16.0 -> '16', 15.6 -> '15.6'."""
    return str(int(value)) if float(value).is_integer() else str(value)


def size(gb):
    """1024 -> '1 TB', 512 -> '512 GB'."""
    if gb >= 1024 and gb % 1024 == 0:
        return f"{gb // 1024} TB"
    return f"{gb} GB"


def kinds(hunt):
    """What a hunt looks for: ['laptop'], ['tower'] or both. Hunts saved before towers were added are laptop hunts."""
    return ["laptop", "tower"] if hunt.get("kind") == "both" else [hunt.get("kind") or "laptop"]


def describe(hunt):
    """Every filter that is set, as (group, label, value, chips) rows in form order."""
    rows = []

    def add(group, label, value, chips):
        rows.append((group, label, value, chips))

    wanted = kinds(hunt)
    group = catalog.KIND_LABELS[wanted[0]] if len(wanted) == 1 else "Model"
    if wanted != ["laptop"]:
        text = " or ".join(catalog.KIND_LABELS[key] for key in wanted)
        add(group, "Type", text, [text])
    if hunt["brands"]:
        add(group, "Brand", ", ".join(hunt["brands"]), list(hunt["brands"]))
    if hunt["series"]:
        add(group, "Series", ", ".join(hunt["series"]), list(hunt["series"]))

    # A brand is implied by its families ("Ryzen 9" says AMD), so it only gets a chip when no family is picked.
    if hunt["cpu_brand"]:
        add("CPU", "CPU brand", ", ".join(hunt["cpu_brand"]),
            [] if hunt["cpu_family"] else [f"{brand} CPU" for brand in hunt["cpu_brand"]])
    if hunt["cpu_family"]:
        add("CPU", "CPU family", ", ".join(hunt["cpu_family"]), list(hunt["cpu_family"]))
    if hunt["cpu_model"]:
        add("CPU", "CPU model", ", ".join(hunt["cpu_model"]), list(hunt["cpu_model"]))

    if hunt["gpu_brand"]:
        add("GPU", "GPU brand", ", ".join(hunt["gpu_brand"]),
            [] if hunt["gpu_models"] else [f"{brand} GPU" for brand in hunt["gpu_brand"]])
    if hunt["gpu_models"]:
        add("GPU", "GPU model", ", ".join(hunt["gpu_models"]), list(hunt["gpu_models"]))
    if hunt["vram_min_gb"]:
        add("GPU", "VRAM", f"At least {hunt['vram_min_gb']} GB", [f"VRAM ≥ {hunt['vram_min_gb']} GB"])

    if hunt["ram_min_gb"]:
        add("Memory", "RAM size", f"At least {hunt['ram_min_gb']} GB", [f"RAM ≥ {hunt['ram_min_gb']} GB"])
    for group_name, field, label in (("Memory", "ram_type", "RAM type"), ("Storage", "storage_type", "Storage type"),
                                     ("Storage", "storage_interface", "Storage interface")):
        if hunt[field]:
            add(group_name, label, ", ".join(hunt[field]), list(hunt[field]))
    if hunt["storage_min_gb"]:
        text = size(hunt["storage_min_gb"])
        add("Storage", "Storage size", f"At least {text}", [f"Storage ≥ {text}"])

    low, high = hunt["screen_min_in"], hunt["screen_max_in"]
    if low and high:
        text = f'{num(low)}"' if low == high else f'{num(low)}" to {num(high)}"'
        add("Display", "Screen size", text, [text.replace(" to ", "–")])
    elif low:
        add("Display", "Screen size", f'At least {num(low)}"', [f'≥ {num(low)}"'])
    elif high:
        add("Display", "Screen size", f'Up to {num(high)}"', [f'≤ {num(high)}"'])
    if hunt["resolution_min"]:
        add("Display", "Resolution", f"At least {hunt['resolution_min']}", [f"≥ {hunt['resolution_min']}"])
    if hunt["refresh_min_hz"]:
        add("Display", "Refresh rate", f"At least {hunt['refresh_min_hz']} Hz", [f"≥ {hunt['refresh_min_hz']} Hz"])
    if hunt["panels"]:
        add("Display", "Panel", ", ".join(hunt["panels"]), list(hunt["panels"]))

    if hunt["weight_max_kg"]:
        add("Other", "Max weight", f"Up to {num(hunt['weight_max_kg'])} kg", [f"≤ {num(hunt['weight_max_kg'])} kg"])
    if hunt["budget_max_inr"]:
        add("Price", "Budget", f"Up to {inr(hunt['budget_max_inr'])}", [f"≤ {inr(hunt['budget_max_inr'])}"])
    return rows


def chips(hunt):
    return [chip for row in describe(hunt) for chip in row[3]]


def filter_groups(hunt):
    """describe() regrouped as [(group, [(label, value), ...]), ...]."""
    groups = {}
    for group, label, value, _ in describe(hunt):
        groups.setdefault(group, []).append((label, value))
    return list(groups.items())


# ---------- form parsing ----------

def blank():
    hunt = {field: None for field in COLUMNS}
    hunt.update({field: [] for field in LIST_FIELDS})
    hunt.update(name="", kind="laptop", retailers=list(catalog.DEFAULT_RETAILERS),
                alert_new_low=1, alert_back_in_stock=1, alert_new_match=1)
    return hunt


def _canonical(value, choices):
    """Match a typed value to a known choice regardless of case; otherwise keep it as typed."""
    for choice in choices:
        if choice.lower() == value.lower():
            return choice
    return value


def _text(form, field, choices=()):
    value = " ".join(form.get(field, "").split())
    return _canonical(value, choices) if value else None


def _multi(form, field, choices=()):
    """Every choice made for a filter: the ticked ones plus anything typed, comma separated."""
    typed = re.split(r"[,\n]", form.get(f"{field}_other", ""))
    ticked = [part for value in form.getlist(field) for part in re.split(r"[,\n]", value)]
    values = []
    for value in ticked + typed:
        value = " ".join(value.split())
        if value:
            value = _canonical(value, choices)
            if value not in values:
                values.append(value)
    return values


def _number(form, field, label, errors, whole=False, units=None):
    """Read a positive number, tolerating commas, a rupee sign and a trailing unit.

    `units` maps a typed unit to a multiplier, e.g. {"gb": 1, "tb": 1024}.
    """
    raw = form.get(field, "").strip()
    if not raw:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([a-z\"”]*)\.?", re.sub(r"[₹,]", "", raw).strip().lower())
    value = float(match.group(1)) if match else 0
    if match and units and match.group(2):
        if match.group(2) not in units:
            match = None
        else:
            value *= units[match.group(2)]
    if not match or value <= 0:
        errors[field] = f"{label}: “{raw}” is not a number we can use."
        return None
    if whole:
        if value != int(value):
            errors[field] = f"{label}: “{raw}” needs to be a whole number."
            return None
        return int(value)
    return value


def parse_form(form):
    """Turn the submitted filter form into typed values. Returns (hunt, errors)."""
    errors = {}
    gb = {"gb": 1, "g": 1, "tb": 1024, "t": 1024}
    all_families = [f for families in catalog.CPU_FAMILIES.values() for f in families]
    all_gpus = [m for models in catalog.GPU_MODELS.values() for m in models]

    hunt = blank()
    hunt["name"] = " ".join(form.get("name", "").split())
    if not hunt["name"]:
        errors["name"] = "Give the hunt a name."

    ticked = [key for key in catalog.KIND_LABELS if key in form.getlist("kinds")] or ["laptop"]
    hunt["kind"] = "both" if len(ticked) > 1 else ticked[0]
    hunt["brands"] = _multi(form, "brands", catalog.BRANDS)
    hunt["series"] = _multi(form, "series")
    hunt["cpu_brand"] = _multi(form, "cpu_brand", catalog.CPU_FAMILIES)
    hunt["cpu_family"] = _multi(form, "cpu_family", all_families)
    hunt["cpu_model"] = _multi(form, "cpu_model")
    hunt["gpu_brand"] = _multi(form, "gpu_brand", catalog.GPU_MODELS)
    hunt["gpu_models"] = _multi(form, "gpu_models", all_gpus)
    hunt["vram_min_gb"] = _number(form, "vram_min_gb", "VRAM", errors, whole=True, units=gb)
    hunt["ram_min_gb"] = _number(form, "ram_min_gb", "RAM size", errors, whole=True, units=gb)
    hunt["ram_type"] = _multi(form, "ram_type", catalog.RAM_TYPES)
    hunt["storage_type"] = _multi(form, "storage_type", catalog.STORAGE_TYPES)
    hunt["storage_interface"] = _multi(form, "storage_interface", catalog.STORAGE_INTERFACES)
    hunt["storage_min_gb"] = _number(form, "storage_min_gb", "Storage size", errors, whole=True, units=gb)
    hunt["screen_min_in"] = _number(form, "screen_min_in", "Screen size (min)", errors)
    hunt["screen_max_in"] = _number(form, "screen_max_in", "Screen size (max)", errors)
    hunt["refresh_min_hz"] = _number(form, "refresh_min_hz", "Refresh rate", errors, whole=True)
    hunt["panels"] = _multi(form, "panels", catalog.PANELS)
    hunt["weight_max_kg"] = _number(form, "weight_max_kg", "Max weight", errors)
    hunt["budget_max_inr"] = _number(form, "budget_max_inr", "Budget", errors, whole=True)

    if hunt["screen_min_in"] and hunt["screen_max_in"] and hunt["screen_min_in"] > hunt["screen_max_in"]:
        errors["screen_max_in"] = "Screen size: the maximum is smaller than the minimum."

    resolution = _text(form, "resolution_min", catalog.RESOLUTIONS)
    if resolution and resolution not in catalog.RESOLUTIONS:
        errors["resolution_min"] = f"Resolution: “{resolution}” is not one of the choices."
        resolution = None
    hunt["resolution_min"] = resolution

    hunt["retailers"] = [r for r in form.getlist("retailers") if r in catalog.RETAILER_LABELS]
    if hunt["kind"] == "tower":
        for field in LAPTOP_ONLY_FIELDS:
            hunt[field] = [] if field in LIST_FIELDS else None
            errors.pop(field, None)
        hunt["retailers"] = [r for r in hunt["retailers"] if r in catalog.TOWER_RETAILERS]
    if not hunt["retailers"]:
        errors["retailers"] = ("Pick at least one store that sells tower PCs." if hunt["kind"] == "tower"
                               else "Pick at least one store to search.")

    hunt["alert_new_low"] = int(bool(form.get("alert_new_low")))
    hunt["alert_back_in_stock"] = int(bool(form.get("alert_back_in_stock")))
    hunt["alert_new_match"] = int(bool(form.get("alert_new_match")))
    hunt["alert_target_inr"] = _number(form, "alert_target_inr", "Target price", errors, whole=True)
    hunt["alert_drop_pct"] = _number(form, "alert_drop_pct", "Price drop", errors, whole=True, units={"%": 1})
    if hunt["alert_drop_pct"] and hunt["alert_drop_pct"] >= 100:
        errors["alert_drop_pct"] = "Price drop: pick a percentage under 100."
    return hunt, errors


# ---------- storage ----------

def as_list(value):
    """A list filter as stored: a JSON list, or the single plain value a hunt saved before it could hold several."""
    if isinstance(value, (list, tuple)):
        return list(value)
    if not value:
        return []
    return json.loads(value) if value.lstrip().startswith("[") else [value]


def _encode(hunt):
    return [json.dumps(as_list(hunt[c])) if c in LIST_FIELDS else hunt[c] for c in COLUMNS]


def _decode(row):
    hunt = dict(row)
    for field in LIST_FIELDS:
        hunt[field] = as_list(hunt[field])
    return hunt


def insert(conn, hunt, status="active"):
    """Insert on a given connection without committing (also used by the legacy import)."""
    stamp = now()
    placeholders = ", ".join("?" for _ in COLUMNS)
    cursor = conn.execute(
        f"INSERT INTO hunts ({', '.join(COLUMNS)}, status, created_at, updated_at) "
        f"VALUES ({placeholders}, ?, ?, ?)",
        _encode(hunt) + [status, stamp, stamp],
    )
    return cursor.lastrowid


def create(hunt):
    db = get_db()
    hunt_id = insert(db, hunt)
    db.commit()
    return hunt_id


def update(hunt_id, hunt):
    db = get_db()
    assignments = ", ".join(f"{c} = ?" for c in COLUMNS)
    db.execute(
        f"UPDATE hunts SET {assignments}, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
        _encode(hunt) + [now(), hunt_id],
    )
    db.commit()


_SELECT = """
    SELECT h.*,
           (SELECT COUNT(*) FROM listings l WHERE l.hunt_id = h.id AND l.state != 'ignored') AS listing_count,
           (SELECT COUNT(*) FROM listings l WHERE l.hunt_id = h.id AND l.state = 'tracked' AND l.gone_at IS NULL) AS tracked_count
    FROM hunts h
    WHERE h.deleted_at IS NULL
"""


def all_hunts():
    rows = get_db().execute(_SELECT + " ORDER BY h.created_at DESC, h.id DESC").fetchall()
    return [_decode(row) for row in rows]


def get(hunt_id):
    row = get_db().execute(_SELECT + " AND h.id = ?", (hunt_id,)).fetchone()
    return _decode(row) if row else None


def set_status(hunt_id, status):
    db = get_db()
    db.execute(
        "UPDATE hunts SET status = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
        (status, now(), hunt_id),
    )
    db.commit()


def delete(hunt_id):
    """Hide the hunt now; purge() removes it for good once Undo has expired."""
    db = get_db()
    db.execute("UPDATE hunts SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL", (now(), hunt_id))
    db.commit()


def restore(hunt_id):
    db = get_db()
    restored = db.execute(
        "UPDATE hunts SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL", (hunt_id,)
    ).rowcount
    db.commit()
    return bool(restored)


def purge():
    """Permanently remove hunts (and, by cascade, their listings) whose Undo window has passed."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=PURGE_AFTER_SECONDS)
    db = get_db()
    db.execute(
        "DELETE FROM hunts WHERE deleted_at IS NOT NULL AND deleted_at < ?",
        (cutoff.isoformat(timespec="seconds"),),
    )
    db.commit()
