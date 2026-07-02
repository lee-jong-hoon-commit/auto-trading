"""자동매매 봇 메인 루프"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from config import config

KST = timezone(timedelta(hours=9))

def _is_stock_market_open() -> bool:
    """주식 거래 가능 시간 여부 (KST 09:00~18:00, 평일)"""
    now = datetime.now(KST)
    if now.weekday() >= 5:   # 토(5)·일(6) 제외
        return False
    return 9 <= now.hour < 18
from trader import kis_client, upbit_client, analyzer, ai_engine, executor, watchlist

logger = logging.getLogger(__name__)

_state = {
    "running": False,
    "last_run": None,
    "last_decisions": [],
    "last_summaries": [],
    "market_summary": "",
    "last_trades": [],
    "errors": [],
    "activity_log": [],
}

# 잔고 조회 실패 시 폴백할 직전 성공 값
_last_stock_portfolio: dict = {"cash": 0, "holdings": []}
_last_crypto_portfolio: dict = {"cash": 0, "holdings": []}


def get_state() -> dict:
    return _state


def _log(msg: str, level: str = "info"):
    """Python 로거 + 대시보드 activity_log에 동시 기록."""
    if level == "error":
        logger.error(msg)
    elif level == "warning":
        logger.warning(msg)
    else:
        logger.info(msg)
    _state["activity_log"].append({"time": datetime.now(KST).isoformat(), "msg": msg, "level": level})
    if len(_state["activity_log"]) > 200:
        _state["activity_log"] = _state["activity_log"][-200:]


async def run_cycle():
    """1회 분석 + 매매 사이클 — 모든 블로킹 I/O는 스레드에서 실행."""
    _state["errors"] = []
    _log("=== 자동매매 사이클 시작 ===")

    try:
        # 1. 포트폴리오 조회 (병렬)
        _log("잔고 조회 중...")
        async def _get_stock_portfolio():
            global _last_stock_portfolio
            try:
                result = await asyncio.to_thread(kis_client.get_balance) if config.is_kis_ready else {"cash": 0, "holdings": []}
                _last_stock_portfolio = result
                return result
            except Exception as e:
                _log(f"주식 잔고 조회 실패 — 직전 값 사용 (cash={_last_stock_portfolio.get('cash',0):,.0f}원): {e}", "warning")
                return _last_stock_portfolio

        async def _get_crypto_portfolio():
            global _last_crypto_portfolio
            try:
                result = await asyncio.to_thread(upbit_client.get_balance) if config.is_upbit_ready else {"cash": 0, "holdings": []}
                _last_crypto_portfolio = result
                return result
            except Exception as e:
                _log(f"코인 잔고 조회 실패 — 직전 값 사용: {e}", "warning")
                return _last_crypto_portfolio

        stock_portfolio, crypto_portfolio = await asyncio.gather(
            _get_stock_portfolio(), _get_crypto_portfolio()
        )

        # orderable_cash = 실제 주문가능금액 (주식 매수 후 T+2 정산 반영)
        # cash = 예수금 총액 (매수 후에도 변하지 않으므로 예산으로 쓰면 안 됨)
        stock_cash = stock_portfolio.get("orderable_cash") or stock_portfolio.get("cash", 0)
        crypto_cash = crypto_portfolio.get("cash", 0)
        stock_holdings = stock_portfolio.get("holdings", [])
        crypto_holdings = crypto_portfolio.get("holdings", [])
        _log(f"잔고 — 주식 주문가능: {stock_cash:,.0f}원 (보유 {len(stock_holdings)}종목), 코인: {crypto_cash:,.0f}원 (보유 {len(crypto_holdings)}종류)")

        # 2. 분석 대상 종목 선정
        stock_candidates = []
        crypto_candidates = []

        stock_market_open = _is_stock_market_open()
        if config.is_kis_ready and not stock_market_open:
            now_kst = datetime.now(KST)
            _log(f"주식 시장 시간 외 ({now_kst.strftime('%H:%M')} KST) — 주식 분석·거래 건너뜀 (09:00~18:00만 운영)")

        if config.is_kis_ready and stock_market_open:
            held_codes = {h["code"] for h in stock_holdings}
            _log(f"거래대금 상위 저가주 스캔 중 (예산 {stock_cash:,.0f}원 이하)...")
            dynamic = await asyncio.to_thread(kis_client.get_dynamic_stocks, stock_cash, 40)
            if dynamic:
                source = "거래대금 상위"
                # 채권·구조화상품(알파벳 포함) 및 ETF 브랜드명 제외
                _ETF_PREFIXES = ("KODEX", "TIGER", "KINDEX", "SOL", "ACE", "RISE", "HANARO", "ARIRANG", "KOSEF")
                universe = [
                    s for s in dynamic
                    if s["code"] not in held_codes
                    and len(s["code"]) == 6 and s["code"].isdigit()
                    and not s.get("name", "").startswith(_ETF_PREFIXES)
                ]
            else:
                source = "관심종목(폴백)"
                universe = [s for s in watchlist.get_stocks() if s["code"] not in held_codes]
            # quick_screen 없이 거래대금 상위 직접 사용 (API가 이미 볼륨순 정렬)
            stock_candidates = list(stock_holdings) + universe[:config.STOCK_ANALYSIS_LIMIT]
            _log(f"[{source}] 주식 분석 대상 확정: {len(stock_candidates)}개")

        if config.is_upbit_ready:
            held_tickers = {h["ticker"] for h in crypto_holdings}
            top_tickers = await asyncio.to_thread(upbit_client.get_top_tickers, 30)
            all_tickers = list(dict.fromkeys(watchlist.get_tickers() + top_tickers))
            ticker_dicts = [{"ticker": t, "name": t} for t in all_tickers if t not in held_tickers]
            _log(f"코인 {len(ticker_dicts)}개 AI 1차 스크리닝 중...")
            screened = await asyncio.to_thread(ai_engine.quick_screen, ticker_dicts, "crypto")
            crypto_candidates = [{"ticker": h["ticker"]} for h in crypto_holdings] + screened
            crypto_candidates = crypto_candidates[:config.CRYPTO_ANALYSIS_LIMIT]
            _log(f"코인 분석 대상 확정: {len(crypto_candidates)}개")

        # 3. 기술적 지표 계산 (병렬)
        _log("기술적 지표 계산 중...")

        def _stock_summary_sync(s):
            code = s.get("code", s.get("ticker", ""))
            name = s.get("name", code)
            df = kis_client.get_ohlcv(code)
            indicators = analyzer.compute_indicators(df)
            text = analyzer.summarize_for_ai(name, indicators, "stock")
            price = indicators.get("current_price", 0) if indicators else 0
            return text, price

        def _crypto_summary_sync(c):
            ticker = c.get("ticker", "")
            df = upbit_client.get_ohlcv(ticker)
            indicators = analyzer.compute_indicators(df)
            return analyzer.summarize_for_ai(ticker, indicators, "crypto")

        _kis_sem = asyncio.Semaphore(3)  # KIS API 동시 호출 3개로 제한

        async def _stock_summary(s):
            code = s.get("code", s.get("ticker", ""))
            is_holding = code in held_codes
            async with _kis_sem:
                try:
                    text, price = await asyncio.to_thread(_stock_summary_sync, s)
                    return text, price, is_holding
                except Exception as e:
                    _log(f"주식 지표 계산 실패 {code}: {e}", "warning")
                    return None, 0, is_holding

        async def _crypto_summary(c):
            ticker = c.get("ticker", "")
            try:
                return await asyncio.to_thread(_crypto_summary_sync, c)
            except Exception as e:
                _log(f"코인 지표 계산 실패 {ticker}: {e}", "warning")
                return None

        stock_results, crypto_results = await asyncio.gather(
            asyncio.gather(*[_stock_summary(s) for s in stock_candidates]),
            asyncio.gather(*[_crypto_summary(c) for c in crypto_candidates]),
        )
        # 주식은 1주 단위 매수 → 현재가 > 잔고면 살 수 없으므로 AI에 넘기기 전 필터링
        # 보유 종목(is_holding=True)은 SELL 판단을 위해 가격 무관 항상 포함
        raw_stock = [(text, price, is_holding) for text, price, is_holding in stock_results if text]
        unaffordable = [text.split("\n")[0] for text, price, is_holding in raw_stock
                        if price > stock_cash > 0 and not is_holding]
        if unaffordable:
            _log(f"예산 초과 주식 제외 ({stock_cash:,.0f}원): {', '.join(unaffordable)}")
        stock_summaries = [text for text, price, is_holding in raw_stock
                           if is_holding or price <= stock_cash or stock_cash <= 0]
        crypto_summaries = [r for r in crypto_results if r]
        _log(f"지표 계산 완료 — 주식 {len(stock_summaries)}개, 코인 {len(crypto_summaries)}개")

        # 4. AI 의사결정
        _log(f"AI 분석 요청 중 ({config.AI_PROVIDER})...")
        portfolio_status = {
            "stock_cash": stock_cash,
            "crypto_cash": crypto_cash,
            "stock_holdings": stock_holdings,
            "crypto_holdings": crypto_holdings,
        }
        ai_result = await asyncio.to_thread(
            ai_engine.analyze_and_decide, stock_summaries, crypto_summaries, portfolio_status
        )

        _state["last_decisions"] = ai_result.get("decisions", [])
        _state["market_summary"] = ai_result.get("market_summary", "")
        _state["last_summaries"] = stock_summaries + crypto_summaries

        decisions = ai_result.get("decisions", [])
        buy_cnt  = sum(1 for d in decisions if d.get("action") == "BUY")
        sell_cnt = sum(1 for d in decisions if d.get("action") == "SELL")
        hold_cnt = sum(1 for d in decisions if d.get("action") == "HOLD")
        _log(f"AI 결정 완료: BUY {buy_cnt}건, SELL {sell_cnt}건, HOLD {hold_cnt}건")

        # 5. 매매 실행
        # AI가 반환한 name은 틀릴 수 있으므로 실제 분석 대상 목록의 이름 우선 사용
        stock_name_map = {s.get("code", ""): s.get("name", "") for s in stock_candidates}

        executed = []
        for decision in decisions:
            action     = decision.get("action", "HOLD")
            confidence = decision.get("confidence", 0)
            reason     = decision.get("reason", "")
            ticker     = decision.get("ticker", "")
            name       = stock_name_map.get(ticker) or decision.get("name", ticker)
            amount_krw = decision.get("amount_krw")

            if action == "HOLD":
                continue

            _log(f"주문 실행: {action} {name}({ticker}) confidence={confidence:.2f}")
            if ticker.startswith("KRW-"):
                result = await asyncio.to_thread(
                    executor.execute_crypto, ticker, action, confidence, reason, crypto_portfolio, amount_krw
                )
            else:
                result = await asyncio.to_thread(
                    executor.execute_stock, ticker, name, action, confidence, reason, stock_portfolio, amount_krw
                )

            status = result.get("status")
            if status == "executed":
                amt = result.get("amount", result.get("amount_krw", 0))
                _log(f"✓ 체결: {action} {name} {amt:,.0f}원")
                executed.append(result)
            elif status == "skipped":
                _log(f"→ 스킵: {name} — {result.get('reason', '')}")
            else:
                _log(f"✗ 오류: {name} — {result.get('error', '')}", "error")

        _state["last_trades"] = executed
        _state["last_run"] = datetime.now(KST).isoformat()
        _log(f"=== 사이클 완료: {len(executed)}건 체결 ===")

    except Exception as e:
        logger.error(f"사이클 오류: {e}", exc_info=True)
        _log(f"사이클 오류: {e}", "error")
        _state["errors"].append(str(e))


_cycle_task: asyncio.Task | None = None


async def _cycle_loop():
    """5분 주기 사이클 루프 — APScheduler 없이 asyncio.sleep 사용."""
    while _state["running"]:
        await run_cycle()
        if not _state["running"]:
            break
        await asyncio.sleep(config.TRADE_INTERVAL_MINUTES * 60)


async def start_bot():
    """봇 시작"""
    global _cycle_task
    _state["running"] = True
    _state["errors"] = []
    _cycle_task = asyncio.create_task(_cycle_loop())
    _log(f"봇 시작 — {config.TRADE_INTERVAL_MINUTES}분 주기로 자동 매매")


async def stop_bot():
    """봇 정지"""
    global _cycle_task
    _state["running"] = False
    if _cycle_task and not _cycle_task.done():
        _cycle_task.cancel()
        try:
            await _cycle_task
        except asyncio.CancelledError:
            pass
    _log("봇 정지")


async def run_once():
    """수동 1회 실행"""
    await run_cycle()
