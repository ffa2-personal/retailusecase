"""dim_style, dim_sku."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..config import Config
from ..io_utils import write_table

_CATEGORY_ATTRS = {
    "Outerwear": dict(silhouettes=["Parka", "Bomber", "Trench Coat", "Puffer", "Overcoat", "Anorak"],
                       materials=["Wool", "Technical Shell", "Leather", "Cashmere Blend", "Nylon Shell"],
                       fill_types=["Down", "Synthetic", "None"], warmth_range=(3, 5), price_range=(600, 2200),
                       sizes=["XS", "S", "M", "L", "XL", "XXL"]),
    "Knitwear": dict(silhouettes=["Crewneck Sweater", "Cardigan", "Turtleneck", "Cable Knit", "Half-Zip"],
                      materials=["Merino Wool", "Cashmere", "Cotton Blend", "Alpaca"],
                      fill_types=["None"], warmth_range=(2, 4), price_range=(250, 900),
                      sizes=["XS", "S", "M", "L", "XL", "XXL"]),
    "Tailoring": dict(silhouettes=["Blazer", "Suit Jacket", "Trousers", "Waistcoat", "Overshirt"],
                       materials=["Wool Suiting", "Cotton Twill", "Linen Blend"],
                       fill_types=["None"], warmth_range=(1, 3), price_range=(400, 1800),
                       sizes=["XS", "S", "M", "L", "XL", "XXL"]),
    "Accessories": dict(silhouettes=["Scarf", "Gloves", "Beanie", "Belt", "Bag"],
                         materials=["Cashmere", "Leather", "Wool", "Silk"],
                         fill_types=["None"], warmth_range=(1, 3), price_range=(80, 600),
                         sizes=["One Size"]),
    "Footwear": dict(silhouettes=["Chelsea Boot", "Sneaker", "Loafer", "Derby", "Winter Boot"],
                      materials=["Leather", "Suede", "Technical Textile"],
                      fill_types=["None"], warmth_range=(1, 4), price_range=(350, 1200),
                      sizes=["39", "40", "41", "42", "43", "44", "45", "46"]),
}

_COLOR_POOL = ["Black", "Navy", "Camel", "Grey", "Ivory", "Olive", "Burgundy", "Charcoal", "Beige",
               "Forest Green", "Cognac", "Blush", "Rust", "Stone", "Midnight Blue"]
_COLOR_FAMILY = {
    "Black": "Neutral", "Navy": "Blue", "Camel": "Brown", "Grey": "Neutral", "Ivory": "Neutral",
    "Olive": "Green", "Burgundy": "Red", "Charcoal": "Neutral", "Beige": "Brown", "Forest Green": "Green",
    "Cognac": "Brown", "Blush": "Pink", "Rust": "Red", "Stone": "Neutral", "Midnight Blue": "Blue",
}
_GENDERS = ["Mens", "Womens", "Unisex"]
_GENDER_P = [0.42, 0.42, 0.16]

_DROP_START = {  # approximate first day of each seasonal fiscal drop
    "SS2023": dt.date(2023, 2, 6), "FW2023": dt.date(2023, 8, 7),
    "SS2024": dt.date(2024, 2, 5), "FW2024": dt.date(2024, 8, 5),
    "SS2025": dt.date(2025, 2, 3), "FW2025": dt.date(2025, 8, 4),
}


def _style_row(style_id: str, name: str, category: str, gender: str, launch_date: dt.date,
               rng: np.random.Generator, drop: str, warmth_override: int | None = None) -> dict:
    attrs = _CATEGORY_ATTRS[category]
    lo, hi = attrs["warmth_range"]
    warmth = warmth_override if warmth_override is not None else int(rng.integers(lo, hi + 1))
    price_lo, price_hi = attrs["price_range"]
    retail = round(float(rng.uniform(price_lo, price_hi)), 2)
    return {
        "style_id": style_id,
        "style_name": name,
        "category": category,
        "silhouette": attrs["silhouettes"][rng.integers(0, len(attrs["silhouettes"]))],
        "gender": gender,
        "material": attrs["materials"][rng.integers(0, len(attrs["materials"]))],
        "fill_type": attrs["fill_types"][rng.integers(0, len(attrs["fill_types"]))],
        "warmth_rating": warmth,
        "season_collection": drop,
        "launch_date": launch_date,
        "planned_exit_date": launch_date + dt.timedelta(weeks=int(rng.integers(30, 42))),
        "base_wholesale_cost": round(retail * 0.4, 2),
        "base_retail_price_usd": retail,
    }


def _skus_for_style(rng: np.random.Generator, style: dict, force_color: str | None,
                     force_size: str | None) -> list[dict]:
    attrs = _CATEGORY_ATTRS[style["category"]]
    n_colors = int(np.clip(rng.normal(3.8, 1.1), 2, 6))
    colors = list(rng.choice(_COLOR_POOL, size=n_colors, replace=False))
    if force_color and force_color not in colors:
        colors[0] = force_color

    sizes = attrs["sizes"]
    if style["category"] == "Accessories":
        sizes = sizes if rng.random() < 0.7 else ["S", "M", "L"]
    if force_size and force_size not in sizes:
        sizes = sizes + [force_size] if force_size not in sizes else sizes

    rows = []
    for color in colors:
        for size in sizes:
            price_jitter = rng.uniform(0.97, 1.05)
            sku_id = f"{style['style_id']}-{color[:3].upper()}-{size.replace(' ', '')}"
            rows.append({
                "sku_id": sku_id,
                "style_id": style["style_id"],
                "color_name": color,
                "color_family": _COLOR_FAMILY.get(color, "Neutral"),
                "size": size,
                "size_type": "OneSize" if size == "One Size" else ("Numeric" if size.isdigit() else "Alpha"),
                "current_retail_price": round(style["base_retail_price_usd"] * price_jitter, 2),
                "active_flag": True,
            })
    return rows


def build_products(cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = cfg.rng("products")
    styles: list[dict] = []
    skus: list[dict] = []

    # --- hero styles ---
    for h in cfg.hero_styles:
        launch = _DROP_START["FW2025"]
        style = _style_row(h["style_id"], h["name"], h["category"], h["gender"], launch, rng, "FW2025",
                            warmth_override=h["warmth_rating"])
        styles.append(style)
        skus.extend(_skus_for_style(rng, style, h["hero_color"], h["hero_size"]))

    # override Aurora Bomber launch date to match the viral-scenario trigger timeline
    for s in styles:
        if s["style_id"] == "STY-AUR-BOMBER":
            s["launch_date"] = dt.date(2025, 10, 6)
            s["season_collection"] = "FW2025"

    # --- capsule styles ---
    cap = cfg.capsule
    launch = dt.date.fromisoformat(cap["launch_date"])
    for i in range(1, cap["n_styles"] + 1):
        category = cap["categories"][(i - 1) % len(cap["categories"])]
        style_id = f"{cap['style_prefix']}-{i:02d}"
        gender = _GENDERS[rng.integers(0, len(_GENDERS))]
        style = _style_row(style_id, f"{cap['name_prefix']} {i:02d}", category, gender, launch, rng, "FW2025")
        styles.append(style)
        skus.extend(_skus_for_style(rng, style, None, None))

    # --- bulk catalog fill ---
    n_bulk = max(cfg.scale["n_styles"] - len(styles), 0)
    cats = list(cfg.category_mix.keys())
    cat_p = list(cfg.category_mix.values())
    drops = cfg.seasonal_drops
    for i in range(1, n_bulk + 1):
        category = cats[rng.choice(len(cats), p=cat_p)]
        drop = drops[rng.integers(0, len(drops))]
        launch = _DROP_START[drop]
        gender = rng.choice(_GENDERS, p=_GENDER_P)
        style_id = f"STY-{category[:3].upper()}-{i:04d}"
        attrs = _CATEGORY_ATTRS[category]
        style_name = f"{attrs['silhouettes'][rng.integers(0, len(attrs['silhouettes']))]} {i:04d}"
        style = _style_row(style_id, style_name, category, gender, launch, rng, drop)
        styles.append(style)
        skus.extend(_skus_for_style(rng, style, None, None))

    dim_style = pd.DataFrame(styles)
    dim_sku = pd.DataFrame(skus)

    as_of = cfg.as_of_date
    exit_map = dim_style.set_index("style_id")["planned_exit_date"]
    style_exit = dim_sku["style_id"].map(exit_map)
    dim_sku["active_flag"] = (style_exit + dt.timedelta(weeks=6)) >= as_of

    # Uncapped exit week (clearance included): unlike the assortment bridge's
    # assortment_end_week -- which is clipped to whatever week-range this run
    # actually generated -- this always reflects the style's true planned end,
    # so "remaining season weeks" calculations aren't distorted by a short
    # dev-mode generation window.
    clearance_exit = dim_style["planned_exit_date"] + dt.timedelta(weeks=6)
    delta_days = (pd.to_datetime(clearance_exit) - pd.Timestamp(cfg.fiscal_start_date)).dt.days
    dim_style["exit_week_uncapped"] = (delta_days // 7 + 1).clip(lower=1).astype("int32")

    write_table(cfg, "dim_style", dim_style)
    write_table(cfg, "dim_sku", dim_sku)
    return dim_style, dim_sku
