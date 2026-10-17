import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import ccxt
import pandas as pd
import requests

TIMEFRAME = '5m'
NTFY_TOPIC = os.environ['NTFY_TOPIC']  # GitHub Secret'tan gelir

# Sırayla denenir: ilki çalışırsa diğerlerine bakılmaz
CANDIDATES = [
    ('binance', 'BTC/TRY'),
    ('btcturk', 'BTC/TRY'),
    ('kraken', 'BTC/USD'),
]


def send_notification(message, title='BTC Scalping Botu'):
    for attempt in range(1, 4):
        try:
            r = requests.post(
                f'https://ntfy.sh/{NTFY_TOPIC}',
                data=message.encode('utf-8'),
                headers={'Title': title, 'Priority': 'high'},
                timeout=15,
            )
            if r.status_code == 200:
                print('Bildirim gönderildi.')
                return True
            print(f'Ntfy cevabı {r.status_code} (deneme {attempt}/3)')
        except Exception as e:
            print(f'Bildirim gönderilemedi (deneme {attempt}/3): {e}')
        time.sleep(5)
    print('Bildirim 3 denemede de gönderilemedi.')
    return False


def fetch_bars():
    errors = []
    for ex_name, symbol in CANDIDATES:
        try:
            ex = getattr(ccxt, ex_name)({'enableRateLimit': True})
            bars = ex.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
            if bars and len(bars) >= 50:
                return bars, ex_name, symbol
            errors.append(f'{ex_name}: yetersiz veri')
        except Exception as e:
            errors.append(f'{ex_name}: {str(e)[:120]}')
    return None, None, errors


def run_bot():
    now = datetime.now(ZoneInfo('Europe/Istanbul'))

    # Sağlık raporu: 00, 06, 12, 18 (TSİ) saatlerinde ilk çalışmada
    if now.hour % 6 == 0 and now.minute < 25:
        msg = (
            f'🟢 Bot Sağlık Raporu: Aktif.\n'
            f'Son Kontrol: {now.strftime("%H:%M - %d/%m/%Y")} (TSİ)'
        )
        print(msg)
        send_notification(msg, title='Bot Durum Raporu')

    bars, ex_name, symbol = fetch_bars()
    if bars is None:
        err_text = ' | '.join(symbol)
        print(f'Veri alınamadı: {err_text}')
        send_notification(f'⚠️ Veri alınamadı:\n{err_text}', title='Bot Hatasi')
        return

    try:
        df = pd.DataFrame(
            bars,
            columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'],
        )
        df['SMA_20'] = df['Close'].rolling(window=20).mean()
        df['STD'] = df['Close'].rolling(window=20).std()
        df['Upper_Band'] = df['SMA_20'] + (df['STD'] * 2)
        df['Lower_Band'] = df['SMA_20'] - (df['STD'] * 2)
        df['SMA_50'] = df['Close'].rolling(window=50).mean()

        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        df['RSI'] = 100 - (100 / (1 + (gain / loss)))

        latest = df.iloc[-1]
        close_val = float(latest['Close'])
        lower_val = float(latest['Lower_Band'])
        upper_val = float(latest['Upper_Band'])
        rsi_val = float(latest['RSI'])
        sma50_val = float(latest['SMA_50'])

        cur = '₺' if symbol.endswith('TRY') else '$'
        tag = f'{symbol} ({ex_name})'

        print(
            f'[{now.strftime("%H:%M:%S")}] {tag}: {cur}{close_val:,.2f} | '
            f'Alt Bant: {cur}{lower_val:,.2f} | RSI: {rsi_val:.2f}'
        )

        if close_val <= lower_val and rsi_val < 48 and close_val > sma50_val:
            msg = (
                f'🚨 {tag} ALIM SİNYALİ!\n'
                f'Fiyat: {cur}{close_val:,.2f}\nRSI: {rsi_val:.2f}'
            )
            print(msg)
            send_notification(msg)
        elif close_val >= upper_val:
            msg = (
                f'🚨 {tag} SATIŞ SİNYALİ!\n'
                f'Fiyat: {cur}{close_val:,.2f}\nRSI: {rsi_val:.2f}'
            )
            print(msg)
            send_notification(msg)
        else:
            print('💤 Koşullar bekleniyor...')

    except Exception as e:
        print(f'Hata oluştu: {e}')
        send_notification(f'⚠️ Hesaplama hatası: {str(e)[:150]}', title='Bot Hatasi')


if __name__ == '__main__':
    run_bot()
