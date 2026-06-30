"""기술적 지표 계산 모듈"""
import pandas as pd
import numpy as np
import ta


def compute_indicators(df: pd.DataFrame) -> dict:
    """OHLCV DataFrame → 핵심 지표 딕셔너리"""
    if df.empty or len(df) < 20:
        return {}

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # RSI
    rsi = ta.momentum.RSIIndicator(close, window=14).rsi()

    # MACD
    macd_obj = ta.trend.MACD(close)
    macd = macd_obj.macd()
    macd_signal = macd_obj.macd_signal()
    macd_hist = macd_obj.macd_diff()

    # 볼린저 밴드
    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)

    # 이동평균
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean() if len(df) >= 60 else pd.Series([None] * len(df))

    # Stochastic
    stoch = ta.momentum.StochasticOscillator(high, low, close, window=14, smooth_window=3)

    # ATR (변동성)
    atr = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()

    # 거래량 평균 대비
    vol_ratio = volume.iloc[-1] / volume.rolling(20).mean().iloc[-1] if volume.rolling(20).mean().iloc[-1] > 0 else 1.0

    current = close.iloc[-1]
    prev = close.iloc[-2] if len(close) > 1 else current
    change_pct = (current - prev) / prev * 100

    return {
        "current_price": round(current, 2),
        "change_pct": round(change_pct, 2),
        "rsi": round(rsi.iloc[-1], 2) if not pd.isna(rsi.iloc[-1]) else None,
        "macd": round(macd.iloc[-1], 4) if not pd.isna(macd.iloc[-1]) else None,
        "macd_signal": round(macd_signal.iloc[-1], 4) if not pd.isna(macd_signal.iloc[-1]) else None,
        "macd_hist": round(macd_hist.iloc[-1], 4) if not pd.isna(macd_hist.iloc[-1]) else None,
        "bb_upper": round(bb.bollinger_hband().iloc[-1], 2) if not pd.isna(bb.bollinger_hband().iloc[-1]) else None,
        "bb_middle": round(bb.bollinger_mavg().iloc[-1], 2) if not pd.isna(bb.bollinger_mavg().iloc[-1]) else None,
        "bb_lower": round(bb.bollinger_lband().iloc[-1], 2) if not pd.isna(bb.bollinger_lband().iloc[-1]) else None,
        "bb_pct": round(bb.bollinger_pband().iloc[-1], 4) if not pd.isna(bb.bollinger_pband().iloc[-1]) else None,
        "ma5": round(ma5.iloc[-1], 2) if not pd.isna(ma5.iloc[-1]) else None,
        "ma20": round(ma20.iloc[-1], 2) if not pd.isna(ma20.iloc[-1]) else None,
        "ma60": round(ma60.iloc[-1], 2) if ma60.iloc[-1] is not None and not pd.isna(ma60.iloc[-1]) else None,
        "stoch_k": round(stoch.stoch().iloc[-1], 2) if not pd.isna(stoch.stoch().iloc[-1]) else None,
        "stoch_d": round(stoch.stoch_signal().iloc[-1], 2) if not pd.isna(stoch.stoch_signal().iloc[-1]) else None,
        "atr": round(atr.iloc[-1], 4) if not pd.isna(atr.iloc[-1]) else None,
        "volume_ratio": round(vol_ratio, 2),
        "recent_prices": close.tail(5).tolist(),
        "recent_volumes": volume.tail(5).tolist(),
    }


def summarize_for_ai(name: str, indicators: dict, market_type: str = "stock") -> str:
    """AI에게 전달할 분석 요약 텍스트 생성"""
    if not indicators:
        return f"{name}: 데이터 부족"

    lines = [f"[{name}] ({market_type})"]
    lines.append(f"현재가: {indicators['current_price']:,} | 등락: {indicators['change_pct']:+.2f}%")

    if indicators.get("rsi"):
        rsi_comment = "과매수" if indicators["rsi"] > 70 else ("과매도" if indicators["rsi"] < 30 else "중립")
        lines.append(f"RSI(14): {indicators['rsi']} ({rsi_comment})")

    if indicators.get("macd") is not None and indicators.get("macd_hist") is not None:
        signal = "골든크로스" if indicators["macd_hist"] > 0 else "데드크로스"
        lines.append(f"MACD: {indicators['macd']:.4f} / Signal: {indicators['macd_signal']:.4f} → {signal}")

    if indicators.get("bb_pct") is not None:
        bb_comment = "상단 돌파 위험" if indicators["bb_pct"] > 1 else ("하단 근접(반등 가능)" if indicators["bb_pct"] < 0.2 else "밴드 내")
        lines.append(f"볼린저밴드 %B: {indicators['bb_pct']:.3f} ({bb_comment})")

    if indicators.get("ma5") and indicators.get("ma20"):
        ma_signal = "단기>중기(상승)" if indicators["ma5"] > indicators["ma20"] else "단기<중기(하락)"
        lines.append(f"MA5: {indicators['ma5']:,} | MA20: {indicators['ma20']:,} → {ma_signal}")

    if indicators.get("stoch_k"):
        lines.append(f"Stochastic K/D: {indicators['stoch_k']}/{indicators['stoch_d']}")

    lines.append(f"거래량 비율: {indicators['volume_ratio']}x (20일 평균 대비)")

    return "\n".join(lines)
