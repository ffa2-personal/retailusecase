"""Single shared ShoppingSession instance -- the source of truth for cart and
catalog state, used by both the webapp routes (src/agentic_commerce/webapp/app.py)
and the chat backend (src/agentic_commerce/chat_backend.py), so an item the
agent adds to cart shows up on /cart too, and vice versa.
"""
from __future__ import annotations

from .session import ShoppingSession

shop = ShoppingSession()
