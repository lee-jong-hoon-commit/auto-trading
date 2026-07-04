"""매매 실행 + 거래 기록 관리"""
import json
import logging
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
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


def execute_stock(code: str, name: str, action: str, confidence: float, reason: str,
                  portfolio: dict, amount_krw: float = None, manual_qty: int = None) -> dict:
    """주식 매매 실행"""
    if action == "HOLD":
        return {"status": "skipped", "reason": "HOLD 결정"}

    # BUY는 0.70 이상 필요 (score=1짜리 0.6x 차단), SELL은 0.60 이상
    min_conf = 0.70 if action == "BUY" else 0.60
    if confidence < min_conf:
        return {"status": "skipped", "reason": f"신뢰도 부족 ({confidence:.2f} < {min_conf:.2f})"}

    try:
        current_price = kis_client.get_current_price(code)
        cash = portfolio.get("cash", 0)

        if not current_price or current_price <= 0:
            return {"status": "skipped", "reason": f"현재가 조회 실패 ({code})"}

        if action == "BUY":
            # KIS 주문가능금액 우선 사용 (예수금과 다를 수 있음 — T+2 미결제, 수수료 등)
            _oc = portfolio.get("orderable_cash")
            orderable = _oc if _oc is not None else cash
            budget = float(amount_krw) if amount_krw else orderable * 0.5
            budget = max(budget, orderable * 0.3)    # AI 소액 지정 시 최소 30%로 보정
            budget = min(budget, orderable * 0.95)   # 주문가능금액의 95% 상한 (수수료 여유)
            qty = int(budget // current_price)
            if qty < 1:
                return {"status": "skipped", "reason": f"예산 부족 ({budget:,.0f}원으로 {current_price:,.0f}원짜리 매수 불가)"}
            result = kis_client.place_order(code, qty, int(current_price), "buy")

        elif action == "SELL":
            holding = next((h for h in portfolio.get("holdings", []) if h["code"] == code), None)
            if not holding:
                return {"status": "skipped", "reason": "보유 종목 없음"}
            # 수동 매매 시 명시적 수량 지정 가능 (비율 계산 결과), 없으면 전량 매도
            qty = int(manual_qty) if manual_qty and int(manual_qty) <= holding["qty"] else holding["qty"]
            avg_price = holding.get("avg_price", 0)
            result = kis_client.place_order(code, qty, int(current_price), "sell")

        else:
            return {"status": "skipped", "reason": "알 수 없는 액션"}

        # KIS 주문 결과 확인 — rt_cd != '0' 이면 서버 측 오류
        kis_ok = str(result.get("rt_cd", "0")) == "0"
        if not kis_ok:
            err_msg = result.get("msg1") or result.get("msg_cd") or "KIS 주문 오류"
            # 거래정지·관리종목 등 KIS 사유 메시지를 그대로 노출
            result["error"] = {"message": err_msg, "rt_cd": result.get("rt_cd")}
            logger.warning(f"[STOCK] KIS 주문 거부 {name}({code}): {err_msg}")

        record = {
            "time": datetime.now(KST).isoformat(),
            "market": "stock",
            "code": code,
            "name": name,
            "action": action,
            "price": current_price,
            "qty": qty,
            "amount": current_price * qty,
            "confidence": confidence,
            "reason": reason,
            "result": result,
        }
        if action == "SELL":
            holding = next((h for h in portfolio.get("holdings", []) if h["code"] == code), None)
            if holding:
                record["avg_price"]   = holding.get("avg_price", 0)
                record["profit_rate"] = holding.get("profit_rate", 0)

        _save_trade(record)
        if kis_ok:
            logger.info(f"[STOCK] {action} {name}({code}) {qty}주 @ {current_price:,}원")
            return {"status": "executed", **record}
        else:
            logger.error(f"[STOCK] 주문 실패 {name}({code}): {result.get('msg1')}")
            return {"status": "error", "error": result.get("msg1", "KIS 주문 오류")}

    except Exception as e:
        logger.error(f"[STOCK] 주문 실패 {code}: {e}")
        return {"status": "error", "error": str(e)}


def execute_crypto(ticker: str, action: str, confidence: float, reason: str,
                   portfolio: dict, amount_krw: float = None, manual_qty: float = None) -> dict:
    """코인 매매 실행"""
    if action == "HOLD":
        return {"status": "skipped", "reason": "HOLD 결정"}

    min_conf = 0.70 if action == "BUY" else 0.60
    if confidence < min_conf:
        return {"status": "skipped", "reason": f"신뢰도 부족 ({confidence:.2f} < {min_conf:.2f})"}

    try:
        current_price = upbit_client.get_current_price(ticker)
        cash = portfolio.get("cash", 0)

        if not current_price or current_price <= 0:
            return {"status": "skipped", "reason": f"현재가 조회 실패 ({ticker})"}

        if action == "BUY":
            # AI가 지정한 금액 사용, 없으면 잔고의 20%
            amount = float(amount_krw) if amount_krw else cash * 0.2
            amount = min(amount, cash * 0.8)  # 최대 잔고 80% 안전 제한
            if amount < config.UPBIT_MIN_ORDER_KRW:
                return {"status": "skipped", "reason": f"매수금액 부족 ({amount:,.0f}원 < 최소 {config.UPBIT_MIN_ORDER_KRW:,}원)"}
            result = upbit_client.place_order(ticker, "buy", amount_krw=amount)
            # 업비트 시장가 매수는 원화 금액으로 주문 → 체결 수량은 응답 executed_volume에서 파싱
            executed_qty = float(result.get("executed_volume") or 0)
            if not executed_qty and current_price:
                executed_qty = amount / current_price  # 체결 전 응답이면 추정값 사용
            record = {
                "time": datetime.now(KST).isoformat(),
                "market": "crypto",
                "ticker": ticker,
                "action": "BUY",
                "price": current_price,
                "qty": executed_qty,
                "amount_krw": amount,
                "confidence": confidence,
                "reason": reason,
                "result": result,
            }

        elif action == "SELL":
            holding = next((h for h in portfolio.get("holdings", []) if h["ticker"] == ticker), None)
            if not holding:
                return {"status": "skipped", "reason": "보유 코인 없음"}
            # 수동 매매 시 명시적 수량 지정 가능, 없으면 전량
            qty = float(manual_qty) if manual_qty and float(manual_qty) <= holding["qty"] else holding["qty"]
            sell_value = current_price * qty
            if sell_value < config.UPBIT_MIN_ORDER_KRW:
                return {"status": "skipped", "reason": f"매도 금액 부족 ({sell_value:.0f}원, 최소 {config.UPBIT_MIN_ORDER_KRW:,}원)"}
            result = upbit_client.place_order(ticker, "sell", qty=qty)
            record = {
                "time": datetime.now(KST).isoformat(),
                "market": "crypto",
                "ticker": ticker,
                "action": "SELL",
                "price": current_price,
                "qty": qty,
                "amount_krw": current_price * qty,
                "avg_price":  holding.get("avg_price", 0),
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



def get_realized_pl() -> dict:
    """매도 기록 기반 실현 손익 집계."""
    trades = _load_trades()
    total_pl = 0
    total_pl_stock = 0
    total_pl_crypto = 0
    count = 0
    for t in trades:
        if t.get("action") != "SELL":
            continue
        count += 1
        avg = t.get("avg_price", 0)
        price = t.get("price", 0)
        qty = t.get("qty", 0) or (t.get("amount_krw", 0) / price if price else 0)
        # avg_price가 있을 때만 실현 손익 계산 (없으면 0으로 처리)
        if avg and price and qty:
            pl = (price - avg) * qty
            total_pl += pl
            if t.get("market") == "stock":
                total_pl_stock += pl
            else:
                total_pl_crypto += pl
    return {
        "total":  round(total_pl),
        "stock":  round(total_pl_stock),
        "crypto": round(total_pl_crypto),
        "count":  count,
    }


def get_trade_count() -> int:
    return len(_load_trades())


def get_trade_history(limit: int = 50) -> list:
    trades = _load_trades()
    return list(reversed(trades[-limit:]))

def get_trade_history_page(page: int = 1, per_page: int = 20) -> dict:
    trades = list(reversed(_load_trades()))
    total = len(trades)
    start = (page - 1) * per_page
    end   = start + per_page
    return {
        "trades":    trades[start:end],
        "total":     total,
        "page":      page,
        "per_page":  per_page,
        "total_pages": max(1, -(-total // per_page)),  # ceiling division
    }
