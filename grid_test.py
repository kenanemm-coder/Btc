import os

import numpy as np
import pandas as pd

import trend_backtest as t  # ayni klasorde olmali (veri ve simulasyon fonksiyonlari)

SPLIT = t.SPLIT
FEE = t.FEE
WARMUP = 310  # en uzun gosterge (300) icin


def build_strategies():
    strat = []
    for n in [20, 30, 50, 75, 100, 150, 200, 250, 300]:
        strat.append((f'Fiyat>SMA{n}', 'sma', lambda d, n=n: t.pos_price_sma(d, n)))
    for fast in [10, 20, 30, 50]:
        for slow in [50, 100, 150, 200, 300]:
            if fast < slow:
                strat.append(
                    (f'SMA{fast}>SMA{slow}', 'cross',
                     lambda d, f=fast, s=slow: t.pos_cross(d, f, s))
                )
    for entry in [20, 40, 55, 100]:
        for exit_ in [10, 20, 30, 50]:
            if exit_ < entry:
                strat.append(
                    (f'Donchian{entry}/{exit_}', 'donchian',
                     lambda d, e=entry, x=exit_: t.pos_donchian(d, e, x))
                )
    return strat


def score(m):
    return m['tot'] / max(abs(m['dd']), 1.0)  # getiri / maks.dusus


def main():
    df = t.get_daily()
    n = len(df)
    k = int(n * SPLIT)
    close = df['close'].values
    rows = []
    for name, fam, fn in build_strategies():
        strat, ret, held, entries = t.simulate(close, fn(df), FEE)
        mi = t.metrics(strat, ret, held, entries, WARMUP, k)
        mo = t.metrics(strat, ret, held, entries, k, n)
        rows.append({
            'name': name, 'fam': fam,
            'is_tot': mi['tot'], 'is_dd': mi['dd'], 'is_sc': score(mi),
            'oos_tot': mo['tot'], 'oos_dd': mo['dd'], 'oos_sc': score(mo),
            'oos_trades': mo['trades'],
            'btot_is': mi['btot'], 'bdd_is': mi['bdd'],
            'btot_oos': mo['btot'], 'bdd_oos': mo['bdd'],
        })
    r = pd.DataFrame(rows)
    b = r.iloc[0]

    corr = r['is_sc'].corr(r['oos_sc'], method='spearman')
    beat_ret = (r['oos_tot'] > b['btot_oos']).mean() * 100
    shallower = (r['oos_dd'] > b['bdd_oos']).mean() * 100  # dd negatif: buyuk = daha sig
    lines = [
        f'Parametre taramasi (gunluk, {n} gun, {df.index[0].date()} - {df.index[-1].date()}, '
        f'yalniz alim/nakit, komisyon %{FEE * 100:.2f}/islem)',
        f'IS: ilk %60 (bolme {df.index[k].date()}) | OOS: son %40',
        f'AL-TUT: IS {b["btot_is"]:+.0f}%/dus{b["bdd_is"]:.0f}% | '
        f'OOS {b["btot_oos"]:+.0f}%/dus{b["bdd_oos"]:.0f}%',
        f'Toplam kural: {len(r)}',
        f'IS-OOS siralama korelasyonu (Spearman): {corr:+.2f} '
        f'(+1: IS\'te iyi olan OOS\'ta da iyi, 0: iliski yok)',
        f'OOS\'ta al-tutu getiride gecen kural orani: %{beat_ret:.0f} | '
        f'daha sig dusus veren: %{shallower:.0f}',
        f'Tum kurallarin OOS medyani: getiri {r["oos_tot"].median():+.0f}% / '
        f'dusus {r["oos_dd"].median():.0f}%',
        '',
        'IS skoruna (getiri/dusus) gore secilen ilk 5 -> OOS sonucu:',
    ]
    for i, (_, x) in enumerate(r.sort_values('is_sc', ascending=False).head(5).iterrows(), 1):
        lines.append(
            f' {i}. {x["name"]}: IS {x["is_tot"]:+.0f}%/{x["is_dd"]:.0f}% | '
            f'OOS {x["oos_tot"]:+.0f}%/{x["oos_dd"]:.0f}% (islem {x["oos_trades"]})'
        )
    top5 = r.sort_values('is_sc', ascending=False).head(5)
    lines.append(
        f'Bu 5\'in OOS ortalamasi: getiri {top5["oos_tot"].mean():+.0f}% / '
        f'dusus {top5["oos_dd"].mean():.0f}%'
    )
    lines += ['', 'Fiyat>SMA(n) ailesi (IS getiri/dusus | OOS getiri/dusus):']
    for _, x in r[r['fam'] == 'sma'].iterrows():
        lines.append(
            f' {x["name"].replace("Fiyat>", "")}: {x["is_tot"]:+.0f}%/{x["is_dd"]:.0f}% | '
            f'{x["oos_tot"]:+.0f}%/{x["oos_dd"]:.0f}%'
        )
    text = '\n'.join(lines)
    print(text)
    t.notify(text)
    if len(text) > 3900:
        print(f'UYARI: metin {len(text)} karakter, ntfy icin kesildi')


if __name__ == '__main__':
    main()
