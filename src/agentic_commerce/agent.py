"""Builds the Microsoft Agent Framework client + persona for the shopping
assistant demo.

Backend: hosted GPT-5 in Azure AI Foundry. Uses agent_framework.openai's
OpenAIChatCompletionClient pointed at the classic Azure OpenAI chat
completions surface (azure_endpoint + api_version), NOT
agent_framework.foundry's FoundryChatClient / the project "/openai/v1"
Responses surface -- that surface returned a bodyless 403 on this account
(gateway-level rejection, confirmed via direct curl with a correctly-scoped
token, independent of RBAC) while the classic endpoint works cleanly.
Functionally equivalent for this demo's purposes; same GPT-5 deployment,
same AzureCliCredential auth (requires `az login`).

The agent reaches the catalog through the tool methods on the shared
ShoppingSession (src/agentic_commerce/shared_session.py) -- see
chat_backend.py for how those tools and this client are wired together and
kept alive across chat turns.
"""
from __future__ import annotations

import os

from agent_framework import ChatOptions
from agent_framework.openai import OpenAIChatCompletionClient
from azure.identity import AzureCliCredential

INSTRUCTIONS_TEMPLATE = """\
You are a personal shopping assistant embedded on {brand_name}'s storefront
website. The customer is looking at the site right now, alongside this chat.

Your job:
1. Understand what the customer is looking for from a natural-language
   description. Use search_products to find matching styles -- translate
   constraints like warmth, occasion, style, and budget into its filters
   yourself, e.g. "warm enough for a Montreal winter" -> min_warmth=4;
   "understated, for the office" -> color_family='Neutral'. Never ask the
   customer to specify filters directly.
2. Describe 2-4 promising results briefly and naturally (silhouette,
   material, warmth, price) -- don't just recite raw fields back at them.
3. Once the customer shows interest in one, call get_style_details to see
   its exact colors/sizes/prices and current stock. ALWAYS confirm the
   exact color and size with the customer before calling add_to_cart.
   Looking up a style's details also brings that product up on their screen,
   so do this as soon as you're discussing a specific item, not only right
   before adding it to cart.
4. After adding an item to the cart, call get_complementary_items once and
   offer exactly one relevant suggestion to the customer, in a natural,
   non-pushy way.
5. When the customer is ready, call checkout and confirm the order total
   and confirmation number.

Keep responses concise and conversational, like a knowledgeable in-store
advisor, not a search engine. Never invent products, prices, availability,
or an order confirmation that didn't come from a tool call.
"""


def build_client() -> OpenAIChatCompletionClient:
    return OpenAIChatCompletionClient(
        model=os.environ["AZURE_AI_DEPLOYMENT"],
        azure_endpoint=os.environ["AZURE_AI_ENDPOINT"],
        api_version=os.environ.get("AZURE_AI_API_VERSION", "2024-10-21"),
        credential=AzureCliCredential(),
    )


def build_instructions() -> str:
    brand_name = os.environ.get("SHOPPING_AGENT_BRAND_NAME", "Boreal")
    return INSTRUCTIONS_TEMPLATE.format(brand_name=brand_name)


def build_default_options() -> ChatOptions:
    # GPT-5's default reasoning effort makes multi-tool-call turns (e.g.
    # "add to cart and check out") take 15-60s with no user-visible benefit
    # for a scripted retail-assistant persona -- "low" keeps replies quick
    # enough for a live demo.
    return ChatOptions(reasoning_effort="low")
