"""사용자 관심종목(워치리스트) 관리.

분석 대상 종목/코인을 data/watchlist.json에 영속화한다.
파일이 없으면 CUSTOM_STOCKS/CUSTOM_TICKERS(env) 또는 기본 목록으로 시드한다.
대시보드에서 추가/삭제하면 다음 분석부터 즉시 반영된다.
"""
import json
import re
from pathlib import Path
from threading import Lock

from trader import kis_client, upbit_client

_PATH = Path(__file__).parent.parent / "data" / "watchlist.json"
_lock = Lock()

_STOCK_RE = re.compile(r"^\d{6}$")
_TICKER_RE = re.compile(r"^KRW-[A-Z0-9]+$")


def _seed() -> dict:
    """최초 1회 기본 워치리스트 생성 (CUSTOM_* env 우선)."""
    return {
        "stocks": [dict(s) for s in kis_client.get_analysis_stocks()],
        "tickers": list(upbit_client.get_analysis_tickers()),
    }


def _read() -> dict:
    if _PATH.exists():
        try:
            data = json.loads(_PATH.read_text(encoding="utf-8"))
            return {"stocks": data.get("stocks", []), "tickers": data.get("tickers", [])}
        except (json.JSONDecodeError, OSError):
            pass
    data = _seed()
    _write(data)
    return data


def _write(data: dict):
    _PATH.parent.mkdir(exist_ok=True)
    _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load() -> dict:
    with _lock:
        return _read()


def get_stocks() -> list[dict]:
    return load()["stocks"]


def get_tickers() -> list[str]:
    return load()["tickers"]


def _resolve_stock_name(code: str) -> str:
    for s in kis_client.DEFAULT_STOCKS:
        if s["code"] == code:
            return s["name"]
    return code


def add_stock(code: str, name: str = "") -> list[dict]:
    code = (code or "").strip()
    if not _STOCK_RE.match(code):
        raise ValueError("종목코드는 6자리 숫자여야 합니다 (예: 005930)")
    name = (name or "").strip() or _resolve_stock_name(code)
    with _lock:
        data = _read()
        if any(s["code"] == code for s in data["stocks"]):
            raise ValueError("이미 등록된 종목입니다")
        data["stocks"].append({"code": code, "name": name})
        _write(data)
        return data["stocks"]


def remove_stock(code: str) -> list[dict]:
    code = (code or "").strip()
    with _lock:
        data = _read()
        data["stocks"] = [s for s in data["stocks"] if s["code"] != code]
        _write(data)
        return data["stocks"]


def add_ticker(ticker: str) -> list[str]:
    ticker = (ticker or "").strip().upper()
    if ticker and not ticker.startswith("KRW-"):
        ticker = f"KRW-{ticker}"  # "BTC" → "KRW-BTC" 보정
    if not _TICKER_RE.match(ticker):
        raise ValueError("코인은 KRW-마켓 형식이어야 합니다 (예: KRW-BTC)")
    with _lock:
        data = _read()
        if ticker in data["tickers"]:
            raise ValueError("이미 등록된 코인입니다")
        data["tickers"].append(ticker)
        _write(data)
        return data["tickers"]


def remove_ticker(ticker: str) -> list[str]:
    ticker = (ticker or "").strip().upper()
    with _lock:
        data = _read()
        data["tickers"] = [t for t in data["tickers"] if t != ticker]
        _write(data)
        return data["tickers"]
