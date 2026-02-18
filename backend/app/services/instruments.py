import yfinance as yf
from backend.app.services.db import (
    get_connection,
    get_instrument,
    update_instrument_sector
)


def _yahoo_symbol(symbol: str, exchange: str) -> str:
    """
    Convert Zerodha symbol → Yahoo Finance symbol
    """
    if exchange == "NSE":
        return f"{symbol}.NS"
    elif exchange == "BSE":
        return f"{symbol}.BO"
    return symbol


def fetch_sector_industry(symbol: str, exchange: str) -> tuple[str, str]:
    yf_symbol = _yahoo_symbol(symbol, exchange)

    ticker = yf.Ticker(yf_symbol)
    info = ticker.info or {}

    sector = info.get("sector")
    industry = info.get("industry")

    if not sector or not industry:
        raise ValueError(f"Sector data unavailable for {symbol}")

    return sector, industry


def enrich_instrument_if_missing(symbol: str, exchange: str):
    """
    Fetch sector/industry only if missing in DB.
    Falls back to sector_map, then Yahoo Finance. Never crashes the request.
    """
    instrument = get_instrument(symbol, exchange)

    if instrument and instrument["sector"] and instrument["industry"]:
        return instrument

    # Try hardcoded sector map first (fast, no network)
    try:
        from backend.app.services.sector_map import get_sector_info
        info = get_sector_info(symbol)
        if info.get("sector") and info["sector"] != "Unknown":
            update_instrument_sector(
                symbol=symbol,
                exchange=exchange,
                sector=info["sector"],
                industry=info.get("industry", "Unknown")
            )
            return get_instrument(symbol, exchange)
    except Exception:
        pass

    # Fall back to Yahoo Finance (slow, may fail)
    try:
        sector, industry = fetch_sector_industry(symbol, exchange)
        update_instrument_sector(
            symbol=symbol,
            exchange=exchange,
            sector=sector,
            industry=industry
        )
        return get_instrument(symbol, exchange)
    except Exception:
        # If all enrichment fails, return instrument as-is (sector=None)
        return instrument
