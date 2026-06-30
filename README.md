# Auto Trader - AI 자동매매 시스템

한국투자증권(주식) + 업비트(코인) Claude AI 자동매매

## 빠른 시작

### 1. 설치
```bash
cd auto-trader
pip install -r requirements.txt
```

### 2. API 키 설정
```bash
cp .env.example .env
# .env 파일을 열어 API 키 입력
```

#### API 키 발급 방법
| 서비스 | 발급 경로 |
|--------|-----------|
| 한국투자증권 | https://apiportal.koreainvestment.com → 앱 신청 |
| 업비트 | 마이페이지 → Open API 관리 |
| Claude AI | https://console.anthropic.com → API Keys |

### 3. 실행

```bash
# 대화형 CLI (메뉴 방식)
python main.py

# 웹 대시보드 (http://localhost:8000)
python main.py web

# 봇 자동 시작 (백그라운드)
python main.py start

# 1회 분석만 실행
python main.py once
```

## 시스템 구조

```
사용자
  ├── 콘솔 CLI (main.py)
  └── 웹 대시보드 (localhost:8000)

AI 엔진 (Claude Sonnet)
  ├── 종목/코인 1차 스크리닝 (Claude Haiku - 빠름)
  ├── 기술적 지표 분석 (RSI, MACD, 볼린저밴드, MA, Stochastic)
  └── 매수/매도/홀드 결정 + 신뢰도 산출

실행
  ├── 한국투자증권 KIS API (주식)
  └── 업비트 API (코인)

리스크 관리
  ├── 손절: -5% 자동 매도
  ├── 익절: +15% 자동 매도
  ├── 종목당 최대 비중: 10%
  └── 신뢰도 70% 미만 → 스킵
```

## 주요 설정 (.env)

| 항목 | 기본값 | 설명 |
|------|--------|------|
| KIS_MOCK | true | true=모의투자, false=실전 |
| TRADE_INTERVAL_MINUTES | 30 | AI 분석 주기 |
| MAX_POSITION_RATIO | 0.1 | 종목당 최대 비중 (10%) |
| STOP_LOSS_RATIO | 0.05 | 손절 기준 (-5%) |
| TAKE_PROFIT_RATIO | 0.15 | 익절 기준 (+15%) |

## 주의사항

- **반드시 모의투자(KIS_MOCK=true)로 먼저 테스트하세요**
- 자동매매는 손실 위험이 있습니다
- API 키는 절대 공유하지 마세요
