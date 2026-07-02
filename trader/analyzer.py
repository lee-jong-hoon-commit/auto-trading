"""기술적 지표 계산 모듈 — 5가지 전문 매매 기법 기반"""
import pandas as pd
import numpy as np
import ta


def _safe(series, idx=-1, digits=2):
    try:
        v = series.iloc[idx]
        return round(float(v), digits) if not pd.isna(v) else None
    except Exception:
        return None


def compute_indicators(df: pd.DataFrame) -> dict:
    """OHLCV DataFrame → 핵심 지표 딕셔너리 (5가지 전문 매매 기법)"""
    if df.empty or len(df) < 20:
        return {}

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    # ── 기본 ──────────────────────────────────────────────────
    current    = close.iloc[-1]
    prev       = close.iloc[-2] if len(close) > 1 else current
    change_pct = (current - prev) / prev * 100

    # ── 전략 1: 추세추종 (Trend Following) ────────────────────
    # 이동평균 (단기/중기/장기 배열로 추세 방향 판단)
    ma5  = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean() if len(df) >= 60 else pd.Series([np.nan] * len(df), index=df.index)
    # ADX: 추세 강도 (25 이상 = 추세 강함, 20 미만 = 횡보)
    adx_obj = ta.trend.ADXIndicator(high, low, close, window=14)
    adx     = adx_obj.adx()
    di_pos  = adx_obj.adx_pos()   # +DI: 상승 추세 강도
    di_neg  = adx_obj.adx_neg()   # -DI: 하락 추세 강도

    # ── 전략 2: 모멘텀 (Momentum) ─────────────────────────────
    rsi      = ta.momentum.RSIIndicator(close, window=14).rsi()
    stoch    = ta.momentum.StochasticOscillator(high, low, close, window=14, smooth_window=3)
    williams = ta.momentum.WilliamsRIndicator(high, low, close, lbp=14).williams_r()
    roc      = ta.momentum.ROCIndicator(close, window=12).roc()  # 12일 변화율

    # ── 전략 3: 평균회귀 (Mean Reversion) ────────────────────
    bb       = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    macd_obj = ta.trend.MACD(close)
    macd     = macd_obj.macd()
    macd_sig = macd_obj.macd_signal()
    macd_hist= macd_obj.macd_diff()

    # ── 전략 4: 돌파 (Breakout) ──────────────────────────────
    high_52w = high.rolling(min(len(df), 252)).max().iloc[-1]
    low_52w  = low.rolling(min(len(df), 252)).min().iloc[-1]
    # 20일 고점 대비 현재가 위치
    high_20d = high.rolling(20).max().iloc[-1]
    low_20d  = low.rolling(20).min().iloc[-1]
    breakout_20d = current >= high_20d * 0.99   # 20일 고점 돌파/근접
    # ATR: 변동성 기반 손절/목표가 계산용
    atr      = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()
    # 피벗포인트 (전일 고저종 기반 지지/저항)
    ph = high.iloc[-2]; pl = low.iloc[-2]; pc = close.iloc[-2]
    pivot = (ph + pl + pc) / 3
    r1 = 2 * pivot - pl
    s1 = 2 * pivot - ph
    r2 = pivot + (ph - pl)
    s2 = pivot - (ph - pl)

    # ── 전략 5: 거래량 분석 (Volume Analysis) ────────────────
    obv       = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    obv_ma5   = obv.rolling(5).mean()
    vol_ratio = (volume.iloc[-1] / volume.rolling(20).mean().iloc[-1]
                 if volume.rolling(20).mean().iloc[-1] > 0 else 1.0)
    # VWAP (당일 단순 근사: 최근 20일 평균)
    vwap = (close * volume).rolling(20).sum() / volume.rolling(20).sum()

    return {
        # 기본
        "current_price": round(current, 2),
        "change_pct":    round(change_pct, 2),
        # 전략 1: 추세추종
        "ma5":    _safe(ma5),  "ma20": _safe(ma20), "ma60": _safe(ma60),
        "adx":    _safe(adx),  "di_pos": _safe(di_pos), "di_neg": _safe(di_neg),
        # 전략 2: 모멘텀
        "rsi":      _safe(rsi),
        "stoch_k":  _safe(stoch.stoch()),
        "stoch_d":  _safe(stoch.stoch_signal()),
        "williams": _safe(williams),
        "roc":      _safe(roc, digits=2),
        # 전략 3: 평균회귀
        "macd":       _safe(macd, digits=4),
        "macd_signal":_safe(macd_sig, digits=4),
        "macd_hist":  _safe(macd_hist, digits=4),
        "bb_upper":   _safe(bb.bollinger_hband()),
        "bb_middle":  _safe(bb.bollinger_mavg()),
        "bb_lower":   _safe(bb.bollinger_lband()),
        "bb_pct":     _safe(bb.bollinger_pband(), digits=4),
        # 전략 4: 돌파
        "high_52w":     round(high_52w, 2),
        "low_52w":      round(low_52w, 2),
        "high_20d":     round(high_20d, 2),
        "low_20d":      round(low_20d, 2),
        "breakout_20d": breakout_20d,
        "atr":          _safe(atr, digits=2),
        "pivot":  round(pivot, 2), "r1": round(r1, 2), "r2": round(r2, 2),
        "s1":     round(s1, 2),    "s2": round(s2, 2),
        # 전략 5: 거래량 분석
        "obv_rising":   bool(_safe(obv) is not None and _safe(obv_ma5) is not None
                             and obv.iloc[-1] > obv_ma5.iloc[-1]),
        "volume_ratio": round(vol_ratio, 2),
        "vwap":         _safe(vwap),
        "recent_prices":  close.tail(5).tolist(),
        "recent_volumes": volume.tail(5).tolist(),
    }


def summarize_for_ai(name: str, indicators: dict, market_type: str = "stock") -> str:
    """5가지 전문 매매 기법 기반 AI 분석 요약"""
    if not indicators:
        return f"{name}: 데이터 부족"

    p = indicators
    cur = p["current_price"]
    lines = [f"[{name}] ({market_type})",
             f"현재가: {cur:,} | 등락: {p['change_pct']:+.2f}%"]

    # ── 전략 1: 추세추종 ──────────────────────────────────────
    trend_parts = []
    if p.get("ma5") and p.get("ma20"):
        arr = "상승배열" if (p["ma5"] > p["ma20"] and
                             (p.get("ma60") is None or p["ma20"] > p["ma60"])) \
              else ("하락배열" if p["ma5"] < p["ma20"] else "혼조")
        trend_parts.append(f"MA5/20/60={p['ma5']:,}/{p['ma20']:,}/{p.get('ma60','N/A')} ({arr})")
    if p.get("adx") is not None:
        adx_str = f"ADX={p['adx']}"
        if p["adx"] >= 25:
            dir_str = "상승추세" if (p.get("di_pos", 0) or 0) > (p.get("di_neg", 0) or 0) else "하락추세"
            adx_str += f"(강한 {dir_str}, +DI={p.get('di_pos')}/−DI={p.get('di_neg')})"
        else:
            adx_str += "(횡보/추세 약함)"
        trend_parts.append(adx_str)
    if trend_parts:
        lines.append("▶ 추세추종: " + " | ".join(trend_parts))

    # ── 전략 2: 모멘텀 ───────────────────────────────────────
    mom_parts = []
    if p.get("rsi") is not None:
        rc = "과매수" if p["rsi"] > 70 else ("과매도" if p["rsi"] < 30 else "중립")
        mom_parts.append(f"RSI={p['rsi']}({rc})")
    if p.get("stoch_k") is not None:
        sk = p["stoch_k"]
        sc = "과매수" if sk > 80 else ("과매도" if sk < 20 else "중립")
        cross = "골든" if (p.get("stoch_k", 0) > p.get("stoch_d", 0)) else "데드"
        mom_parts.append(f"Stoch={sk}/{p.get('stoch_d')}({sc},{cross}크로스)")
    if p.get("williams") is not None:
        wc = "과매수" if p["williams"] > -20 else ("과매도" if p["williams"] < -80 else "중립")
        mom_parts.append(f"Williams%R={p['williams']}({wc})")
    if p.get("roc") is not None:
        mom_parts.append(f"ROC(12)={p['roc']:+.1f}%")
    if mom_parts:
        lines.append("▶ 모멘텀: " + " | ".join(mom_parts))

    # ── 전략 3: 평균회귀 ─────────────────────────────────────
    rev_parts = []
    if p.get("macd_hist") is not None:
        sig = "골든크로스" if p["macd_hist"] > 0 else "데드크로스"
        rev_parts.append(f"MACD={p['macd']:.3f}/Sig={p['macd_signal']:.3f}({sig})")
    if p.get("bb_pct") is not None:
        bc = ("상단 돌파—과열" if p["bb_pct"] > 1
              else ("하단 근접—반등 가능" if p["bb_pct"] < 0.2 else "밴드 중간"))
        rev_parts.append(f"BB%B={p['bb_pct']:.3f}({bc})")
        if p.get("vwap"):
            vwap_pos = "VWAP 위(강세)" if cur > p["vwap"] else "VWAP 아래(약세)"
            rev_parts.append(f"VWAP={p['vwap']:,}({vwap_pos})")
    if rev_parts:
        lines.append("▶ 평균회귀: " + " | ".join(rev_parts))

    # ── 전략 4: 돌파 ─────────────────────────────────────────
    brk_parts = []
    if p.get("high_52w"):
        pct_from_high = (cur - p["high_52w"]) / p["high_52w"] * 100
        brk_parts.append(f"52주고점={p['high_52w']:,}({pct_from_high:+.1f}%)")
    if p.get("breakout_20d"):
        brk_parts.append("20일 고점 돌파(강한 매수 신호)")
    if p.get("pivot"):
        pos = ("저항R1 근접" if cur > p["r1"] * 0.99
               else ("지지S1 근접" if cur < p["s1"] * 1.01 else "피벗 내"))
        brk_parts.append(f"피벗={p['pivot']:,} R1={p['r1']:,} S1={p['s1']:,}({pos})")
    if p.get("atr"):
        brk_parts.append(f"ATR={p['atr']:,}(목표가+{p['atr']*2:,.0f}/손절−{p['atr']:,.0f})")
    if brk_parts:
        lines.append("▶ 돌파전략: " + " | ".join(brk_parts))

    # ── 전략 5: 거래량 분석 ──────────────────────────────────
    vol_parts = [f"거래량={p['volume_ratio']}x(20일 평균 대비)"]
    vol_parts.append("OBV 상승(매집)" if p.get("obv_rising") else "OBV 하락(분산)")
    if p["volume_ratio"] >= 2.0:
        vol_parts.append("⚡ 거래량 급증")
    lines.append("▶ 거래량분석: " + " | ".join(vol_parts))

    return "\n".join(lines)
