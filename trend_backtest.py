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