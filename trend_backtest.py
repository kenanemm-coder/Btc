import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

NTFY_TOPIC = os.environ.get('NTFY_TOPIC')
SYNTH = os.environ.get('BT_SYNTH') == '1'
FEE = float(os.environ.get('BT_FEE', '0.001'))  # her islemde (alim veya satim) %0.1
DAILY_DAYS = int(os.environ.get('BT_DAILY_DAYS', '2190'))
H4_DAYS = int(os.environ.get('BT_4H_DAYS', '1095'))
SPLIT = 0.6  # ilk %60 ornek ici, son %40 ornek disi
WARMUP = 210


# ---------------- Veri ----------------
def fetch_coinbase(gran, days):
    end = datetime.now(timezone.utc)
    cur = end - timedelta(days=days)
    span = timedelta(seconds=gran * 290)
    rows = []
    while cur < end:
        nxt = min(cur + span, end)
        r = requests.get(
            'https://api.exchange.coinbase.com/products/BTC-USD/candles',
            params={
                'granularity': gran,
                'start': cur.isoformat(),
                'end': nxt.isoformat(),
            },
            headers={'User-Agent': 'trendtest'},
            timeout=30,
        )
        r.raise_for_status()
        rows.extend(r.json())
        cur = nxt
        time.sleep(0.35)
    df = pd.DataFrame(
        rows, columns=['time', 'low', 'high', 'open', 'close', 'volume']
    )
    for col in ['time', 'low', 'high', 'open', 'close']:
        df[col] = pd.to_numeric(df[col])
    df = df.drop_duplicates('time').sort_values('time').reset_index(drop=True)
    df.index = pd.to_datetime(df['time'], unit='s')
    return df[['open', 'high', 'low', 'close']]


def synth(n, vol, seed):
    rng = np.random.default_rng(seed)
    drift = np.zeros(n)
    pos = 0
    sign = 1
    while pos < n:
        length = int(rng.integers(n // 12, n // 5))
        drift[pos:pos + length] = sign * vol * 0.08
        sign = -sign if rng.random() < 0.6 else sign
        pos += length
    close = 20000 * np.exp(np.cumsum(rng.normal(0, vol, n) + drift))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 3, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 3, n)))
    return open_, high, low, close


def get_daily():
    if SYNTH:
        idx = pd.date_range('2020-01-01', periods=DAILY_DAYS, freq='D')
        o, h, l, c = synth(DAILY_DAYS, 0.03, 1)
        return pd.DataFrame({'open': o, 'high': h, 'low': l, 'close': c}, index=idx)
    return fetch_coinbase(86400, DAILY_DAYS)


def get_4h():
    if SYNTH:
        n = H4_DAYS * 6
        idx = pd.date_range('2023-01-01', periods=n, freq='4h')
        o, h, l, c = synth(n, 0.012, 2)
        return pd.DataFrame({'open': o, 'high': h, 'low': l, 'close': c}, index=idx)
    h1 = fetch_coinbase(3600, H4_DAYS)
    out = h1.resample('4h').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()
    return out


# ---------------- Kurallar (parametreler klasik, ayarlanmadi) ----------------
def pos_price_sma(df, n):
    return df['close'] > df['close'].rolling(n).mean()


def pos_cross(df, a, b):
    return df['close'].rolling(a).mean() > df['close'].rolling(b).mean()


def pos_donchian(df, entry, exit_):
    hi = df['high'].rolling(entry).max().shift(1).values
    lo = df['low'].rolling(exit_).min().shift(1).values
    c = df['close'].values
    state, out = 0, np.zeros(len(df))
    for i in range(len(df)):
        if state == 0 and not np.isnan(hi[i]) and c[i] > hi[i]:
            state = 1
        elif state == 1 and not np.isnan(lo[i]) and c[i] < lo[i]:
            state = 0
        out[i] = state
    return pd.Series(out, index=df.index).astype(bool)


STRATEGIES = [
    ('Fiyat>SMA200', lambda d: pos_price_sma(d, 200)),
    ('SMA50>SMA200', lambda d: pos_cross(d, 50, 200)),
    ('SMA20>SMA50', lambda d: pos_cross(d, 20, 50)),
    ('Donchian55/20', lambda d: pos_donchian(d, 55, 20)),
]


# ---------------- Hesap ----------------
def simulate(close, pos, fee):
    """Sinyal mum kapanisinda uretilir, islem o kapanista yapilir.
    Mum t'de tutulan pozisyon = t-1 kapanisindaki sinyal."""
    ret = np.r_[0.0, close[1:] / close[:-1] - 1]
    p = pos.values.astype(float)
    held = np.r_[0.0, p[:-1]]
    prev = np.r_[0.0, held[:-1]]
    strat = held * ret - np.abs(held - prev) * fee
    entries = (held == 1) & (prev == 0)
    return strat, ret, held, entries


def metrics(strat, ret, held, entries, a, b):
    s = strat[a:b]
    eq = np.cumprod(1 + s)
    dd = (eq / np.maximum.accumulate(eq) - 1).min() * 100
    tot = (eq[-1] - 1) * 100
    beq = np.cumprod(1 + ret[a:b])
    bdd = (beq / np.maximum.accumulate(beq) - 1).min() * 100
    btot = (beq[-1] - 1) * 100
    return {
        'tot': tot, 'dd': dd, 'btot': btot, 'bdd': bdd,
        'trades': int(entries[a:b].sum()),
        'inmkt': held[a:b].mean() * 100,
    }


def report(label, df):
    n = len(df)
    k = int(n * SPLIT)
    close = df['close'].values
    d0, d1 = df.index[0].date(), df.index[-1].date()
    dk = df.index[k].date()
    lines = [f'== {label} | {n} mum | {d0} - {d1} | bolme {dk} ==']
    first = True
    for name, fn in STRATEGIES:
        strat, ret, held, entries = simulate(close, fn(df), FEE)
        mi = metrics(strat, ret, held, entries, WARMUP, k)
        mo = metrics(strat, ret, held, entries, k, n)
        if first:
            lines.append(
                f'AL-TUT: IS {mi["btot"]:+.0f}%/dus{mi["bdd"]:.0f}% | '
                f'OOS {mo["btot"]:+.0f}%/dus{mo["bdd"]:.0f}%'
            )
            first = False
        lines.append(
            f'{name}: IS {mi["tot"]:+.0f}%/dus{mi["dd"]:.0f}% | '
            f'OOS {mo["tot"]:+.0f}%/dus{mo["dd"]:.0f}% | '
            f'islem {mo["trades"]} piyasada%{mo["inmkt"]:.0f}'
        )
    return '\n'.join(lines)


def notify(text):
    if not NTFY_TOPIC:
        return
    try:
        r = requests.post(
            f'https://ntfy.sh/{NTFY_TOPIC}',
            data=text[:3900].encode('utf-8'),
            headers={'Title': 'Trend testi sonucu', 'Priority': 'default'},
            timeout=15,
        )
        print('ntfy:', r.status_code)
    except Exception as e:
        print(f'ntfy gonderilemedi: {e}')


if __name__ == '__main__':
    parts = [
        f'Trend takibi testi (yalniz alim/nakit, komisyon islem basi %{FEE * 100:.2f}). '
        'IS = ilk %60 (ornek ici), OOS = son %40 (ornek disi). '
        'Her hucre: getiri / maks.dusus. AL-TUT = ayni donemde sadece tutmak.'
    ]
    for label, getter in [('Gunluk', get_daily), ('4 saatlik', get_4h)]:
        try:
            parts.append(report(label, getter()))
        except Exception as e:
            parts.append(f'== {label}: HATA {e}')
    text = '\n\n'.join(parts)
    print(text)
    notify(text)
