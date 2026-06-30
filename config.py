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

    # AI 엔진 프로바이더: "ollama"(로컬·무료, 기본) | "anthropic"(Claude) | "gemini"
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "ollama").lower()
    # Ollama (로컬 LLM) — API 키/결제 불필요
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5")
    # Anthropic (선택)
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    # Google Gemini (선택) — 무료 티어 있음
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    # Trading
    TRADE_INTERVAL_MINUTES: int = int(os.getenv("TRADE_INTERVAL_MINUTES", "30"))
    # AI가 포지션 수/금액/손절·익절을 자율 결정 — 아래는 분석 후보 상한선(성능용)
    STOCK_ANALYSIS_LIMIT: int = 10   # 1회 사이클에 기술적 지표 계산할 최대 주식 수
    CRYPTO_ANALYSIS_LIMIT: int = 8   # 1회 사이클에 기술적 지표 계산할 최대 코인 수
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
        if self.AI_PROVIDER == "ollama":
            return True
        if self.AI_PROVIDER == "gemini":
            return bool(self.GEMINI_API_KEY)
        return bool(self.ANTHROPIC_API_KEY)


config = Config()
