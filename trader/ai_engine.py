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

SYSTEM_PROMPT = """당신은 전문 퀀트 트레이더 AI입니다. 기술적 지표를 분석하여 매매 결정과 투자 금액을 직접 결정합니다.

판단 기준:
- RSI, MACD, 볼린저밴드, 거래량 등 기술적 지표를 종합적으로 고려합니다
- BUY: 상승 신호가 명확할 때. amount_krw에 투자할 금액(원)을 직접 지정하세요
  - 잔고(stock_cash 또는 crypto_cash)를 초과할 수 없습니다
  - 코인은 최소 5,000원 이상이어야 합니다
  - 신호 강도에 따라 잔고의 20~50% 범위에서 결정하세요
  - 보유 종목 수 제한 없음 — 신호가 좋으면 여러 종목 동시 매수 가능
- SELL: 하락/과매수 신호 또는 수익 실현 시. 보유 전량 매도합니다
- HOLD: 명확한 신호가 없을 때
- confidence가 0.6 미만이면 HOLD 권장

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
      "reason": "결정 이유 (한국어, 2~3문장)"
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


def _validate_decisions(decisions: list[dict], stock_summaries: list[str], crypto_summaries: list[str]) -> list[dict]:
    """AI 응답의 ticker가 올바른 시장(주식/코인)에 속하는지 검증."""
    has_stocks = bool(stock_summaries)
    has_crypto = bool(crypto_summaries)
    valid = []
    for d in decisions:
        ticker = d.get("ticker", "")
        is_crypto = ticker.upper().startswith("KRW-")
        if is_crypto and has_crypto:
            valid.append(d)
        elif not is_crypto and has_stocks and re.match(r"^\d{5,6}$", ticker):
            valid.append(d)
        else:
            logger.debug(f"잘못된 ticker 제거: {ticker} (is_crypto={is_crypto}, has_stocks={has_stocks}, has_crypto={has_crypto})")
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
        max_tokens=4096,
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
        result["decisions"] = _validate_decisions(result["decisions"], stock_summaries, crypto_summaries)
        return result
    except json.JSONDecodeError:
        # 응답이 잘린 경우 decisions 배열까지만 추출 시도
        match = re.search(r'"decisions"\s*:\s*(\[.*?\])', text, re.DOTALL)
        if match:
            try:
                return {"decisions": json.loads(match.group(1)),
                        "market_summary": "응답 파싱 오류로 요약 생략"}
            except json.JSONDecodeError:
                pass
        return {"decisions": [], "market_summary": "AI 응답 파싱 실패", "error": True}


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
