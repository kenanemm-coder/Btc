import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

import trend_backtest as t  # ayni klasorde olmali

NTFY_TOPIC = os.environ.get('NTFY_TOPIC')
DAYS = int(os.environ.get('BT_DAILY_DAYS', '3650'))
FEE = t.FEE
SPLIT = t.SPLIT
WARMUP = 210
COINS = [('BTC', 'btcusd'), ('ETH', 'ethusd'), ('SOL', 'solusd'), ('XRP', 'xrpusd')]
SMAS = [50, 100, 150, 200]


def fetch_bitstamp(pair, days):
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    end = int(datetime.now(timezone.utc).timestamp())
    rows = []
    while start < end:
        r = requests.get(
            f'https://www.bitstamp.net/api/v2/ohlc/{pair}/',
            params={'step': 86400, 'limit': 1000, 'start': start},
            headers={'User-Agent': 'coin-test'},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()['data']['ohlc']
        if not data:
            break
        rows.extend(data)
        last = int(data[-1]['timestamp'])
        if last + 86400 <= start:
            break
        start = last + 86400
        time.sleep(0.3)
    df = pd.DataFrame(rows)
    for col in ['timestamp', 'open', 'high', 'low', 'close']:
        df[col] = pd.to_numeric(df[col])
    df = df.drop_duplicates('timestamp').sort_values('timestamp')
    df.index = pd.to_datetime(df['timestamp'], unit='s')
    return df[['open', 'high', 'low', 'close']]


def longest_segment(df):
    """Gunluk veride bosluk varsa en uzun kesintisiz parcayi al."""
    gaps = df.index.to_series().diff().dt.days.fillna(1) > 1
    seg = gaps.cumsum()
    sizes = seg.value_counts()
    best = sizes.idxmax()
    out = df[seg == best]
    return out, int(gaps.sum())


def get_coin(pair):
    if os.environ.get('BT_SYNTH') == '1':
        n = 2200
        idx = pd.date_range('2020-01-01', periods=n, freq='D')
        o, h, l, c = t.synth(n, 0.035, abs(hash(pair)) % 1000)
        return pd.DataFrame({'open': o, 'high': h, 'low': l, 'close': c}, index=idx)
    return fetch_bitstamp(pair, DAYS)


def report(name, pair):
    df = get_coin(pair)
    df, ngaps = longest_segment(df)
    n = len(df)
    if n < WARMUP + 300:
        return f'== {name}: yetersiz veri ({n} gun) =='
    k = int(n * SPLIT)
    close = df['close'].values
    lines = [f'== {name} | {n} gun | {df.index[0].date()} - {df.index[-1].date()}'
             f'{" | BOSLUK VAR" if ngaps else ""} | bolme {df.index[k].date()} ==']
    first = True
    for s in SMAS:
        strat, ret, held, entries = t.simulate(close, t.pos_price_sma(df, s), FEE)
        mi = t.metrics(strat, ret, held, entries, WARMUP, k)
        mo = t.metrics(strat, ret, held, entries, k, n)
        if first:
            lines.append(
                f'AL-TUT: IS {mi["btot"]:+.0f}%/dus{mi["bdd"]:.0f}% | '
                f'OOS {mo["btot"]:+.0f}%/dus{mo["bdd"]:.0f}%'
            )
            first = False
        lines.append(
            f'SMA{s}: IS {mi["tot"]:+.0f}%/{mi["dd"]:.0f}% | '
            f'OOS {mo["tot"]:+.0f}%/{mo["dd"]:.0f}% | islem {mo["trades"]} piyasada%{mo["inmkt"]:.0f}'
        )
    return '\n'.join(lines)


def notify(text):
    if not NTFY_TOPIC:
        return
    try:
        r = requests.post(
            f'https://ntfy.sh/{NTFY_TOPIC}',
            data=text[:3900].encode('utf-8'),
            headers={'Title': 'Coin testi sonucu', 'Priority': 'default'},
            timeout=15,
        )
        print('ntfy:', r.status_code)
    except Exception as e:
        print(f'ntfy gonderilemedi: {e}')


if __name__ == '__main__':
    parts = [
        f'Coin trend testi (gunluk, Bitstamp USD, yalniz alim/nakit, komisyon %{FEE * 100:.2f}/islem). '
        'IS = ilk %60, OOS = son %40. Hucre: getiri/maks.dusus.'
    ]
    for name, pair in COINS:
        try:
            parts.append(report(name, pair))
        except Exception as e:
            parts.append(f'== {name}: HATA {e}')
    text = '\n\n'.join(parts)
    print(text)
    print(f'[{len(text)} karakter]')
    notify(text)
