# Auto Trader 인계 문서 (Claude Code 작업용)

> 로컬 Claude Code에서 이 파일을 열고 이어서 작업하세요.
> 브랜치 **`claude/auto-trader-handover-nesasr`** 에서 계속 진행합니다.

## 1. 프로젝트 개요
- 목적: 한국투자증권(주식) + 업비트(코인) **AI 자동매매**
- AI 엔진: **Ollama(로컬·무료) 기본**, 원하면 Claude(Anthropic)로 전환
- 실행: `python main.py web` → http://localhost:8000
- GitHub: `lee-jong-hoon-commit/auto-trading`
- 로컬: `C:\Users\kim\Desktop\auto-trader\` (WSL: `/mnt/c/Users/kim/Desktop/auto-trader/`)

## 2. 파일 구조
```
main.py            # CLI 진입점
config.py          # .env 기반 설정
.env               # 키 설정(.gitignore — 직접 생성)
trader/
  kis_client.py    # KIS API (OHLCV/잔고/주문/순위/동적 유니버스)
  upbit_client.py  # 업비트(pyupbit) + 먼지잔고 필터
  analyzer.py      # 기술적 지표(RSI/MACD/볼린저/Stochastic)
  ai_engine.py     # LLM 의사결정(Ollama 기본 / Claude 선택)
  executor.py      # 매매 실행 + 거래 로그
  bot.py           # APScheduler 주기 실행 루프
  watchlist.py     # 관심종목 영속화(data/watchlist.json)
web/
  app.py           # FastAPI + SSE + 워치리스트 REST
  templates/index.html
logs/  data/       # 런타임 산출물(.gitignore)
```

## 3. 이번 세션에서 완료(원격 push 완료)
1. **OHLCV 통일**: `inquire-daily-chartprice`(404) → `inquire-daily-price`(FHKST01010400).
   web/app.py의 인라인 중복 제거 → `kis_client.get_ohlcv`로 단일화.
2. **시총 순위 tr_id 교정**: FHPST01710000/20171 → FHPST01740000/20174.
3. **업비트 먼지 잔고**: `get_balance`에서 보유목록 제외(가격조회 실패 건은 유지).
   최소주문금액 `5000` 하드코딩 → `config.UPBIT_MIN_ORDER_KRW`로 통일.
4. **관심종목(워치리스트)**: `data/watchlist.json` 영속화 + 대시보드 추가/삭제 UI
   (REST: `GET/POST/DELETE /api/watchlist...`).
5. **종목 다양화 + 잔고 맞춤**: `DEFAULT_STOCKS` 15→33개(섹터·가격대 분산).
   `select_affordable_stocks`로 "예수금×MAX_POSITION_RATIO 예산으로 1주 이상
   매수 가능한 종목"만 분석. AI 프롬프트에 종목당 예산 규칙 추가.
6. **매일 바뀌는 동적 유니버스**: `get_volume_rank`(거래대금 상위, 가격상한=예산)
   → `get_dynamic_stocks`(거래대금 → 시총 → 워치리스트 폴백). bot/analyze가 1차 소스로 사용.
   하드코딩 리스트는 API 실패 시 폴백 전용.
7. **AI 엔진 Ollama 전환**: `config.AI_PROVIDER`(기본 `ollama`)/`OLLAMA_HOST`/`OLLAMA_MODEL`.
   `ai_engine._chat()`가 Ollama `/api/chat`(`format=json`) 호출. 호출 실패 시 안전 폴백.

## 4. 로컬에서 지금 할 일
```bash
# (1) Ollama 설치: https://ollama.com → 모델 받기(한 번만)
ollama pull qwen2.5              # 가벼운 PC면: ollama pull qwen2.5:3b

# (2) 코드 받기
cd /mnt/c/Users/kim/Desktop/auto-trader
git pull origin claude/auto-trader-handover-nesasr

# (3) .env 생성/확인
cp .env.example .env
#   AI_PROVIDER=ollama (기본), OLLAMA_MODEL=qwen2.5
#   KIS_*, UPBIT_* 키만 본인 것으로. ANTHROPIC_API_KEY는 비워도 됨.

# (4) 실행 → "AI 분석"(매매 없음) 먼저
python main.py web              # → http://localhost:8000
```
- `.env` 경로: 프로젝트 루트(`config.py`와 같은 폴더). 예) `C:\Users\kim\Desktop\auto-trader\.env`
- 확인 포인트: 상단 칩 `AI 엔진 (ollama) ✓`, 분석 로그에 `[거래대금 상위] 종목당 예산 …`.

## 5. 검증 상태
- 라이브 KIS/업비트 호출이 막힌 작업 환경이라 **mock 기반으로 검증**(봇 사이클/워치리스트/
  예산선별/동적유니버스/Ollama 모두 통과). 실제 라이브 동작은 본인 PC에서 "AI 분석"으로 1차 확인 필요.

## 6. 다음 작업 후보 (로컬 Claude Code에 시킬 것)
- [ ] 실 KIS에서 `volume-rank` 응답 필드/엔드포인트 실동작 확인(안 맞으면 자동 폴백됨)
- [ ] Ollama 프롬프트를 로컬 모델(qwen2.5 등)에 맞게 단순화·튜닝
- [ ] 모의투자(`KIS_MOCK=true`) 또는 소액으로 실제 봇 가동 테스트
- [ ] 백테스트/거래 통계 화면 추가

## 7. 주의
- **`.env`(실제 키)는 절대 커밋 금지.** 이미 외부에 노출된 키는 재발급 권장.
- 실거래는 `KIS_MOCK=false` + 업비트 실거래 → **진짜 체결**. 모의/소액 먼저.
- 매매는 봇이 **RUNNING**일 때만 실행("AI 분석" 버튼은 분석만, 주문 안 함).
- 매매 조건: AI 신뢰도 ≥ 0.7, 예산 충족, HOLD 아님.
