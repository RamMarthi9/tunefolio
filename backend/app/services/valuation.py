"""Normalize broker quantities once; retain raw observations for audit."""
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

def india_today():
    return datetime.now(IST).date()

def normalize_holding(raw):
    h = dict(raw)
    if h.get("quantity_basis") == "settled+t1+mtf":
        return h
    settled = h.get("quantity", 0)
    t1 = h.get("t1_quantity", 0)
    mtf = h.get("mtf") or {}
    funded = mtf.get("quantity", 0)
    total = settled + t1 + funded
    cost = (settled + t1) * h.get("average_price", 0)
    if funded:
        cost += funded * mtf["average_price"]
    h.update(quantity=total, settled_quantity=settled, t1_quantity=t1,
             mtf_quantity=funded, average_price=cost / total if total else 0,
             quantity_basis="settled+t1+mtf")
    return h
