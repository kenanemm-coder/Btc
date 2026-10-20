"""Göstergeler ve sinyal kuralı.

Eski bot.py ile AYNI formüller (pandas ile birebir uyumu testle doğrulanır):
  - Bollinger(20, 2): SMA20 ± 2 * örneklem std (ddof=1)
  - SMA50
  - RSI(14): Wilder değil, 14 farkın SIMPLE ortalaması (eski koddaki gibi)
Sinyal kuralı da aynıdır:
  ALIM : kapanış <= alt bant  VE  RSI < 48  VE  kapanış > SMA50
  SATIŞ: kapanış >= üst bant  (ALIM koşulu sağlanmıyorsa)

compute_row yalnızca verilen kapanışları kullanır; sonraki muma bakmaz
(look-ahead yok). Veri yetersizse None döner, sinyal üretilmez.
"""
import math
from collections import namedtuple

Row = namedtuple("Row", "close sma20 std upper lower sma50 rsi")

RSI_BUY_MAX = 48.0
MIN_CLOSES = 50


def compute_row(closes):
    n = len(closes)
    if n < MIN_CLOSES:
        return None
    last20 = closes[-20:]
    sma20 = sum(last20) / 20.0
    std = math.sqrt(sum((x - sma20) ** 2 for x in last20) / 19.0)
    upper = sma20 + 2.0 * std
    lower = sma20 - 2.0 * std
    sma50 = sum(closes[-50:]) / 50.0

    deltas = [closes[i] - closes[i - 1] for i in range(n - 14, n)]
    gain = sum(d for d in deltas if d > 0) / 14.0
    loss = sum(-d for d in deltas if d < 0) / 14.0
    if loss == 0:
        if gain == 0:
            return None  # pandas'ta NaN olurdu: sinyal yok
        rsi = 100.0
    else:
        rsi = 100.0 - 100.0 / (1.0 + gain / loss)

    row = Row(float(closes[-1]), sma20, std, upper, lower, sma50, rsi)
    if not all(math.isfinite(v) for v in row):
        return None
    return row


def signal_from_row(row):
    """'buy', 'sell' veya None."""
    if row is None:
        return None
    buy = row.close <= row.lower and row.rsi < RSI_BUY_MAX and row.close > row.sma50
    if buy:
        return "buy"
    if row.close >= row.upper:
        return "sell"
    return None
