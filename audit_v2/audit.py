"""Strateji Araştırması v2 — BAĞIMSIZ DENETİM.

Parametre değiştirmez, kabul koşulu değiştirmez, yeni strateji seçmez, aktif bota dokunmaz.
Girdi: lab dalındaki DONDURULMUŞ selection.json ve results_final.json.
Çıktı: audit_out/audit.json, trades_B_*.csv

  python -m audit_v2.audit --sel selection.json --res results_final.json --out audit_out
"""
import argparse
import copy
import csv
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timezone

import numpy as np

from paper.binance import BinanceClient
from strategy_lab import metrics as M
from strategy_lab import protocol as P
from strategy_lab import run as R
from strategy_lab import spec, strategies
from strategy_lab.backtest import backtest, costs_from
from strategy_lab.data import cut, fingerprint

from . import indep

TFS = R.TFS
DAY = M.DAY_MS


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


class SpyClient(BinanceClient):
    """Her /klines isteğinin endTime değerini kaydeder (test verisi istenmedi kanıtı için)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.kline_end_times = []

    def _get(self, path, params=None):
        if path == "/api/v3/klines" and params:
            self.kline_end_times.append(int(params.get("endTime", -1)))
        return super()._get(path, params)


def jdump(o):
    return json.dumps(o, ensure_ascii=False, indent=1, sort_keys=True, default=float)


def close_enough(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def deep_diff(a, b, path="", tol=1e-9, out=None):
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: anahtar eksik")
            else:
                deep_diff(a[k], b[k], f"{path}/{k}", tol, out)
    elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            out.append(f"{path}: uzunluk {len(a)} != {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                deep_diff(x, y, f"{path}[{i}]", tol, out)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if not close_enough(float(a), float(b), tol):
            out.append(f"{path}: {a} != {b}")
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out


# ------------------------------------------------------------------ A) veri ve seçim aşaması yeniden üretimi
def replicate_select(sel, base_url, client_factory):
    """stage_select'in gövdesini DONDURULMUŞ sınırlarla yeniden çalıştırır; yalnızca t_val-1'e kadar veri ister."""
    b = sel["boundaries_ms"]
    t_start, t_train, t_val = b["eval_start"], b["train_end"], b["val_end"]
    spy = client_factory(base_url)
    data, reports = R.load_data(base_url, R.to_ms(spec.FETCH_START), t_val - 1, t_start, client=spy, log=lambda *_: None)
    max_end = max(spy.kline_end_times)
    last_open = {tf: int(data[tf]["open_ms"][-1]) for tf in TFS}
    cands = strategies.all_candidates(data)
    results = [P.run_cand(c, data) for c in cands]
    om = R.open_ms_by_tf(data)
    chosen, table = P.select(cands, results, om, t_start, t_train, t_val)
    wf = P.walk_forward(cands, results, om, t_start, t_val)
    fps = {tf: fingerprint(data[tf], t_val) for tf in TFS}
    out = {
        "max_klines_endTime_requested": max_end, "t_val": t_val,
        "max_endTime_before_val_end": bool(max_end < t_val),
        "n_kline_requests": len(spy.kline_end_times),
        "last_bar_open_by_tf": {tf: iso(v) for tf, v in last_open.items()},
        "all_last_bars_before_val_end": all(v < t_val for v in last_open.values()),
        "fingerprints_equal_to_frozen": fps == sel["fingerprints_pre_test"],
        "integrity_equal_to_frozen": deep_diff(reports, sel["integrity"]) == [],
        "chosen_equal": chosen == sel["chosen"], "chosen": chosen,
        "table_diffs": deep_diff(json.loads(jdump(table)), sel["candidates"]),
        "wf_diffs": deep_diff(json.loads(jdump(wf)), sel["walk_forward"]),
    }
    return out


# ------------------------------------------------------------------ B) nedensellik (ileriye bakma) testi
def causality(data, t_val, t_end, cutoffs_ms):
    """Veri kesilip tekrar hesaplanır: kesim noktasından ÖNCEKİ tüm sinyaller, ATR ve özkaynak/işlemler
    tam veriyle birebir aynı olmalı. Gelecek bilgisi kullanılsaydı fark çıkardı."""
    full = strategies.all_candidates(data)
    full_res = {c.name: P.run_cand(c, data) for c in full}
    rep = {"cutoffs": [], "violations": []}
    for cm in cutoffs_ms:
        dd = {"_eval_start_ms": data["_eval_start_ms"]}
        for tf in TFS:
            dd[tf] = cut(data[tf], cm)
        part = strategies.all_candidates(dd)
        cnt = 0
        for cp, cf in zip(part, full):
            assert cp.name == cf.name
            tf = cf.tf
            n = len(dd[tf]["close"])
            for attr in ("entry", "exit_sig"):
                if not np.array_equal(getattr(cp, attr), getattr(cf, attr)[:n]):
                    rep["violations"].append(f"{cf.name}:{attr}@{iso(cm)}")
            if cf.atr is not None and not np.array_equal(cp.atr, cf.atr[:n], equal_nan=True):
                rep["violations"].append(f"{cf.name}:atr@{iso(cm)}")
            if cf.family == "A" and cm != cutoffs_ms[0]:
                continue  # 5 dk adayı yalnızca ilk kesimde (hız)
            rp = P.run_cand(cp, dd)
            rf = full_res[cf.name]
            if not np.array_equal(rp["eq"], rf["eq"][:n]):
                rep["violations"].append(f"{cf.name}:eq@{iso(cm)}")
            tp = [t for t in rp["trades"] if t["exit_i"] < n]
            tf_ = [t for t in rf["trades"] if t["exit_i"] < n]
            if len(tp) != len(tf_) or any(
                    (a["entry_i"], a["exit_i"], a["ret"]) != (b["entry_i"], b["exit_i"], b["ret"])
                    for a, b in zip(tp, tf_)):
                rep["violations"].append(f"{cf.name}:trades@{iso(cm)}")
            cnt += 1
        rep["cutoffs"].append({"cutoff": iso(cm), "candidates_checked": cnt})
    return rep


# ------------------------------------------------------------------ C) bağımsız simülatör karşılaştırması
def run_indep(d, entry_n, k, sc, start):
    cs = spec.COSTS[sc]
    return indep.simulate_b_trail(list(map(float, d["open"])), list(map(float, d["high"])),
                                  list(map(float, d["low"])), list(map(float, d["close"])),
                                  entry_n, k, cs["fee_buy"], cs["slip_buy"], cs["delay_bars"], start)


def compare_sims(lab_res, ind, d):
    lt, it = lab_res["trades"], ind["trades"]
    out = {"lab_trades": len(lt), "indep_trades": len(it), "open_lab": lab_res["open"], "open_indep": ind["open"]}
    mism = []
    for a, b in zip(lt, it):
        if (a["entry_i"], a["exit_i"]) != (b["entry_i"], b["exit_i"]) or not close_enough(a["ret"], b["ret"], 1e-12):
            mism.append({"lab": [a["entry_i"], a["exit_i"], a["ret"]], "indep": [b["entry_i"], b["exit_i"], b["ret"]]})
    out["trade_mismatches"] = mism[:10]
    out["n_trade_mismatches"] = len(mism) + abs(len(lt) - len(it))
    e1, e2 = np.asarray(lab_res["eq"]), np.asarray(ind["eq"])
    out["max_abs_eq_diff"] = float(np.max(np.abs(e1 - e2)))
    out["held_equal"] = bool(np.array_equal(lab_res["held"], np.asarray(ind["held"])))
    # fiyat ve stop kontrolleri (bağımsız kayıtlardan)
    om = d["open_ms"]
    chk = {"stop_exits": 0, "low_le_stop_all": True, "fill_is_min_stop_open": True,
           "stopped_on_entry_bar": 0, "gap_fills_below_stop": 0}
    for t in it:
        chk["stop_exits"] += 1
        if not t["low_at_exit"] <= t["stop_level"]:
            chk["low_le_stop_all"] = False
        if not close_enough(t["exit_ref"], min(t["stop_level"], t["open_at_exit"]), 1e-15):
            chk["fill_is_min_stop_open"] = False
        if t["exit_i"] == t["entry_i"]:
            chk["stopped_on_entry_bar"] += 1
        if t["open_at_exit"] < t["stop_level"]:
            chk["gap_fills_below_stop"] += 1
    out["stop_checks"] = chk
    return out


def per_trade_cost_check(ind_trades, cs):
    """Her işlemin net getirisini kapalı formülle yeniden hesapla: ret = [x(1-s)(1-f)] / [e(1+s)(1+f)] - 1."""
    worst = 0.0
    for t in ind_trades:
        f, s = cs["fee_buy"], cs["slip_buy"]
        r = (t["exit_ref"] * (1 - s) * (1 - f)) / (t["ref"] * (1 + s) * (1 + f)) - 1.0
        worst = max(worst, abs(r - t["ret"]))
    return worst


# ------------------------------------------------------------------ D) işlem tablosu ve en iyi işlem analizi
def trade_table(res, d, labels, t_val, t_end):
    om = d["open_ms"]
    i0, i1 = M.span(om, t_val, t_end)
    eq = res["eq"]
    base = eq[i0 - 1]
    rows = []
    for t in res["trades"]:
        if not (i0 <= t["exit_i"] < i1):
            continue
        s0 = max(t["entry_i"] - 1, i0 - 1)
        e0, e1 = eq[s0], eq[t["exit_i"]]
        sig = t["sig_entry_i"]
        rows.append({
            "entry_time": iso(om[t["entry_i"]]), "exit_time": iso(om[t["exit_i"]]),
            "entry_i": t["entry_i"], "exit_i": t["exit_i"],
            "bars_held": t["exit_i"] - t["entry_i"] + 1,
            "entered_before_test": bool(t["entry_i"] < i0),
            "entry_ref": float(t["entry_ref"]), "exit_ref": float(t["exit_ref"]), "reason": t["reason"],
            "trade_ret_pct": t["ret"] * 100,
            "contrib_log_pct": math.log(e1 / e0) * 100 if e0 > 0 and e1 > 0 else None,
            "equity_before": float(e0 / base), "equity_after": float(e1 / base),
            "regime_at_signal": labels[sig] if sig < len(labels) else None,
            "month": iso(om[t["entry_i"]])[:7],
        })
    open_contrib = None
    if res["open"]:
        last_entry = None
        # açık pozisyon: son işlem sonrası başlangıç özkaynağı
        # (son kapanan işlem çıkışındaki özkaynak = nakit) ile dilim sonu özkaynağı
        ex_idx = [t["exit_i"] for t in res["trades"]]
        last_exit = max([x for x in ex_idx if x < i1] or [i0 - 1])
        e0 = eq[max(last_exit, i0 - 1)]
        open_contrib = math.log(eq[i1 - 1] / e0) * 100
    return rows, open_contrib, float(eq[i1 - 1] / base - 1.0) * 100


def ex_best_analysis(rows, open_contrib, total_net_pct):
    tl = [r["contrib_log_pct"] for r in rows]
    tot_log = sum(tl) + (open_contrib or 0.0)
    out = {"n_trades": len(rows), "sum_contrib_log_pct": sum(tl), "open_position_contrib_log_pct": open_contrib,
           "closure_error_pct_points": tot_log - math.log(1 + total_net_pct / 100) * 100,
           "total_net_pct": total_net_pct}
    order = sorted(range(len(rows)), key=lambda i: -tl[i])
    out["ranking"] = [{"rank": j + 1, "entry": rows[i]["entry_time"], "exit": rows[i]["exit_time"],
                       "trade_ret_pct": rows[i]["trade_ret_pct"], "contrib_log_pct": tl[i],
                       "share_of_gross_gain_pct": None, "regime": rows[i]["regime_at_signal"]}
                      for j, i in enumerate(order)]
    gains = sum(x for x in tl if x > 0)
    for rk in out["ranking"]:
        rk["share_of_gross_gain_pct"] = rk["contrib_log_pct"] / gains * 100 if gains else None
    out["gross_gain_log_pct"] = gains
    out["gross_loss_log_pct"] = sum(x for x in tl if x < 0)
    # lab tanımı: ilk işlem getirilerinin çarpımı (en iyi çıkarılınca)
    r = [x["trade_ret_pct"] / 100 for x in rows]
    prod = lambda xs: float(np.prod([1 + v for v in xs]) - 1) * 100
    out["lab_def_ex_best_pct"] = prod([v for i, v in enumerate(r) if i != order[0]]) if len(r) > 1 else None
    # özkaynak tabanlı (kesin): işlemi 'hiç yapılmamış' say; diğer katkılar + açık pozisyon
    out["equity_based_ex_top"] = {}
    for m in (1, 2, 3):
        rem = sum(tl[i] for i in range(len(tl)) if i not in order[:m]) + (open_contrib or 0.0)
        out["equity_based_ex_top"][str(m)] = (math.exp(rem / 100) - 1) * 100
    worst = sorted(range(len(rows)), key=lambda i: tl[i])
    out["equity_based_ex_worst"] = {}
    for m in (1, 2, 3):
        rem = sum(tl[i] for i in range(len(tl)) if i not in worst[:m]) + (open_contrib or 0.0)
        out["equity_based_ex_worst"][str(m)] = (math.exp(rem / 100) - 1) * 100
    # toplamsal (basit) defter
    out["additive_sum_trade_ret_pct"] = sum(x * 100 for x in r)
    out["additive_ex_best_pct"] = sum(x * 100 for x in r) - max(x * 100 for x in r) if r else None
    return out


def monthly(res, d, t_val, t_end):
    om = d["open_ms"]
    i0, i1 = M.span(om, t_val, t_end)
    eq = res["eq"]
    base = eq[i0 - 1]
    mo, last = {}, {}
    for j in range(i0, i1):
        key = iso(om[j])[:7]
        last[key] = eq[j]
    prev = base
    out = []
    for key in sorted(last):
        out.append({"month": key, "return_pct": (last[key] / prev - 1) * 100})
        prev = last[key]
    return out


# ------------------------------------------------------------------ F) rastgele giriş sıfır modeli
def random_entry_null(cand, data, tf, n_sims, seed, target_total_trades):
    d = data[tf]
    om = d["open_ms"]
    n = len(d["close"])
    start = P.start_index(d, data["_eval_start_ms"])
    sma = np.asarray(strategies.I.sma(d["close"], spec.TREND_SMA))
    ok = ~np.isnan(cand.atr) & ~np.isnan(sma) & (d["close"] > sma)
    zeros = np.zeros(n, dtype=bool)
    costs = costs_from(spec.COSTS["temel"])
    rng = np.random.default_rng(seed)

    def one(p):
        entry = ok & (rng.random(n) < p)
        return backtest(d["open"], d["high"], d["low"], d["close"], entry, zeros, costs,
                        cand.atr, cand.init_k, cand.trail_k, start)

    best_p, best_gap = None, None
    for p in (0.004, 0.008, 0.015, 0.03, 0.06):
        mean_tr = np.mean([len(one(p)["trades"]) for _ in range(12)])
        gap = abs(mean_tr - target_total_trades)
        if best_gap is None or gap < best_gap:
            best_p, best_gap = p, gap
    return best_p, one, om


def null_stats(one, om, t_val, t_end, n_sims, real_net, real_sharpe, real_trades):
    nets, sharpes, trs, exb, full = [], [], [], [], []
    for _ in range(n_sims):
        r = one()
        m = M.slice_metrics(r, om, t_val, t_end)
        if m is None:
            continue
        nets.append(m["net_return_pct"])
        sharpes.append(m["sharpe"])
        trs.append(m["trades"])
        exb.append(m["ex_best_trade_return_pct"])
    nets, sharpes, trs = np.array(nets), np.array(sharpes), np.array(trs)
    ex = np.array([x if x is not None else np.nan for x in exb])
    sel = (trs >= 15) & (trs <= 28)
    q = lambda a: {k: float(np.nanpercentile(a, v)) for k, v in (("p5", 5), ("p25", 25), ("p50", 50), ("p75", 75), ("p95", 95))}
    return {
        "n_sims": int(len(nets)), "mean_test_trades": float(trs.mean()),
        "net_pct_quantiles": q(nets), "sharpe_quantiles": q(sharpes), "ex_best_quantiles": q(ex),
        "share_sims_net_ge_real": float(np.mean(nets >= real_net)),
        "share_sims_sharpe_ge_real": float(np.mean(sharpes >= real_sharpe)),
        "share_sims_exbest_gt0": float(np.nanmean(ex > 0)),
        "restricted_15_28_trades": {
            "n": int(sel.sum()),
            "share_net_ge_real": float(np.mean(nets[sel] >= real_net)) if sel.any() else None,
            "share_exbest_gt0": float(np.nanmean(ex[sel] > 0)) if sel.any() else None,
        },
    }


# ------------------------------------------------------------------ G) komşu ayarlar: aynı büyük işleme mi dayanıyor
def neighbors_b4h(data, t_val, t_end):
    d = data["4h"]
    om = d["open_ms"]
    out = []
    for c in strategies.all_candidates(data):
        if not (c.family == "B" and c.tf == "4h"):
            continue
        res = P.run_cand(c, data)
        m = M.slice_metrics(res, om, t_val, t_end)
        i0, i1 = M.span(om, t_val, t_end)
        tr = [t for t in res["trades"] if i0 <= t["exit_i"] < i1]
        best = max(tr, key=lambda t: t["ret"]) if tr else None
        out.append({"name": c.name, "test_net_pct": m["net_return_pct"], "trades": m["trades"],
                    "ex_best_pct": m["ex_best_trade_return_pct"],
                    "best_trade": None if best is None else {
                        "entry": iso(om[best["entry_i"]]), "exit": iso(om[best["exit_i"]]),
                        "ret_pct": best["ret"] * 100}})
    return out


# ------------------------------------------------------------------ H) walk-forward pozisyon devri duyarlılığı
def wf_carryover(cands, results, om_by_tf, sel, t_start, t_val, costs):
    day0, day_end = int(t_start // DAY), int(t_val // DAY)
    byname = {c.name: (c, r) for c, r in zip(cands, results)}
    ser, held_day = {}, {}
    for nm, (c, r) in byname.items():
        ser[nm] = M.daily_returns_series(r, om_by_tf[c.tf], day0, day_end)
        om = om_by_tf[c.tf]
        days = om // DAY
        last = np.flatnonzero(np.diff(days) != 0)
        idx = np.concatenate([last, [len(days) - 1]])
        import pandas as pd
        hs = pd.Series(r["held"][idx].astype(float), index=days[idx]).reindex(range(int(days[0]), int(days[-1]) + 1)).ffill()
        held_day[nm] = hs.reindex(range(day0, day_end)).fillna(0.0).to_numpy() > 0.5
    one_way = costs["fee_sell"] + costs["slip_sell"]
    out = {}
    bh_name = next(c.name for c in cands if c.family == "E")
    for fam, info in sel["walk_forward"].items():
        picks = info["picks"]
        r_all, prev = [], None
        switches, state_changes, hair = 0, 0, 1.0
        fold_rows = []
        for p in picks:
            f, g, nm = p["from_day"], p["to_day"], p["pick"]
            seg = ser[nm][f:g]
            r_all.append(seg)
            fr = float(np.prod(1 + seg) - 1) * 100
            row = {"from_day": f, "to_day": g, "pick": nm, "fold_return_pct": fr,
                   "bh_same_days_pct": float(np.prod(1 + ser[bh_name][f:g]) - 1) * 100}
            if prev is not None and prev != nm:
                switches += 1
                so, sn = bool(held_day[prev][f - 1]), bool(held_day[nm][f - 1])
                row["switch"] = {"from": prev, "old_held": so, "new_held": sn}
                if so != sn:
                    state_changes += 1
                    hair *= (1 - one_way)
            fold_rows.append(row)
            prev = nm
        r = np.concatenate(r_all)
        base_ret = float(np.prod(1 + r) - 1) * 100
        out[fam] = {"oos_days": int(len(r)), "oos_return_pct": base_ret, "pick_switches": switches,
                    "switches_with_state_change": state_changes,
                    "oos_return_after_switch_cost_pct": ((1 + base_ret / 100) * hair - 1) * 100,
                    "oos_return_after_full_roundtrip_each_switch_pct":
                        ((1 + base_ret / 100) * (1 - 2 * one_way) ** switches - 1) * 100,
                    "bh_oos_pct": float(np.prod(1 + np.concatenate([ser[bh_name][p["from_day"]:p["to_day"]] for p in picks])) - 1) * 100,
                    "folds": fold_rows}
    return out


# ------------------------------------------------------------------ ana akış
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sel", required=True)
    ap.add_argument("--res", required=True)
    ap.add_argument("--out", default="audit_out")
    ap.add_argument("--base-url", default=os.environ.get("BINANCE_BASE_URL") or spec.BASE_URL_DEFAULT)
    ap.add_argument("--base-sha", default="39ea75f")
    ap.add_argument("--n-sims", type=int, default=1000)
    ap.add_argument("--client", default="real")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    sel = json.load(open(a.sel, encoding="utf-8"))
    lab = json.load(open(a.res, encoding="utf-8"))
    b = sel["boundaries_ms"]
    t_start, t_train, t_val, t_end = b["eval_start"], b["train_end"], b["val_end"], b["data_end"]
    factory = SpyClient
    A = {"spec_hash_equal_to_frozen": sel["spec_sha256"] == P.spec_hash(),
         "boundaries": {"eval_start": iso(t_start), "train_end": iso(t_train), "val_end(test_start)": iso(t_val),
                        "data_end": iso(t_end)}}
    print("A1) seçim aşaması yeniden üretiliyor (yalnızca t_val-1'e kadar veri isteği)...", flush=True)
    A["select_replica"] = replicate_select(sel, a.base_url, factory)

    print("A2) tam veri indiriliyor (test dahil, dondurulmuş bitişe kadar)...", flush=True)
    spy = factory(a.base_url)
    data, reports = R.load_data(a.base_url, R.to_ms(spec.FETCH_START), t_end - 1, t_start, client=spy, log=lambda *_: None)
    A["full_data"] = {"integrity_equal_to_results_final": deep_diff(reports, lab["integrity_final"]) == [],
                      "pre_test_fingerprints_equal": {tf: fingerprint(data[tf], t_val) == sel["fingerprints_pre_test"][tf] for tf in TFS},
                      "max_endTime_requested": iso(max(spy.kline_end_times)), "t_end": iso(t_end),
                      "gaps_4h": int(np.sum(np.diff(data["4h"]["open_ms"]) != strategies.BAR_MS["4h"]))}
    cands = strategies.all_candidates(data)
    by = {c.name: c for c in cands}
    om = R.open_ms_by_tf(data)
    results = [P.run_cand(c, data) for c in cands]
    res_by = {c.name: r for c, r in zip(cands, results)}

    print("B) nedensellik testi...", flush=True)
    span = t_end - t_val
    cutoffs = [t_val] + [P.floor4h(t_val + span * k // 6) for k in (1, 2, 3, 4, 5)]
    B = causality(data, t_val, t_end, cutoffs)

    frozen_b = lab["frozen"]["B"]
    cb = by[frozen_b]
    d4 = data["4h"]
    st4 = P.start_index(d4, t_start)
    entry_n, k = cb.params["entry_n"], cb.params["atr_k"]
    i0, i1 = M.span(d4["open_ms"], t_val, t_end)

    print("C) bağımsız simülatör...", flush=True)
    C = {"candidate": frozen_b, "scenarios": {}}
    ind_by_sc = {}
    for sc in ("temel", "yuksek", "gecikmeli"):
        lab_r = res_by[frozen_b] if sc == "temel" else P.run_cand(cb, data, sc)
        ind = run_indep(d4, entry_n, k, sc, st4)
        ind_by_sc[sc] = (lab_r, ind)
        cmp_ = compare_sims(lab_r, ind, d4)
        cmp_["max_per_trade_cost_formula_error"] = per_trade_cost_check(ind["trades"], spec.COSTS[sc])
        C["scenarios"][sc] = cmp_

    print("E) metrikleri bağımsız yeniden hesapla...", flush=True)
    E = {}
    for sc, (lab_r, ind) in ind_by_sc.items():
        mi = indep.seg_metrics(ind["eq"], ind["held"], d4["open_ms"], i0, i1, ind["trades"])
        ml = lab["metrics"][frozen_b][sc]
        E[sc] = {"indep": mi, "lab_report": {k_: ml.get(k_) for k_ in (
            "net_return_pct", "cagr_pct", "max_dd_pct", "sharpe", "sortino", "trades", "win_rate_pct",
            "profit_factor", "avg_trade_pct", "time_in_market_pct", "ex_best_trade_return_pct")}}
        E[sc]["diff"] = {
            "net": mi["net_return_pct"] - ml["net_return_pct"], "cagr": (mi["cagr_pct"] or 0) - (ml["cagr_pct"] or 0),
            "maxdd": mi["max_dd_lab_def_pct"] - ml["max_dd_pct"], "sharpe": mi["sharpe"] - ml["sharpe"],
            "sortino": mi["sortino"] - ml["sortino"], "trades": mi["trades"] - ml["trades"]}

    print("D) işlem tablosu ve en iyi işlem analizi...", flush=True)
    labels = P.regimes(d4["close"], d4["open_ms"])
    D = {}
    for sc in ("temel", "yuksek", "gecikmeli"):
        lab_r = ind_by_sc[sc][0]
        rows, open_c, net = trade_table(lab_r, d4, labels, t_val, t_end)
        D[sc] = {"analysis": ex_best_analysis(rows, open_c, net), "monthly": monthly(lab_r, d4, t_val, t_end),
                 "trades": rows}
        with open(os.path.join(a.out, f"trades_B_{sc}.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["empty"])
            w.writeheader()
            w.writerows(rows)

    print("F) rastgele giriş sıfır modeli...", flush=True)
    real_m = lab["metrics"][frozen_b]["temel"]
    n_total_trades = len(res_by[frozen_b]["trades"])
    best_p, one, omn = random_entry_null(cb, data, "4h", a.n_sims, 20261023, n_total_trades)
    F = {"p_per_bar": best_p, "calibrated_to_total_trades": n_total_trades,
         "stats": null_stats(lambda: one(best_p), omn, t_val, t_end, a.n_sims,
                             real_m["net_return_pct"], real_m["sharpe"], real_m["trades"])}

    print("G) komşu ayarlar...", flush=True)
    G = neighbors_b4h(data, t_val, t_end)

    print("H) walk-forward devir duyarlılığı...", flush=True)
    H = wf_carryover(cands, results, om, sel, t_start, t_val, spec.COSTS["temel"])

    print("I) final aşaması birebir yeniden üretim...", flush=True)
    tmp = tempfile.mkdtemp()
    json.dump(sel, open(os.path.join(tmp, "selection.json"), "w", encoding="utf-8"))
    orig = R.load_data
    R.load_data = lambda *x, **y: (data, reports)
    try:
        fin = R.stage_final(tmp, a.base_url, a.base_sha, log=lambda *_: None)
    finally:
        R.load_data = orig
    I = {"metrics_diffs": deep_diff(json.loads(jdump(fin["metrics"])), lab["metrics"]),
         "gates_diffs": deep_diff(json.loads(jdump(fin["gates"])), lab["gates"]),
         "quarters_diffs": deep_diff(json.loads(jdump(fin["quarters"])), lab["quarters"]),
         "regimes_diffs": deep_diff(json.loads(jdump(fin["regimes"])), lab["regimes"]),
         "yearly_diffs": deep_diff(json.loads(jdump(fin["yearly"])), lab["yearly"]),
         "test_all_base_diffs": deep_diff(json.loads(jdump(fin["test_all_base"])), lab["test_all_base"]),
         "active_bot_changed_files_now": fin["active_bot_changed_files"]}

    out = {"A_provenance": A, "B_causality": B, "C_indep_sim": C, "D_trades": D, "E_metrics": E,
           "F_random_null": F, "G_neighbors": G, "H_walk_forward": H, "I_reproduction": I}
    with open(os.path.join(a.out, "audit.json"), "w", encoding="utf-8") as f:
        f.write(jdump(out))
    print("TAMAM. audit.json yazıldı.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
