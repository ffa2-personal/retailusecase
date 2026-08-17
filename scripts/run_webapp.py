"""Starts the Boreal storefront webapp on http://localhost:5000.

    python scripts/run_webapp.py

This is the page the shopping agent navigates (see shopping_agent_chat.py).
It can also just be opened directly in a normal browser to click through the
flow by hand -- no agent or Azure credentials involved.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic_commerce.webapp.app import app  # noqa: E402

if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
