"""Performans ölçümleri (ücret ve slipaj SONRASI özkaynak eğrisinden)."""
import numpy as np
import pandas as pd

from . import spec

DAY_MS = 86_400_000


def daily_equity(eq, open_ms):
    """Her UTC günün son mumundaki özkaynak. (gün_no, özkaynak) döner."""
    days = open_ms // DAY_MS
    last = np.flatnonzero(np.diff(days) != 0)
    idx = np.concatenate([last, [len(days) - 1]])
    return days[idx], eq[idx]


def _sharpe_sortino(daily_ret):
    r = np.asarray(daily_ret, dtype="float64")
    if len(r) < 2:
        return 0.0, 0.0
    sd = r.std(ddof=1)
    ann = np.sqrt(spec.ANNUALIZATION_DAYS)
    sharpe = float(r.mean() / sd * ann) if sd > 0 else 0.0
    dd = np.sqrt(np.mean(np.minimum(r, 0.0) ** 2))
    sortino = float(r.mean() / dd * ann) if dd > 0 else 0.0
    return sharpe, sortino


def max_dd(seg):
    peak = np.maximum.accumulate(seg)
    return float(np.max((peak - seg) / peak) * 100.0)


def span(open_ms, t0, t1):
    i0 = int(np.searchsorted(open_ms, t0, side="left"))
    i1 = int(np.searchsorted(open_ms, t1, side="left"))
    return i0, i1


def slice_metrics(res, open_ms, t0, t1):
    """[t0, t1) dilimi için metrikler. Özkaynak dilim başlangıcındaki değere göre normalize edilir."""
    eq, held = res["eq"], res["held"]
    i0, i1 = span(open_ms, t0, t1)
    if i1 - i0 < 2:
        return None
    base = eq[i0 - 1] if i0 > 0 else 1.0
    seg = eq[i0:i1] / base
    total = float(seg[-1] - 1.0)
    days = (open_ms[i1 - 1] - open_ms[i0]) / DAY_MS
    cagr = float(seg[-1] ** (365.25 / days) - 1.0) if days > 1 and seg[-1] > 0 else None
    d_days, d_eq = daily_equity(seg, open_ms[i0:i1])
    d_prev = np.concatenate([[1.0], d_eq[:-1]])
    dret = d_eq / d_prev - 1.0
    sharpe, sortino = _sharpe_sortino(dret)
    tr = [t for t in res["trades"] if i0 <= t["exit_i"] < i1]
    r = np.array([t["ret"] for t in tr], dtype="float64")
    wins, losses = r[r > 0], r[r < 0]
    ex_best = float(np.prod(1 + np.delete(r, r.argmax())) - 1) if len(r) > 1 else None
    return {
        "net_return_pct": total * 100.0,
        "cagr_pct": None if cagr is None else cagr * 100.0,
        "max_dd_pct": max_dd(seg),
        "sharpe": sharpe,
        "sortino": sortino,
        "trades": int(len(r)),
        "avg_trade_pct": float(r.mean() * 100.0) if len(r) else None,
        "win_rate_pct": float(len(wins) / len(r) * 100.0) if len(r) else None,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) else None,
        "time_in_market_pct": float(held[i0:i1].mean() * 100.0),
        "ex_best_trade_return_pct": None if ex_best is None else ex_best * 100.0,
        "best_trade_pct": float(r.max() * 100.0) if len(r) else None,
        "fees_slip_pct_of_trades": float(np.mean([(t["fees"] + t["slip"]) / t["cost"] for t in tr]) * 100.0) if tr else None,
        "stops": int(sum(1 for t in tr if t["reason"] == "stop")),
    }


def quarters(res, open_ms, t0, t1, parts=4):
    """[t0,t1) dilimini eşit zamana böler; her parçanın net getirisi (%)."""
    edges = [t0 + (t1 - t0) * k // parts for k in range(parts + 1)]
    out = []
    for a, b in zip(edges, edges[1:]):
        m = slice_metrics(res, open_ms, a, b)
        out.append(None if m is None else m["net_return_pct"])
    return out


def yearly(res, open_ms, t0, t1):
    from datetime import datetime, timezone
    y0 = datetime.fromtimestamp(t0 / 1000, tz=timezone.utc).year
    y1 = datetime.fromtimestamp((t1 - 1) / 1000, tz=timezone.utc).year
    rows = []
    for y in range(y0, y1 + 1):
        a = max(t0, int(datetime(y, 1, 1, tzinfo=timezone.utc).timestamp() * 1000))
        b = min(t1, int(datetime(y + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000))
        m = slice_metrics(res, open_ms, a, b)
        if m:
            rows.append({"year": y, "net_return_pct": m["net_return_pct"], "max_dd_pct": m["max_dd_pct"],
                         "trades": m["trades"]})
    return rows


def by_regime(res, bh, close, labels, open_ms, t0, t1):
    """Mum bazlı log getiri: strateji ve al-tut, rejime göre (önceki mumun etiketiyle: ileriye bakma yok)."""
    i0, i1 = span(open_ms, t0, t1)
    ls = np.concatenate([[0.0], np.diff(np.log(res["eq"]))])
    lb = np.concatenate([[0.0], np.diff(np.log(bh["eq"]))])
    held = res["held"]
    out = {}
    for rg in ("yukselis", "yatay", "dusus"):
        m = np.array([l == rg for l in labels[i0:i1]])
        mp = np.concatenate([[False], m[:-1]])
        out[rg] = {
            "days": float(m.sum() * (open_ms[1] - open_ms[0]) / DAY_MS),
            "strategy_pct": float((np.exp(ls[i0:i1][mp].sum()) - 1) * 100.0),
            "buyhold_pct": float((np.exp(lb[i0:i1][mp].sum()) - 1) * 100.0),
            "time_in_market_pct": float(held[i0:i1][m].mean() * 100.0) if m.any() else None,
        }
    return out


def daily_returns_series(res, open_ms, day0, day1):
    """[day0, day1) gün aralığında günlük getiri dizisi (eksik günler özkaynak ileri taşınarak, getiri 0)."""
    d, e = daily_equity(res["eq"], open_ms)
    s = pd.Series(e, index=d).reindex(range(int(d[0]), int(d[-1]) + 1)).ffill()
    r = s.pct_change().fillna(0.0)
    return r.reindex(range(day0, day1)).fillna(0.0).to_numpy()


def sharpe_of(returns):
    return _sharpe_sortino(returns)[0]
