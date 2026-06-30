"""Claude AI 기반 매매 의사결정 엔진"""
import json
from anthropic import Anthropic
from config import config

client = Anthropic(api_key=config.ANTHROPIC_API_KEY) if config.ANTHROPIC_API_KEY else None

SYSTEM_PROMPT = """당신은 전문 퀀트 트레이더 AI입니다. 기술적 지표를 분석하여 매매 결정을 내립니다.

규칙:
1. 각 종목/코인에 대해 BUY, SELL, HOLD 중 하나를 결정합니다
2. 결정에는 반드시 신뢰도(0.0~1.0)와 이유를 포함합니다
3. 신뢰도 0.7 미만이면 HOLD를 권장합니다
4. 과매수(RSI>70) + MACD 데드크로스 → 매도 신호
5. 과매도(RSI<30) + MACD 골든크로스 → 매수 신호
6. 볼린저밴드 하단 이탈 후 회귀 → 매수 기회
7. 거래량 급증 + 가격 상승 → 강력한 매수 신호
8. 반드시 JSON 형식으로만 응답하세요

응답 형식 (반드시 이 JSON만):
{
  "decisions": [
    {
      "name": "종목명",
      "ticker": "티커/코드",
      "action": "BUY|SELL|HOLD",
      "confidence": 0.0~1.0,
      "reason": "결정 이유 (한국어, 2~3문장)",
      "target_ratio": 0.0~1.0  // 포트폴리오 내 목표 비중 (BUY시)
    }
  ],
  "market_summary": "전반적인 시장 상황 요약 (한국어, 2~3문장)"
}"""


def analyze_and_decide(
    stock_summaries: list[str],
    crypto_summaries: list[str],
    portfolio_status: dict,
) -> dict:
    """AI가 전체 종목을 분석하고 매매 결정을 반환"""
    if not client:
        return {"decisions": [], "market_summary": "AI API 키 미설정", "error": True}

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

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    text = response.content[0].text.strip()
    # JSON 블록 추출
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 응답이 잘린 경우 decisions 배열까지만 추출 시도
        import re
        match = re.search(r'"decisions"\s*:\s*(\[.*?\])', text, re.DOTALL)
        if match:
            decisions = json.loads(match.group(1))
            return {"decisions": decisions, "market_summary": "응답 파싱 오류로 요약 생략"}
        return {"decisions": [], "market_summary": "AI 응답 파싱 실패", "error": True}


def quick_screen(tickers: list[dict], market_type: str = "stock") -> list[dict]:
    """AI가 유망 종목을 1차 스크리닝"""
    if not client:
        return tickers[:5]

    ticker_list = "\n".join([f"- {t.get('name', t.get('ticker', ''))} ({t.get('code', t.get('ticker', ''))})"
                             for t in tickers])

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""다음 {market_type} 목록에서 현재 시장에서 단기 트레이딩에 가장 유망한 5개를 선택하세요.
선택 기준: 유동성, 변동성, 섹터 모멘텀

목록:
{ticker_list}

응답 형식 (JSON만):
{{"selected": ["티커1", "티커2", "티커3", "티커4", "티커5"]}}"""
        }],
    )

    text = response.content[0].text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    selected_codes = json.loads(text).get("selected", [])
    return [t for t in tickers if t.get("code", t.get("ticker", "")) in selected_codes]
