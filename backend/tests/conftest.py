"""
Shared pytest fixtures.

All fixtures are deterministic — no network calls, no file I/O beyond this file.
"""
import numpy as np
import pandas as pd
import pytest

from seed import set_seed


@pytest.fixture(autouse=True)
def fixed_seed():
    """Ensure every test starts from the same random state."""
    set_seed(42)


@pytest.fixture
def synthetic_ohlcv() -> pd.DataFrame:
    """
    500 daily bars of synthetic OHLCV with known statistical properties.

    Construction:
      - Log-returns drawn from N(0.0003, 0.015) — slight upward drift, ~24% annualised vol.
      - Open  = previous close × (1 + small gap noise)
      - High  = max(open, close) × (1 + intraday_range)
      - Low   = min(open, close) × (1 − intraday_range)
      - Volume fixed at 1,000,000 (not used by metrics tests)

    This gives a realistic-looking daily bar series with no lookahead embedded.
    Fully reproducible given fixed_seed autouse fixture.
    """
    set_seed(42)
    n       = 500
    mu      = 0.0003
    sigma   = 0.015
    start   = 100.0

    log_rets = np.random.normal(mu, sigma, n)
    closes   = start * np.exp(np.cumsum(log_rets))

    gap_noise    = np.random.uniform(-0.003, 0.003, n)
    opens        = np.roll(closes, 1) * (1 + gap_noise)
    opens[0]     = start

    intraday     = np.abs(np.random.normal(0, 0.008, n))
    highs        = np.maximum(opens, closes) * (1 + intraday)
    lows         = np.minimum(opens, closes) * (1 - intraday)

    dates = pd.date_range("2023-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "datetime": dates,
        "open":     opens,
        "high":     highs,
        "low":      lows,
        "close":    closes,
        "volume":   np.full(n, 1_000_000.0),
    })


@pytest.fixture
def known_trade_log():
    """
    A hand-crafted trade P&L list with pre-computed expected metrics.

    6 wins of +200 each, 4 losses of -100 each.
      expectancy    = (0.6 × 200) + (0.4 × -100) = 120 - 40 = 80.0
      profit_factor = (6×200) / (4×100)           = 1200/400 = 3.0
      payoff_ratio  = 200 / 100                   = 2.0
    """
    return [200, 200, 200, 200, 200, 200, -100, -100, -100, -100]
