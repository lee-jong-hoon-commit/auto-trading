"""규칙 기반 리스크 관리 레이어.

AI는 진입(BUY) 선택만 담당하고, 청산은 이 모듈이 기계적으로 결정한다:
- 손절: 손익률이 STOP_LOSS_PCT 이하로 떨어지면 즉시 매도
- 트레일링 익절: TAKE_PROFIT_TRIGGER_PCT 도달 후 고점 대비 TRAILING_STOP_PCT 하락 시 매도
- 재매수 쿨다운: 매도 후 REBUY_COOLDOWN_HOURS 동안 동일 종목 매수 금지
- 최소 보유시간: MIN_HOLD_MINUTES 이내 AI SELL 차단 (손절선은 예외)
- 일일 손실 한도: 당일 실현손실이 총자산의 DAILY_LOSS_LIMIT_PCT% 초과 시 신규매수 중단

상태는 logs/positions.json에 저장한다. 브로커 잔고에는 있는데 기록이 없는
포지션(재시작·기존 보유분)은 avg_price를 진입가로 간주해 자동 등록한다.
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import config

KST = timezone(timedelta(hours=9))
logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent.parent / "logs" / "positions.json"
STATE_FILE.parent.mkdir(exist_ok=True)


def _load_state() -> dict:
    try:
        state = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    state.setdefault("positions", {})
    state.setdefault("cooldowns", {})
    return state


def _save_state(state: dict):
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(STATE_FILE)


def register_entry(key: str, name: str, market: str, entry_price: float):
    """매수 체결 시 포지션 등록 (key = 종목코드 또는 KRW-티커)."""
    state = _load_state()
    state["positions"][key] = {
        "name": name,
        "market": market,
        "entry_price": entry_price,
        "entry_time": datetime.now(KST).isoformat(),
        "peak_price": entry_price,
        "trailing_armed": False,
    }
    _save_state(state)
    logger.info(f"[RISK] 포지션 등록: {name}({key}) 진입가 {entry_price:,.2f}")


def clear_position(key: str, set_cooldown: bool = True):
    """매도 체결 시 포지션 제거 + 재매수 쿨다운 설정."""
    state = _load_state()
    pos = state["positions"].pop(key, None)
    if set_cooldown:
        until = datetime.now(KST) + timedelta(hours=config.REBUY_COOLDOWN_HOURS)
        state["cooldowns"][key] = until.isoformat()
    _save_state(state)
    if pos:
        logger.info(f"[RISK] 포지션 종료: {pos.get('name', key)}({key})")


def is_in_cooldown(key: str) -> bool:
    """매도 후 재매수 쿨다운 중인지."""
    state = _load_state()
    until_str = state["cooldowns"].get(key)
    if not until_str:
        return False
    try:
        until = datetime.fromisoformat(until_str)
    except ValueError:
        return False
    if datetime.now(KST) >= until:
        # 만료된 쿨다운 정리
        state["cooldowns"].pop(key, None)
        _save_state(state)
        return False
    return True


def held_too_short(key: str) -> bool:
    """최소 보유시간(MIN_HOLD_MINUTES) 미달 여부 — AI SELL 차단용 (손절선은 예외)."""
    state = _load_state()
    pos = state["positions"].get(key)
    if not pos:
        return False
    try:
        entry_time = datetime.fromisoformat(pos["entry_time"])
    except (KeyError, ValueError):
        return False
    return datetime.now(KST) - entry_time < timedelta(minutes=config.MIN_HOLD_MINUTES)


def evaluate_holdings(stock_holdings: list[dict], crypto_holdings: list[dict]) -> list[dict]:
    """보유 종목 전체를 규칙으로 평가해 강제 매도 목록을 반환.

    반환 항목: {"key", "name", "market", "reason"}
    부수효과: 미등록 포지션 자동 등록, 고점(peak) 갱신, 트레일링 발동 상태 저장.
    """
    state = _load_state()
    positions = state["positions"]
    exits = []
    now_iso = datetime.now(KST).isoformat()

    def _check(key: str, name: str, market: str, current_price: float, profit_rate: float, avg_price: float):
        pos = positions.get(key)
        if pos is None:
            # 기록 없는 기존 보유분 — avg_price를 진입가로 자동 등록
            pos = {
                "name": name, "market": market,
                "entry_price": avg_price or current_price,
                "entry_time": now_iso,
                "peak_price": max(current_price, avg_price or 0),
                "trailing_armed": False,
            }
            positions[key] = pos
            logger.info(f"[RISK] 기존 보유분 자동 등록: {name}({key}) 진입가 {pos['entry_price']:,.2f}")

        # 고점 갱신
        if current_price > pos.get("peak_price", 0):
            pos["peak_price"] = current_price

        # 1) 백스톱 강제 손절 (기록 유실 등 어떤 경우에도 걸리는 최후 방어선)
        if profit_rate <= config.EMERGENCY_CUT_PCT:
            exits.append({"key": key, "name": name, "market": market,
                          "reason": f"긴급 손절 (손익률 {profit_rate:+.1f}% ≤ {config.EMERGENCY_CUT_PCT:.0f}%)"})
            return

        # 2) 손절선
        if profit_rate <= config.STOP_LOSS_PCT:
            exits.append({"key": key, "name": name, "market": market,
                          "reason": f"손절 (손익률 {profit_rate:+.1f}% ≤ {config.STOP_LOSS_PCT:.0f}%)"})
            return

        # 3) 트레일링 익절: 발동 수익률 도달 → 고점 대비 하락폭 감시
        if not pos.get("trailing_armed") and profit_rate >= config.TAKE_PROFIT_TRIGGER_PCT:
            pos["trailing_armed"] = True
            logger.info(f"[RISK] 트레일링 발동: {name}({key}) 손익률 {profit_rate:+.1f}%")
        if pos.get("trailing_armed") and pos.get("peak_price", 0) > 0:
            drop_pct = (current_price - pos["peak_price"]) / pos["peak_price"] * 100
            if drop_pct <= -config.TRAILING_STOP_PCT:
                exits.append({"key": key, "name": name, "market": market,
                              "reason": f"트레일링 익절 (고점 {pos['peak_price']:,.2f} 대비 {drop_pct:.1f}%, 손익률 {profit_rate:+.1f}%)"})

    for h in stock_holdings:
        _check(h["code"], h.get("name", h["code"]), "stock",
               h.get("current_price", 0), h.get("profit_rate", 0), h.get("avg_price", 0))
    for h in crypto_holdings:
        _check(h["ticker"], h["ticker"], "crypto",
               h.get("current_price", 0), h.get("profit_rate", 0), h.get("avg_price", 0))

    # 잔고에서 사라진 포지션 정리 (수동 매도 등)
    held_keys = {h["code"] for h in stock_holdings} | {h["ticker"] for h in crypto_holdings}
    stale = [k for k in positions if k not in held_keys]
    for k in stale:
        positions.pop(k)

    _save_state(state)
    return exits


def daily_buy_count() -> int:
    """오늘(KST) 실행된 매수 건수 — 하루 신규 진입 상한 체크용."""
    try:
        from trader import executor
        trades = executor._load_trades()
    except Exception:
        return 0
    today = datetime.now(KST).date().isoformat()
    return sum(1 for t in trades
               if t.get("action") == "BUY" and str(t.get("time", "")).startswith(today))


def daily_realized_loss_exceeded(total_assets: float) -> tuple[bool, float]:
    """당일(KST) 실현손실이 총자산의 DAILY_LOSS_LIMIT_PCT%를 초과했는지.

    반환: (초과 여부, 당일 실현손익 원)
    """
    if total_assets <= 0:
        return False, 0.0
    try:
        from trader import executor
        trades = executor._load_trades()
    except Exception:
        return False, 0.0

    today = datetime.now(KST).date().isoformat()
    pl = 0.0
    for t in trades:
        if t.get("action") != "SELL" or not str(t.get("time", "")).startswith(today):
            continue
        avg, price = t.get("avg_price", 0), t.get("price", 0)
        qty = t.get("qty", 0) or 0
        if avg and price and qty:
            pl += (price - avg) * qty
    limit = -total_assets * config.DAILY_LOSS_LIMIT_PCT / 100
    return pl <= limit, pl
