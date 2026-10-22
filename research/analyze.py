"""Dayanıklılık analizi: aynı strateji/maliyetle geçmiş veride rejim, dönem ve duyarlılık dökümü.

Parametreler bu veriye göre AYARLANMAZ; botun mevcut sabit değerleri kullanılır. Duyarlılık
tablosu en iyiyi seçmek için değil, sonucun ne kadar kırılgan olduğunu görmek içindir.
Kullanım:  python -m research.analyze --start 2022-01-01 --out research_out
"""
import argparse
import csv
import itertools
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

from . import engine as E
from .data import data_quality, fetch_klines

REGIMES = ("yukselis", "yatay", "dusus")
REGIME_TR = {"yukselis": "Yükseliş", "yatay": "Yatay", "dusus": "Düşüş"}


def pct(x, d=1):
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}".replace(".", ",") + "%"


def num(x, d=2):
    return "-" if x is None else f"{x:.{d}f}".replace(".", ",")


def year_of(ms):
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).year


def run_strategy(close, open_ms, p=E.Params(), costs=E.BASE, latency=0):
    buy, sell = E.signals(close, open_ms, p)
    sim = E.simulate(close, buy, sell, costs, latency)
    return sim


def summarize(sim, close):
    eq = sim["eq"]
    st = E.trade_stats(sim["trades"])
    st.update({
        "return_pct": float((eq[-1] / sim["cash0"] - 1.0) * 100.0),
        "max_dd_pct": E.max_drawdown_pct(eq),
        "open": sim["open"] is not None,
    })
    return st


def buy_hold(close, costs):
    r = close[-1] / close[0] * (1 - costs.slip_sell) * (1 - costs.fee_sell) / ((1 + costs.slip_buy) * (1 + costs.fee_buy)) - 1
    return float(r * 100.0)


def by_regime(sim, close, open_ms, labels):
    """Mum bazlı log getiri (strateji özkaynağı ve al-tut) rejime göre; işlemler giriş rejimine göre."""
    lr_s = E.log_returns(sim["eq"])
    lr_b = E.log_returns(close)
    held = np.zeros(len(close), dtype=bool)
    for t in sim["trades"]:
        held[t["entry"]:t["exit"]] = True
    if sim["open"]:
        held[sim["open"]["entry"]:] = True
    out = {}
    for r in REGIMES:
        m = np.array([l == r for l in labels])
        m_prev = np.concatenate([[False], m[:-1]])  # getiri, önceki mumdaki etikete göre (ileriye bakma yok)
        tr = [t for t in sim["trades"] if labels[t["entry"]] == r]
        st = E.trade_stats(tr)
        out[r] = {
            "days": float(m.sum() * 300 / 86400),
            "strategy_pct": float((np.exp(lr_s[m_prev].sum()) - 1) * 100.0),
            "buyhold_pct": float((np.exp(lr_b[m_prev].sum()) - 1) * 100.0),
            "time_in_market_pct": float(held[m].mean() * 100.0) if m.any() else None,
            "trades": st,
        }
    return out


def by_period(sim, close, open_ms):
    years = np.array([year_of(x) for x in open_ms])
    rows = []
    for y in sorted(set(years)):
        idx = np.flatnonzero(years == y)
        a, b = idx[0], idx[-1]
        base_eq = sim["eq"][a - 1] if a > 0 else sim["cash0"]
        base_px = close[a - 1] if a > 0 else close[a]
        eq = sim["eq"][a:b + 1]
        tr = [t for t in sim["trades"] if a <= t["exit"] <= b]
        st = E.trade_stats(tr)
        rows.append({
            "period": str(y), "candles": int(len(idx)),
            "strategy_pct": float((sim["eq"][b] / base_eq - 1) * 100.0),
            "buyhold_pct": float((close[b] / base_px - 1) * 100.0),
            "max_dd_pct": E.max_drawdown_pct(eq),
            "trades": st["n"], "win_rate": st["win_rate"],
        })
    mid = len(close) // 2
    for name, a, b in (("1. yarı", 0, mid - 1), ("2. yarı", mid, len(close) - 1)):
        base_eq = sim["eq"][a - 1] if a > 0 else sim["cash0"]
        base_px = close[a - 1] if a > 0 else close[a]
        tr = [t for t in sim["trades"] if a <= t["exit"] <= b]
        st = E.trade_stats(tr)
        rows.append({
            "period": name, "candles": int(b - a + 1),
            "strategy_pct": float((sim["eq"][b] / base_eq - 1) * 100.0),
            "buyhold_pct": float((close[b] / base_px - 1) * 100.0),
            "max_dd_pct": E.max_drawdown_pct(sim["eq"][a:b + 1]),
            "trades": st["n"], "win_rate": st["win_rate"],
        })
    return rows


def bootstrap_mean_ci(rets, n=5000, seed=1):
    r = np.asarray(rets, dtype="float64")
    if len(r) < 2:
        return None
    rng = np.random.default_rng(seed)
    means = rng.choice(r, size=(n, len(r)), replace=True).mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"mean_pct": float(r.mean() * 100.0), "lo_pct": float(lo * 100.0), "hi_pct": float(hi * 100.0),
            "positive_share": float((means > 0).mean() * 100.0)}


def random_entry_baseline(sim, close, costs, n_sims=2000, seed=2):
    """Aynı sayıda işlem ve aynı bekleme sürelerinde RASTGELE giriş: bileşik getiri dağılımı."""
    trades = sim["trades"]
    if len(trades) < 3:
        return None
    holds = np.array([t["exit"] - t["entry"] for t in trades])
    rng = np.random.default_rng(seed)
    n = len(close)
    rt = (1 - costs.slip_sell) * (1 - costs.fee_sell) / ((1 + costs.slip_buy) * (1 + costs.fee_buy))
    out = np.empty(n_sims)
    for k in range(n_sims):
        h = rng.permutation(holds)
        starts = rng.integers(0, np.maximum(1, n - h))
        f = close[np.minimum(starts + h, n - 1)] / close[starts] * rt
        out[k] = np.prod(f) - 1.0
    actual = float(np.prod([1 + t["ret"] for t in trades]) - 1.0)
    return {"actual_pct": actual * 100.0, "p5": float(np.percentile(out, 5) * 100.0),
            "median": float(np.median(out) * 100.0), "p95": float(np.percentile(out, 95) * 100.0),
            "share_random_ge_actual": float((out >= actual).mean() * 100.0)}


def sensitivity(close, open_ms, costs):
    rows = []
    for rsi, k, sma in itertools.product((44.0, 48.0, 52.0), (1.8, 2.0, 2.2), (40, 50, 60)):
        s = run_strategy(close, open_ms, E.Params(bb_k=k, rsi_max=rsi, sma_n=sma), costs)
        sm = summarize(s, close)
        rows.append({"rsi_max": rsi, "bb_k": k, "sma_n": sma, "trades": sm["n"],
                     "return_pct": sm["return_pct"], "max_dd_pct": sm["max_dd_pct"]})
    return rows


def analyze(open_ms, close, regime_defs=((0.15, -0.15), (0.10, -0.10), (0.25, -0.25)), log=print):
    res = {"quality": data_quality(open_ms)}
    log("Ana senaryo...")
    scen = {}
    for name, costs in (("maliyetsiz", E.ZERO), ("temel", E.BASE), ("cift_maliyet", E.DOUBLE)):
        for lat in (0, 1, 5):
            if name != "temel" and lat != 0:
                continue
            sim = run_strategy(close, open_ms, costs=costs, latency=lat)
            scen[f"{name} / gecikme={lat}"] = summarize(sim, close)
    res["scenarios"] = scen
    base = run_strategy(close, open_ms, costs=E.BASE, latency=0)
    lag = run_strategy(close, open_ms, costs=E.BASE, latency=5)
    res["buyhold"] = {"temel_pct": buy_hold(close, E.BASE), "maliyetsiz_pct": buy_hold(close, E.ZERO)}
    res["regimes"] = {}
    for up, down in regime_defs:
        labels, _ = E.regime_labels(close, open_ms, 90, up, down)
        res["regimes"][f"{int(up*100)}"] = {"latency0": by_regime(base, close, open_ms, labels),
                                           "latency5": by_regime(lag, close, open_ms, labels)}
    res["periods"] = by_period(base, close, open_ms)
    res["periods_latency5"] = by_period(lag, close, open_ms)
    res["bootstrap"] = bootstrap_mean_ci([t["ret"] for t in base["trades"]])
    res["random_baseline"] = random_entry_baseline(base, close, E.BASE)
    log("Duyarlılık (27 kombinasyon)...")
    res["sensitivity"] = sensitivity(close, open_ms, E.BASE)
    res["_trades"] = base["trades"]
    return res


def render(res, symbol="BTCTRY"):
    q = res["quality"]
    d0 = datetime.fromtimestamp(q["first_ms"] / 1000, tz=timezone.utc).strftime("%d.%m.%Y")
    d1 = datetime.fromtimestamp(q["last_ms"] / 1000, tz=timezone.utc).strftime("%d.%m.%Y")
    L = []
    w = L.append
    w(f"# Strateji dayanıklılık raporu ({symbol}, 5 dk)\n")
    w("> Bu rapor **kârlılık kanıtı değildir**. Geçmiş sonuçlar geleceği garanti etmez. Botun mevcut sabit "
      "parametreleri bu veriye göre ayarlanmadı; yalnızca farklı dönem ve piyasa koşullarında nasıl davrandığı ölçüldü.\n")
    w("## Veri\n")
    w(f"- Dönem: {d0} – {d1}; {q['candles']:,} mum; ".replace(",", ".") +
      f"eksik mum {q['missing_candles']:,} ({pct(q['missing_pct'], 2)}); ".replace(",", ".") +
      f"en uzun boşluk {q['longest_gap_candles']} mum")
    w("- Kaynak: Binance Spot genel verisi. Sinyal mum kapanışında görülür; maliyet varsayımları gerçek ücret değildir "
      "(komisyon %0,10/yön, slipaj %0,05/yön).")
    w("- Dolum gecikmesi: `gecikme=0` iyimserdir (sinyal anındaki kapanışta dolum). GitHub Actions gecikmesi için "
      "`gecikme=5` (25 dk) de gösterilir.\n")
    w("## Genel sonuç\n")
    w("| Senaryo | İşlem | Net getiri | Maks. düşüş | Kazanma | Ort. işlem | Kâr faktörü |")
    w("|---|---|---|---|---|---|---|")
    for k, s in res["scenarios"].items():
        w(f"| {k} | {s['n']} | {pct(s['return_pct'])} | {pct(s['max_dd_pct'])} | {pct(s['win_rate'], 0)} | "
          f"{pct(s['avg_ret_pct'], 2)} | {num(s['profit_factor'])} |")
    bh = res["buyhold"]
    w(f"\nAl-tut (aynı dönem): maliyetsiz {pct(bh['maliyetsiz_pct'])}, maliyetli {pct(bh['temel_pct'])}. "
      "TRY'deki kur kayması BTC/TRY fiyatını yukarı iter; bu yüzden al-tut karşılaştırması TRY bazında "
      "enflasyon/kur etkisini de içerir.\n")
    bs, rb = res["bootstrap"], res["random_baseline"]
    w("## İstatistiksel güven\n")
    if bs:
        incl = bs["lo_pct"] <= 0 <= bs["hi_pct"]
        w(f"- İşlem başı ortalama getiri {pct(bs['mean_pct'], 2)}; %95 güven aralığı [{pct(bs['lo_pct'], 2)}; {pct(bs['hi_pct'], 2)}]"
          f"{' — **aralık sıfırı içeriyor, ortalamanın pozitif olduğu söylenemez**' if incl else ''}.")
    if rb:
        w(f"- Aynı sayıda/sürede **rastgele girişli** işlemler: medyan {pct(rb['median'])}, 5–95. yüzdelik "
          f"[{pct(rb['p5'])}; {pct(rb['p95'])}]. Strateji {pct(rb['actual_pct'])}; rastgele denemelerin "
          f"%{rb['share_random_ge_actual']:.0f}'i stratejiden iyi veya eşit.")
    w("\n## Piyasa rejimine göre (son 90 gün getirisine göre sınıflama)\n")
    w("Rejim, stratejiden bağımsız ve yalnızca geçmişe bakar: ±%15 eşik (ana), ±%10 ve ±%25 (duyarlılık). "
      "Mum bazlı getiriler karşılaştırılabilirdir; az işlemli rejimlerde sonuçlar istatistiksel olarak zayıftır.\n")
    for thr, d in res["regimes"].items():
        for lat in ("latency0", "latency5"):
            w(f"\n**Eşik ±%{thr}, {'gecikme=0' if lat == 'latency0' else 'gecikme=5'}**\n")
            w("| Rejim | Gün | Strateji | Al-tut | Piyasada kalma | İşlem | Kazanma | Ort. işlem |")
            w("|---|---|---|---|---|---|---|---|")
            for r in REGIMES:
                x = d[lat][r]
                t = x["trades"]
                w(f"| {REGIME_TR[r]} | {x['days']:.0f} | {pct(x['strategy_pct'])} | {pct(x['buyhold_pct'])} | "
                  f"{pct(x['time_in_market_pct'], 0)} | {t['n']} | {pct(t['win_rate'], 0)} | {pct(t['avg_ret_pct'], 2)} |")
    for key, title in (("periods", "gecikme=0"), ("periods_latency5", "gecikme=5")):
        w(f"\n## Dönemlere göre ({title})\n")
        w("| Dönem | Strateji | Al-tut (maliyetsiz) | Maks. düşüş | İşlem | Kazanma |")
        w("|---|---|---|---|---|---|")
        for p in res[key]:
            w(f"| {p['period']} | {pct(p['strategy_pct'])} | {pct(p['buyhold_pct'])} | {pct(p['max_dd_pct'])} | "
              f"{p['trades']} | {pct(p['win_rate'], 0)} |")
        yrs = [p for p in res[key] if p["period"].isdigit()]
        if yrs:
            pos = sum(1 for p in yrs if p["strategy_pct"] > 0)
            beat = sum(1 for p in yrs if p["strategy_pct"] > p["buyhold_pct"])
            w(f"\nYıllık: {len(yrs)} yılın {pos}'inde strateji pozitif, {beat}'inde al-tutu geçti.")
    s = res["sensitivity"]
    rets = np.array([r["return_pct"] for r in s])
    w("\n## Parametre duyarlılığı (en iyiyi seçmek için DEĞİL)\n")
    w("RSI eşiği {44, 48, 52} × Bollinger çarpanı {1,8; 2; 2,2} × SMA {40, 50, 60} = 27 kombinasyon, "
      "temel maliyet, gecikme=0.\n")
    w(f"- Net getiri: en düşük {pct(rets.min())}, medyan {pct(float(np.median(rets)))}, en yüksek {pct(rets.max())}; "
      f"{int((rets > 0).sum())}/{len(rets)} kombinasyon pozitif.")
    cur = [r for r in s if r["rsi_max"] == 48.0 and r["bb_k"] == 2.0 and r["sma_n"] == 50]
    if cur:
        rank = int((rets > cur[0]["return_pct"]).sum()) + 1
        w(f"- Mevcut ayar ({pct(cur[0]['return_pct'])}) 27 içinde {rank}. sırada. "
          "Komşu ayarlarla sonuç çok değişiyorsa strateji parametreye duyarlı/kırılgandır.")
    w("\n## Sınırlar\n")
    w("- Rejim ve dönem kesitleri çok sayıdadır; çoklu karşılaştırmada rastgele iyi görünen bir kesit çıkması beklenir.")
    w("- İşlem sayısı az olan kesitlerde yüzdeler anlamsızdır. Küçük örneklem uyarısı için işlem sütununa bakın.")
    w("- Emir defteri derinliği, gerçek slipaj, vergi ve Binance minimum emir kuralları modellenmedi.")
    w("- Strateji parametreleri önceki bir çalışmadan geliyor; o çalışmada hangi verilerin kullanıldığı bilinmiyor.")
    return "\n".join(L) + "\n"


def write_outputs(res, out_dir, symbol="BTCTRY"):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "RAPOR.md"), "w", encoding="utf-8") as f:
        f.write(render(res, symbol))
    slim = {k: v for k, v in res.items() if k != "_trades"}
    with open(os.path.join(out_dir, "sonuclar.json"), "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=1, default=float)
    with open(os.path.join(out_dir, "duyarlilik.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(res["sensitivity"][0].keys()))
        w.writeheader()
        w.writerows(res["sensitivity"])
    with open(os.path.join(out_dir, "islemler.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entry_idx", "exit_idx", "ret_pct"])
        for t in res["_trades"]:
            w.writerow([t["entry"], t["exit"], round(t["ret"] * 100, 4)])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--out", default="research_out")
    ap.add_argument("--base-url", default=os.environ.get("BINANCE_BASE_URL") or "https://data-api.binance.vision")
    ap.add_argument("--symbol", default="BTCTRY")
    args = ap.parse_args(argv)
    start = int(datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    print(f"Veri çekiliyor: {args.symbol} 5m, {args.start} →")
    om, _, _, _, close = fetch_klines(args.base_url, args.symbol, start_ms=start)
    print(f"{len(om)} mum alındı")
    res = analyze(om, close)
    write_outputs(res, args.out, args.symbol)
    print("Rapor yazıldı:", os.path.join(args.out, "RAPOR.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
