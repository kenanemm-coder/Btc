"""Göstergeler. Hepsi yalnızca t anına kadar olan KAPANMIŞ mumları kullanır (ileriye bakma yok)."""
import numpy as np
import pandas as pd


def _s(x):
    return pd.Series(np.asarray(x, dtype="float64"))


def sma(x, n):
    return _s(x).rolling(n).mean().to_numpy()


def ema(x, n):
    return _s(x).ewm(span=n, adjust=False, min_periods=n).mean().to_numpy()


def atr(high, low, close, n=14):
    """Wilder ATR: gerçek aralığın alpha=1/n üstel ortalaması."""
    h, l, c = _s(high), _s(low), _s(close)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1, skipna=False)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().to_numpy()


def prev_max(x, n):
    """t'den ÖNCEKİ n mumun en yükseği (t dahil değil)."""
    return _s(x).rolling(n).max().shift(1).to_numpy()


def prev_min(x, n):
    return _s(x).rolling(n).min().shift(1).to_numpy()


def ret(close, n):
    c = _s(close)
    return (c / c.shift(n) - 1.0).to_numpy()
