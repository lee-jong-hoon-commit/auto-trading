"""한국투자증권 KIS OpenAPI 클라이언트"""
import json
import logging
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from config import config

logger = logging.getLogger(__name__)


TOKEN_CACHE = Path(__file__).parent.parent / "data" / "kis_token.json"

# 시가총액 순위 API 실패 시 사용할 기본 분석 종목.
# 섹터/가격대를 다양화하여 잔고 규모에 맞는 종목을 폭넓게 탐색할 수 있게 한다.
# 거래대금 상위 API 실패 시 폴백 — 100,000원 이하 저가주 위주로 구성
DEFAULT_STOCKS = [
    # 건설
    {"code": "047040", "name": "대우건설"},
    {"code": "006360", "name": "GS건설"},
    {"code": "000720", "name": "현대건설"},
    {"code": "028050", "name": "삼성E&A"},
    # 철강/소재
    {"code": "001440", "name": "대한전선"},
    {"code": "006340", "name": "대원전선"},
    {"code": "093370", "name": "후성"},
    {"code": "011780", "name": "금호석유"},
    # 에너지/전력
    {"code": "015760", "name": "한국전력"},
    {"code": "034730", "name": "SK"},
    # 반도체/장비 저가
    {"code": "089030", "name": "테크윙"},
    {"code": "086520", "name": "에코프로"},
    {"code": "475150", "name": "SK이터닉스"},
    # 금융
    {"code": "316140", "name": "우리금융지주"},
    {"code": "086790", "name": "하나금융지주"},
    {"code": "055550", "name": "신한지주"},
    {"code": "105560", "name": "KB금융"},
    # 통신/유통
    {"code": "017670", "name": "SK텔레콤"},
    {"code": "030200", "name": "KT"},
    {"code": "282330", "name": "BGF리테일"},
    # 바이오/제약 저가
    {"code": "000100", "name": "유한양행"},
    {"code": "068270", "name": "셀트리온"},
    # 항공/물류
    {"code": "003490", "name": "대한항공"},
    {"code": "020560", "name": "아시아나항공"},
    # 게임/엔터
    {"code": "035720", "name": "카카오"},

    {"code": "036570", "name": "엔씨소프트"},
]

# 종목명 검색에 사용하는 확장 리스트 (주요 KOSPI/KOSDAQ 종목)
SEARCH_STOCKS: list[dict] = [
    # 반도체/IT
    {"code": "005930", "name": "삼성전자"},
    {"code": "000660", "name": "SK하이닉스"},
    {"code": "066570", "name": "LG전자"},
    {"code": "009150", "name": "삼성전기"},
    {"code": "011070", "name": "LG이노텍"},
    {"code": "006400", "name": "삼성SDI"},
    {"code": "373220", "name": "LG에너지솔루션"},
    {"code": "247540", "name": "에코프로비엠"},
    {"code": "086520", "name": "에코프로"},
    {"code": "003670", "name": "포스코퓨처엠"},
    {"code": "475150", "name": "SK이터닉스"},
    {"code": "089030", "name": "테크윙"},
    # 자동차
    {"code": "005380", "name": "현대차"},
    {"code": "000270", "name": "기아"},
    {"code": "012330", "name": "현대모비스"},
    {"code": "086280", "name": "현대글로비스"},
    {"code": "011210", "name": "현대위아"},
    # 인터넷/플랫폼
    {"code": "035420", "name": "네이버"},
    {"code": "035720", "name": "카카오"},
    {"code": "323410", "name": "카카오뱅크"},
    {"code": "293490", "name": "카카오페이"},
    # 금융
    {"code": "105560", "name": "KB금융"},
    {"code": "055550", "name": "신한지주"},
    {"code": "086790", "name": "하나금융지주"},
    {"code": "316140", "name": "우리금융지주"},
    {"code": "024110", "name": "기업은행"},
    {"code": "005940", "name": "NH투자증권"},
    {"code": "006800", "name": "미래에셋증권"},
    {"code": "071050", "name": "한국금융지주"},
    {"code": "030200", "name": "KT"},
    # 통신
    {"code": "017670", "name": "SK텔레콤"},
    {"code": "032640", "name": "LGU+"},
    # 에너지/정유
    {"code": "096770", "name": "SK이노베이션"},
    {"code": "010950", "name": "S-Oil"},
    {"code": "015760", "name": "한국전력"},
    {"code": "036460", "name": "한국가스공사"},
    {"code": "034730", "name": "SK"},
    # 화학/소재
    {"code": "051910", "name": "LG화학"},
    {"code": "011170", "name": "롯데케미칼"},
    {"code": "011780", "name": "금호석유화학"},
    {"code": "009830", "name": "한화솔루션"},
    {"code": "093370", "name": "후성"},
    {"code": "010060", "name": "OCI홀딩스"},
    # 철강
    {"code": "005490", "name": "POSCO홀딩스"},
    {"code": "004020", "name": "현대제철"},
    {"code": "001440", "name": "대한전선"},
    {"code": "006340", "name": "대원전선"},
    # 건설
    {"code": "000720", "name": "현대건설"},
    {"code": "006360", "name": "GS건설"},
    {"code": "047040", "name": "대우건설"},
    {"code": "028050", "name": "삼성E&A"},
    # 조선/중공업
    {"code": "009540", "name": "한국조선해양"},
    {"code": "010140", "name": "삼성중공업"},
    {"code": "329180", "name": "HD현대중공업"},
    {"code": "034020", "name": "두산에너빌리티"},
    {"code": "012450", "name": "한화에어로스페이스"},
    # 유통/소비
    {"code": "282330", "name": "BGF리테일"},
    {"code": "007070", "name": "GS리테일"},
    {"code": "023530", "name": "롯데쇼핑"},
    {"code": "004170", "name": "신세계"},
    {"code": "139480", "name": "이마트"},
    {"code": "069960", "name": "현대백화점"},
    # 바이오/제약
    {"code": "207940", "name": "삼성바이오로직스"},
    {"code": "068270", "name": "셀트리온"},
    {"code": "000100", "name": "유한양행"},
    {"code": "128940", "name": "한미약품"},
    {"code": "326030", "name": "SK바이오팜"},
    {"code": "145020", "name": "휴젤"},
    # 항공/물류
    {"code": "003490", "name": "대한항공"},
    {"code": "020560", "name": "아시아나항공"},
    {"code": "011200", "name": "HMM"},
    {"code": "028670", "name": "팬오션"},
    # 게임/엔터
    {"code": "259960", "name": "크래프톤"},
    {"code": "251270", "name": "넷마블"},
    {"code": "036570", "name": "엔씨소프트"},
    {"code": "293940", "name": "카카오게임즈"},
    # 지주
    {"code": "003550", "name": "LG"},
    {"code": "028260", "name": "삼성물산"},
    {"code": "006260", "name": "LS"},
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


def _get_orderable_cash(acct: str, suffix: str) -> int:
    """inquire-psbl-order로 실제 주문가능현금 조회.
    inquire-balance output2에는 ord_psbl_cash 필드가 없으므로 별도 호출 필요."""
    try:
        tr_id = "VTTC8908R" if config.KIS_MOCK else "TTTC8908R"
        resp = requests.get(
            f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            headers=_headers(tr_id),
            params={
                "CANO": acct,
                "ACNT_PRDT_CD": suffix,
                "PDNO": "005930",       # 삼성전자 — 종목 무관하게 현금 조회용
                "ORD_UNPR": "0",
                "ORD_DVSN": "01",       # 시장가
                "CMA_EVLU_AMT_ICLD_YN": "N",
                "OVRS_ICLD_YN": "N",
            },
            timeout=10,
        )
        resp.raise_for_status()
        out = resp.json().get("output", {})
        return int(out.get("ord_psbl_cash", 0))
    except Exception as e:
        logger.warning(f"주문가능현금 조회 실패 (inquire-psbl-order): {e}")
        return 0


def get_balance() -> dict:
    """계좌 잔고 조회 (500 에러 시 최대 2회 재시도)"""
    acct, suffix = config.KIS_ACCOUNT_NO.split("-")
    tr_id = "VTTC8434R" if config.KIS_MOCK else "TTTC8434R"
    params = {
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
    }
    for attempt in range(3):
        resp = requests.get(
            f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=_headers(tr_id),
            params=params,
        )
        if resp.status_code == 500 and attempt < 2:
            time.sleep(1)
            continue
        resp.raise_for_status()
        break
    data = resp.json()
    o2 = data["output2"][0] if data.get("output2") else {}

    # inquire-psbl-order로 실제 주문가능현금 조회 (inquire-balance output2엔 ord_psbl_cash 없음)
    orderable_cash = _get_orderable_cash(acct, suffix)

    return {
        "cash":            int(o2.get("dnca_tot_amt", 0)),
        "orderable_cash":  orderable_cash,                         # 실제 주문가능금액
        "settlement_cash": int(o2.get("prvs_rcdl_excc_amt", 0)),  # T+2 정산 후 출금 가능
        "total":           int(o2.get("tot_evlu_amt", 0)),         # 현금 + 주식 평가 합계
        "unrealized_pl":   int(o2.get("evlu_pfls_smtl_amt", 0)),  # 미실현 손익 합계
        "holdings": [
            {
                "code":          h["pdno"],
                "name":          h["prdt_name"],
                "qty":           int(h["hldg_qty"]),
                "avg_price":     float(h["pchs_avg_pric"]),
                "current_price": float(h["prpr"]),
                "eval_amount":   int(h.get("evlu_amt", 0)),        # 평가금액
                "pl_amount":     int(h.get("evlu_pfls_amt", 0)),   # 평가손익금액
                "profit_rate":   float(h["evlu_pfls_rt"]),
            }
            for h in data.get("output1", [])
            if int(h.get("hldg_qty", 0)) > 0
        ],
    }


def get_ohlcv(code: str, days: int = 100) -> pd.DataFrame:
    """주가 OHLCV 데이터 조회 (500 에러 시 최대 2회 재시도)

    inquire-daily-price(FHKST01010400) 사용. 최근 약 30영업일치를 반환한다.
    (구 inquire-daily-chartprice 엔드포인트는 일부 계정에서 404를 반환하여 교체)
    """
    for attempt in range(3):
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
        if resp.status_code == 500 and attempt < 2:
            time.sleep(1)
            continue
        resp.raise_for_status()
        break
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
    """주문 실행 (side: 'buy' | 'sell'). 500 에러 시 응답 본문에서 사유를 추출해 반환."""
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
    if resp.status_code == 500:
        # 주문 중복 방지를 위해 재시도 없이 본문에서 사유 추출
        try:
            body = resp.json()
            msg = body.get("msg1") or body.get("msg") or f"KIS 500 오류 ({code})"
            body["rt_cd"] = body.get("rt_cd", "E")
            body["error"] = {"message": msg, "rt_cd": "500"}
            return body
        except Exception:
            return {"rt_cd": "E", "msg1": f"KIS 서버 오류 500 ({code})", "error": {"message": f"KIS 서버 오류 ({code})"}}
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
            item = {
                "code": r["mksc_shrn_iscd"],
                "name": r["hts_kor_isnm"],
                "price": float(r["stck_prpr"]),
            }
            # 스크리닝용 부가 데이터 (필드가 없으면 생략)
            try:
                item["change_pct"] = float(r["prdy_ctrt"])
            except (KeyError, ValueError, TypeError):
                pass
            try:
                item["trade_value"] = float(r["acc_trdval"])  # 누적 거래대금(원)
            except (KeyError, ValueError, TypeError):
                pass
            out.append(item)
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
