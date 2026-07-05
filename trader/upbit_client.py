"""업비트 API 클라이언트"""
import pyupbit
import pandas as pd
import requests
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
            current = get_current_price(ticker)  # 재시도 포함 (429 방어)
            value = qty * current
            profit_rate = ((current - avg) / avg * 100) if avg > 0 else 0
            total += value
            # 100원 미만 잔고만 제외 (매도 가능 여부는 executor에서 판단)
            if qty > 0 and value >= 100:
                holdings.append({
                    "ticker": ticker,
                    "currency": currency,
                    "qty": qty,
                    "avg_price": avg,
                    "current_price": current,
                    "eval_amount": int(value),
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
    """현재가 조회 — REST 직접 호출 + 재시도.

    pyupbit.get_current_price는 API 제한(429)/오류 응답에서 KeyError: 0을
    던지는 경우가 있어, 공개 ticker API를 직접 호출하고 3회 재시도한다.
    """
    import time as _time
    for attempt in range(3):
        try:
            resp = requests.get(
                "https://api.upbit.com/v1/ticker",
                params={"markets": ticker}, timeout=5,
            )
            if resp.status_code == 429:  # 요청 제한 — 잠시 대기 후 재시도
                _time.sleep(0.3 * (attempt + 1))
                continue
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data:
                return float(data[0].get("trade_price") or 0)
            return 0.0
        except Exception:
            _time.sleep(0.2 * (attempt + 1))
    return 0.0


DEFAULT_TICKERS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]


def get_market_snapshot(tickers: list[str]) -> dict[str, dict]:
    """공개 ticker API로 24h 등락률·거래대금 일괄 조회 (인증 불필요).

    반환: {ticker: {"price", "change_pct", "trade_value"}}
    """
    if not tickers:
        return {}
    snapshot = {}
    try:
        # 업비트 ticker API는 markets 쿼리로 다건 조회 가능 (100개씩 분할)
        for i in range(0, len(tickers), 100):
            chunk = tickers[i:i + 100]
            resp = requests.get(
                "https://api.upbit.com/v1/ticker",
                params={"markets": ",".join(chunk)},
                timeout=10,
            )
            resp.raise_for_status()
            for r in resp.json():
                snapshot[r["market"]] = {
                    "price": float(r.get("trade_price") or 0),
                    "change_pct": float(r.get("signed_change_rate") or 0) * 100,
                    "trade_value": float(r.get("acc_trade_price_24h") or 0),
                }
    except Exception:
        pass
    return snapshot


def get_top_tickers(limit: int = 20) -> list[str]:
    """KRW 마켓 24h 거래대금 상위 코인 (거래대금 내림차순 정렬)"""
    tickers = pyupbit.get_tickers(fiat="KRW")
    if not tickers:
        return []
    snapshot = get_market_snapshot(tickers)
    if not snapshot:
        return tickers[:limit]  # 조회 실패 시 기존 동작 폴백
    ranked = sorted(snapshot, key=lambda t: snapshot[t]["trade_value"], reverse=True)
    return ranked[:limit]


def get_analysis_tickers() -> list[str]:
    """기본 분석 대상 코인 목록.

    config.CUSTOM_TICKERS가 설정돼 있으면 이를 우선 사용, 없으면 DEFAULT_TICKERS.
    """
    custom = [t.strip() for t in config.CUSTOM_TICKERS.split(",") if t.strip()]
    return custom if custom else list(DEFAULT_TICKERS)


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
