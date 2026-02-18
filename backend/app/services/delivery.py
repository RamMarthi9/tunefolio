from datetime import datetime, timedelta
from nselib import capital_market
import pandas as pd


def fetch_delivery_data(symbol: str, period_days: int = 365) -> list[dict]:
    """
    Fetch delivery volume data for an NSE symbol using nselib.
    Returns list of dicts: {date, total_traded_qty, delivered_qty, not_delivered_qty, delivery_pct}
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)

    # nselib expects "DD-MM-YYYY" format
    from_date = start_date.strftime("%d-%m-%Y")
    to_date = end_date.strftime("%d-%m-%Y")

    df = capital_market.price_volume_and_deliverable_position_data(
        symbol, from_date, to_date
    )

    if df is None or df.empty:
        return []

    results = []
    for _, row in df.iterrows():
        total_traded = int(row["TotalTradedQuantity"]) if pd.notna(row["TotalTradedQuantity"]) else 0
        delivered = int(row["DeliverableQty"]) if pd.notna(row["DeliverableQty"]) else 0
        not_delivered = max(total_traded - delivered, 0)
        delivery_pct = round(float(row["%DlyQttoTradedQty"]), 2) if pd.notna(row["%DlyQttoTradedQty"]) else 0

        results.append({
            "date": str(row["Date"]),
            "total_traded_qty": total_traded,
            "delivered_qty": delivered,
            "not_delivered_qty": not_delivered,
            "delivery_pct": delivery_pct
        })

    # Sort by date ascending
    results.sort(key=lambda x: x["date"])

    return results
