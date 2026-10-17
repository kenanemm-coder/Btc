import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

DAYS = int(os.environ.get('BT_DAYS', '60'))
NTFY_TOPIC = os.environ.get('NTFY_TOPIC')
SYNTH = os.environ.get('BT_SYNTH') == '1'
LAG_MIN = 20  # GitHub zamanlama gecikmesi tahmini (dakika)
FEE_ROUND_TRIP = 0.002  # %0.2 gidis-donus komisyon varsayimi
HORIZONS_MIN = [30, 60, 240, 1440]
TIMEFRAMES = [(300, '5dk'), (900, '15dk'), (3600, '1sa')]


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
            headers={'User-Agent': 'backtest'},
            timeout=30,
        )
        r.raise_for_status()
        rows.extend(r.json())
        cur = nxt
        time.sleep(0.35)
    df = pd.DataFrame(
        rows, columns=['time', 'low', 'high', 'open', 'close', 'volume']
    )
    return df


def fetch_kraken(gran):
    r = requests.get(
        'https://api.kraken.com/0/public/OHLC',
        params={'pair': 'XBTUSD', 'interval': gran // 60},
        timeout=30,
    )
    r.raise_for_status()
    res = r.json()['result']
    key = [k for k in res if k != 'last'][0]
    df = pd.DataFrame(
        res[key],
        columns=['time', 'open', 'high', 'low', 'close', 'vwap', 'volume', 'count'],
    )
    return df


def synth_data(gran, days):
    rng = np.random.default_rng(42)
    n = days * 86400 // 300
    ret = rng.normal(0, 0.0012, n)
    price = 80000 * np.exp(np.cumsum(ret))
    idx = pd.date_range('2026-01-01', periods=n, freq='5min')
    s = pd.Series(price, index=idx)
    if gran > 300:
        s = s.resample(f'{gran // 60}min').last().dropna()
    secs = (s.index - pd.Timestamp('1970-01-01')).total_seconds().astype('int64')
    return pd.DataFrame({'time': secs, 'close': s.values})


def get_data(gran, days):
    if SYNTH:
        return synth_data(gran, days), 'sentetik'
    try:
        df, src = fetch_coinbase(gran, days), 'Coinbase BTC/USD'
    except Exception as e:
        print(f'Coinbase basarisiz ({e}), Kraken deneniyor')
        df, src = fetch_kraken(gran), 'Kraken BTC/USD (sinirli gecmis)'
    df['time'] = pd.to_numeric(df['time'])
    df['close'] = pd.to_numeric(df['close'])
    df = df.drop_duplicates('time').sort_values('time').reset_index(drop=True)
    return df, src


# ---------------- Bot mantigi (bot.py ile ayni) ----------------
def signals(df):
    c = df['close']
    sma20 = c.rolling(20).mean()
    std = c.rolling(20).std()
    upper = sma20 + std * 2
    lower = sma20 - std * 2
    sma50 = c.rolling(50).mean()
    delta = c.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rsi = 100 - (100 / (1 + (gain / loss)))
    buy = (c <= lower) & (rsi < 48) & (c > sma50)
    sell = (c >= upper) & ~buy
    return buy.fillna(False), sell.fillna(False)


def episodes(mask):
    return mask & ~mask.shift(1, fill_value=False)


def forward(c, idx, delay, h, side):
    out = []
    for i in idx:
        e, x = i + delay, i + delay + h
        if x >= len(c):
            continue
        r = c[x] / c[e] - 1
        out.append(r if side == 'buy' else -r)
    return np.array(out)


def baseline(c, delay, h, side):
    n = len(c) - delay - h
    if n <= 0:
        return float('nan')
    r = c[delay + h:delay + h + n] / c[delay:delay + n] - 1
    return float(np.mean(r if side == 'buy' else -r))


def fmt(arr):
    if len(arr) == 0:
        return 'n=0'
    win = np.mean(arr > 0) * 100
    return f'n={len(arr)} kaz%{win:.0f} ort{np.mean(arr) * 100:+.2f}%'


def report_tf(gran, label):
    df, src = get_data(gran, DAYS)
    c = df['close'].values
    days_cov = (df['time'].iloc[-1] - df['time'].iloc[0]) / 86400
    buy, sell = signals(df)
    tf_min = gran // 60
    lag = int(round(LAG_MIN / tf_min))
    lines = [f'== {label} mum | {src} | {len(df)} mum, {days_cov:.0f} gun ==']
    for side, mask, name in [('buy', buy, 'ALIM'), ('sell', sell, 'SATIS')]:
        ep = episodes(mask)
        idx = np.where(ep.values)[0]
        lines.append(
            f'{name}: ham {int(mask.sum())}, tekil {len(idx)} '
            f'(gunde {len(idx) / max(days_cov, 1):.1f})'
        )
        for hm in HORIZONS_MIN:
            h = int(round(hm / tf_min))
            if h < 1:
                continue
            a0 = forward(c, idx, 0, h, side)
            a1 = forward(c, idx, lag, h, side)
            net = (np.mean(a0) - FEE_ROUND_TRIP) * 100 if len(a0) else float('nan')
            ref = baseline(c, 0, h, side) * 100
            hl = f'{hm}dk' if hm < 60 else f'{hm // 60}sa'
            lines.append(
                f' +{hl}: {fmt(a0)} komis.sonrasi{net:+.2f}% ref{ref:+.2f}%'
                f' | gecikmeli {fmt(a1)}'
            )
    return '\n'.join(lines)


def notify(text):
    if not NTFY_TOPIC:
        return
    try:
        r = requests.post(
            f'https://ntfy.sh/{NTFY_TOPIC}',
            data=text[:3900].encode('utf-8'),
            headers={'Title': 'Backtest sonucu', 'Priority': 'default'},
            timeout=15,
        )
        print('ntfy:', r.status_code)
    except Exception as e:
        print(f'ntfy gonderilemedi: {e}')


if __name__ == '__main__':
    parts = [
        f'Backtest (son {DAYS} gun). kaz% = isaretin dogru yone gitme orani, '
        f'ort = ortalama getiri, ref = rastgele giris ortalamasi, '
        f'gecikmeli = ~{LAG_MIN} dk sonra giris.'
    ]
    for gran, label in TIMEFRAMES:
        try:
            parts.append(report_tf(gran, label))
        except Exception as e:
            parts.append(f'== {label} mum: HATA {e}')
    text = '\n\n'.join(parts)
    print(text)
    notify(text)
