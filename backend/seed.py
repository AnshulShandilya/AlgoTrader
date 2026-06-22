"""Deterministic seeding for reproducible backtests."""
import random
import numpy as np

_DEFAULT_SEED = 42


def set_seed(seed: int = _DEFAULT_SEED) -> None:
    """Set numpy and stdlib random seeds so every backtest run is reproducible."""
    random.seed(seed)
    np.random.seed(seed)
