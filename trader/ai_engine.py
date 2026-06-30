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

SYSTEM_PROMPT = """당신은 전문 퀀트 트레이더 AI입니다. 기술적 지표를 분석하여 매매 결정을 내립니다.

규칙:
1. 각 종목/코인에 대해 BUY, SELL, HOLD 중 하나를 결정합니다
2. 결정에는 반드시 신뢰도(0.0~1.0)와 이유를 포함합니다
3. 신뢰도 0.7 미만이면 HOLD를 권장합니다
4. 과매수(RSI>70) + MACD 데드크로스 → 매도 신호
5. 과매도(RSI<30) + MACD 골든크로스 → 매수 신호
6. 볼린저밴드 하단 이탈 후 회귀 → 매수 기회
7. 거래량 급증 + 가격 상승 → 강력한 매수 신호
8. BUY 결정은 1주 가격이 종목당 예산(portfolio의 stock_budget_per_position) 이하인
   종목에만 내립니다. 예산을 초과해 1주도 살 수 없으면 HOLD로 처리하세요.
   (예산 정보가 없거나 0이면 이 제약은 무시합니다)
9. 반드시 JSON 형식으로만 응답하세요

응답 형식 (반드시 이 JSON만):
{
  "decisions": [
    {
      "name": "종목명",
      "ticker": "티커/코드",
      "action": "BUY|SELL|HOLD",
      "confidence": 0.0~1.0,
      "reason": "결정 이유 (한국어, 2~3문장)",
      "target_ratio": 0.0~1.0
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

    # 기본: Ollama (로컬)
    try:
        body = {
            "model": config.OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": max_tokens},
        }
        if force_json:
            body["format"] = "json"  # Ollama가 유효한 JSON만 출력하도록 강제
        resp = requests.post(f"{config.OLLAMA_HOST}/api/chat", json=body, timeout=120)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content")
    except Exception as e:
        logger.warning(f"Ollama 호출 실패 ({config.OLLAMA_HOST}, model={config.OLLAMA_MODEL}): {e}")
        return None


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
현재 보유 중인 종목이 있다면 손절/익절 기준도 적용하세요.
(손절: -{config.STOP_LOSS_RATIO*100:.0f}%, 익절: +{config.TAKE_PROFIT_RATIO*100:.0f}%)"""

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
