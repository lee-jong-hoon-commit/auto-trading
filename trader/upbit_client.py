"""업비트 API 클라이언트"""
import pyupbit
import pandas as pd
from config import config

_upbit = None


def _client():
    global _upbit
    if _upbit is None and config.is_upbit_ready:
        _upbit = pyupbit.Upbit(config.UPBIT_ACCESS_KEY, config.UPBIT_SECRET_KEY)
    return _upbit


def get_balance() -> dict:
    """잔고 조회"""
    client = _client()
    if not client:
        return {"cash": 0, "total": 0, "holdings": []}

    balances = client.get_balances()
    cash = 0
    holdings = []
    total = 0

    for b in balances:
        currency = b["currency"]
        qty = float(b["balance"])
        avg = float(b["avg_buy_price"])

        if currency == "KRW":
            cash = qty
            total += qty
        else:
            ticker = f"KRW-{currency}"
            try:
                current = pyupbit.get_current_price(ticker) or 0
            except Exception:
                current = 0
            value = qty * current
            profit_rate = ((current - avg) / avg * 100) if avg > 0 else 0
            total += value
            if qty > 0:
                holdings.append({
                    "ticker": ticker,
                    "currency": currency,
                    "qty": qty,
                    "avg_price": avg,
                    "current_price": current,
                    "value": value,
                    "profit_rate": profit_rate,
                })

    return {"cash": cash, "total": total, "holdings": holdings}


def get_ohlcv(ticker: str, interval: str = "day", count: int = 100) -> pd.DataFrame:
    """OHLCV 데이터 조회"""
    df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.reset_index()
    df.columns = ["date", "open", "high", "low", "close", "volume", "value"]
    return df[["date", "open", "high", "low", "close", "volume"]]


def get_current_price(ticker: str) -> float:
    return pyupbit.get_current_price(ticker) or 0.0


def get_top_tickers(limit: int = 20) -> list[str]:
    """KRW 마켓 거래량 상위 코인"""
    tickers = pyupbit.get_tickers(fiat="KRW")
    return tickers[:limit] if tickers else []


def place_order(ticker: str, side: str, amount_krw: float = None, qty: float = None) -> dict:
    """주문 실행 (side: 'buy' | 'sell')"""
    client = _client()
    if not client:
        return {"error": "Upbit client not initialized"}

    if side == "buy" and amount_krw:
        return client.buy_market_order(ticker, amount_krw)
    elif side == "sell" and qty:
        return client.sell_market_order(ticker, qty)
    return {"error": "Invalid order params"}
