"""FastAPI 웹 대시보드"""
import asyncio
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pathlib import Path
from config import config
from trader import bot, executor


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 봇 자동 시작"""
    if config.is_kis_ready or config.is_upbit_ready:
        await bot.start_bot()
    yield
    await bot.stop_bot()


app = FastAPI(title="Auto Trader Dashboard", lifespan=lifespan)

TEMPLATE = Path(__file__).parent / "templates" / "index.html"


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return TEMPLATE.read_text(encoding="utf-8")


@app.get("/api/status")
async def status():
    state = bot.get_state()
    return {
        "running": state["running"],
        "last_run": state["last_run"],
        "market_summary": state["market_summary"],
        "decisions": state["last_decisions"],
        "summaries": state["last_summaries"],
        "last_trades": state["last_trades"],
        "errors": state["errors"],
        "config": {
            "kis_ready": config.is_kis_ready,
            "upbit_ready": config.is_upbit_ready,
            "ai_ready": config.is_ai_ready,
            "ai_provider": config.AI_PROVIDER,
            "mock_mode": config.KIS_MOCK,
            "interval_min": config.TRADE_INTERVAL_MINUTES,
        },
    }


@app.get("/api/stream")
async def stream():
    """SSE 실시간 스트림"""
    async def event_generator():
        from trader import kis_client, upbit_client
        import time
        # 잔고는 30초마다 갱신 (KIS API 느림 방지)
        # 단, 새 거래 체결이 감지되면 즉시 갱신
        cached_portfolio = {"stock": {}, "crypto": {}}
        last_balance_fetch = 0
        last_trade_count = -1  # -1 = 초기화 전

        while True:
            try:
                state = bot.get_state()
                now = time.time()
                trades = executor.get_trade_history(20)

                # 새 거래 체결 감지 → 포트폴리오 즉시 갱신
                new_trade_count = len(trades)
                if last_trade_count >= 0 and new_trade_count > last_trade_count:
                    last_balance_fetch = 0  # 캐시 무효화 → 다음 조건에서 즉시 재조회
                last_trade_count = new_trade_count

                if now - last_balance_fetch >= 30:
                    try:
                        if config.is_kis_ready:
                            cached_portfolio["stock"] = await asyncio.to_thread(kis_client.get_balance)
                    except Exception:
                        pass
                    try:
                        if config.is_upbit_ready:
                            cached_portfolio["crypto"] = await asyncio.to_thread(upbit_client.get_balance)
                    except Exception:
                        pass
                    last_balance_fetch = now
                data = {
                    "running": state["running"],
                    "last_run": state["last_run"],
                    "market_summary": state["market_summary"],
                    "decisions": state["last_decisions"],
                    "summaries": state["last_summaries"],
                    "errors": state["errors"],
                    "portfolio": cached_portfolio,
                    "trades": trades,
                    "trade_count": len(trades),
                    "activity_log": state.get("activity_log", []),
                }
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/bot/start")
async def start():
    if not bot.get_state()["running"]:
        await bot.start_bot()
    return {"ok": True}


@app.post("/api/bot/stop")
async def stop():
    await bot.stop_bot()
    return {"ok": True}


@app.post("/api/bot/run-once")
async def run_once():
    await bot.run_once()
    return {"ok": True}


@app.post("/api/analyze")
async def analyze_only():
    """매매 실행 없이 AI 분석만 수행"""
    from trader import kis_client, upbit_client, analyzer, ai_engine, watchlist

    state = bot.get_state()
    state["errors"] = []

    try:
        stock_summaries = []
        crypto_summaries = []

        # 잔고 먼저 조회 → 종목당 예산 산정
        try:
            stock_balance = kis_client.get_balance() if config.is_kis_ready else {"cash": 0, "holdings": []}
        except Exception:
            stock_balance = {"cash": 0, "holdings": []}
        try:
            crypto_balance = upbit_client.get_balance() if config.is_upbit_ready else {"cash": 0, "holdings": []}
        except Exception:
            crypto_balance = {"cash": 0, "holdings": []}

        # 주요 주식 분석: 그날그날 거래대금 상위(동적) + 관심종목
        affordable_count = 0
        universe_source = ""
        if config.is_kis_ready:
            dynamic = kis_client.get_dynamic_stocks(budget=None, limit=25)
            if dynamic:
                universe_source = "거래대금 상위"
                dyn_codes = {d["code"] for d in dynamic}
                merged = dynamic + [s for s in watchlist.get_stocks() if s["code"] not in dyn_codes]
            else:
                universe_source = "관심종목"
                merged = watchlist.get_stocks()
            candidates = merged[:15]  # 분석 시 예산 필터 제거 (매수 시 executor에서 체크)
            affordable_count = len(candidates)
            for s in candidates:
                try:
                    df = kis_client.get_ohlcv(s["code"])
                    if df.empty:
                        continue
                    ind = analyzer.compute_indicators(df)
                    stock_summaries.append(analyzer.summarize_for_ai(s["name"], ind, "stock"))
                except Exception:
                    pass

        # 주요 코인 분석 (관심 코인)
        if config.is_upbit_ready:
            for ticker in watchlist.get_tickers():
                try:
                    df = upbit_client.get_ohlcv(ticker, count=60)
                    ind = analyzer.compute_indicators(df)
                    crypto_summaries.append(analyzer.summarize_for_ai(ticker, ind, "crypto"))
                except Exception:
                    pass

        portfolio_status = {
            "stock_cash": stock_balance.get("cash", 0),
            "crypto_cash": crypto_balance.get("cash", 0),
            "stock_holdings": stock_balance.get("holdings", []),
            "crypto_holdings": crypto_balance.get("holdings", []),
        }

        ai_result = ai_engine.analyze_and_decide(stock_summaries, crypto_summaries, portfolio_status)
        state["last_decisions"] = ai_result.get("decisions", [])
        state["market_summary"] = ai_result.get("market_summary", "")
        state["last_summaries"] = stock_summaries + crypto_summaries
        state["last_run"] = __import__("datetime").datetime.now().isoformat()

        return {"ok": True, "decisions": state["last_decisions"], "market_summary": state["market_summary"],
                "analyzed_stocks": affordable_count, "universe_source": universe_source}

    except Exception as e:
        state["errors"].append(str(e))
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})




@app.get("/api/stocks/search")
async def search_stocks(q: str = ""):
    """주식 종목명/코드 검색"""
    from trader import kis_client, watchlist as wl
    q = q.strip()
    if not q:
        return []

    results, seen = [], set()

    def add(code, name, price=None):
        if code not in seen:
            seen.add(code)
            item = {"code": code, "name": name}
            if price is not None:
                item["price"] = price
            results.append(item)

    # 1. 관심종목 (우선순위 최상)
    for s in wl.get_stocks():
        if q in s.get("name", "") or q in s.get("code", ""):
            add(s["code"], s.get("name", s["code"]))

    # 2. 확장 종목 리스트
    for s in kis_client.SEARCH_STOCKS:
        if q in s["name"] or q in s["code"]:
            add(s["code"], s["name"])

    # 3. DEFAULT_STOCKS
    for s in kis_client.DEFAULT_STOCKS:
        if q in s["name"] or q in s["code"]:
            add(s["code"], s["name"])

    # 4. 실시간 거래대금 상위 (KIS 연결 시, 결과 부족할 때)
    if config.is_kis_ready and len(results) < 5:
        try:
            stocks = await asyncio.to_thread(kis_client.get_volume_rank, "ALL", 0, 50)
            for s in stocks:
                if q in s.get("name", "") or q in s.get("code", ""):
                    add(s["code"], s["name"], s.get("price"))
        except Exception:
            pass

    return results[:10]


@app.post("/api/guide")
async def trading_guide(payload: dict):
    """수동 투자 가이드: 단일 종목/코인 상세 분석"""
    from trader import kis_client, upbit_client, analyzer, ai_engine
    import logging
    _log = logging.getLogger(__name__)

    code = (payload.get("code") or "").strip()
    ticker = (payload.get("ticker") or "").strip()
    name = (payload.get("name") or "").strip()

    if not code and not ticker:
        return JSONResponse(status_code=400, content={"ok": False, "error": "code 또는 ticker를 입력하세요"})

    try:
        if code:
            if not (len(code) == 6 and code.isdigit()):
                return JSONResponse(status_code=400, content={"ok": False, "error": "종목코드는 6자리 숫자입니다"})
            df = await asyncio.to_thread(kis_client.get_ohlcv, code)
            if df is None or df.empty:
                return JSONResponse(status_code=500, content={"ok": False, "error": "OHLCV 데이터를 가져올 수 없습니다"})
            indicators = await asyncio.to_thread(analyzer.compute_indicators, df)
            display_name = name or code
            summary = analyzer.summarize_for_ai(display_name, indicators, "stock")
            guide = await asyncio.to_thread(ai_engine.generate_guide, summary, "stock", display_name)
            return {
                "ok": True, "type": "stock", "code": code, "name": display_name,
                "current_price": float(indicators.get("current_price", 0) or 0),
                "summary": summary, "guide": guide,
            }
        else:
            if not ticker.upper().startswith("KRW-"):
                ticker = "KRW-" + ticker.upper()
            df = await asyncio.to_thread(upbit_client.get_ohlcv, ticker, count=60)
            if df is None or df.empty:
                return JSONResponse(status_code=500, content={"ok": False, "error": "OHLCV 데이터를 가져올 수 없습니다"})
            indicators = await asyncio.to_thread(analyzer.compute_indicators, df)
            summary = analyzer.summarize_for_ai(ticker, indicators, "crypto")
            guide = await asyncio.to_thread(ai_engine.generate_guide, summary, "crypto", ticker)
            return {
                "ok": True, "type": "crypto", "ticker": ticker,
                "current_price": float(indicators.get("current_price", 0) or 0),
                "summary": summary, "guide": guide,
            }
    except Exception as e:
        _log.exception(f"Guide 분석 오류: {e}")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.get("/api/trades")
async def trades(limit: int = 50):
    return executor.get_trade_history(limit)


@app.get("/api/portfolio")
async def portfolio():
    from trader import kis_client, upbit_client
    result = {}
    if config.is_kis_ready:
        try:
            result["stock"] = kis_client.get_balance()
        except Exception as e:
            result["stock"] = {"error": str(e)}
    if config.is_upbit_ready:
        try:
            result["crypto"] = upbit_client.get_balance()
        except Exception as e:
            result["crypto"] = {"error": str(e)}
    return result


# ---- 관심종목(워치리스트) 관리 ----
@app.get("/api/watchlist")
async def watchlist_get():
    from trader import watchlist
    return watchlist.load()


@app.post("/api/watchlist/stock")
async def watchlist_add_stock(payload: dict):
    from trader import watchlist
    try:
        return {"ok": True, "stocks": watchlist.add_stock(payload.get("code", ""), payload.get("name", ""))}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.delete("/api/watchlist/stock/{code}")
async def watchlist_remove_stock(code: str):
    from trader import watchlist
    return {"ok": True, "stocks": watchlist.remove_stock(code)}


@app.post("/api/watchlist/ticker")
async def watchlist_add_ticker(payload: dict):
    from trader import watchlist
    try:
        return {"ok": True, "tickers": watchlist.add_ticker(payload.get("ticker", ""))}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.delete("/api/watchlist/ticker/{ticker}")
async def watchlist_remove_ticker(ticker: str):
    from trader import watchlist
    return {"ok": True, "tickers": watchlist.remove_ticker(ticker)}
