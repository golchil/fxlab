"""
Feature extractor for window bar data.

Computes per-window numerical features:
- SMA slopes (normalized by ATR)
- Cross-MA spreads (normalized by ATR)
- ATR14
- Volume mean
"""
from typing import List, Tuple, Optional, Dict
import math


def _sma(values: List[float], period: int) -> List[Optional[float]]:
    """Compute simple moving average. Returns None for positions with insufficient data."""
    result = [None] * len(values)
    if len(values) < period:
        return result
    window_sum = sum(values[:period])
    result[period - 1] = window_sum / period
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        result[i] = window_sum / period
    return result


def _true_range(highs: List[float], lows: List[float], closes: List[float]) -> List[float]:
    """Compute true range series."""
    tr = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    return tr


def _slope(sma_values: List[Optional[float]], lookback: int, idx: int) -> Optional[float]:
    """Compute slope of SMA over lookback period at given index.
    Returns (sma[idx] - sma[idx - lookback]) / lookback."""
    if idx < lookback:
        return None
    v_now = sma_values[idx]
    v_prev = sma_values[idx - lookback]
    if v_now is None or v_prev is None:
        return None
    return (v_now - v_prev) / lookback


def extract_features(
    bars: List[Tuple[float, float, float, float, float]],
) -> Optional[Dict[str, float]]:
    """
    Extract features from a list of bars.

    Args:
        bars: List of (open, high, low, close, volume) tuples, ordered by time.

    Returns:
        Dict with feature names as keys, or None if insufficient data.
        Features are computed from the last bar in the window.
    """
    n = len(bars)
    if n < 60:
        return None

    opens = [b[0] for b in bars]
    highs = [b[1] for b in bars]
    lows = [b[2] for b in bars]
    closes = [b[3] for b in bars]
    volumes = [b[4] for b in bars]

    # Compute SMAs
    sma5 = _sma(closes, 5)
    sma20 = _sma(closes, 20)
    sma60 = _sma(closes, 60)

    # Compute ATR14
    tr = _true_range(highs, lows, closes)
    atr14_series = _sma(tr, 14)

    last = n - 1
    atr14 = atr14_series[last]
    if atr14 is None or atr14 < 1e-10:
        return None

    # SMA slopes normalized by ATR
    sma5_slope_5 = _slope(sma5, 5, last)
    sma20_slope_5 = _slope(sma20, 5, last)
    sma20_slope_20 = _slope(sma20, 20, last)
    sma60_slope_20 = _slope(sma60, 20, last)

    if any(v is None for v in [sma5_slope_5, sma20_slope_5, sma20_slope_20, sma60_slope_20]):
        return None

    # Normalize slopes by ATR
    sma5_slope_5 /= atr14
    sma20_slope_5 /= atr14
    sma20_slope_20 /= atr14
    sma60_slope_20 /= atr14

    # Close to SMA20 spread normalized by ATR
    sma20_val = sma20[last]
    if sma20_val is None:
        return None
    close_to_sma20 = (closes[last] - sma20_val) / atr14

    # MA spreads normalized by ATR
    sma5_val = sma5[last]
    sma60_val = sma60[last]
    if sma5_val is None or sma60_val is None:
        return None

    spread_5_20 = (sma5_val - sma20_val) / atr14
    spread_20_60 = (sma20_val - sma60_val) / atr14

    # Volume mean
    vol_mean = sum(volumes) / n if n > 0 else 0.0

    return {
        "sma5_slope_5": sma5_slope_5,
        "sma20_slope_5": sma20_slope_5,
        "sma20_slope_20": sma20_slope_20,
        "sma60_slope_20": sma60_slope_20,
        "close_to_sma20": close_to_sma20,
        "spread_5_20": spread_5_20,
        "spread_20_60": spread_20_60,
        "atr14": atr14,
        "vol_mean": vol_mean,
    }
