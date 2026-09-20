"""Account-scoped, auditable performance and holdings-change evidence."""
import json
import math
from datetime import datetime, timezone
from backend.app.services.db import get_connection


def init_evidence_tables():
    with get_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS portfolio_observations (
            observed_at TEXT PRIMARY KEY, holdings_json TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS performance_events (
            observed_at TEXT PRIMARY KEY, value_before_flow REAL NOT NULL,
            external_flow REAL NOT NULL, source TEXT NOT NULL,
            coverage_verified INTEGER NOT NULL DEFAULT 0
        )""")
    conn.close()


def observe_holdings(holdings, retrieved_at):
    """Store actual broker retrievals, including empty portfolios; never synthetic prices."""
    init_evidence_tables()
    with get_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        latest = conn.execute('SELECT holdings_json FROM portfolio_observations ORDER BY observed_at DESC LIMIT 1').fetchone()
        if not latest or json.loads(latest['holdings_json']) != holdings:
            conn.execute("INSERT OR IGNORE INTO portfolio_observations VALUES (?, ?)",
                         (retrieved_at, json.dumps(holdings)))
    conn.close()


def explain_changes(before, after):
    def indexed(rows):
        return {(r['tradingsymbol'], r['exchange']): r for r in rows}
    old, new = indexed(before), indexed(after)
    result = []
    for symbol, exchange in sorted(old.keys() | new.keys()):
        a, b = old.get((symbol, exchange)), new.get((symbol, exchange))
        q0, q1 = (a['quantity'] if a else 0), (b['quantity'] if b else 0)
        p0, p1 = (a['last_price'] if a else 0), (b['last_price'] if b else 0)
        if any(v is None or not math.isfinite(float(v)) for v in (q0, q1, p0, p1)):
            result.append({'symbol': symbol, 'exchange': exchange, 'status': 'missing_price'})
            continue
        # Removed positions have no ending quote: attribute the observed removal
        # to quantity at the last observed price, not invented sale proceeds.
        if b is None:
            p1 = p0
        price_effect = q0 * (p1 - p0)
        quantity_effect = (q1 - q0) * p1
        result.append({'symbol': symbol, 'exchange': exchange, 'status': 'available',
                       'quantity_before': q0, 'quantity_after': q1,
                       'price_effect': round(price_effect, 2),
                       'quantity_effect': round(quantity_effect, 2),
                       'value_change': round(q1*p1-q0*p0, 2),
                       'explanation': 'Opening quantity × price change; quantity change × ending price. Quantity changes may include trades, transfers or corporate actions; no cause is inferred.'})
    return sorted(result, key=lambda r: abs(r.get('value_change', 0)), reverse=True)


def changes():
    init_evidence_tables()
    conn = get_connection()
    rows = conn.execute('SELECT * FROM portfolio_observations ORDER BY observed_at DESC LIMIT 2').fetchall()
    conn.close()
    if len(rows) < 2:
        return {'status': 'unavailable', 'reason': 'Two distinct broker observations are needed.', 'data': []}
    return {'status': 'available', 'from': rows[1]['observed_at'], 'to': rows[0]['observed_at'],
            'data': explain_changes(json.loads(rows[1]['holdings_json']), json.loads(rows[0]['holdings_json']))}


def time_weighted_return(events):
    """Exact linked subperiod returns when every external flow has a pre-flow valuation.

    Coverage verification attests ALL external flows and valuations (including
    cash, fees and corporate actions), not merely that a CSV was parsed.
    """
    if len(events) < 2:
        return {'status': 'unavailable', 'reason': 'At least two verified valuations are required.', 'points': []}
    growth, previous_after, previous_at = 1.0, None, None
    points = []
    for event in events:
        if event.get('coverage_verified') not in (True, 1) or not isinstance(event.get('source'), str) or not event['source'].strip():
            return {'status': 'incomplete', 'reason': 'Cash-flow and valuation coverage has not been verified.', 'points': []}
        try:
            when = datetime.fromisoformat(event['observed_at'])
            value, flow = float(event['value_before_flow']), float(event['external_flow'])
            valid = when.tzinfo is not None and math.isfinite(value) and math.isfinite(flow)
            valid = valid and value >= 0 and value + flow > 0
            valid = valid and (previous_at is None or when > previous_at)
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            return {'status': 'incomplete', 'reason': 'Invalid or unordered valuation/cash-flow events.', 'points': []}
        if previous_after is not None:
            growth *= value / previous_after
        points.append({'at': event['observed_at'], 'return_pct': round((growth-1)*100, 6),
                       'value_before_flow': value, 'external_flow': flow, 'source': event['source']})
        previous_after, previous_at = value + flow, when
    return {'status': 'available', 'return_pct': points[-1]['return_pct'], 'points': points,
            'method': 'Linked time-weighted return: product(pre-flow value / previous post-flow value) − 1. Includes cash; external flows are excluded from return.'}


def performance():
    init_evidence_tables()
    conn = get_connection()
    rows = conn.execute('SELECT * FROM performance_events ORDER BY observed_at').fetchall()
    conn.close()
    return time_weighted_return([dict(r) for r in rows])
