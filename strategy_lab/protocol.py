"""Doğrulama protokolü: kronolojik bölme, seçim (test verisi OLMADAN), walk-forward, dondurma, tek seferlik test."""
import hashlib
import json
import os

import numpy as np

from research import engine as RE

from . import metrics as M
from . import spec, strategies
from .backtest import ZERO, backtest, costs_from
from .data import cut, fingerprint

DAY = M.DAY_MS
H4 = 4 * 3_600_000


def spec_hash():
    with open(os.path.join(os.path.dirname(__file__), "spec.py"), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def floor4h(ms):
    return ms // H4 * H4


def boundaries(eval_start_ms, data_end_ms):
    """(eğitim_bitişi, doğrulama_bitişi, veri_bitişi) — 4 saatlik sınıra yuvarlanmış, süreye göre 60/20/20."""
    total = data_end_ms - eval_start_ms
    tr = floor4h(eval_start_ms + int(total * spec.SPLIT[0]))
    va = floor4h(eval_start_ms + int(total * (spec.SPLIT[0] + spec.SPLIT[1])))
    return tr, va, floor4h(data_end_ms)


def start_index(d, eval_start_ms):
    return int(np.searchsorted(d["open_ms"], eval_start_ms, side="left"))


def run_cand(c, data, scenario="temel", zero=False):
    d = data[c.tf]
    costs = ZERO if zero else costs_from(spec.COSTS[scenario])
    return backtest(d["open"], d["high"], d["low"], d["close"], c.entry, c.exit_sig, costs,
                    c.atr, c.init_k, c.trail_k, start_index(d, _eval_start(data)))


def _eval_start(data):
    return data["_eval_start_ms"]


def _score(res, open_ms, t0, t1):
    """SKOR = min(Sharpe ilk %75, Sharpe son %25) — 60/20 eğitim/doğrulama oranıyla aynı."""
    cut_ms = t0 + int((t1 - t0) * 0.75)
    a = M.slice_metrics(res, open_ms, t0, cut_ms)
    b = M.slice_metrics(res, open_ms, cut_ms, t1)
    if a is None or b is None:
        return None
    return min(a["sharpe"], b["sharpe"])


def family_of(c):
    return c.family


def select(cands, results, open_ms_by_tf, t_start, t_train_end, t_val_end):
    """Aile başına tek ayar. Yalnızca [t_start, t_val_end) verisiyle. Dönüş: (seçim sözlüğü, tablo)."""
    table = []
    for c, r in zip(cands, results):
        om = open_ms_by_tf[c.tf]
        mt = M.slice_metrics(r, om, t_start, t_train_end)
        mv = M.slice_metrics(r, om, t_train_end, t_val_end)
        n_tv = (mt["trades"] if mt else 0) + (mv["trades"] if mv else 0)
        score = min(mt["sharpe"], mv["sharpe"]) if (mt and mv) else None
        table.append({"name": c.name, "family": c.family, "tf": c.tf, "params": c.params,
                      "train": mt, "val": mv, "trades_trainval": n_tv, "score": score,
                      "eligible": bool(score is not None and n_tv >= spec.MIN_TRADES)})
    chosen = {}
    for fam in ("B", "C", "D"):
        rows = [t for t in table if t["family"] == fam and t["eligible"]]
        if rows:
            best = max(rows, key=lambda t: t["score"])
            chosen[fam] = best["name"]
        else:
            chosen[fam] = None
    return chosen, table


def walk_forward(cands, results, open_ms_by_tf, t_start, t_val_end):
    """Genişleyen pencereli ileri-örneklem testi (yalnızca eğitim+doğrulama içinde).
    Her adımda aday, yalnızca o ana kadarki veriyle seçilir; sonraki pencerede uygulanır.
    Not: aday geçişlerinde pozisyon devri modellenmez (günlük getiri dizileri birleştirilir)."""
    day0 = int(t_start // DAY)
    day_end = int(t_val_end // DAY)
    total = day_end - day0
    ser = {}
    for c, r in zip(cands, results):
        ser[c.name] = M.daily_returns_series(r, open_ms_by_tf[c.tf], day0, day_end)
    out = {}
    for fam in ("A", "B", "C", "D", "E"):
        names = [c.name for c in cands if c.family == fam]
        oos, picks, f = [], [], spec.WF_FIRST_DAYS
        while f < total:
            g = min(f + spec.WF_STEP_DAYS, total)
            if g - f < 30:
                break
            best, best_s = None, None
            for nm in names:
                r_in = ser[nm][:f]
                cut_i = int(f * 0.75)
                s = min(_sh(r_in[:cut_i]), _sh(r_in[cut_i:]))
                c_obj = next(x for x in cands if x.name == nm)
                res = results[[x.name for x in cands].index(nm)]
                n_tr = sum(1 for t in res["trades"]
                           if t_start <= open_ms_by_tf[c_obj.tf][t["exit_i"]] < t_start + f * DAY)
                if fam in ("A", "E") or n_tr >= spec.MIN_TRADES:
                    if best_s is None or s > best_s:
                        best, best_s = nm, s
            picks.append({"from_day": f, "to_day": g, "pick": best})
            oos.append(ser[best][f:g] if best else np.zeros(g - f))
            f = g
        r = np.concatenate(oos) if oos else np.zeros(0)
        out[fam] = {"folds": len(picks), "picks": picks,
                    "oos_return_pct": float((np.prod(1 + r) - 1) * 100.0) if len(r) else None,
                    "oos_days": int(len(r)), "oos_sharpe": _sh(r) if len(r) else None}
    return out


def _sh(r):
    return M.sharpe_of(r) if len(r) > 1 else 0.0


def evaluate_gates(fam, chosen_name, ctx):
    """ctx: test dönemi sonuçları. Dönüş: {kapı: (geçti_mi, açıklama)}."""
    m = ctx["metrics"][chosen_name]
    g = {}
    base = m["temel"]
    g["G1"] = (base["net_return_pct"] > 0, f"temel TEST net getiri {base['net_return_pct']:.1f}%")
    g["G2"] = (m["yuksek"]["net_return_pct"] > -10, f"yüksek maliyet TEST net getiri {m['yuksek']['net_return_pct']:.1f}%")
    g["G3"] = (m["gecikmeli"]["net_return_pct"] > 0, f"gecikmeli TEST net getiri {m['gecikmeli']['net_return_pct']:.1f}%")
    ex = base["ex_best_trade_return_pct"]
    g["G4"] = (base["trades"] >= 10 and ex is not None and ex > 0,
               f"TEST işlem {base['trades']}, en iyi işlem çıkarılınca {None if ex is None else round(ex, 1)}%")
    q = ctx["quarters"][chosen_name]
    pos = sum(1 for x in q if x is not None and x > 0)
    g["G5"] = (pos >= 2, f"4 parçadan {pos} pozitif: " + ", ".join("-" if x is None else f"{x:.1f}%" for x in q))
    share = ctx["neighbor_pos_share"][chosen_name]
    g["G6"] = (share >= 0.5, f"komşu ayarların %{share * 100:.0f}'i TEST'te pozitif")
    wf = ctx["wf"][fam]["oos_return_pct"]
    g["G7"] = (wf is not None and wf > 0, f"walk-forward ileri-örneklem {None if wf is None else round(wf, 1)}%")
    e = ctx["bh"]
    sharpe_ok = base["sharpe"] > e["sharpe"]
    dd_ok = base["max_dd_pct"] <= 0.5 * e["max_dd_pct"] and base["net_return_pct"] > 0
    g["G8"] = (sharpe_ok or dd_ok, f"Sharpe {base['sharpe']:.2f} (al-tut {e['sharpe']:.2f}), maks. düşüş "
               f"{base['max_dd_pct']:.1f}% (al-tut {e['max_dd_pct']:.1f}%)")
    return g


def regimes(close, open_ms):
    return RE.regime_labels(close, open_ms, spec.REGIME_LOOKBACK_DAYS, spec.REGIME_UP, spec.REGIME_DOWN)[0]


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True, default=float)
