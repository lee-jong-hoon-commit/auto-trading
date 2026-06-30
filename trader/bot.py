"""자동매매 봇 메인 루프"""
import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import SchedulerAlreadyRunningError, SchedulerNotRunningError
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
}


def get_state() -> dict:
    return _state


async def run_cycle():
    """1회 분석 + 매매 사이클"""
    _state["errors"] = []
    logger.info("=== 자동매매 사이클 시작 ===")

    try:
        # 1. 포트폴리오 조회
        try:
            stock_portfolio = kis_client.get_balance() if config.is_kis_ready else {"cash": 0, "holdings": []}
        except Exception as e:
            logger.warning(f"주식 잔고 조회 실패 (분석 계속): {e}")
            stock_portfolio = {"cash": 0, "holdings": []}
        try:
            crypto_portfolio = upbit_client.get_balance() if config.is_upbit_ready else {"cash": 0, "holdings": []}
        except Exception as e:
            logger.warning(f"코인 잔고 조회 실패 (분석 계속): {e}")
            crypto_portfolio = {"cash": 0, "holdings": []}

        # 2. 손절/익절 우선 체크
        emergency_actions = executor.check_stop_loss_take_profit(stock_portfolio, crypto_portfolio)
        for ea in emergency_actions:
            if ea["type"] == "stock":
                executor.execute_stock(ea["code"], ea["name"], "SELL", 1.0, ea["reason"], stock_portfolio)
            else:
                executor.execute_crypto(ea["ticker"], "SELL", 1.0, ea["reason"], crypto_portfolio)

        # 3. 분석 대상 종목 선정 (보유 + 유망 후보)
        stock_candidates = []
        crypto_candidates = []

        # 종목당 매수 예산 = 예수금 × 최대 비중. 이 예산으로 살 수 있는 종목만 후보로.
        stock_budget = stock_portfolio.get("cash", 0) * config.MAX_POSITION_RATIO

        if config.is_kis_ready:
            held_codes = {h["code"] for h in stock_portfolio.get("holdings", [])}
            # 1차: 그날그날 거래대금 상위(예산 내) 동적 유니버스. 실패 시 관심종목으로 폴백.
            dynamic = kis_client.get_dynamic_stocks(budget=stock_budget, limit=40)
            if dynamic:
                source = "거래대금 상위"
                universe = [s for s in dynamic if s["code"] not in held_codes]
            else:
                source = "관심종목(폴백)"
                universe = [s for s in watchlist.get_stocks() if s["code"] not in held_codes]
            affordable = kis_client.select_affordable_stocks(universe, stock_budget)
            logger.info(f"분석 대상 종목[{source}]: {len(affordable)}개 (예산 {stock_budget:,.0f}원/종목, 후보 {len(universe)}개)")
            screened = ai_engine.quick_screen(affordable, "stock")
            stock_candidates = list(stock_portfolio.get("holdings", [])) + screened
            stock_candidates = stock_candidates[:config.MAX_STOCK_POSITIONS + 3]

        if config.is_upbit_ready:
            held_tickers = {h["ticker"] for h in crypto_portfolio.get("holdings", [])}
            # 사용자 관심 코인을 우선 포함하고 거래량 상위 코인으로 보강(중복 제거)
            all_tickers = list(dict.fromkeys(watchlist.get_tickers() + upbit_client.get_top_tickers(30)))
            ticker_dicts = [{"ticker": t, "name": t} for t in all_tickers if t not in held_tickers]
            screened = ai_engine.quick_screen(ticker_dicts, "crypto")
            crypto_candidates = [{"ticker": h["ticker"]} for h in crypto_portfolio.get("holdings", [])] + screened
            crypto_candidates = crypto_candidates[:config.MAX_CRYPTO_POSITIONS + 2]

        # 4. 기술적 지표 계산
        stock_summaries = []
        for s in stock_candidates:
            code = s.get("code", s.get("ticker", ""))
            name = s.get("name", code)
            try:
                df = kis_client.get_ohlcv(code)
                indicators = analyzer.compute_indicators(df)
                summary = analyzer.summarize_for_ai(name, indicators, "stock")
                stock_summaries.append(summary)
            except Exception as e:
                logger.warning(f"주식 분석 실패 {code}: {e}")

        crypto_summaries = []
        for c in crypto_candidates:
            ticker = c.get("ticker", "")
            try:
                df = upbit_client.get_ohlcv(ticker)
                indicators = analyzer.compute_indicators(df)
                summary = analyzer.summarize_for_ai(ticker, indicators, "crypto")
                crypto_summaries.append(summary)
            except Exception as e:
                logger.warning(f"코인 분석 실패 {ticker}: {e}")

        # 5. AI 의사결정
        portfolio_status = {
            "stock_cash": stock_portfolio.get("cash", 0),
            "crypto_cash": crypto_portfolio.get("cash", 0),
            "stock_budget_per_position": round(stock_budget),
            "stock_holdings": stock_portfolio.get("holdings", []),
            "crypto_holdings": crypto_portfolio.get("holdings", []),
        }
        ai_result = ai_engine.analyze_and_decide(stock_summaries, crypto_summaries, portfolio_status)

        _state["last_decisions"] = ai_result.get("decisions", [])
        _state["market_summary"] = ai_result.get("market_summary", "")
        _state["last_summaries"] = stock_summaries + crypto_summaries

        # 6. 매매 실행
        executed = []
        for decision in ai_result.get("decisions", []):
            action = decision.get("action", "HOLD")
            confidence = decision.get("confidence", 0)
            reason = decision.get("reason", "")
            ticker = decision.get("ticker", "")
            name = decision.get("name", ticker)

            if ticker.startswith("KRW-"):
                result = executor.execute_crypto(ticker, action, confidence, reason, crypto_portfolio)
            else:
                result = executor.execute_stock(ticker, name, action, confidence, reason, stock_portfolio)

            if result.get("status") == "executed":
                executed.append(result)

        _state["last_trades"] = executed
        _state["last_run"] = datetime.now().isoformat()
        logger.info(f"=== 사이클 완료: {len(executed)}건 실행 ===")

    except Exception as e:
        logger.error(f"사이클 오류: {e}", exc_info=True)
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
    # 첫 사이클은 백그라운드로 실행 (HTTP 응답 블로킹 방지)
    asyncio.create_task(run_cycle())
    logger.info(f"봇 시작 (주기: {config.TRADE_INTERVAL_MINUTES}분)")


async def stop_bot():
    """봇 정지"""
    _state["running"] = False
    try:
        if scheduler.running:
            scheduler.remove_job("trade_cycle")
    except Exception:
        pass
    logger.info("봇 정지")


async def run_once():
    """수동 1회 실행"""
    await run_cycle()
