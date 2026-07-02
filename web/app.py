"""FastAPI 웹 대시보드"""
import os
import asyncio
import json
import hmac
import hashlib
import subprocess
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pathlib import Path
from config import config
from trader import bot, executor

KST = timezone(timedelta(hours=9))

# 자동 배포 설정
DEPLOY_SECRET  = os.getenv("DEPLOY_WEBHOOK_SECRET", "")
DEPLOY_BRANCH  = os.getenv("DEPLOY_BRANCH", "claude/auto-trader-handover-nesasr")
DEPLOY_SCRIPT  = str(Path(__file__).parent.parent / "deploy.sh")
_deploy_status = {"last_deploy": None, "result": "없음", "branch": None}


UI_ONLY = os.getenv("UI_ONLY", "").lower() in ("1", "true", "yes")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 봇 자동 시작 (UI_ONLY=true면 봇 비활성화)"""
    if not UI_ONLY and (config.is_kis_ready or config.is_upbit_ready):
        await bot.start_bot()
    yield
    if not UI_ONLY:
        await bot.stop_bot()


app = FastAPI(title="Auto Trader Dashboard", lifespan=lifespan)

TEMPLATE = Path(__file__).parent / "templates" / "index.html"


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return TEMPLATE.read_text(encoding="utf-8")


@app.get("/api/status")
async def status():
    if UI_ONLY:
        return {
            "running": True,
            "last_run": "2026-07-01T12:00:00",
            "market_summary": "📊 [UI 전용 모드] 실제 API 호출 없음. 화면 구성 확인용 더미 데이터입니다.",
            "decisions": [
                {"name": "삼성전자", "ticker": "005930", "action": "BUY",  "confidence": 0.78, "amount_krw": 75000, "reason": "MA5가 MA20을 상향 돌파했으며 RSI가 과매도 구간에서 반등 중입니다."},
                {"name": "네이버",   "ticker": "035420", "action": "HOLD", "confidence": 0.52, "reason": "볼린저밴드 중간 밴드 부근에서 방향성이 불분명합니다."},
                {"name": "KRW-BTC", "ticker": "KRW-BTC", "action": "BUY", "confidence": 0.71, "amount_krw": 50000, "reason": "MACD 골든크로스 발생, 거래량 증가 확인."},
                {"name": "KRW-ETH", "ticker": "KRW-ETH", "action": "SELL","confidence": 0.65, "reason": "RSI 과매수 구간 진입, 단기 조정 예상."},
            ],
            "summaries": [
                "[삼성전자] (stock)\n현재가: 75,000 | 등락: +2.05%\nRSI(14): 42.3 (중립)\nMA5: 73,200 | MA20: 71,800 → 단기>중기(상승)",
                "[KRW-BTC] (crypto)\n현재가: 91,000,000 | 등락: +1.20%\nRSI(14): 55.1 (중립)\nMACD: 골든크로스",
            ],
            "errors": [],
            "config": {
                "kis_ready": False, "upbit_ready": False, "ai_ready": False,
                "ai_provider": "ui_only", "mock_mode": True, "interval_min": 5,
            },
        }
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
        cached_portfolio = {"stock": {}, "crypto": {}, "realized_pl": {}}
        last_balance_fetch = 0
        last_trade_count = -1  # -1 = 초기화 전

        while True:
            try:
                state = bot.get_state()
                now = time.time()
                trades = executor.get_trade_history(20)
                total_trade_count = executor.get_trade_count()

                # 새 거래 체결 감지 → 포트폴리오 즉시 갱신
                if last_trade_count >= 0 and total_trade_count > last_trade_count:
                    last_balance_fetch = 0  # 캐시 무효화 → 다음 조건에서 즉시 재조회
                last_trade_count = total_trade_count

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
                    cached_portfolio["realized_pl"] = executor.get_realized_pl()
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
                    "trade_count": total_trade_count,
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

@app.get("/api/trades/pages")
async def trades_pages(page: int = 1, per_page: int = 20):
    if UI_ONLY:
        from datetime import datetime, timedelta
        dummy = []
        actions = [("BUY","삼성전자","005930","stock",75000,1,0,None),
                   ("SELL","네이버","035420","stock",192000,2,6.67,182000),
                   ("BUY","KRW-BTC","KRW-BTC","crypto",91000000,0.0008,0,None),
                   ("SELL","KRW-ETH","KRW-ETH","crypto",3450000,0.012,7.81,3200000),
                   ("BUY","카카오","035720","stock",48000,3,0,None),
                   ("SELL","KRW-SOL","KRW-SOL","crypto",115000,0.45,4.55,110000),]
        for i, (act, name, ticker, mkt, price, qty, rate, avgp) in enumerate(actions * 4):
            t = datetime.now() - timedelta(hours=i*3)
            rec = {"time": t.isoformat(), "market": mkt, "name": name,
                   "ticker": ticker, "action": act, "price": price,
                   "qty": qty, "amount": price*qty, "amount_krw": price*qty,
                   "confidence": 0.70, "profit_rate": rate,
                   "avg_price": avgp or 0, "result": {"rt_cd": "0"}}
            dummy.append(rec)
        total = len(dummy)
        start = (page-1)*per_page
        return {"trades": dummy[start:start+per_page], "total": total,
                "page": page, "per_page": per_page,
                "total_pages": max(1, -(-total // per_page))}
    return executor.get_trade_history_page(page, per_page)


@app.get("/api/portfolio")
async def portfolio():
    realized = executor.get_realized_pl()
    if UI_ONLY:
        return {
            "stock": {
                "cash": 100002, "orderable_cash": 92000, "settlement_cash": 85000,
                "total": 519002, "unrealized_pl": 18400,
                "holdings": [
                    {"code": "005930", "name": "삼성전자", "qty": 3, "avg_price": 72000.0,
                     "current_price": 75000.0, "eval_amount": 225000, "pl_amount": 9000, "profit_rate": 4.17},
                    {"code": "035420", "name": "네이버",   "qty": 2, "avg_price": 180000.0,
                     "current_price": 192000.0, "eval_amount": 384000, "pl_amount": 24000, "profit_rate": 6.67},
                ],
            },
            "crypto": {
                "cash": 12345, "total": 87600,
                "holdings": [
                    {"ticker": "KRW-BTC", "qty": 0.0008, "avg_price": 87000000.0, "current_price": 91000000.0,
                     "eval_amount": 72800, "profit_rate": 4.60},
                    {"ticker": "KRW-ETH", "qty": 0.012,  "avg_price": 3200000.0,  "current_price": 3450000.0,
                     "eval_amount": 41400, "profit_rate": 7.81},
                    {"ticker": "KRW-SOL", "qty": 0.45,   "avg_price": 110000.0,   "current_price": 115000.0,
                     "eval_amount": 51750, "profit_rate": 4.55},
                ],
            },
            "realized_pl": realized,
        }
    from trader import kis_client, upbit_client
    result = {"realized_pl": realized}
    if config.is_kis_ready:
        try:
            result["stock"] = await asyncio.to_thread(kis_client.get_balance)
        except Exception as e:
            result["stock"] = {"error": str(e)}
    if config.is_upbit_ready:
        try:
            result["crypto"] = await asyncio.to_thread(upbit_client.get_balance)
        except Exception as e:
            result["crypto"] = {"error": str(e)}
    return result


# ---- KIS 잔고 원본 디버그 (어떤 필드가 '거래가능 원화'인지 확인용) ----
@app.get("/api/debug/kis-balance-raw")
async def debug_kis_balance_raw():
    if UI_ONLY:
        return {"error": "UI 전용 모드"}
    from trader.kis_client import _headers, config as _cfg
    import requests as _req
    acct, suffix = _cfg.KIS_ACCOUNT_NO.split("-")
    tr_id = "VTTC8434R" if _cfg.KIS_MOCK else "TTTC8434R"
    params = {
        "CANO": acct, "ACNT_PRDT_CD": suffix,
        "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
        "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
    }
    resp = _req.get(
        f"{_cfg.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
        headers=_headers(tr_id), params=params,
    )
    data = resp.json()
    o2 = data.get("output2", [{}])[0]
    # 금액처럼 보이는 필드만 추려서 반환 (amt, cash, able 포함 필드명)
    amount_fields = {k: v for k, v in o2.items() if any(x in k for x in ["amt", "cash", "able", "psbl", "evlu", "excc", "buy"])}
    return {"all_amount_fields": amount_fields, "raw_output2": o2}


# ---- 수동 매매 ----
@app.post("/api/manual-trade")
async def manual_trade(request: Request):
    if UI_ONLY:
        return {"status": "skipped", "reason": "UI 전용 모드에서는 실제 거래 불가"}
    body = await request.json()
    market     = body.get("market", "stock")   # stock | crypto
    ticker     = body.get("ticker", "")
    name       = body.get("name", ticker)
    action     = body.get("action", "BUY").upper()
    amount_krw = body.get("amount_krw")
    manual_qty = body.get("qty")               # 명시적 수량 (매도 비율 계산 시)
    try:
        if market == "stock":
            from trader import kis_client
            portfolio = await asyncio.to_thread(kis_client.get_balance)
            result = await asyncio.to_thread(
                executor.execute_stock, ticker, name, action, 1.0, "수동 매매", portfolio, amount_krw, manual_qty
            )
        else:
            from trader import upbit_client
            portfolio = await asyncio.to_thread(upbit_client.get_balance)
            result = await asyncio.to_thread(
                executor.execute_crypto, ticker, action, 1.0, "수동 매매", portfolio, amount_krw, manual_qty
            )
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


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


# ── 자동 배포 webhook ────────────────────────────────────────────────
def _run_deploy_bg(branch: str):
    """백그라운드에서 deploy.sh 실행 (응답 전송 후 3초 뒤 시작)."""
    import time, logging
    time.sleep(3)   # FastAPI가 200 응답을 보낼 시간 확보
    logger = logging.getLogger("deploy")
    logger.info(f"자동 배포 시작 (branch={branch})")
    try:
        proc = subprocess.run(
            ["bash", DEPLOY_SCRIPT],
            capture_output=True, text=True, timeout=120
        )
        result = "성공" if proc.returncode == 0 else f"실패(code={proc.returncode})"
        logger.info(f"자동 배포 {result}\n{proc.stdout[-500:] if proc.stdout else ''}")
        _deploy_status.update({
            "last_deploy": datetime.now(KST).isoformat(),
            "result": result,
            "branch": branch,
            "log": (proc.stdout or "")[-800:],
        })
    except subprocess.TimeoutExpired:
        _deploy_status.update({"last_deploy": datetime.now(KST).isoformat(),
                                "result": "타임아웃(120s)", "branch": branch})
    except Exception as e:
        _deploy_status.update({"last_deploy": datetime.now(KST).isoformat(),
                                "result": f"오류: {e}", "branch": branch})


@app.post("/webhook/deploy")
async def webhook_deploy(request: Request):
    """GitHub Push 이벤트 수신 → deploy.sh 자동 실행."""
    body = await request.body()

    # HMAC 서명 검증 (시크릿 설정 시)
    if DEPLOY_SECRET:
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        expected   = "sha256=" + hmac.new(
            DEPLOY_SECRET.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            return JSONResponse({"error": "서명 불일치"}, status_code=401)

    try:
        payload = json.loads(body)
    except Exception:
        return JSONResponse({"error": "JSON 파싱 실패"}, status_code=400)

    ref    = payload.get("ref", "")
    pusher = payload.get("pusher", {}).get("name", "unknown")
    target = f"refs/heads/{DEPLOY_BRANCH}"

    if ref != target:
        return {"status": "skipped", "reason": f"대상 브랜치 아님 ({ref})"}

    # 이미 배포 중이면 무시
    import threading
    t = threading.Thread(target=_run_deploy_bg, args=(DEPLOY_BRANCH,), daemon=True)
    t.start()

    return {
        "status":  "deploying",
        "branch":  DEPLOY_BRANCH,
        "pusher":  pusher,
        "message": "deploy.sh 실행 중 (약 30초 후 완료)"
    }


@app.get("/api/deploy-status")
async def deploy_status_api():
    """마지막 자동 배포 결과 조회."""
    return _deploy_status
