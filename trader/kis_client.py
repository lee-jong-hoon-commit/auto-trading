"""한국투자증권 KIS OpenAPI 클라이언트"""
import json
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from config import config


TOKEN_CACHE = Path(__file__).parent.parent / "data" / "kis_token.json"

# 시가총액 순위 API 실패 시 사용할 기본 분석 종목.
# 섹터/가격대를 다양화하여 잔고 규모에 맞는 종목을 폭넓게 탐색할 수 있게 한다.
DEFAULT_STOCKS = [
    # 반도체
    {"code": "005930", "name": "삼성전자"},
    {"code": "000660", "name": "SK하이닉스"},
    {"code": "042700", "name": "한미반도체"},
    # 자동차
    {"code": "005380", "name": "현대차"},
    {"code": "000270", "name": "기아"},
    {"code": "012330", "name": "현대모비스"},
    # 2차전지/화학
    {"code": "373220", "name": "LG에너지솔루션"},
    {"code": "006400", "name": "삼성SDI"},
    {"code": "051910", "name": "LG화학"},
    {"code": "011170", "name": "롯데케미칼"},
    # 인터넷/게임/엔터
    {"code": "035420", "name": "NAVER"},
    {"code": "035720", "name": "카카오"},
    {"code": "036570", "name": "엔씨소프트"},
    {"code": "352820", "name": "하이브"},
    # 바이오/제약
    {"code": "207940", "name": "삼성바이오로직스"},
    {"code": "068270", "name": "셀트리온"},
    {"code": "000100", "name": "유한양행"},
    {"code": "128940", "name": "한미약품"},
    # 금융
    {"code": "105560", "name": "KB금융"},
    {"code": "055550", "name": "신한지주"},
    {"code": "086790", "name": "하나금융지주"},
    {"code": "316140", "name": "우리금융지주"},
    # 철강/소재/조선
    {"code": "005490", "name": "POSCO홀딩스"},
    {"code": "010130", "name": "고려아연"},
    {"code": "009540", "name": "HD한국조선해양"},
    {"code": "042660", "name": "한화오션"},
    # 방산/항공
    {"code": "012450", "name": "한화에어로스페이스"},
    {"code": "047810", "name": "한국항공우주"},
    # 소비/유통/통신/유틸리티
    {"code": "097950", "name": "CJ제일제당"},
    {"code": "282330", "name": "BGF리테일"},
    {"code": "017670", "name": "SK텔레콤"},
    {"code": "015760", "name": "한국전력"},
    {"code": "003550", "name": "LG"},
]


def select_affordable_stocks(stocks: list[dict], budget: float, limit: int | None = None) -> list[dict]:
    """예산(budget)으로 최소 1주 매수 가능한 종목만 선별한다.

    각 항목에 현재가('price')를 채워서 반환한다. price가 없으면 현재가 API로 조회한다.
    budget<=0(잔고 미확인 등)이면 필터링하지 않고 가격만 채워 그대로 반환한다.
    """
    out = []
    for s in stocks:
        price = float(s.get("price") or 0)
        if price <= 0:
            try:
                price = get_current_price(s["code"])
            except Exception:
                price = 0.0
        item = {**s, "price": price}
        if budget <= 0 or 0 < price <= budget:
            out.append(item)
        if limit and len(out) >= limit:
            break
    return out


def get_analysis_stocks() -> list[dict]:
    """기본 분석 대상 종목 목록을 반환.

    config.CUSTOM_STOCKS(쉼표 구분 종목코드)가 설정돼 있으면 이를 우선 사용하고,
    없으면 DEFAULT_STOCKS를 사용한다. 시가총액 순위 API가 실패할 때의 폴백으로도 쓰인다.
    """
    codes = [c.strip() for c in config.CUSTOM_STOCKS.split(",") if c.strip()]
    if codes:
        name_map = {s["code"]: s["name"] for s in DEFAULT_STOCKS}
        return [{"code": c, "name": name_map.get(c, c)} for c in codes]
    return list(DEFAULT_STOCKS)


def _get_token() -> str:
    if TOKEN_CACHE.exists():
        cached = json.loads(TOKEN_CACHE.read_text())
        if datetime.fromisoformat(cached["expires"]) > datetime.now():
            return cached["token"]

    resp = requests.post(
        f"{config.KIS_BASE_URL}/oauth2/tokenP",
        json={
            "grant_type": "client_credentials",
            "appkey": config.KIS_APP_KEY,
            "appsecret": config.KIS_APP_SECRET,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    expires = datetime.now() + timedelta(hours=23)
    TOKEN_CACHE.parent.mkdir(exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps({"token": token, "expires": expires.isoformat()}))
    return token


def _headers(tr_id: str) -> dict:
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {_get_token()}",
        "appkey": config.KIS_APP_KEY,
        "appsecret": config.KIS_APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P",
    }


def get_balance() -> dict:
    """계좌 잔고 조회"""
    acct, suffix = config.KIS_ACCOUNT_NO.split("-")
    tr_id = "VTTC8434R" if config.KIS_MOCK else "TTTC8434R"
    resp = requests.get(
        f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
        headers=_headers(tr_id),
        params={
            "CANO": acct,
            "ACNT_PRDT_CD": suffix,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "cash": int(data["output2"][0]["dnca_tot_amt"]) if data.get("output2") else 0,
        "total": int(data["output2"][0]["tot_evlu_amt"]) if data.get("output2") else 0,
        "holdings": [
            {
                "code": h["pdno"],
                "name": h["prdt_name"],
                "qty": int(h["hldg_qty"]),
                "avg_price": float(h["pchs_avg_pric"]),
                "current_price": float(h["prpr"]),
                "profit_rate": float(h["evlu_pfls_rt"]),
            }
            for h in data.get("output1", [])
            if int(h.get("hldg_qty", 0)) > 0
        ],
    }


def get_ohlcv(code: str, days: int = 100) -> pd.DataFrame:
    """주가 OHLCV 데이터 조회

    inquire-daily-price(FHKST01010400) 사용. 최근 약 30영업일치를 반환한다.
    (구 inquire-daily-chartprice 엔드포인트는 일부 계정에서 404를 반환하여 교체)
    """
    resp = requests.get(
        f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-price",
        headers=_headers("FHKST01010400"),
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_PERIOD_DIV_CODE": "D",   # D:일 W:주 M:월
            "FID_ORG_ADJ_PRC": "0",       # 0:수정주가 1:원주가
        },
    )
    resp.raise_for_status()
    rows = resp.json().get("output", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(columns={
        "stck_bsop_date": "date",
        "stck_oprc": "open",
        "stck_hgpr": "high",
        "stck_lwpr": "low",
        "stck_clpr": "close",
        "acml_vol": "volume",
    })[["date", "open", "high", "low", "close", "volume"]]
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True).tail(days)


def get_current_price(code: str) -> float:
    """현재가 조회"""
    resp = requests.get(
        f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
        headers=_headers("FHKST01010100"),
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
    )
    resp.raise_for_status()
    return float(resp.json()["output"]["stck_prpr"])


def place_order(code: str, qty: int, price: int, side: str) -> dict:
    """주문 실행 (side: 'buy' | 'sell')"""
    mock_map = {"buy": "VTTC0802U", "sell": "VTTC0801U"}
    real_map = {"buy": "TTTC0802U", "sell": "TTTC0801U"}
    tr_id = mock_map[side] if config.KIS_MOCK else real_map[side]
    acct, suffix = config.KIS_ACCOUNT_NO.split("-")
    resp = requests.post(
        f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash",
        headers=_headers(tr_id),
        json={
            "CANO": acct,
            "ACNT_PRDT_CD": suffix,
            "PDNO": code,
            "ORD_DVSN": "01",   # 시장가
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
        },
    )
    resp.raise_for_status()
    return resp.json()


def get_top_stocks(market: str = "KOSPI", limit: int = 20) -> list[dict]:
    """시가총액 상위 종목 조회"""
    resp = requests.get(
        f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/ranking/market-cap",
        headers=_headers("FHPST01740000"),
        params={
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20174",
            "fid_div_cls_code": "0" if market == "KOSPI" else "1",
            "fid_input_iscd": "0001" if market == "KOSPI" else "1001",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
            "fid_input_date_1": "",
        },
    )
    resp.raise_for_status()
    return [
        {"code": r["mksc_shrn_iscd"], "name": r["hts_kor_isnm"], "price": float(r["stck_prpr"])}
        for r in resp.json().get("output", [])[:limit]
    ]


def get_volume_rank(market: str = "ALL", budget: float = 0, limit: int = 30) -> list[dict]:
    """거래대금 상위 종목 조회 — 그날그날 시장 활동성에 따라 매일 바뀌는 유니버스.

    budget>0이면 가격 상한(FID_INPUT_PRICE_2)으로 1주 매수 가능한 종목만 받아온다.
    """
    iscd = {"KOSPI": "0001", "KOSDAQ": "1001"}.get(market, "0000")
    resp = requests.get(
        f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank",
        headers=_headers("FHPST01710000"),
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": iscd,
            "FID_DIV_CLS_CODE": "0",          # 0:전체 1:보통주 2:우선주
            "FID_BLNG_CLS_CODE": "3",         # 3:거래금액순
            "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "0000000000",
            "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": str(int(budget)) if budget and budget > 0 else "",
            "FID_VOL_CNT": "",
            "FID_INPUT_DATE_1": "",
        },
    )
    resp.raise_for_status()
    out = []
    for r in resp.json().get("output", [])[:limit]:
        try:
            out.append({
                "code": r["mksc_shrn_iscd"],
                "name": r["hts_kor_isnm"],
                "price": float(r["stck_prpr"]),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return out


def get_dynamic_stocks(budget: float = 0, limit: int = 30) -> list[dict]:
    """그날의 시장 활동성 기반 동적 종목 유니버스.

    거래대금 상위 → (실패 시) 시가총액 상위 순으로 시도하며, 둘 다 실패하면 빈 리스트.
    호출 측에서 빈 리스트일 때 관심종목(워치리스트)으로 폴백한다.
    """
    try:
        stocks = get_volume_rank("ALL", budget, limit)
        if stocks:
            return stocks
    except Exception:
        pass
    try:
        return get_top_stocks("KOSPI", limit)
    except Exception:
        return []
