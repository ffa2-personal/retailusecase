"""ShoppingSession: the tool layer for the agentic shopping demo.

Bound methods on this class are passed straight to a Microsoft Agent
Framework Agent as `tools=[...]` (the "class with multiple function tools"
pattern) -- `self` holds the cart and the one read-only DuckDB connection
this session uses for its lifetime. Never opens retail.duckdb read-write.

This module has no dependency on agent_framework itself -- it's plain
Python/DuckDB, independently testable without any LLM or Azure credentials.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import uuid
from pathlib import Path
from typing import Annotated

import duckdb
from pydantic import Field

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "warehouse" / "retail.duckdb"
ORDERS_LOG_PATH = REPO_ROOT / "data" / "agentic_commerce" / "orders.jsonl"

# Simulated "frequently styled together" pairing -- a stand-in for a real
# next-best-item recommender. Not derived from any behavioral data.
_COMPLEMENTARY_CATEGORIES = {
    "Outerwear": ["Knitwear", "Accessories"],
    "Knitwear": ["Tailoring", "Accessories"],
    "Tailoring": ["Footwear", "Accessories"],
    "Accessories": ["Outerwear", "Knitwear"],
    "Footwear": ["Tailoring", "Accessories"],
}

TAX_RATE = 0.08


class ShoppingSession:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.con = duckdb.connect(str(db_path), read_only=True)
        self.cart: list[dict] = []
        # The agent framework can run multiple tool calls concurrently within
        # one turn (e.g. "add to cart and check out"); duckdb connections
        # aren't safe for concurrent use, so all access to self.con is
        # serialized through this lock. Reentrant because add_to_cart calls
        # check_availability, which also acquires it.
        self._lock = threading.RLock()

    def close(self) -> None:
        self.con.close()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def search_products(
        self,
        category: Annotated[str | None, Field(
            description="One of Outerwear, Knitwear, Tailoring, Accessories, Footwear. Omit to search all categories.")] = None,
        gender: Annotated[str | None, Field(
            description="One of Mens, Womens, Unisex. Omit to search all.")] = None,
        min_warmth: Annotated[int | None, Field(
            description="Minimum warmth rating 1 (light) to 5 (heavy winter). Use for cold-weather asks.")] = None,
        max_price: Annotated[float | None, Field(description="Maximum price in USD.")] = None,
        color_family: Annotated[str | None, Field(
            description="One of Neutral, Blue, Brown, Green, Red, Pink. Use for 'understated'/'bold' style asks.")] = None,
        keyword: Annotated[str | None, Field(
            description="Free-text match against silhouette, material, or style name, e.g. 'parka' or 'cashmere'.")] = None,
        in_stock_only: Annotated[bool, Field(description="Only return styles with current on-hand inventory.")] = True,
        limit: Annotated[int, Field(description="Max number of styles to return.")] = 5,
    ) -> list[dict]:
        """Search the product catalog. Translate the customer's natural-language
        request into these structured filters yourself -- e.g. 'warm enough for
        Montreal' -> min_warmth=4, 'understated for work' -> color_family='Neutral'.
        Returns up to `limit` matching styles with id, name, category, silhouette,
        material, warmth rating, price range, and available colors. Call
        get_style_details on a style_id next to see exact sizes/colors/prices."""
        clauses = ["sku.active_flag"]
        params: list = []
        if category:
            clauses.append("sty.category = ?")
            params.append(category)
        if gender:
            clauses.append("(sty.gender = ? OR sty.gender = 'Unisex')")
            params.append(gender)
        if min_warmth is not None:
            clauses.append("sty.warmth_rating >= ?")
            params.append(min_warmth)
        if max_price is not None:
            clauses.append("sku.current_retail_price <= ?")
            params.append(max_price)
        if color_family:
            clauses.append("sku.color_family = ?")
            params.append(color_family)
        if keyword:
            clauses.append("(sty.silhouette ILIKE ? OR sty.material ILIKE ? OR sty.style_name ILIKE ?)")
            params.extend([f"%{keyword}%"] * 3)
        if in_stock_only:
            clauses.append("""EXISTS (
                SELECT 1 FROM gold.inventory_imbalance_signals sig
                WHERE sig.style_id = sty.style_id AND sig.on_hand_units > 0
            )""")

        sql = f"""
            SELECT sty.style_id, sty.style_name, sty.category, sty.silhouette, sty.material,
                   sty.gender, sty.warmth_rating,
                   MIN(sku.current_retail_price) AS min_price, MAX(sku.current_retail_price) AS max_price,
                   STRING_AGG(DISTINCT sku.color_name, ', ' ORDER BY sku.color_name) AS colors
            FROM silver.dim_sku sku
            JOIN silver.dim_style sty ON sty.style_id = sku.style_id
            WHERE {' AND '.join(clauses)}
            GROUP BY sty.style_id, sty.style_name, sty.category, sty.silhouette, sty.material, sty.gender, sty.warmth_rating
            ORDER BY sty.warmth_rating DESC, min_price
            LIMIT ?
        """
        params.append(limit)
        with self._lock:
            return self.con.execute(sql, params).df().to_dict(orient="records")

    def get_style_details(
        self,
        style_id: Annotated[str, Field(description="The style_id from a search_products result.")],
    ) -> dict:
        """Get every color/size combination for one style, with its exact sku_id,
        price, and current on-hand units -- use this to help the customer choose
        size and color, and to get the sku_id needed for add_to_cart."""
        with self._lock:
            style = self.con.execute(
                "SELECT style_id, style_name, category, silhouette, material, gender, warmth_rating "
                "FROM silver.dim_style WHERE style_id = ?", [style_id]
            ).df()
            if style.empty:
                return {"error": f"No style found with style_id={style_id}"}

            variants = self.con.execute("""
                SELECT sku.sku_id, sku.color_name, sku.size, sku.current_retail_price,
                       CAST(COALESCE(SUM(sig.on_hand_units), 0) AS BIGINT) AS on_hand_units
                FROM silver.dim_sku sku
                LEFT JOIN gold.inventory_imbalance_signals sig ON sig.sku_id = sku.sku_id
                WHERE sku.style_id = ? AND sku.active_flag
                GROUP BY sku.sku_id, sku.color_name, sku.size, sku.current_retail_price
                ORDER BY sku.color_name, CASE sku.size
                    WHEN 'XS' THEN 1 WHEN 'S' THEN 2 WHEN 'M' THEN 3
                    WHEN 'L' THEN 4 WHEN 'XL' THEN 5 WHEN 'XXL' THEN 6
                    WHEN 'One Size' THEN 7
                    -- Footwear uses numeric EU sizes; offset so they sort
                    -- numerically after the lettered range, not as strings.
                    ELSE 100 + TRY_CAST(sku.size AS INTEGER)
                END
            """, [style_id]).df()

        result = style.iloc[0].to_dict()
        result["variants"] = variants.to_dict(orient="records")
        return result

    def check_availability(
        self,
        sku_id: Annotated[str, Field(description="The exact sku_id (specific color+size) to check.")],
        region_code: Annotated[str | None, Field(description="Optional region code to scope the check, e.g. 'ONT', 'ITA'.")] = None,
    ) -> dict:
        """Check current on-hand inventory for one exact SKU, optionally scoped to a region."""
        clauses = ["sig.sku_id = ?"]
        params = [sku_id]
        if region_code:
            clauses.append("sig.region_code = ?")
            params.append(region_code)
        with self._lock:
            df = self.con.execute(f"""
                SELECT CAST(COALESCE(SUM(sig.on_hand_units), 0) AS BIGINT) AS total_on_hand,
                       COUNT(DISTINCT sig.location_id) AS n_locations
                FROM gold.inventory_imbalance_signals sig WHERE {' AND '.join(clauses)}
            """, params).df()
        row = df.iloc[0].to_dict()
        row["sku_id"] = sku_id
        row["region_code"] = region_code
        row["in_stock"] = row["total_on_hand"] > 0
        return row

    def get_complementary_items(
        self,
        style_id: Annotated[str, Field(description="The style_id to find a complementary item for.")],
        limit: Annotated[int, Field(description="Max number of suggestions.")] = 2,
    ) -> list[dict]:
        """SIMULATED upsell / next-best-item suggestion -- a category-pairing
        heuristic in a similar price tier, not a real behavioral recommender.
        Call this once after an item is added to cart, or just before checkout,
        and offer exactly one relevant suggestion to the customer."""
        with self._lock:
            base = self.con.execute(
                "SELECT category, gender, base_retail_price_usd FROM silver.dim_style WHERE style_id = ?", [style_id]
            ).df()
            if base.empty:
                return []
            category, gender, price = base.iloc[0][["category", "gender", "base_retail_price_usd"]]
            pair_categories = _COMPLEMENTARY_CATEGORIES.get(category, [])
            if not pair_categories:
                return []

            df = self.con.execute("""
                SELECT sty.style_id, sty.style_name, sty.category, sty.silhouette,
                       MIN(sku.current_retail_price) AS min_price,
                       STRING_AGG(DISTINCT sku.color_name, ', ' ORDER BY sku.color_name) AS colors
                FROM silver.dim_style sty
                JOIN silver.dim_sku sku ON sku.style_id = sty.style_id
                WHERE sty.category IN ({placeholders})
                  AND (sty.gender = ? OR sty.gender = 'Unisex')
                  AND sku.active_flag
                  AND EXISTS (SELECT 1 FROM gold.inventory_imbalance_signals sig
                              WHERE sig.style_id = sty.style_id AND sig.on_hand_units > 0)
                GROUP BY sty.style_id, sty.style_name, sty.category, sty.silhouette
                ORDER BY ABS(MIN(sku.current_retail_price) - ?) ASC
                LIMIT ?
            """.format(placeholders=",".join("?" * len(pair_categories))),
                [*pair_categories, gender, price, limit],
            ).df()
        return df.to_dict(orient="records")

    # ------------------------------------------------------------------
    # Cart / checkout
    # ------------------------------------------------------------------
    def add_to_cart(
        self,
        sku_id: Annotated[str, Field(description="The exact sku_id to add (from get_style_details).")],
        quantity: Annotated[int, Field(description="How many units to add.")] = 1,
    ) -> dict:
        """Add a specific SKU (exact color+size) to the cart. Confirm size and
        color with the customer before calling this."""
        with self._lock:
            row = self.con.execute("""
                SELECT sku.sku_id, sku.style_id, sty.style_name, sku.color_name, sku.size, sku.current_retail_price
                FROM silver.dim_sku sku JOIN silver.dim_style sty ON sty.style_id = sku.style_id
                WHERE sku.sku_id = ?
            """, [sku_id]).df()
            if row.empty:
                return {"error": f"No SKU found with sku_id={sku_id}"}

            item = row.iloc[0].to_dict()
            item["quantity"] = quantity
            item["line_total"] = round(item["current_retail_price"] * quantity, 2)
            self.cart.append(item)

            availability = self.check_availability(sku_id)

        warning = None
        if availability["total_on_hand"] < quantity:
            warning = f"Only {availability['total_on_hand']} units currently on hand across the network."

        return {"added": item, "cart_subtotal": self._subtotal(), "warning": warning}

    def view_cart(self) -> dict:
        """View the current cart contents and running subtotal."""
        return {"items": self.cart, "subtotal": self._subtotal()}

    def checkout(self) -> dict:
        """Complete a SIMULATED checkout for the current cart: computes the
        total (with estimated tax), generates an order confirmation, logs it
        locally, and clears the cart. No real payment or order system involved."""
        if not self.cart:
            return {"error": "Cart is empty -- nothing to check out."}

        subtotal = self._subtotal()
        tax = round(subtotal * TAX_RATE, 2)
        total = round(subtotal + tax, 2)
        order = {
            "order_id": f"ORD-{uuid.uuid4().hex[:8].upper()}",
            "placed_at": dt.datetime.now().isoformat(timespec="seconds"),
            "items": self.cart,
            "subtotal": subtotal,
            "tax": tax,
            "total": total,
        }

        ORDERS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(ORDERS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(order) + "\n")

        self.cart = []
        return order

    def _subtotal(self) -> float:
        return round(sum(item["line_total"] for item in self.cart), 2)
