"""BAĞIMSIZ yeniden uygulama: strategy_lab.backtest / indicators / metrics KULLANMAZ.

Saf Python listeleri; yalnızca ham OHLC dizilerini alır. Amaç, B[4h N=? k=? trail] adayının
işlem listesini ve özkaynak eğrisini ayrı yazılmış kodla yeniden üretip lab sonuçlarıyla karşılaştırmaktır.
"""
import math

DAY_MS = 86_400_000


def sma_list(x, n):
    out = [None] * len(x)
    for i in range(n - 1, len(x)):
        out[i] = sum(x[i - n + 1:i + 1]) / n
    return out


def atr_list(h, l, c, n=14):
    """Lab ile aynı tanım: TR'nin alpha=1/n üstel ortalaması, ilk TR ile tohumlanır, n gözlemden sonra geçerli."""
    m = len(c)
    out = [None] * m
    a = 1.0 / n
    y = None
    for i in range(1, m):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        y = tr if y is None else (1 - a) * y + a * tr
        if i >= n:  # i=1..n arası n gözlem
            out[i] = y
    return out


def prev_max_list(x, n):
    out = [None] * len(x)
    for i in range(n, len(x)):
        out[i] = max(x[i - n:i])
    return out


def simulate_b_trail(o, h, l, c, entry_n, k, fee, slip, delay, start):
    """B (Donchian + SMA200 + ATR takip eden stop). Dönüş: dict(eq, held, trades)."""
    n = len(c)
    sma = sma_list(c, 200)
    atr = atr_list(h, l, c, 14)
    hi = prev_max_list(h, entry_n)
    flat, cash, qty = True, 1.0, 0.0
    order = None
    stop = hw = None
    info = None
    eq = [1.0] * n
    held = [False] * n
    trades = []
    for i in range(start, n):
        # --- bar açılışı: bekleyen alış
        if order is not None and order[0] == i:
            sig = order[1]
            order = None
            if flat:
                fill = o[i] * (1 + slip)
                notional = cash / (1 + fee)
                qty = notional / fill
                info = {"entry_i": i, "sig_i": sig, "ref": o[i], "fill": fill, "cash_in": cash}
                stop = o[i] - k * atr[sig]
                hw = c[sig]
                cash = 0.0
                flat = False
        # --- bar içi: stop (muhafazakâr: düşük stop'a değdiyse tetiklenir)
        if (not flat) and l[i] <= stop:
            ref = min(stop, o[i])
            fill = ref * (1 - slip)
            proceeds = qty * fill * (1 - fee)
            trades.append({**info, "exit_i": i, "exit_ref": ref, "stop_level": stop,
                           "ret": proceeds / info["cash_in"] - 1.0,
                           "open_at_exit": o[i], "low_at_exit": l[i], "proceeds": proceeds})
            cash, qty, flat, stop, hw = proceeds, 0.0, True, None, None
        # --- bar kapanışı
        if flat:
            eq[i] = cash
        else:
            held[i] = True
            eq[i] = qty * c[i] * (1 - slip) * (1 - fee)
            hw = max(hw, c[i])
            stop = max(stop, hw - k * atr[i])
        j = i + 1 + delay
        if j < n and flat and order is None:
            if sma[i] is not None and atr[i] is not None and hi[i] is not None \
                    and c[i] > sma[i] and c[i] > hi[i]:
                order = (j, i)
    return {"eq": eq, "held": held, "trades": trades, "open": (not flat)}


def seg_metrics(eq, held, om, i0, i1, trades):
    """[i0,i1) dilimi (UTC açılış zamanına göre). Tanımlar lab ile aynı + iki alternatif düşüş ölçümü."""
    base = eq[i0 - 1] if i0 > 0 else 1.0
    seg = [x / base for x in eq[i0:i1]]
    total = seg[-1] - 1.0
    days = (om[i1 - 1] - om[i0]) / DAY_MS
    cagr = seg[-1] ** (365.25 / days) - 1.0 if days > 1 and seg[-1] > 0 else None
    # düşüş: (a) lab tanımı: tepe, dilimin kendi ilk değerinden başlar
    peak, dd_lab = seg[0], 0.0
    for v in seg:
        peak = max(peak, v)
        dd_lab = max(dd_lab, (peak - v) / peak)
    # (b) başlangıç sermayesi (1.0) de tepe sayılır
    peak, dd_base = 1.0, 0.0
    for v in seg:
        peak = max(peak, v)
        dd_base = max(dd_base, (peak - v) / peak)
    # günlük getiriler: her UTC günün son barındaki özkaynak; ilk gün önceki değer 1.0
    days_idx = [om[i0 + j] // DAY_MS for j in range(len(seg))]
    last_of_day = {}
    for j, d in enumerate(days_idx):
        last_of_day[d] = seg[j]
    ds = [last_of_day[d] for d in sorted(last_of_day)]
    prev = [1.0] + ds[:-1]
    r = [a / b - 1.0 for a, b in zip(ds, prev)]
    sharpe = sortino = 0.0
    if len(r) >= 2:
        mu = sum(r) / len(r)
        sd = math.sqrt(sum((x - mu) ** 2 for x in r) / (len(r) - 1))
        ann = math.sqrt(365)
        sharpe = mu / sd * ann if sd > 0 else 0.0
        dn = math.sqrt(sum(min(x, 0.0) ** 2 for x in r) / len(r))
        sortino = mu / dn * ann if dn > 0 else 0.0
    # 4 saatlik bar bazlı Sharpe (alternatif)
    br = [seg[j] / (seg[j - 1] if j else 1.0) - 1.0 for j in range(len(seg))]
    bm = sum(br) / len(br)
    bs = math.sqrt(sum((x - bm) ** 2 for x in br) / (len(br) - 1))
    bar_sharpe = bm / bs * math.sqrt(365 * 6) if bs > 0 else 0.0
    tr = [t for t in trades if i0 <= t["exit_i"] < i1]
    rets = [t["ret"] for t in tr]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x < 0]
    return {
        "net_return_pct": total * 100, "cagr_pct": None if cagr is None else cagr * 100,
        "max_dd_lab_def_pct": dd_lab * 100, "max_dd_incl_start_pct": dd_base * 100,
        "sharpe": sharpe, "sortino": sortino, "sharpe_bar_level": bar_sharpe,
        "trades": len(rets), "win_rate_pct": (len(wins) / len(rets) * 100) if rets else None,
        "profit_factor": (sum(wins) / -sum(losses)) if losses else None,
        "avg_trade_pct": (sum(rets) / len(rets) * 100) if rets else None,
        "time_in_market_pct": sum(1 for x in held[i0:i1] if x) / (i1 - i0) * 100,
        "days": days, "n_days": len(r),
    }
