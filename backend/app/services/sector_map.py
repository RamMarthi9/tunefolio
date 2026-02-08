SECTOR_MAP = {
"ADANIPOWER": {"sector": "Power", "industry": "Power Generation/Distribution"},
"ANANTRAJ": {"sector": "Real Estate", "industry": "Real Estate Development"},
"AWHCL": {"sector": "Industrials", "industry": "Waste Management"},
"AZAD": {"sector": "Industrials", "industry": "Aerospace & Defense"},
"BEL": {"sector": "Industrials", "industry": "Aerospace & Defense"},
"BSE": {"sector": "Financials", "industry": "Stock Exchanges"},
"CDSL": {"sector": "Financials", "industry": "Stock Exchanges"},
"CGPOWER": {"sector": "Industrials", "industry": "Electric Equipment"},
"COCHINSHIP": {"sector": "Industrials", "industry": "Shipbuilding"},
"ECORECO": {"sector": "Materials", "industry": "Recycling"},
"EIEL": {"sector": "Industrials", "industry": "Engineering"},
"GAIL": {"sector": "Energy", "industry": "Gas Utilities"},
"HINDCOPPER": {"sector": "Materials", "industry": "Copper Mining"},
"IRFC": {"sector": "Financials", "industry": "Railway Finance"},
"JPPOWER": {"sector": "Utilities", "industry": "Power Generation"},
"JSWENERGY": {"sector": "Utilities", "industry": "Power Generation"},
"NETWEB": {"sector": "Technology", "industry": "IT Hardware"},
"NSDL": {"sector": "Financials", "industry": "Depository Services"},
"RAILTEL": {"sector": "Technology", "industry": "Railway IT"},
"RUSHIL": {"sector": "Materials", "industry": "Laminates"},
"SAREGAMA": {"sector": "Communication Services", "industry": "Entertainment"},
"TATAPOWER": {"sector": "Utilities", "industry": "Power Generation"},
"TIPSMUSIC": {"sector": "Communication Services", "industry": "Entertainment"},
"ZENTEC": {"sector": "Healthcare", "industry": "Medical Equipment"}
}

def get_sector_info(symbol: str):
    return SECTOR_MAP.get(symbol, {
        "sector": "Unknown",
        "industry": "Unknown"
    })
