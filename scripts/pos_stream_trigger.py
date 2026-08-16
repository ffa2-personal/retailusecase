"""Run this in a second terminal, on cue, to kick off the live Aurora Bomber
viral spike in a running pos_stream_simulator.py session:

    python scripts/pos_stream_trigger.py

Just flips data/stream/control.json -- the simulator polls it every tick.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from retail_synth.config import REPO_ROOT  # noqa: E402

CONTROL_PATH = REPO_ROOT / "data" / "stream" / "control.json"


def main() -> None:
    if not CONTROL_PATH.exists():
        print("No live session found -- start scripts/pos_stream_simulator.py first.")
        raise SystemExit(1)

    tmp = CONTROL_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"triggered": True}), encoding="utf-8")
    os.replace(tmp, CONTROL_PATH)
    print("Triggered. The Aurora Bomber spike will start on the simulator's next tick.")


if __name__ == "__main__":
    main()
