"""Keeps a persistent, stateful shopping Agent alive across Flask requests.

Flask's dev server is sync (request/response), but the Agent + AgentSession
need to persist across requests for conversation memory, and the underlying
async HTTP client can't safely be reused across ad-hoc asyncio.run() calls in
different event loops. So: one background thread runs one persistent asyncio
event loop, created lazily on the first chat message. Flask's sync /chat
route hands work to it via run_coroutine_threadsafe(...).result().
"""
from __future__ import annotations

import asyncio
import threading
from typing import Annotated

from pydantic import Field

from .agent import build_client, build_default_options, build_instructions
from .shared_session import shop

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_agent = None
_chat_session = None
_ready = threading.Event()
_init_error: str | None = None
_start_lock = threading.Lock()
_focused_style_id: str | None = None


def _tracked_get_style_details(
    style_id: Annotated[str, Field(description="The style_id from a search_products result.")],
) -> dict:
    """Get every color/size combination for one style, with its exact sku_id,
    price, and current on-hand units -- use this to help the customer choose
    size and color, and to get the sku_id needed for add_to_cart. Calling
    this also brings the product up on the customer's screen, so do it as
    soon as you're discussing a specific item."""
    global _focused_style_id
    _focused_style_id = style_id
    return shop.get_style_details(style_id)


async def _init_agent() -> None:
    global _agent, _chat_session, _init_error
    try:
        from agent_framework import Agent

        client = build_client()
        _agent = Agent(
            client=client,
            name="ShoppingAssistant",
            instructions=build_instructions(),
            default_options=build_default_options(),
            tools=[
                shop.search_products,
                _tracked_get_style_details,
                shop.check_availability,
                shop.get_complementary_items,
                shop.add_to_cart,
                shop.view_cart,
                shop.checkout,
            ],
        )
        _chat_session = _agent.create_session()
    except Exception as exc:  # noqa: BLE001 -- surface any init failure as a chat message, not a crash
        _init_error = f"Could not start the shopping assistant: {exc}"
    finally:
        _ready.set()


def _thread_main() -> None:
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_until_complete(_init_agent())
    _loop.run_forever()


def _ensure_started() -> None:
    global _thread
    with _start_lock:
        if _thread is None:
            _thread = threading.Thread(target=_thread_main, daemon=True)
            _thread.start()
    _ready.wait(timeout=30)


def send_chat_message(text: str) -> dict:
    """Runs one chat turn on the background event loop.

    Returns {"reply": str, "focused_style_id": str | None} -- the latter is
    set whenever this turn looked up a specific product's details, and drives
    the storefront redirecting to show it.
    """
    global _focused_style_id

    _ensure_started()
    if _init_error:
        return {"reply": _init_error, "focused_style_id": None}
    if _agent is None or _loop is None:
        return {
            "reply": "The shopping assistant is still starting up -- try again in a moment.",
            "focused_style_id": None,
        }

    _focused_style_id = None
    future = asyncio.run_coroutine_threadsafe(_agent.run(text, session=_chat_session), _loop)
    try:
        result = future.result(timeout=60)
        reply = result.text
    except Exception as exc:  # noqa: BLE001
        reply = f"Sorry, something went wrong: {exc}"

    return {"reply": reply, "focused_style_id": _focused_style_id}
