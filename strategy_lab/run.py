"""Çalıştırıcı. Aşamalar:  select (test verisi İNDİRİLMEZ)  →  final (dondurulmuş seçimle TEK değerlendirme).

  python -m strategy_lab.run select --out lab_out
  python -m strategy_lab.run final  --out lab_out --base-sha <ön kayıt commit>
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np

from paper.binance import BinanceClient

from . import metrics as M
from . import protocol as P
from . import spec, strategies
from .data import cut, fetch_tf, fingerprint, integrity, integrity_ok, to_ms
from .report import render

TFS = ("5m", "1h", "4h")


def git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return os.environ.get("GITHUB_SHA", "bilinmiyor")


def load_data(base_url, t_start_fetch, end_ms, eval_start_ms, log=print, client=None):
    data = {"_eval_start_ms": eval_start_ms}
    reports = []
    for tf in TFS:
        log(f"{tf} indiriliyor...")
        d = fetch_tf(base_url, spec.SYMBOL, tf, t_start_fetch, end_ms, client=client, log=log)
        rep = integrity(d, tf)
        reports.append(rep)
        if not integrity_ok(rep):
            raise SystemExit(f"Veri bütünlüğü hatası ({tf}): {rep}")
        data[tf] = d
    return data, reports


def open_ms_by_tf(data):
    return {tf: data[tf]["open_ms"] for tf in TFS}


def stage_select(out_dir, base_url, log=print, client=None):
    client = client or BinanceClient(base_url)
    server_ms = client.server_time_ms()
    data_end = P.floor4h(server_ms)
    t_start = to_ms(spec.EVAL_START)
    t_train, t_val, t_end = P.boundaries(t_start, data_end)
    # TEST verisi bu aşamada HİÇ indirilmez: bitiş t_val - 1.
    data, reports = load_data(base_url, to_ms(spec.FETCH_START), t_val - 1, t_start, log, client)
    assert all(d["open_ms"][-1] < t_val for d in (data[tf] for tf in TFS)), "test verisi sızdı"
    cands = strategies.all_candidates(data)
    log(f"{len(cands)} aday çalıştırılıyor...")
    results = [P.run_cand(c, data) for c in cands]
    om = open_ms_by_tf(data)
    chosen, table = P.select(cands, results, om, t_start, t_train, t_val)
    wf = P.walk_forward(cands, results, om, t_start, t_val)
    sel = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha(), "spec_sha256": P.spec_hash(),
        "boundaries_ms": {"eval_start": t_start, "train_end": t_train, "val_end": t_val, "data_end": t_end},
        "candidate_count": len(cands),
        "integrity": reports,
        "fingerprints_pre_test": {tf: fingerprint(data[tf], t_val) for tf in TFS},
        "chosen": chosen, "candidates": table, "walk_forward": wf,
    }
    os.makedirs(out_dir, exist_ok=True)
    txt = P.dumps(sel)
    with open(os.path.join(out_dir, "selection.json"), "w", encoding="utf-8") as f:
        f.write(txt)
    import hashlib
    print("SEÇİM DONDURULDU. selection.json sha256 =", hashlib.sha256(txt.encode()).hexdigest())
    print("Seçilenler:", chosen)
    return sel


def changed_active_files(base_sha):
    if not base_sha:
        return None
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", base_sha, "HEAD", "--", "paper", "bot.py", "scripts",
             ".github/workflows/bot.yml", ".github/workflows/watchdog.yml"], text=True)
        return [x for x in out.splitlines() if x]
    except Exception:  # noqa: BLE001
        return None


def stage_final(out_dir, base_url, base_sha=None, log=print, client=None):
    import json
    with open(os.path.join(out_dir, "selection.json"), encoding="utf-8") as f:
        sel = json.load(f)
    if sel["spec_sha256"] != P.spec_hash():
        raise SystemExit("spec.py seçimden sonra değişmiş; test değerlendirmesi reddedildi.")
    b = sel["boundaries_ms"]
    t_start, t_train, t_val, t_end = b["eval_start"], b["train_end"], b["val_end"], b["data_end"]
    data, reports = load_data(base_url, to_ms(spec.FETCH_START), t_end - 1, t_start, log, client or BinanceClient(base_url))
    for tf in TFS:
        if fingerprint(data[tf], t_val) != sel["fingerprints_pre_test"][tf]:
            raise SystemExit(f"Veri parmak izi uyuşmuyor ({tf}): geçmiş veri değişmiş.")
    cands = strategies.all_candidates(data)
    by = {c.name: c for c in cands}
    om = open_ms_by_tf(data)
    frozen = {fam: nm for fam, nm in sel["chosen"].items() if nm}
    show = list(frozen.values()) + [c.name for c in cands if c.family in ("A", "E")]
    # Tüm adaylar temel maliyette (yalnızca sağlamlık/komşu analizi; seçimi DEĞİŞTİRMEZ)
    base_all = {c.name: P.run_cand(c, data) for c in cands}
    e_name = next(c.name for c in cands if c.family == "E")
    metrics = {}
    quarters = {}
    runs = {}
    for nm in show:
        c = by[nm]
        runs[nm] = {sc: (base_all[nm] if sc == "temel" else P.run_cand(c, data, sc)) for sc in spec.COSTS}
        runs[nm]["sifir"] = P.run_cand(c, data, zero=True)
        metrics[nm] = {sc: M.slice_metrics(runs[nm][sc], om[c.tf], t_val, t_end) for sc in runs[nm]}
        quarters[nm] = M.quarters(runs[nm]["temel"], om[c.tf], t_val, t_end)
    neighbor = {}
    test_all = {}
    for c in cands:
        m = M.slice_metrics(base_all[c.name], om[c.tf], t_val, t_end)
        test_all[c.name] = m["net_return_pct"] if m else None
    for fam, nm in frozen.items():
        c = by[nm]
        peers = [x for x in cands if x.family == c.family and x.tf == c.tf]
        neighbor[nm] = float(np.mean([1.0 if (test_all[x.name] or 0) > 0 else 0.0 for x in peers]))
    ctx = {"metrics": metrics, "quarters": quarters, "neighbor_pos_share": neighbor,
           "wf": sel["walk_forward"], "bh": metrics[e_name]["temel"]}
    gates = {fam: P.evaluate_gates(fam, nm, ctx) for fam, nm in frozen.items()}
    accepted = [fam for fam, g in gates.items() if all(v[0] for v in g.values())]
    # rejim ve yıl
    reg, yr = {}, {}
    for nm in show:
        c = by[nm]
        lab = P.regimes(data[c.tf]["close"], om[c.tf])
        bh_run = _bh_for(data, c.tf)
        reg[nm] = {"test": M.by_regime(runs[nm]["temel"], bh_run, data[c.tf]["close"], lab, om[c.tf], t_val, t_end),
                   "all": M.by_regime(runs[nm]["temel"], bh_run, data[c.tf]["close"], lab, om[c.tf], t_start, t_end)}
        yr[nm] = M.yearly(runs[nm]["temel"], om[c.tf], t_start, t_end)
    final = {"frozen": frozen, "show": show, "metrics": metrics, "quarters": quarters,
             "neighbor_pos_share": neighbor, "gates": {f: {k: [bool(v[0]), v[1]] for k, v in g.items()}
                                                       for f, g in gates.items()},
             "accepted": accepted, "regimes": reg, "yearly": yr, "test_all_base": test_all,
             "integrity_final": reports, "e_name": e_name,
             "active_bot_changed_files": changed_active_files(base_sha), "base_sha": base_sha,
             "git_sha": git_sha(), "evaluated_test_once": True}
    with open(os.path.join(out_dir, "results_final.json"), "w", encoding="utf-8") as f:
        f.write(P.dumps(final))
    with open(os.path.join(out_dir, "RAPOR.md"), "w", encoding="utf-8") as f:
        f.write(render(sel, final))
    print("Kabul edilen:", accepted or "YOK (şimdilik doğrulanmış strateji yok)")
    return final


def _bh_for(data, tf):
    """Al-tut özkaynağını başka zaman dilimindeki mum dizisine karşı kıyas için o dizide yeniden hesaplar."""
    d = data[tf]
    c = strategies.make_e(d, tf, int(np.searchsorted(d["open_ms"], data["_eval_start_ms"], side="left")))
    return P.backtest(d["open"], d["high"], d["low"], d["close"], c.entry, c.exit_sig,
                      P.costs_from(spec.COSTS["temel"]), None, None, None,
                      int(np.searchsorted(d["open_ms"], data["_eval_start_ms"], side="left")))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("select", "final"))
    ap.add_argument("--out", default="lab_out")
    ap.add_argument("--base-url", default=os.environ.get("BINANCE_BASE_URL") or spec.BASE_URL_DEFAULT)
    ap.add_argument("--base-sha", default=None)
    a = ap.parse_args(argv)
    if a.stage == "select":
        stage_select(a.out, a.base_url)
    else:
        stage_final(a.out, a.base_url, a.base_sha)
    return 0


if __name__ == "__main__":
    sys.exit(main())
