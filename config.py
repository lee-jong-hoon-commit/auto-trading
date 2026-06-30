import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

class Config:
    # KIS
    KIS_APP_KEY: str = os.getenv("KIS_APP_KEY", "")
    KIS_APP_SECRET: str = os.getenv("KIS_APP_SECRET", "")
    KIS_ACCOUNT_NO: str = os.getenv("KIS_ACCOUNT_NO", "")
    KIS_MOCK: bool = os.getenv("KIS_MOCK", "true").lower() == "true"
    KIS_BASE_URL: str = (
        "https://openapivts.koreainvestment.com:29443"
        if os.getenv("KIS_MOCK", "true").lower() == "true"
        else "https://openapi.koreainvestment.com:9443"
    )

    # Upbit
    UPBIT_ACCESS_KEY: str = os.getenv("UPBIT_ACCESS_KEY", "")
    UPBIT_SECRET_KEY: str = os.getenv("UPBIT_SECRET_KEY", "")

    # Anthropic
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # Trading
    TRADE_INTERVAL_MINUTES: int = int(os.getenv("TRADE_INTERVAL_MINUTES", "30"))
    MAX_POSITION_RATIO: float = float(os.getenv("MAX_POSITION_RATIO", "0.1"))
    STOP_LOSS_RATIO: float = float(os.getenv("STOP_LOSS_RATIO", "0.05"))
    TAKE_PROFIT_RATIO: float = float(os.getenv("TAKE_PROFIT_RATIO", "0.15"))
    MAX_STOCK_POSITIONS: int = int(os.getenv("MAX_STOCK_POSITIONS", "5"))
    MAX_CRYPTO_POSITIONS: int = int(os.getenv("MAX_CRYPTO_POSITIONS", "3"))
    # 업비트 최소 주문 금액(원). 이 금액 미만은 매수/매도 불가 → 먼지 잔고로 간주
    UPBIT_MIN_ORDER_KRW: int = int(os.getenv("UPBIT_MIN_ORDER_KRW", "5000"))
    # 사용자 지정 분석 종목(쉼표 구분 종목코드, 예: "005930,000660").
    # 설정 시 기본 종목 목록 대신 우선 사용.
    CUSTOM_STOCKS: str = os.getenv("CUSTOM_STOCKS", "")
    # 사용자 지정 분석 코인(쉼표 구분 마켓코드, 예: "KRW-BTC,KRW-ETH").
    CUSTOM_TICKERS: str = os.getenv("CUSTOM_TICKERS", "")

    # Web
    WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0")
    WEB_PORT: int = int(os.getenv("WEB_PORT", "8000"))

    @property
    def is_kis_ready(self) -> bool:
        return bool(self.KIS_APP_KEY and self.KIS_APP_SECRET and self.KIS_ACCOUNT_NO)

    @property
    def is_upbit_ready(self) -> bool:
        return bool(self.UPBIT_ACCESS_KEY and self.UPBIT_SECRET_KEY)

    @property
    def is_ai_ready(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY)


config = Config()
