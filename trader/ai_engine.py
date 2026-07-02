"""LLM 기반 매매 의사결정 엔진.

기본 프로바이더는 Ollama(로컬 LLM) — API 키/결제 불필요.
config.AI_PROVIDER="anthropic" 으로 두면 Claude를 사용한다.
인터페이스(analyze_and_decide, quick_screen)는 프로바이더와 무관하게 동일하다.
"""
import json
import logging
import re
import requests
from config import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """당신은 전문 퀀트 트레이더 AI입니다. 5가지 기술적 전략이 코드로 이미 투표를 완료했습니다. 당신의 역할은 투표 방향을 확정하고 이유를 설명하는 것입니다.

【핵심 규칙 — 반드시 준수】
각 종목 데이터의 【전략투표】 항목을 확인하세요:
- 방향이 BUY  → action은 반드시 BUY 또는 HOLD만 선택 (SELL 불가)
- 방향이 SELL → action은 반드시 SELL 또는 HOLD만 선택 (BUY 불가)
- 방향이 HOLD → action은 HOLD

confidence는 score 절댓값으로 결정:
- score ±1 → 0.60~0.69
- score ±2 → 0.70~0.79
- score ±3 → 0.80~0.89
- score ±4~5 → 0.90~0.95

BUY 시 amount_krw 규칙:
- 잔고(stock_cash/crypto_cash)를 초과 불가
- 주식: 반드시 현재가 이상 (1주 단위), 잔고의 30~60%
- 코인: 최소 5,000원, 잔고의 20~50%

[중요] ticker 규칙:
- 주식: 6자리 숫자 코드 (예: 005930)
- 코인: KRW-로 시작 (예: KRW-BTC)
- 해당 데이터가 없으면 그 유형의 항목을 decisions에 포함하지 마세요

응답 형식 (JSON만):
{
  "decisions": [
    {
      "name": "종목명",
      "ticker": "티커/코드",
      "action": "BUY|SELL|HOLD",
      "confidence": 0.0~1.0,
      "amount_krw": 매수금액_정수(BUY일때만),
      "reason": "5전략 투표 결과와 주요 지표 기반 결정 이유 (한국어, 2~3문장)"
    }
  ],
  "market_summary": "전반적인 시장 상황 요약 (한국어, 2~3문장)"
}"""


# Anthropic 클라이언트는 provider=anthropic 일 때만 초기화 (없어도 import 에러 안 남)
_anthropic = None
if config.AI_PROVIDER == "anthropic" and config.ANTHROPIC_API_KEY:
    try:
        from anthropic import Anthropic
        _anthropic = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    except Exception as e:  # pragma: no cover - 환경 의존
        logger.warning(f"Anthropic 초기화 실패: {e}")


def _extract_json(text: str) -> str:
    """응답 텍스트에서 JSON 블록만 추출."""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    return text


def _chat(messages: list[dict], max_tokens: int = 4096, force_json: bool = True) -> str | None:
    """설정된 프로바이더로 LLM을 호출하고 응답 텍스트를 반환. 실패 시 None."""
    if config.AI_PROVIDER == "anthropic":
        if not _anthropic:
            return None
        try:
            system = next((m["content"] for m in messages if m["role"] == "system"), None)
            chat_msgs = [m for m in messages if m["role"] != "system"]
            resp = _anthropic.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=chat_msgs,
            )
            return resp.content[0].text
        except Exception as e:
            logger.warning(f"Anthropic 호출 실패: {e}")
            return None

    if config.AI_PROVIDER == "gemini":
        if not config.GEMINI_API_KEY:
            return None
        try:
            system = next((m["content"] for m in messages if m["role"] == "system"), None)
            chat_msgs = [m for m in messages if m["role"] != "system"]
            body = {
                "contents": [
                    {"role": "user", "parts": [{"text": m["content"]}]}
                    for m in chat_msgs
                ],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": max_tokens,
                },
            }
            if system:
                body["system_instruction"] = {"parts": [{"text": system}]}
            if force_json:
                body["generationConfig"]["responseMimeType"] = "application/json"
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models"
                f"/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
            )
            resp = requests.post(url, json=body, timeout=60)
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            logger.warning(f"Gemini 호출 실패 (model={config.GEMINI_MODEL}): {e}")
            return None

    # 기본: Ollama (로컬)
    try:
        body = {
            "model": config.OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": max_tokens},
        }
        if force_json:
            body["format"] = "json"
        resp = requests.post(f"{config.OLLAMA_HOST}/api/chat", json=body, timeout=120)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content")
    except Exception as e:
        logger.warning(f"Ollama 호출 실패 ({config.OLLAMA_HOST}, model={config.OLLAMA_MODEL}): {e}")
        return None


def _validate_decisions(decisions: list[dict], stock_summaries: list[str], crypto_summaries: list[str],
                        portfolio_status: dict | None = None) -> list[dict]:
    """AI 응답 검증: 시장 분류 + SELL은 보유 종목에만 허용."""
    has_stocks = bool(stock_summaries)
    has_crypto = bool(crypto_summaries)

    # 보유 종목 코드 세트 (SELL 필터용)
    held_stocks  = {h["code"]   for h in (portfolio_status or {}).get("stock_holdings",  [])} if portfolio_status else set()
    held_cryptos = {h["ticker"] for h in (portfolio_status or {}).get("crypto_holdings", [])} if portfolio_status else set()

    valid = []
    for d in decisions:
        ticker    = d.get("ticker", "")
        action    = d.get("action", "HOLD")
        is_crypto = ticker.upper().startswith("KRW-")

        # 시장 분류 검증
        if is_crypto and not has_crypto:
            logger.debug(f"코인 ticker 제거 (코인 데이터 없음): {ticker}")
            continue
        if not is_crypto and (not has_stocks or not re.match(r"^\d{5,6}$", ticker)):
            logger.debug(f"잘못된 ticker 제거: {ticker}")
            continue

        # SELL은 실제 보유 종목에만 허용
        if action == "SELL" and portfolio_status:
            if is_crypto and ticker not in held_cryptos:
                logger.info(f"SELL 제거 (미보유 코인): {ticker}")
                continue
            if not is_crypto and ticker not in held_stocks:
                logger.info(f"SELL 제거 (미보유 주식): {ticker} — 보유: {held_stocks}")
                continue

        valid.append(d)
    return valid


def analyze_and_decide(
    stock_summaries: list[str],
    crypto_summaries: list[str],
    portfolio_status: dict,
) -> dict:
    """전체 종목을 분석하고 매매 결정을 반환."""
    portfolio_text = json.dumps(portfolio_status, ensure_ascii=False, indent=2)

    user_message = f"""현재 포트폴리오 상태:
{portfolio_text}

=== 주식 기술적 분석 ===
{chr(10).join(stock_summaries) if stock_summaries else '주식 데이터 없음'}

=== 코인 기술적 분석 ===
{chr(10).join(crypto_summaries) if crypto_summaries else '코인 데이터 없음'}

위 데이터를 분석하여 각 종목/코인에 대한 매매 결정을 JSON 형식으로 출력하세요.
보유 종목이 있다면 현재 기술적 지표를 기준으로 수익 실현 또는 손실 최소화 여부를 판단하세요."""

    text = _chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        max_tokens=8192,
        force_json=True,
    )
    if not text:
        msg = "AI 엔진 호출 실패 (Ollama 미실행/모델 미설치 여부 확인)"
        return {"decisions": [], "market_summary": msg, "error": True}

    text = _extract_json(text)
    try:
        result = json.loads(text)
        result.setdefault("decisions", [])
        result.setdefault("market_summary", "")
        result["decisions"] = _validate_decisions(result["decisions"], stock_summaries, crypto_summaries, portfolio_status)
        return result
    except json.JSONDecodeError:
        logger.warning(f"AI JSON 파싱 실패, 부분 추출 시도. 응답 앞부분: {text[:200]}")
        decisions = []
        summary = ""

        # decisions 배열 추출 — 탐욕적 매칭으로 중첩 괄호 대응
        match_d = re.search(r'"decisions"\s*:\s*(\[.*\])', text, re.DOTALL)
        if match_d:
            try:
                decisions = _validate_decisions(
                    json.loads(match_d.group(1)), stock_summaries, crypto_summaries, portfolio_status
                )
            except json.JSONDecodeError:
                pass

        # market_summary 추출
        match_s = re.search(r'"market_summary"\s*:\s*"(.*?)"(?:\s*[,}])', text, re.DOTALL)
        if match_s:
            summary = match_s.group(1).replace('\\n', '\n')

        if decisions:
            return {"decisions": decisions, "market_summary": summary or "시장 요약 추출 실패"}
        return {"decisions": [], "market_summary": "AI 응답 파싱 실패", "error": True}


GUIDE_SYSTEM_PROMPT = """당신은 경험 많은 기술적 분석 전문가입니다. 수동 투자자를 위한 실질적이고 구체적인 매매 가이드를 제공합니다.
- 현재가 기준으로 구체적인 가격대를 제시하세요
- 진입가·목표가·손절가는 숫자로 명확히 제시하세요
- 핵심 신호는 3~4개로 요약하세요
- 리스크를 반드시 언급하세요"""


def generate_guide(summary: str, market_type: str, name: str = "") -> dict:
    """수동 투자자를 위한 상세 매매 가이드 생성."""
    market_label = "주식" if market_type == "stock" else "코인"
    text = _chat(
        [
            {"role": "system", "content": GUIDE_SYSTEM_PROMPT},
            {"role": "user", "content": f"""다음 {market_label} 기술적 지표를 분석하고 매매 가이드를 JSON으로 제공하세요.

{summary}

다음 JSON 형식으로만 응답하세요:
{{
  "action": "BUY 또는 SELL 또는 HOLD",
  "confidence": 0.0~1.0,
  "assessment": "현재 상황 종합 평가 (2~3문장)",
  "entry": "진입 가격대 (예: 65,000~65,500원, BUY일 때만)",
  "target": "목표가 (예: 69,000원, BUY일 때만)",
  "stop_loss": "손절가 (예: 62,000원, BUY일 때만)",
  "key_signals": ["신호1", "신호2", "신호3"],
  "support_levels": ["지지선 가격1", "지지선 가격2"],
  "resistance_levels": ["저항선 가격1", "저항선 가격2"],
  "risk_note": "주요 리스크 또는 주의사항"
}}"""},
        ],
        max_tokens=4096,
        force_json=True,
    )
    if not text:
        return {"action": "HOLD", "confidence": 0, "assessment": "AI 분석 실패", "risk_note": ""}
    try:
        return json.loads(_extract_json(text))
    except (json.JSONDecodeError, ValueError):
        return {"action": "HOLD", "confidence": 0, "assessment": text[:300] if text else "파싱 실패", "risk_note": ""}


def quick_screen(tickers: list[dict], market_type: str = "stock") -> list[dict]:
    """유망 종목을 1차 스크리닝."""
    if not tickers:
        return []

    ticker_list = "\n".join(
        f"- {t.get('name', t.get('ticker', ''))} ({t.get('code', t.get('ticker', ''))})"
        for t in tickers
    )

    text = _chat(
        [{
            "role": "user",
            "content": f"""다음 {market_type} 목록에서 현재 시장에서 단기 트레이딩에 가장 유망한 5개를 선택하세요.
선택 기준: 유동성, 변동성, 섹터 모멘텀

목록:
{ticker_list}

응답 형식 (JSON만):
{{"selected": ["티커1", "티커2", "티커3", "티커4", "티커5"]}}""",
        }],
        max_tokens=500,
        force_json=True,
    )
    if not text:
        return tickers[:5]  # AI 미사용 시 상위 5개 폴백

    try:
        selected_codes = json.loads(_extract_json(text)).get("selected", [])
    except (json.JSONDecodeError, AttributeError):
        return tickers[:5]
    screened = [t for t in tickers if t.get("code", t.get("ticker", "")) in selected_codes]
    return screened or tickers[:5]
