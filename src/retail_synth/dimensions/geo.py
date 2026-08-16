"""dim_region, dim_dc, dim_store.

Region grain doubles as the weather grain (one weather series per region),
since every region groups multiple stores and no store spans regions.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..config import Config
from ..io_utils import write_table

# Small deterministic city pools per region so store names/cities look real
# without pulling in locale-specific Faker providers for every country.
_CITY_POOLS = {
    "ONT": ["Toronto", "Ottawa", "Mississauga", "Hamilton", "London (ON)", "Kitchener", "Windsor", "Barrie", "Oshawa"],
    "WCA": ["Vancouver", "Calgary", "Edmonton", "Victoria", "Kelowna", "Surrey", "Burnaby", "Richmond (BC)"],
    "QCA": ["Montreal", "Quebec City", "Halifax", "Laval", "Gatineau"],
    "USNE": ["New York", "Boston", "Philadelphia", "Washington DC", "Pittsburgh", "Newark", "Stamford", "Princeton",
             "Hartford", "Providence", "Albany", "Buffalo", "Rochester", "Baltimore", "Jersey City", "White Plains",
             "New Haven", "Portland (ME)", "Burlington (VT)", "Syracuse", "Trenton", "Wilmington"],
    "USMW": ["Chicago", "Detroit", "Minneapolis", "Columbus", "Indianapolis", "Milwaukee", "Kansas City",
             "St. Louis", "Cincinnati", "Cleveland", "Madison", "Des Moines", "Omaha", "Grand Rapids",
             "Ann Arbor", "Naperville", "Rosemont", "South Bend", "Peoria", "Toledo", "Akron", "Dayton", "Fort Wayne"],
    "USSO": ["Dallas", "Houston", "Atlanta", "Miami", "Austin", "Charlotte", "Nashville", "Orlando",
             "New Orleans", "Tampa", "Memphis", "Raleigh", "Richmond (VA)", "San Antonio", "Fort Worth",
             "Jacksonville", "Birmingham", "Louisville", "Oklahoma City", "Tulsa", "Charleston", "Savannah", "Naples (FL)"],
    "USWE": ["Los Angeles", "San Francisco", "Seattle", "San Diego", "Las Vegas", "Phoenix", "Denver",
             "Portland (OR)", "Sacramento", "Salt Lake City", "San Jose", "Scottsdale", "Newport Beach",
             "Beverly Hills", "Palo Alto", "Bellevue", "Boise", "Tucson", "Costa Mesa", "Santa Barbara",
             "Aspen", "Honolulu"],
    "JPN": ["Tokyo", "Osaka", "Yokohama", "Nagoya", "Fukuoka", "Sapporo", "Kobe", "Kyoto", "Sendai",
            "Hiroshima", "Kawasaki", "Saitama", "Chiba", "Kitakyushu", "Nagasaki", "Nara", "Yokosuka", "Utsunomiya"],
    "CNE": ["Shanghai", "Hangzhou", "Nanjing", "Suzhou", "Ningbo", "Qingdao", "Jinan", "Wuxi", "Hefei", "Wenzhou",
            "Xuzhou", "Nantong"],
    "CNW": ["Beijing", "Shenzhen", "Guangzhou", "Chengdu", "Chongqing", "Xian", "Wuhan", "Kunming", "Tianjin", "Xiamen"],
    "APC": ["Singapore", "Hong Kong", "Seoul", "Bangkok", "Taipei", "Kuala Lumpur", "Manila", "Sydney"],
    "UKI": ["London", "Manchester", "Edinburgh", "Birmingham (UK)", "Glasgow", "Leeds", "Bristol", "Dublin",
            "Liverpool", "Cambridge", "Oxford", "Bath", "Belfast", "Cardiff", "Newcastle", "Nottingham",
            "Cork", "Aberdeen", "Brighton", "Reading"],
    "FRA": ["Paris", "Lyon", "Marseille", "Nice", "Bordeaux", "Toulouse", "Strasbourg", "Cannes", "Lille",
            "Nantes", "Aix-en-Provence", "Rennes", "Montpellier", "Deauville", "Grenoble", "Annecy"],
    "DAC": ["Munich", "Berlin", "Frankfurt", "Hamburg", "Dusseldorf", "Cologne", "Stuttgart", "Zurich",
            "Vienna", "Geneva", "Baden-Baden", "Nuremberg", "Salzburg", "Basel", "Hanover", "Leipzig",
            "Bonn", "Graz", "Lucerne", "Dresden", "Mannheim", "Wiesbaden"],
    "ITA": ["Milan", "Rome", "Florence", "Venice", "Turin", "Naples", "Bologna", "Verona", "Bari",
            "Portofino", "Capri", "Como", "Palermo", "Genoa", "Padua", "Bergamo", "Parma", "Rimini"],
    "NOR": ["Stockholm", "Copenhagen", "Oslo", "Helsinki", "Gothenburg", "Aarhus", "Bergen", "Malmo",
            "Reykjavik", "Uppsala", "Trondheim", "Turku", "Odense", "Vasteras"],
    "BLX": ["Amsterdam", "Brussels", "Madrid", "Barcelona", "Rotterdam", "Antwerp", "The Hague", "Lisbon",
            "Porto", "Valencia", "Ghent", "Seville", "Bilbao", "Utrecht", "Eindhoven", "Marbella", "Ibiza Town",
            "Malaga"],
    "MEA": ["Dubai", "Abu Dhabi", "Doha", "Riyadh", "Kuwait City", "Manama", "Jeddah", "Muscat",
            "Beirut", "Amman", "Cairo", "Casablanca"],
}

_COUNTRY_CENTROID = {
    "CA": (56.1, -106.3), "US": (39.8, -98.6), "JP": (36.2, 138.3), "CN": (35.9, 104.2),
    "SG": (1.35, 103.8), "GB": (55.4, -3.4), "FR": (46.6, 2.2), "DE": (51.2, 10.4),
    "IT": (41.9, 12.6), "SE": (60.1, 18.6), "NL": (52.1, 5.3), "AE": (23.4, 53.8),
}

_TIER_ORDER = ["Flagship", "A", "B", "C"]
_TIER_SQM = {"Flagship": (800, 1500), "A": (400, 800), "B": (200, 400), "C": (100, 200)}


def _sample_tier(rng: np.random.Generator, n: int) -> np.ndarray:
    return rng.choice(_TIER_ORDER, size=n, p=[0.05, 0.25, 0.45, 0.25])


def build_dim_dc(cfg: Config) -> pd.DataFrame:
    df = pd.DataFrame(cfg.dcs).rename(columns={"name": "dc_name"})
    write_table(cfg, "dim_dc", df)
    return df


def build_dim_region(cfg: Config) -> pd.DataFrame:
    df = pd.DataFrame(cfg.regions).rename(columns={"name": "region_name", "dc": "primary_dc_id"})
    df["weather_region_id"] = "WR-" + df["region_code"]
    df = df[["region_code", "region_name", "country", "currency", "climate_zone", "primary_dc_id", "weather_region_id"]]
    df = df.rename(columns={"currency": "currency_code"})
    write_table(cfg, "dim_region", df)
    return df


def build_dim_store(cfg: Config, dim_region: pd.DataFrame) -> pd.DataFrame:
    rng = cfg.rng("stores")
    hero_ids = {h["store_id"] for h in cfg.hero_stores}
    rows: list[dict] = []

    for h in cfg.hero_stores:
        region_code = h["region_code"]
        region = next(r for r in cfg.regions if r["region_code"] == region_code)
        rows.append(_make_store_row(cfg, rng, h["store_id"], h["name"], h["city"], region, h["tier"]))

    # Track how many hero stores each region already used, to size the fill.
    hero_count_by_region: dict[str, int] = {}
    for h in cfg.hero_stores:
        hero_count_by_region[h["region_code"]] = hero_count_by_region.get(h["region_code"], 0) + 1

    for region in cfg.regions:
        region_code = region["region_code"]
        target = cfg.region_store_count(region)
        already = hero_count_by_region.get(region_code, 0)
        n_fill = max(target - already, 0)
        if n_fill == 0:
            continue
        cities = _CITY_POOLS[region_code]
        seq = 1
        for _ in range(n_fill):
            city = cities[rng.integers(0, len(cities))]
            store_id = f"ST-{region_code}-{seq:02d}"
            while store_id in hero_ids or any(r["store_id"] == store_id for r in rows):
                seq += 1
                store_id = f"ST-{region_code}-{seq:02d}"
            tier = _sample_tier(rng, 1)[0]
            name = f"{city} Store {seq}"
            rows.append(_make_store_row(cfg, rng, store_id, name, city.split(" (")[0], region, tier))
            seq += 1

    df = pd.DataFrame(rows)
    write_table(cfg, "dim_store", df)
    return df


def _make_store_row(cfg: Config, rng: np.random.Generator, store_id: str, name: str, city: str,
                     region: dict, tier: str) -> dict:
    lat0, lon0 = _COUNTRY_CENTROID.get(region["country"], (20.0, 0.0))
    lat = lat0 + rng.normal(0, 3.5)
    lon = lon0 + rng.normal(0, 5.0)
    sqm_lo, sqm_hi = _TIER_SQM[tier]
    open_date = dt.date(2005, 1, 1) + dt.timedelta(days=int(rng.integers(0, (cfg.fiscal_start_date - dt.date(2005, 1, 1)).days)))
    return {
        "store_id": store_id,
        "store_name": name,
        "region_code": region["region_code"],
        "country": region["country"],
        "city": city,
        "climate_zone": region["climate_zone"],
        "store_tier": tier,
        "square_meters": int(rng.integers(sqm_lo, sqm_hi)),
        "lat": round(float(lat), 4),
        "long": round(float(lon), 4),
        "open_date": open_date,
        "primary_dc_id": region["dc"],
        "currency_code": region["currency"],
    }
