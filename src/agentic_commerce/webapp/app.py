"""Boreal storefront -- a minimal Flask webapp over the synthetic catalog,
with a shopping-agent chat panel embedded on every page.

All the actual data/cart logic lives in ShoppingSession (session.py), shared
between these routes and the chat agent's tools via shared_session.py -- an
item the agent adds to cart shows up on /cart too, and vice versa. This
module is deliberately plain server-rendered HTML (real <form>/<select>/
<button> elements, no JS): the chat form is a normal POST + redirect like
every other interaction here, which is also what makes "the storefront opens
whatever the agent is discussing" work -- the redirect target is chosen by
the chat route based on what the agent's tools just did.

Run with: python scripts/run_webapp.py
"""
from __future__ import annotations

import zlib

from flask import Flask, redirect, render_template, request, url_for

from .. import chat_backend
from ..shared_session import shop

BRAND_NAME = "Boreal"

app = Flask(__name__)

CATEGORIES = ["Outerwear", "Knitwear", "Tailoring", "Accessories", "Footwear"]
COLOR_FAMILIES = ["Neutral", "Blue", "Brown", "Green", "Red", "Pink"]
GENDERS = ["Mens", "Womens", "Unisex"]

# Every color_name in silver.dim_sku, mapped to a muted swatch color. The
# storefront has no product photography, so the swatch IS the product image --
# painting it in the garment's actual color (rather than an arbitrary palette
# index) is what makes the grid read as a real catalog.
COLOR_HEX = {
    "Beige": "#d6c8b0",
    "Black": "#16161a",
    "Blush": "#dcb8b4",
    "Burgundy": "#6b2233",
    "Camel": "#b8894f",
    "Charcoal": "#3a3a3d",
    "Cognac": "#9a5b2c",
    "Forest Green": "#2c4434",
    "Grey": "#9a9a99",
    "Ivory": "#f0ebe0",
    "Midnight Blue": "#1f2b45",
    "Navy": "#23324f",
    "Olive": "#6b6a42",
    "Rust": "#a44a28",
    "Stone": "#b3ab9d",
}
FALLBACK_HEX = "#b3ab9d"

chat_history: list[dict] = []


def color_hex(color_name: str | None) -> str:
    return COLOR_HEX.get((color_name or "").strip(), FALLBACK_HEX)


def hero_color(style_id: str, colors: str | None) -> str:
    """Pick which colorway represents a style in the grid.

    Not simply the first: search_products returns colors alphabetically, so
    taking [0] fills the grid with Beige/Black/Blush and every row looks the
    same. Keying off the style_id keeps each product's hero color stable
    across page loads while spreading the palette across the grid.
    """
    options = [c for c in (colors or "").split(", ") if c]
    if not options:
        return FALLBACK_HEX
    return color_hex(options[zlib.crc32(style_id.encode()) % len(options)])


@app.context_processor
def inject_globals():
    return {
        "brand_name": BRAND_NAME,
        "cart_count": len(shop.cart),
        "chat_history": chat_history,
        "color_hex": color_hex,
        "hero_color": hero_color,
    }


@app.route("/")
def home():
    filters = {
        "category": request.args.get("category") or None,
        "keyword": request.args.get("keyword") or None,
        "color_family": request.args.get("color_family") or None,
        "gender": request.args.get("gender") or None,
    }
    min_warmth = request.args.get("min_warmth")
    filters["min_warmth"] = min_warmth or None
    products = shop.search_products(
        category=filters["category"],
        keyword=filters["keyword"],
        color_family=filters["color_family"],
        gender=filters["gender"],
        min_warmth=int(min_warmth) if min_warmth else None,
        limit=24,
    )
    return render_template(
        "index.html",
        products=products,
        categories=CATEGORIES,
        color_families=COLOR_FAMILIES,
        genders=GENDERS,
        filters=filters,
    )


@app.route("/product/<style_id>")
def product_detail(style_id: str):
    details = shop.get_style_details(style_id)
    if "error" in details:
        return details["error"], 404

    variants = details["variants"]
    colors = sorted({v["color_name"] for v in variants})
    selected_color = request.args.get("color") or (colors[0] if colors else None)
    sizes_for_color = [v for v in variants if v["color_name"] == selected_color]

    complementary = shop.get_complementary_items(style_id, limit=2)
    return render_template(
        "product.html",
        style=details,
        colors=colors,
        selected_color=selected_color,
        sizes=sizes_for_color,
        complementary=complementary,
    )


@app.route("/cart/add", methods=["POST"])
def cart_add():
    sku_id = request.form["sku_id"]
    quantity = int(request.form.get("quantity", 1))
    shop.add_to_cart(sku_id, quantity)
    return redirect(url_for("cart_view"))


@app.route("/cart/remove", methods=["POST"])
def cart_remove():
    sku_id = request.form["sku_id"]
    for i, item in enumerate(shop.cart):
        if item["sku_id"] == sku_id:
            shop.cart.pop(i)
            break
    return redirect(url_for("cart_view"))


@app.route("/cart")
def cart_view():
    return render_template("cart.html", cart=shop.view_cart())


@app.route("/checkout")
def checkout_view():
    cart = shop.view_cart()
    if not cart["items"]:
        return redirect(url_for("cart_view"))
    return render_template("checkout.html", cart=cart)


@app.route("/checkout/confirm", methods=["POST"])
def checkout_confirm():
    order = shop.checkout()
    if "error" in order:
        return redirect(url_for("cart_view"))
    return render_template("order_confirmation.html", order=order)


@app.route("/chat", methods=["POST"])
def chat():
    message = request.form.get("message", "").strip()
    fallback = request.referrer or url_for("home")
    if not message:
        return redirect(fallback)

    chat_history.append({"role": "user", "text": message})
    result = chat_backend.send_chat_message(message)
    chat_history.append({"role": "assistant", "text": result["reply"]})

    if result["focused_style_id"]:
        return redirect(url_for("product_detail", style_id=result["focused_style_id"]))
    return redirect(fallback)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
