"""한국투자증권 KIS OpenAPI 클라이언트"""
import json
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from config import config


TOKEN_CACHE = Path(__file__).parent.parent / "data" / "kis_token.json"


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
    """주가 OHLCV 데이터 조회"""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    resp = requests.get(
        f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-chartprice",
        headers=_headers("FHKST03010100"),
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        },
    )
    resp.raise_for_status()
    rows = resp.json().get("output2", [])
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
        headers=_headers("FHPST01710000"),
        params={
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20171",
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
