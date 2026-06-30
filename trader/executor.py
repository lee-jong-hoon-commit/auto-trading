"""매매 실행 + 거래 기록 관리"""
import json
import logging
from datetime import datetime
from pathlib import Path
from config import config
from trader import kis_client, upbit_client

LOG_FILE = Path(__file__).parent.parent / "logs" / "trades.json"
LOG_FILE.parent.mkdir(exist_ok=True)
if not LOG_FILE.exists():
    LOG_FILE.write_text("[]")

logger = logging.getLogger(__name__)


def _load_trades() -> list:
    return json.loads(LOG_FILE.read_text())


def _save_trade(record: dict):
    trades = _load_trades()
    trades.append(record)
    LOG_FILE.write_text(json.dumps(trades[-500:], ensure_ascii=False, indent=2))  # 최근 500건


def execute_stock(code: str, name: str, action: str, confidence: float, reason: str, portfolio: dict) -> dict:
    """주식 매매 실행"""
    if action == "HOLD":
        return {"status": "skipped", "reason": "HOLD 결정"}

    if confidence < 0.7:
        return {"status": "skipped", "reason": f"신뢰도 부족 ({confidence:.2f})"}

    try:
        current_price = kis_client.get_current_price(code)
        cash = portfolio.get("cash", 0)

        if action == "BUY":
            budget = cash * config.MAX_POSITION_RATIO
            qty = int(budget // current_price)
            if qty < 1:
                return {"status": "skipped", "reason": "예산 부족"}
            result = kis_client.place_order(code, qty, int(current_price), "buy")
            record = {
                "time": datetime.now().isoformat(),
                "market": "stock",
                "code": code,
                "name": name,
                "action": "BUY",
                "price": current_price,
                "qty": qty,
                "amount": current_price * qty,
                "confidence": confidence,
                "reason": reason,
                "result": result,
            }

        elif action == "SELL":
            holding = next((h for h in portfolio.get("holdings", []) if h["code"] == code), None)
            if not holding:
                return {"status": "skipped", "reason": "보유 종목 없음"}
            qty = holding["qty"]
            result = kis_client.place_order(code, qty, int(current_price), "sell")
            record = {
                "time": datetime.now().isoformat(),
                "market": "stock",
                "code": code,
                "name": name,
                "action": "SELL",
                "price": current_price,
                "qty": qty,
                "amount": current_price * qty,
                "profit_rate": holding.get("profit_rate", 0),
                "confidence": confidence,
                "reason": reason,
                "result": result,
            }
        else:
            return {"status": "skipped", "reason": "알 수 없는 액션"}

        _save_trade(record)
        logger.info(f"[STOCK] {action} {name}({code}) {qty}주 @ {current_price:,}원")
        return {"status": "executed", **record}

    except Exception as e:
        logger.error(f"[STOCK] 주문 실패 {code}: {e}")
        return {"status": "error", "error": str(e)}


def execute_crypto(ticker: str, action: str, confidence: float, reason: str, portfolio: dict) -> dict:
    """코인 매매 실행"""
    if action == "HOLD":
        return {"status": "skipped", "reason": "HOLD 결정"}

    if confidence < 0.7:
        return {"status": "skipped", "reason": f"신뢰도 부족 ({confidence:.2f})"}

    try:
        current_price = upbit_client.get_current_price(ticker)
        cash = portfolio.get("cash", 0)

        if action == "BUY":
            amount = cash * config.MAX_POSITION_RATIO
            if amount < config.UPBIT_MIN_ORDER_KRW:
                return {"status": "skipped", "reason": f"예산 부족 (최소 {config.UPBIT_MIN_ORDER_KRW:,}원)"}
            result = upbit_client.place_order(ticker, "buy", amount_krw=amount)
            record = {
                "time": datetime.now().isoformat(),
                "market": "crypto",
                "ticker": ticker,
                "action": "BUY",
                "price": current_price,
                "amount_krw": amount,
                "confidence": confidence,
                "reason": reason,
                "result": result,
            }

        elif action == "SELL":
            holding = next((h for h in portfolio.get("holdings", []) if h["ticker"] == ticker), None)
            if not holding:
                return {"status": "skipped", "reason": "보유 코인 없음"}
            qty = holding["qty"]
            sell_value = current_price * qty
            if sell_value < config.UPBIT_MIN_ORDER_KRW:
                return {"status": "skipped", "reason": f"매도 금액 부족 ({sell_value:.0f}원, 최소 {config.UPBIT_MIN_ORDER_KRW:,}원)"}
            result = upbit_client.place_order(ticker, "sell", qty=qty)
            record = {
                "time": datetime.now().isoformat(),
                "market": "crypto",
                "ticker": ticker,
                "action": "SELL",
                "price": current_price,
                "qty": qty,
                "amount_krw": current_price * qty,
                "profit_rate": holding.get("profit_rate", 0),
                "confidence": confidence,
                "reason": reason,
                "result": result,
            }
        else:
            return {"status": "skipped", "reason": "알 수 없는 액션"}

        _save_trade(record)
        logger.info(f"[CRYPTO] {action} {ticker} @ {current_price:,}원")
        return {"status": "executed", **record}

    except Exception as e:
        logger.error(f"[CRYPTO] 주문 실패 {ticker}: {e}")
        return {"status": "error", "error": str(e)}


def check_stop_loss_take_profit(stock_portfolio: dict, crypto_portfolio: dict) -> list[dict]:
    """손절/익절 자동 체크"""
    actions = []

    for h in stock_portfolio.get("holdings", []):
        rate = h.get("profit_rate", 0)
        if rate <= -config.STOP_LOSS_RATIO * 100:
            actions.append({"type": "stock", "code": h["code"], "name": h["name"],
                           "action": "SELL", "reason": f"손절 발동 ({rate:.2f}%)"})
        elif rate >= config.TAKE_PROFIT_RATIO * 100:
            actions.append({"type": "stock", "code": h["code"], "name": h["name"],
                           "action": "SELL", "reason": f"익절 발동 ({rate:.2f}%)"})

    for h in crypto_portfolio.get("holdings", []):
        rate = h.get("profit_rate", 0)
        value = h.get("value", h.get("qty", 0) * h.get("current_price", 0))
        if value < config.UPBIT_MIN_ORDER_KRW:  # 업비트 최소 주문 금액 미달 → 매도 불가 잔고 제외
            continue
        if rate <= -config.STOP_LOSS_RATIO * 100:
            actions.append({"type": "crypto", "ticker": h["ticker"],
                           "action": "SELL", "reason": f"손절 발동 ({rate:.2f}%)"})
        elif rate >= config.TAKE_PROFIT_RATIO * 100:
            actions.append({"type": "crypto", "ticker": h["ticker"],
                           "action": "SELL", "reason": f"익절 발동 ({rate:.2f}%)"})

    return actions


def get_trade_history(limit: int = 50) -> list:
    trades = _load_trades()
    return list(reversed(trades[-limit:]))
