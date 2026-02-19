from datetime import datetime, timedelta
from nselib import capital_market
import pandas as pd


def fetch_delivery_data(symbol: str, period_days: int = 365) -> list[dict]:
    """
    Fetch delivery volume data for an NSE symbol using nselib.
    Returns list of dicts with price direction for chart coloring:
    {date, total_traded_qty, delivered_qty, not_delivered_qty, delivery_pct, price_up}

    price_up: True if ClosePrice >= PrevClose (green day), False if down (red day)
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)

    # nselib expects "DD-MM-YYYY" format
    from_date = start_date.strftime("%d-%m-%Y")
    to_date = end_date.strftime("%d-%m-%Y")

    try:
        df = capital_market.price_volume_and_deliverable_position_data(
            symbol, from_date, to_date
        )
    except Exception:
        return []

    if df is None or (hasattr(df, 'empty') and df.empty):
        return []

    results = []
    for _, row in df.iterrows():
        try:
            total_traded = int(row["TotalTradedQuantity"]) if pd.notna(row.get("TotalTradedQuantity")) else 0
            delivered = int(row["DeliverableQty"]) if pd.notna(row.get("DeliverableQty")) else 0
            not_delivered = max(total_traded - delivered, 0)
            delivery_pct = round(float(row["%DlyQttoTradedQty"]), 2) if pd.notna(row.get("%DlyQttoTradedQty")) else 0

            # Price direction: close vs previous close
            close_price = float(row["ClosePrice"]) if pd.notna(row.get("ClosePrice")) else 0
            prev_close = float(row["PrevClose"]) if pd.notna(row.get("PrevClose")) else 0
            price_up = close_price >= prev_close

            results.append({
                "date": str(row["Date"]),
                "total_traded_qty": total_traded,
                "delivered_qty": delivered,
                "not_delivered_qty": not_delivered,
                "delivery_pct": delivery_pct,
                "price_up": price_up
            })
        except (ValueError, KeyError, TypeError):
            continue

    if not results:
        return []

    # Sort by date ascending (parse "DD-Mon-YYYY" like "01-Dec-2025")
    try:
        results.sort(key=lambda x: datetime.strptime(x["date"], "%d-%b-%Y"))
    except ValueError:
        # Fallback: leave as-is if date format is different
        pass

    return results
