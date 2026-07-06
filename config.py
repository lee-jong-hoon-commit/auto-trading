import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / os.getenv("DOTENV", ".env"))

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
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # Trading
    TRADE_INTERVAL_MINUTES: int = int(os.getenv("TRADE_INTERVAL_MINUTES", "30"))

    # ── 리스크 관리 (규칙 기반 청산 — AI 판단과 무관하게 기계적으로 실행) ──
    STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "-3.0"))              # 손절선 (손익률 %)
    TAKE_PROFIT_TRIGGER_PCT: float = float(os.getenv("TAKE_PROFIT_TRIGGER_PCT", "6.0"))  # 트레일링 발동 수익률 (%)
    TRAILING_STOP_PCT: float = float(os.getenv("TRAILING_STOP_PCT", "2.0"))       # 발동 후 고점 대비 하락 허용폭 (%)
    EMERGENCY_CUT_PCT: float = float(os.getenv("EMERGENCY_CUT_PCT", "-20.0"))     # 백스톱 강제 손절 (포지션 기록 누락 대비)
    MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "0.15"))        # 종목당 최대 투입 비중 (총자산 대비)
    MAX_POSITIONS: int = int(os.getenv("MAX_POSITIONS", "5"))                     # 최대 동시 보유 종목 수 (주식+코인)
    REBUY_COOLDOWN_HOURS: int = int(os.getenv("REBUY_COOLDOWN_HOURS", "24"))      # 매도 후 동일 종목 재매수 금지 시간
    MIN_HOLD_MINUTES: int = int(os.getenv("MIN_HOLD_MINUTES", "60"))              # AI SELL 최소 보유시간 (손절선은 예외)
    DAILY_LOSS_LIMIT_PCT: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "3.0")) # 일일 실현손실 한도 (총자산 %) — 초과 시 당일 신규매수 중단
    CHASE_LIMIT_PCT: float = float(os.getenv("CHASE_LIMIT_PCT", "10.0"))          # 당일 등락률 이 이상이면 신규매수 금지 (추격매수 방지)

    # AI는 진입 선택만 담당 — 아래는 분석 후보 상한선(성능용)
    STOCK_ANALYSIS_LIMIT: int = 10   # 1회 사이클에 기술적 지표 계산할 최대 주식 수
    CRYPTO_ANALYSIS_LIMIT: int = 8   # 1회 사이클에 기술적 지표 계산할 최대 코인 수
    # 업비트 최소 주문 금액(원). 이 금액 미만은 매수/매도 불가 → 먼지 잔고로 간주
    UPBIT_MIN_ORDER_KRW: int = int(os.getenv("UPBIT_MIN_ORDER_KRW", "5000"))
    # 코인 매수 최소 투자금액(원). 최소주문(5,000원)에 딱 맞춰 사면 조금만 하락해도
    # 매도 불가능한 먼지 잔고가 되므로 버퍼를 둔다 (7,000원이면 -28%까지 매도 가능)
    CRYPTO_MIN_BUY_KRW: int = int(os.getenv("CRYPTO_MIN_BUY_KRW", "7000"))
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
