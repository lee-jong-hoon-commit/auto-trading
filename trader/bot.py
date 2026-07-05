"""자동매매 봇 메인 루프"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from config import config

KST = timezone(timedelta(hours=9))

def _is_stock_market_open() -> bool:
    """주식 거래 가능 시간 여부 (KST 09:00~15:30, 평일)"""
    now = datetime.now(KST)
    if now.weekday() >= 5:   # 토(5)·일(6) 제외
        return False
    t = now.hour * 60 + now.minute
    return 9 * 60 <= t < 15 * 60 + 30
from trader import kis_client, upbit_client, analyzer, ai_engine, executor, watchlist, risk

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
        _oc = stock_portfolio.get("orderable_cash")
        stock_cash = _oc if _oc is not None else stock_portfolio.get("cash", 0)
        crypto_cash = crypto_portfolio.get("cash", 0)
        stock_holdings = stock_portfolio.get("holdings", [])
        crypto_holdings = crypto_portfolio.get("holdings", [])
        _log(f"잔고 — 주식 주문가능: {stock_cash:,.0f}원 (보유 {len(stock_holdings)}종목), 코인: {crypto_cash:,.0f}원 (보유 {len(crypto_holdings)}종류)")

        # 1.5 규칙 기반 청산 (AI 판단과 무관하게 기계적 실행)
        #     손절 -3% / +6% 도달 후 트레일링 -2% / 백스톱 -20%
        rule_exits = risk.evaluate_holdings(stock_holdings, crypto_holdings)
        stock_market_open_now = _is_stock_market_open()
        for ex in rule_exits:
            key, name, market, reason = ex["key"], ex["name"], ex["market"], ex["reason"]
            if market == "stock" and not stock_market_open_now:
                _log(f"⏸ 규칙 매도 대기 (장 마감): {name}({key}) — {reason}", "warning")
                continue
            _log(f"🔻 규칙 매도: {name}({key}) — {reason}")
            if market == "stock":
                res = await asyncio.to_thread(
                    executor.execute_stock, key, name, "SELL", 1.0, reason, stock_portfolio
                )
            else:
                res = await asyncio.to_thread(
                    executor.execute_crypto, key, "SELL", 1.0, reason, crypto_portfolio
                )
            if res.get("status") == "executed":
                _log(f"✓ 규칙 매도 체결: {name} {res.get('amount', res.get('amount_krw', 0)):,.0f}원")
                # 이후 단계(후보 선정·AI)에 팔린 종목이 보유분으로 잡히지 않게 제거
                if market == "stock":
                    stock_holdings[:] = [h for h in stock_holdings if h["code"] != key]
                else:
                    crypto_holdings[:] = [h for h in crypto_holdings if h["ticker"] != key]
            else:
                _log(f"✗ 규칙 매도 실패: {name} — {res.get('error') or res.get('reason','')}", "error")

        # 총자산 (포지션 사이징·일일 손실 한도 기준)
        total_assets = (stock_portfolio.get("total") or 0) + (crypto_portfolio.get("total") or 0)
        max_position_krw = total_assets * config.MAX_POSITION_PCT if total_assets > 0 else 0
        daily_stop, daily_pl = risk.daily_realized_loss_exceeded(total_assets)
        if daily_stop:
            _log(f"🛑 일일 손실 한도 초과 (당일 실현손익 {daily_pl:+,.0f}원) — 오늘 신규 매수 중단", "warning")

        # 2. 분석 대상 종목 선정
        stock_candidates = []
        crypto_candidates = []

        stock_market_open = _is_stock_market_open()
        if config.is_kis_ready and not stock_market_open:
            now_kst = datetime.now(KST)
            _log(f"주식 시장 시간 외 ({now_kst.strftime('%H:%M')} KST) — 주식 분석·거래 건너뜀 (09:00~15:30만 운영)")

        if config.is_kis_ready and stock_market_open:
            held_codes = {h["code"] for h in stock_holdings}
            _log(f"거래대금 상위 저가주 스캔 중 (예산 {stock_cash:,.0f}원 이하)...")
            dynamic = await asyncio.to_thread(kis_client.get_dynamic_stocks, stock_cash, 40)
            if dynamic:
                source = "거래대금 상위"
                _ETF_PREFIXES = ("KODEX", "TIGER", "KINDEX", "SOL", "ACE", "RISE", "HANARO", "ARIRANG", "KOSEF")
                universe = [
                    s for s in dynamic
                    if s["code"] not in held_codes
                    and len(s["code"]) == 6 and s["code"].isdigit()
                    and not s.get("name", "").startswith(_ETF_PREFIXES)
                    and not risk.is_in_cooldown(s["code"])
                ]
            else:
                source = "관심종목(폴백)"
                universe = [s for s in watchlist.get_stocks()
                            if s["code"] not in held_codes and not risk.is_in_cooldown(s["code"])]

            # 주식도 AI 1차 스크리닝 (코인과 동일하게 유망 종목만 선별)
            if len(universe) > config.STOCK_ANALYSIS_LIMIT:
                _log(f"[{source}] 주식 {len(universe)}개 AI 1차 스크리닝 중...")
                screened = await asyncio.to_thread(ai_engine.quick_screen, universe, "stock")
                new_candidates = screened[:config.STOCK_ANALYSIS_LIMIT]
            else:
                new_candidates = universe[:config.STOCK_ANALYSIS_LIMIT]

            stock_candidates = list(stock_holdings) + new_candidates
            _log(f"[{source}] 주식 분석 대상 확정: {len(stock_candidates)}개 (보유 {len(stock_holdings)} + 신규 {len(new_candidates)})")

        if config.is_upbit_ready:
            held_tickers = {h["ticker"] for h in crypto_holdings}
            top_tickers = await asyncio.to_thread(upbit_client.get_top_tickers, 30)
            all_tickers = list(dict.fromkeys(watchlist.get_tickers() + top_tickers))
            snapshot = await asyncio.to_thread(upbit_client.get_market_snapshot, all_tickers)
            ticker_dicts = [{"ticker": t, "name": t, **snapshot.get(t, {})} for t in all_tickers
                            if t not in held_tickers and not risk.is_in_cooldown(t)]
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
            change = indicators.get("change_pct", 0) if indicators else 0
            return text, price, change

        def _crypto_summary_sync(c):
            ticker = c.get("ticker", "")
            df = upbit_client.get_ohlcv(ticker)
            indicators = analyzer.compute_indicators(df)
            text = analyzer.summarize_for_ai(ticker, indicators, "crypto")
            change = indicators.get("change_pct", 0) if indicators else 0
            return text, change

        _kis_sem = asyncio.Semaphore(3)  # KIS API 동시 호출 3개로 제한

        async def _stock_summary(s):
            code = s.get("code", s.get("ticker", ""))
            is_holding = code in held_codes
            async with _kis_sem:
                try:
                    text, price, change = await asyncio.to_thread(_stock_summary_sync, s)
                    return text, price, change, is_holding
                except Exception as e:
                    _log(f"주식 지표 계산 실패 {code}: {e}", "warning")
                    return None, 0, 0, is_holding

        async def _crypto_summary(c):
            ticker = c.get("ticker", "")
            is_holding = ticker in {h["ticker"] for h in crypto_holdings}
            try:
                text, change = await asyncio.to_thread(_crypto_summary_sync, c)
                return text, change, is_holding
            except Exception as e:
                _log(f"코인 지표 계산 실패 {ticker}: {e}", "warning")
                return None, 0, is_holding

        stock_results, crypto_results = await asyncio.gather(
            asyncio.gather(*[_stock_summary(s) for s in stock_candidates]),
            asyncio.gather(*[_crypto_summary(c) for c in crypto_candidates]),
        )
        # 주식은 1주 단위 매수 → 현재가 > 잔고면 살 수 없으므로 AI에 넘기기 전 필터링
        # 보유 종목(is_holding=True)은 SELL 판단을 위해 가격·등락률 무관 항상 포함
        raw_stock = [(text, price, change, ih) for text, price, change, ih in stock_results if text]
        unaffordable = [text.split("\n")[0] for text, price, change, ih in raw_stock
                        if price > stock_cash > 0 and not ih]
        if unaffordable:
            _log(f"예산 초과 주식 제외 ({stock_cash:,.0f}원): {', '.join(unaffordable)}")
        # 추격매수 방지: 당일 등락률이 한도 이상인 신규 종목은 제외
        chased = [text.split("\n")[0] for text, price, change, ih in raw_stock
                  if not ih and change >= config.CHASE_LIMIT_PCT]
        if chased:
            _log(f"급등 추격 방지 제외 (+{config.CHASE_LIMIT_PCT:.0f}%↑): {', '.join(chased)}")
        stock_summaries = [text for text, price, change, ih in raw_stock
                           if ih or ((price <= stock_cash or stock_cash <= 0)
                                     and change < config.CHASE_LIMIT_PCT)]

        raw_crypto = [(text, change, ih) for text, change, ih in crypto_results if text]
        chased_c = [text.split("\n")[0] for text, change, ih in raw_crypto
                    if not ih and change >= config.CHASE_LIMIT_PCT]
        if chased_c:
            _log(f"급등 코인 추격 방지 제외 (+{config.CHASE_LIMIT_PCT:.0f}%↑): {', '.join(chased_c)}")
        crypto_summaries = [text for text, change, ih in raw_crypto
                            if ih or change < config.CHASE_LIMIT_PCT]
        _log(f"지표 계산 완료 — 주식 {len(stock_summaries)}개, 코인 {len(crypto_summaries)}개")

        # 4. AI 의사결정 (진입 선택 담당 — 청산은 1.5단계 규칙 레이어가 전담)
        _model = {"gemini": config.GEMINI_MODEL, "anthropic": config.ANTHROPIC_MODEL, "ollama": config.OLLAMA_MODEL}.get(config.AI_PROVIDER, config.AI_PROVIDER)
        _log(f"AI 분석 요청 중 ({config.AI_PROVIDER} / {_model})...")
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

        # 7. 매매 실행
        # AI가 반환한 name은 틀릴 수 있으므로 실제 분석 대상 목록의 이름 우선 사용
        stock_name_map = {s.get("code", ""): s.get("name", "") for s in stock_candidates}

        # 코인 분석 대상 ticker set (검증용)
        crypto_ticker_set = {c.get("ticker", "") for c in crypto_candidates}

        executed = []
        for decision in decisions:
            action     = decision.get("action", "HOLD")
            confidence = decision.get("confidence", 0)
            reason     = decision.get("reason", "")
            ticker     = decision.get("ticker", "")
            ai_name    = decision.get("name", ticker)
            amount_krw = decision.get("amount_krw")

            if action == "HOLD":
                continue

            # ── 데이터 정합성 체크 ──────────────────────────────────────────
            is_crypto = ticker.startswith("KRW-")

            # 1) 분석하지 않은 종목/코인은 실행 금지 (AI hallucination 방지)
            if not is_crypto and ticker not in stock_name_map:
                _log(f"→ 스킵: {ai_name}({ticker}) — 분석 대상에 없는 종목 코드 (AI 오류)", "warning")
                continue
            if is_crypto and ticker not in crypto_ticker_set:
                _log(f"→ 스킵: {ticker} — 분석 대상에 없는 코인 (AI 오류)", "warning")
                continue

            # 2) 정식 이름은 우리 데이터 기준 사용, AI 이름과 다르면 경고
            name = stock_name_map.get(ticker, ai_name) if not is_crypto else ai_name
            if not is_crypto and ai_name and name != ai_name:
                _log(f"⚠ 종목명 불일치 수정: AI={ai_name} → 실제={name} ({ticker})", "warning")
            # ────────────────────────────────────────────────────────────────

            # 주문가능금액 0원이면 주식 BUY 건너뜀 (SELL은 그대로 실행)
            if action == "BUY" and not is_crypto and stock_cash <= 0:
                _log(f"→ 스킵: {name} BUY — 주문가능금액 부족 ({stock_cash:,.0f}원)")
                continue

            if action == "BUY":
                # 일일 손실 한도 초과 시 당일 신규 매수 중단
                if daily_stop:
                    _log(f"→ 스킵: {name} BUY — 일일 손실 한도 초과 (당일 {daily_pl:+,.0f}원)")
                    continue

                # 이미 보유 중인 종목 추가 매수 금지 (물타기 방지)
                already_held = (
                    ticker in {h["code"] for h in stock_holdings} if not is_crypto
                    else ticker in {h["ticker"] for h in crypto_holdings}
                )
                if already_held:
                    _log(f"→ 스킵: {name} BUY — 이미 보유 중 (물타기 방지)")
                    continue

                # 매도 후 재매수 쿨다운
                if risk.is_in_cooldown(ticker):
                    _log(f"→ 스킵: {name} BUY — 재매수 쿨다운 중 ({config.REBUY_COOLDOWN_HOURS}h)")
                    continue

                # 최대 동시 보유 종목 수 제한
                position_count = (len(stock_holdings) + len(crypto_holdings)
                                  + sum(1 for e in executed if e.get("action") == "BUY"))
                if position_count >= config.MAX_POSITIONS:
                    _log(f"→ 스킵: {name} BUY — 최대 보유 종목 수 도달 ({position_count}/{config.MAX_POSITIONS})")
                    continue

            # AI SELL은 최소 보유시간 이후에만 허용 (손절·트레일링은 규칙 레이어가 별도 처리)
            if action == "SELL" and risk.held_too_short(ticker):
                _log(f"→ 스킵: {name} SELL — 최소 보유시간 미달 ({config.MIN_HOLD_MINUTES}분), 규칙 레이어가 손절 담당")
                continue

            _log(f"주문 실행: {action} {name}({ticker}) confidence={confidence:.2f}")
            if ticker.startswith("KRW-"):
                result = await asyncio.to_thread(
                    executor.execute_crypto, ticker, action, confidence, reason, crypto_portfolio,
                    amount_krw, None, max_position_krw
                )
            else:
                result = await asyncio.to_thread(
                    executor.execute_stock, ticker, name, action, confidence, reason, stock_portfolio,
                    amount_krw, None, max_position_krw
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
