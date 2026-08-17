"""Terminal-only sanity check for the shopping agent -- no webapp/browser
involved, just the tool-calling agent directly against the shared catalog:

    python scripts/shopping_agent_chat.py

For the actual demo experience (chat embedded on the storefront, products
opening automatically as the agent discusses them), run
scripts/run_webapp.py and use the chat panel on the page instead.

Requires:
  - AZURE_AI_ENDPOINT, AZURE_AI_DEPLOYMENT (and optionally AZURE_AI_API_VERSION)
    set in .env (copy .env.example and fill in your Foundry resource's values)
  - `az login` already run in this environment (auth via AzureCliCredential)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# The model sometimes uses characters (en-dashes, curly quotes, non-breaking
# hyphens) that Windows consoles' default cp1252 codepage can't encode --
# reconfigure stdout to UTF-8 so those don't crash the chat loop.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main() -> None:
    load_dotenv()

    from agent_framework import Agent

    from agentic_commerce.agent import build_client, build_default_options, build_instructions
    from agentic_commerce.shared_session import shop

    agent = Agent(
        client=build_client(),
        name="ShoppingAssistant",
        instructions=build_instructions(),
        default_options=build_default_options(),
        tools=[
            shop.search_products,
            shop.get_style_details,
            shop.check_availability,
            shop.get_complementary_items,
            shop.add_to_cart,
            shop.view_cart,
            shop.checkout,
        ],
    )
    chat_session = agent.create_session()

    print("Shopping assistant ready. Type 'exit' to quit.\n")
    while True:
        user_msg = input("You: ").strip()
        if user_msg.lower() in {"exit", "quit"}:
            break
        if not user_msg:
            continue
        response = await agent.run(user_msg, session=chat_session)
        print(f"\nAssistant: {response.text}\n")


if __name__ == "__main__":
    asyncio.run(main())
