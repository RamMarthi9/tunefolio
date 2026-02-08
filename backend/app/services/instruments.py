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
    Fetch sector/industry only if missing in DB
    """
    instrument = get_instrument(symbol, exchange)

    if instrument and instrument["sector"] and instrument["industry"]:
        return instrument

    sector, industry = fetch_sector_industry(symbol, exchange)

    update_instrument_sector(
        symbol=symbol,
        exchange=exchange,
        sector=sector,
        industry=industry
    )

    return get_instrument(symbol, exchange)
