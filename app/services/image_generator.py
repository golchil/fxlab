"""Candlestick chart image generator using matplotlib."""
import io
from typing import List, Tuple
from datetime import datetime

import matplotlib
matplotlib.use('Agg')  # Non-GUI backend
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

from app.config import settings


# Color scheme
COLOR_UP = '#26a69a'  # Green for bullish candles
COLOR_DOWN = '#ef5350'  # Red for bearish candles
COLOR_BG = '#ffffff'  # White background
COLOR_GRID = '#e0e0e0'  # Light gray grid
MA_COLORS = ['#1976d2', '#ff9800', '#9c27b0', '#4caf50', '#f44336']  # Blue, Orange, Purple, Green, Red


def calculate_sma(closes: List[float], period: int) -> List[float]:
    """Calculate Simple Moving Average."""
    sma = []
    for i in range(len(closes)):
        if i < period - 1:
            sma.append(None)
        else:
            avg = sum(closes[i - period + 1:i + 1]) / period
            sma.append(avg)
    return sma


def generate_candlestick_image(
    bars: List[Tuple[datetime, float, float, float, float]],  # (ts, open, high, low, close)
    ma_periods: List[int] = None,
    image_size: int = None,
) -> bytes:
    """
    Generate a candlestick chart with optional moving averages.

    Args:
        bars: List of (timestamp, open, high, low, close) tuples, sorted by time
        ma_periods: List of MA periods to draw (e.g., [5, 20, 60])
        image_size: Image size in pixels (square)

    Returns:
        PNG image as bytes
    """
    if ma_periods is None:
        ma_periods = settings.default_ma_periods
    if image_size is None:
        image_size = settings.image_size

    # Extract OHLC data
    opens = [b[1] for b in bars]
    highs = [b[2] for b in bars]
    lows = [b[3] for b in bars]
    closes = [b[4] for b in bars]

    n_bars = len(bars)

    # Create figure
    dpi = 100
    fig_size = image_size / dpi
    fig, ax = plt.subplots(figsize=(fig_size, fig_size), dpi=dpi)

    # Set background
    ax.set_facecolor(COLOR_BG)
    fig.patch.set_facecolor(COLOR_BG)

    # Draw candlesticks
    candle_width = 0.6
    wick_width = 0.15

    for i in range(n_bars):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        color = COLOR_UP if c >= o else COLOR_DOWN

        # Draw wick (high-low line)
        ax.plot([i, i], [l, h], color=color, linewidth=wick_width * 2, solid_capstyle='round')

        # Draw body (open-close rectangle)
        body_bottom = min(o, c)
        body_height = abs(c - o)
        if body_height < (h - l) * 0.01:
            body_height = (h - l) * 0.01  # Minimum height for doji

        rect = mpatches.Rectangle(
            (i - candle_width / 2, body_bottom),
            candle_width, body_height,
            facecolor=color,
            edgecolor=color,
            linewidth=0.5,
        )
        ax.add_patch(rect)

    # Draw moving averages
    x_indices = list(range(n_bars))
    legend_handles = []

    for idx, period in enumerate(ma_periods):
        if period < n_bars:
            sma = calculate_sma(closes, period)
            color = MA_COLORS[idx % len(MA_COLORS)]

            # Filter out None values
            valid_points = [(x, y) for x, y in zip(x_indices, sma) if y is not None]
            if valid_points:
                x_vals, y_vals = zip(*valid_points)
                line, = ax.plot(x_vals, y_vals, color=color, linewidth=1.5, alpha=0.8)
                legend_handles.append(Line2D([0], [0], color=color, linewidth=1.5, label=f'MA{period}'))

    # Add legend if MAs exist
    if legend_handles:
        ax.legend(handles=legend_handles, loc='upper left', fontsize=8, framealpha=0.8)

    # Set axis limits
    price_min = min(lows)
    price_max = max(highs)
    price_margin = (price_max - price_min) * 0.05
    ax.set_xlim(-0.5, n_bars - 0.5)
    ax.set_ylim(price_min - price_margin, price_max + price_margin)

    # Grid and styling
    ax.grid(True, linestyle='-', alpha=0.3, color=COLOR_GRID)
    ax.set_axisbelow(True)

    # Remove axis labels for cleaner look
    ax.set_xticks([])
    ax.set_xticklabels([])
    ax.tick_params(axis='y', labelsize=8)

    # Tight layout
    plt.tight_layout(pad=0.5)

    # Save to bytes
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=dpi, facecolor=COLOR_BG, edgecolor='none')
    plt.close(fig)
    buf.seek(0)

    return buf.getvalue()
