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
    import requests
    import pandas as pd
    from trader import kis_client, upbit_client, analyzer, ai_engine

    state = bot.get_state()
    state["errors"] = []

    try:
        stock_summaries = []
        crypto_summaries = []

        # 주요 주식 분석
        if config.is_kis_ready:
            stocks = [
                ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("005380", "현대차"),
                ("035720", "카카오"), ("003550", "LG"), ("068270", "셀트리온"),
            ]
            for code, name in stocks:
                try:
                    resp = requests.get(
                        f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-price",
                        headers=kis_client._headers("FHKST01010400"),
                        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                                "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"},
                    )
                    rows = resp.json().get("output", [])
                    if not rows:
                        continue
                    df = pd.DataFrame(rows).rename(columns={
                        "stck_bsop_date": "date", "stck_oprc": "open", "stck_hgpr": "high",
                        "stck_lwpr": "low", "stck_clpr": "close", "acml_vol": "volume",
                    })[["date", "open", "high", "low", "close", "volume"]]
                    for c in ["open", "high", "low", "close", "volume"]:
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                    df["date"] = pd.to_datetime(df["date"])
                    df = df.sort_values("date").reset_index(drop=True)
                    ind = analyzer.compute_indicators(df)
                    stock_summaries.append(analyzer.summarize_for_ai(name, ind, "stock"))
                except Exception:
                    pass

        # 주요 코인 분석
        if config.is_upbit_ready:
            for ticker in ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"]:
                try:
                    df = upbit_client.get_ohlcv(ticker, count=60)
                    ind = analyzer.compute_indicators(df)
                    crypto_summaries.append(analyzer.summarize_for_ai(ticker, ind, "crypto"))
                except Exception:
                    pass

        try:
            stock_balance = kis_client.get_balance() if config.is_kis_ready else {"cash": 0, "holdings": []}
        except Exception:
            stock_balance = {"cash": 0, "holdings": []}
        try:
            crypto_balance = upbit_client.get_balance() if config.is_upbit_ready else {"cash": 0, "holdings": []}
        except Exception:
            crypto_balance = {"cash": 0, "holdings": []}

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

        return {"ok": True, "decisions": state["last_decisions"], "market_summary": state["market_summary"]}

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
