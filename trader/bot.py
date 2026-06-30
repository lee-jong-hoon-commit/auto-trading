"""자동매매 봇 메인 루프"""
import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import SchedulerAlreadyRunningError
from config import config
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
    _state["activity_log"].append({"time": datetime.now().isoformat(), "msg": msg, "level": level})
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
            try:
                return await asyncio.to_thread(kis_client.get_balance) if config.is_kis_ready else {"cash": 0, "holdings": []}
            except Exception as e:
                _log(f"주식 잔고 조회 실패 (분석 계속): {e}", "warning")
                return {"cash": 0, "holdings": []}

        async def _get_crypto_portfolio():
            try:
                return await asyncio.to_thread(upbit_client.get_balance) if config.is_upbit_ready else {"cash": 0, "holdings": []}
            except Exception as e:
                _log(f"코인 잔고 조회 실패 (분석 계속): {e}", "warning")
                return {"cash": 0, "holdings": []}

        stock_portfolio, crypto_portfolio = await asyncio.gather(
            _get_stock_portfolio(), _get_crypto_portfolio()
        )

        stock_cash = stock_portfolio.get("cash", 0)
        crypto_cash = crypto_portfolio.get("cash", 0)
        stock_holdings = stock_portfolio.get("holdings", [])
        crypto_holdings = crypto_portfolio.get("holdings", [])
        _log(f"잔고 — 주식: {stock_cash:,.0f}원 (보유 {len(stock_holdings)}종목), 코인: {crypto_cash:,.0f}원 (보유 {len(crypto_holdings)}종류)")

        # 2. 분석 대상 종목 선정
        stock_candidates = []
        crypto_candidates = []

        if config.is_kis_ready:
            held_codes = {h["code"] for h in stock_holdings}
            _log("거래대금 상위 종목 조회 중...")
            dynamic = await asyncio.to_thread(kis_client.get_dynamic_stocks, None, 40)
            if dynamic:
                source = "거래대금 상위"
                universe = [s for s in dynamic if s["code"] not in held_codes]
            else:
                source = "관심종목(폴백)"
                universe = [s for s in watchlist.get_stocks() if s["code"] not in held_codes]
            top_universe = universe[:40]
            _log(f"[{source}] {len(top_universe)}개 종목 AI 1차 스크리닝 중...")
            screened = await asyncio.to_thread(ai_engine.quick_screen, top_universe, "stock")
            stock_candidates = list(stock_holdings) + screened
            stock_candidates = stock_candidates[:config.STOCK_ANALYSIS_LIMIT]
            _log(f"주식 분석 대상 확정: {len(stock_candidates)}개")

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

        async def _stock_summary(s):
            code = s.get("code", s.get("ticker", ""))
            name = s.get("name", code)
            try:
                df = await asyncio.to_thread(kis_client.get_ohlcv, code)
                indicators = analyzer.compute_indicators(df)
                return analyzer.summarize_for_ai(name, indicators, "stock")
            except Exception as e:
                _log(f"주식 지표 계산 실패 {code}: {e}", "warning")
                return None

        async def _crypto_summary(c):
            ticker = c.get("ticker", "")
            try:
                df = await asyncio.to_thread(upbit_client.get_ohlcv, ticker)
                indicators = analyzer.compute_indicators(df)
                return analyzer.summarize_for_ai(ticker, indicators, "crypto")
            except Exception as e:
                _log(f"코인 지표 계산 실패 {ticker}: {e}", "warning")
                return None

        stock_results, crypto_results = await asyncio.gather(
            asyncio.gather(*[_stock_summary(s) for s in stock_candidates]),
            asyncio.gather(*[_crypto_summary(c) for c in crypto_candidates]),
        )
        stock_summaries = [r for r in stock_results if r]
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
        executed = []
        for decision in decisions:
            action     = decision.get("action", "HOLD")
            confidence = decision.get("confidence", 0)
            reason     = decision.get("reason", "")
            ticker     = decision.get("ticker", "")
            name       = decision.get("name", ticker)
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
        _state["last_run"] = datetime.now().isoformat()
        _log(f"=== 사이클 완료: {len(executed)}건 체결 ===")

    except Exception as e:
        logger.error(f"사이클 오류: {e}", exc_info=True)
        _log(f"사이클 오류: {e}", "error")
        _state["errors"].append(str(e))


scheduler = AsyncIOScheduler()


async def start_bot():
    """봇 시작"""
    _state["running"] = True
    _state["errors"] = []
    scheduler.add_job(
        run_cycle,
        "interval",
        minutes=config.TRADE_INTERVAL_MINUTES,
        id="trade_cycle",
        replace_existing=True,
    )
    try:
        scheduler.start()
    except SchedulerAlreadyRunningError:
        pass
    asyncio.create_task(run_cycle())
    _log(f"봇 시작 — {config.TRADE_INTERVAL_MINUTES}분 주기로 자동 매매")


async def stop_bot():
    """봇 정지"""
    _state["running"] = False
    try:
        if scheduler.running:
            scheduler.remove_job("trade_cycle")
    except Exception:
        pass
    _log("봇 정지")


async def run_once():
    """수동 1회 실행"""
    await run_cycle()
