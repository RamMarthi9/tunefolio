from datetime import datetime, timezone
import pytest
from backend.app.services.valuation import normalize_holding, IST
from backend.app.services.performance import explain_changes
from backend.app.services import trades


def holding(**changes):
    return dict(tradingsymbol="TEST", exchange="NSE", quantity=10,
                t1_quantity=5, average_price=100.1234, last_price=110, **changes)


def test_t1_and_cost_precision():
    raw = holding()
    h = normalize_holding(raw)
    assert h["quantity"] == 15
    assert round(h["quantity"] * h["average_price"], 2) == 1501.85
    assert h["quantity"] * h["last_price"] == 1650
    assert raw["quantity"] == 10
    assert normalize_holding(h) == h


def test_t1_only_and_mtf_cost():
    raw = holding(mtf={"quantity": 2, "average_price": 80})
    raw["quantity"] = 0
    h = normalize_holding(raw)
    assert h["quantity"] == 7
    assert h["average_price"] * 7 == pytest.approx(5 * 100.1234 + 160)


def test_settlement_is_not_a_purchase():
    old = holding()
    new = dict(old, quantity=15, t1_quantity=0)
    result = explain_changes([old], [new])[0]
    assert result["quantity_effect"] == 0
    assert result["value_change"] == 0


def test_fy_rollover_in_india(monkeypatch):
    for instant, expected in [("2026-03-31T18:29:59+00:00", "2025-04-01"),
                              ("2026-03-31T18:30:00+00:00", "2026-04-01")]:
        local_date = datetime.fromisoformat(instant).astimezone(IST).date()
        monkeypatch.setattr(trades, "india_today", lambda: local_date)
        assert trades.get_fy_bounds()[0] == expected
