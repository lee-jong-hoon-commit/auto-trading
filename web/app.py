"""FastAPI 웹 대시보드"""
import asyncio
import json
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pathlib import Path
from config import config
from trader import bot, executor

app = FastAPI(title="Auto Trader Dashboard")

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
            "mock_mode": config.KIS_MOCK,
            "interval_min": config.TRADE_INTERVAL_MINUTES,
            "stop_loss": config.STOP_LOSS_RATIO,
            "take_profit": config.TAKE_PROFIT_RATIO,
        },
    }


@app.get("/api/stream")
async def stream():
    """SSE 실시간 스트림"""
    async def event_generator():
        from trader import kis_client, upbit_client
        last_trade_count = 0
        while True:
            try:
                state = bot.get_state()
                # 포트폴리오
                portfolio = {}
                try:
                    portfolio["stock"] = kis_client.get_balance() if config.is_kis_ready else {}
                except Exception:
                    portfolio["stock"] = {}
                try:
                    portfolio["crypto"] = upbit_client.get_balance() if config.is_upbit_ready else {}
                except Exception:
                    portfolio["crypto"] = {}

                trades = executor.get_trade_history(20)
                data = {
                    "running": state["running"],
                    "last_run": state["last_run"],
                    "market_summary": state["market_summary"],
                    "decisions": state["last_decisions"],
                    "summaries": state["last_summaries"],
                    "errors": state["errors"],
                    "portfolio": portfolio,
                    "trades": trades,
                    "trade_count": len(trades),
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

        stock_budget = stock_balance.get("cash", 0) * config.MAX_POSITION_RATIO

        # 주요 주식 분석: 그날그날 거래대금 상위(동적) + 관심종목을 합쳐 예산 내 종목만 선별
        affordable_count = 0
        universe_source = ""
        if config.is_kis_ready:
            dynamic = kis_client.get_dynamic_stocks(budget=stock_budget, limit=25)
            if dynamic:
                universe_source = "거래대금 상위"
                dyn_codes = {d["code"] for d in dynamic}
                merged = dynamic + [s for s in watchlist.get_stocks() if s["code"] not in dyn_codes]
            else:
                universe_source = "관심종목"
                merged = watchlist.get_stocks()
            affordable = kis_client.select_affordable_stocks(merged, stock_budget, limit=15)
            affordable_count = len(affordable)
            for s in affordable:
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
            "stock_budget_per_position": round(stock_budget),
            "stock_holdings": stock_balance.get("holdings", []),
            "crypto_holdings": crypto_balance.get("holdings", []),
        }

        ai_result = ai_engine.analyze_and_decide(stock_summaries, crypto_summaries, portfolio_status)
        state["last_decisions"] = ai_result.get("decisions", [])
        state["market_summary"] = ai_result.get("market_summary", "")
        state["last_summaries"] = stock_summaries + crypto_summaries
        state["last_run"] = __import__("datetime").datetime.now().isoformat()

        return {"ok": True, "decisions": state["last_decisions"], "market_summary": state["market_summary"],
                "stock_budget": round(stock_budget), "affordable_stocks": affordable_count,
                "universe_source": universe_source}

    except Exception as e:
        state["errors"].append(str(e))
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
