"""fact_purchase_order_line, fact_shipment_event.

Also selects the concrete style sets used by the supply-disruption (scenario 4)
and poor-allocation (scenario 5) biases in demand_engine.py, so both the
narrative PO/shipment tables here and the inventory math there tell the same
story about the same products.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..calendar import date_to_week_id
from ..config import Config
from ..io_utils import write_table

_EVENT_SEQUENCE_NORMAL = ["Booked", "Departed-Origin", "Customs-Clear", "Received-DC"]
_EVENT_SEQUENCE_DISRUPTED = ["Booked", "Departed-Origin", "Customs-Hold"]


def select_scenario_styles(cfg: Config, dim_style: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Returns (supply_disruption_affected_styles, poor_allocation_extra_styles)."""
    rng = cfg.rng("allocation_po")
    hero_style_ids = {h["style_id"] for h in cfg.hero_styles}
    outerwear = dim_style.loc[
        (dim_style["category"] == "Outerwear") & (~dim_style["style_id"].isin(hero_style_ids)), "style_id"
    ].tolist()
    rng.shuffle(outerwear)

    sd = cfg.scenarios["supply_disruption"]
    n_sd = min(sd["n_affected_styles"], len(outerwear))
    disruption_styles = outerwear[:n_sd]

    pa = cfg.scenarios["poor_allocation"]
    n_pa = min(pa["n_additional_style_colors"], max(len(outerwear) - n_sd, 0))
    allocation_extra_styles = outerwear[n_sd:n_sd + n_pa]

    return disruption_styles, allocation_extra_styles


def build_allocation_po(cfg: Config, dim_style: pd.DataFrame, dim_supplier: pd.DataFrame,
                         disruption_styles: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = cfg.rng("allocation_po")
    sd = cfg.scenarios["supply_disruption"]
    hero_supplier_id = sd["supplier_id"]
    hero_dc = sd["dc_id"]
    order_date = dt.date.fromisoformat(sd["order_date"])
    orig_receipt = dt.date.fromisoformat(sd["original_expected_receipt_date"])
    delay_days = sd["delay_days"]
    disruption_set = set(disruption_styles)

    dcs = ["DC-NAEAST", "DC-NAWEST", "DC-EUCEN", "DC-EUUK", "DC-APAC", "DC-MEA"]
    supplier_ids = dim_supplier["supplier_id"].tolist()

    po_lines = []
    shipments = []
    po_seq = 1
    ship_seq = 1

    for style in dim_style.itertuples():
        is_disrupted = style.style_id in disruption_set
        waves = ["Pre-season"] if is_disrupted else ["Pre-season", "Replenishment-1"]
        n_dcs_for_style = 1 if is_disrupted else int(rng.integers(1, 4))
        style_dcs = [hero_dc] if is_disrupted else list(rng.choice(dcs, size=n_dcs_for_style, replace=False))

        for dc_id in style_dcs:
            for wave in waves:
                po_id = f"PO-{po_seq:06d}"
                po_seq += 1
                supplier_id = hero_supplier_id if is_disrupted else supplier_ids[rng.integers(0, len(supplier_ids))]

                if wave == "Pre-season":
                    o_date = order_date if is_disrupted else style.launch_date - dt.timedelta(weeks=int(rng.integers(10, 16)))
                    expected = orig_receipt if is_disrupted else o_date + dt.timedelta(days=int(rng.integers(30, 60)))
                else:
                    o_date = style.launch_date + dt.timedelta(weeks=int(rng.integers(4, 10)))
                    expected = o_date + dt.timedelta(days=int(rng.integers(20, 45)))

                revised = expected + dt.timedelta(days=delay_days) if is_disrupted else expected
                units = int(rng.integers(400, 4000))
                unit_cost = round(float(style.base_wholesale_cost), 2)

                actual_receipt = None if is_disrupted else revised - dt.timedelta(days=int(rng.integers(-3, 3)))

                po_lines.append({
                    "po_id": po_id, "po_line_id": f"{po_id}-L1", "supplier_id": supplier_id, "dc_id": dc_id,
                    "style_id": style.style_id, "order_date": o_date, "original_expected_receipt_date": expected,
                    "revised_expected_receipt_date": revised, "actual_receipt_date": actual_receipt,
                    "units_ordered": units, "unit_cost": unit_cost, "is_delayed": is_disrupted,
                })

                seq = _EVENT_SEQUENCE_DISRUPTED if is_disrupted else _EVENT_SEQUENCE_NORMAL
                event_span_days = max((revised - o_date).days, 1)
                for i, etype in enumerate(seq):
                    frac = i / max(len(seq) - 1, 1)
                    e_date = o_date + dt.timedelta(days=int(event_span_days * frac))
                    shipments.append({
                        "shipment_event_id": f"SHP-{ship_seq:07d}", "po_line_id": f"{po_id}-L1",
                        "event_seq": i + 1, "event_type": etype, "event_date": e_date,
                        "location_hint": dc_id if etype in ("Received-DC",) else "In-Transit",
                    })
                    ship_seq += 1

    po_df = pd.DataFrame(po_lines)
    ship_df = pd.DataFrame(shipments)
    write_table(cfg, "fact_purchase_order_line", po_df)
    write_table(cfg, "fact_shipment_event", ship_df)
    return po_df, ship_df
