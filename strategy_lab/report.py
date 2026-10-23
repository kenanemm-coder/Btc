"""Türkçe rapor üretimi."""
from datetime import datetime, timezone

from research.analyze import num, pct

from . import spec

FAM = {"A": "A (mevcut Bollinger+RSI+SMA, 5 dk, referans)", "B": "B (Donchian + SMA200 + ATR stopu)",
       "C": "C (EMA kesişimi)", "D": "D (20/50 momentum)", "E": "E (al-tut kontrolü)"}


def dt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%d.%m.%Y")


def mrow(name, m):
    return (f"| {name} | {pct(m['net_return_pct'])} | {pct(m['cagr_pct'])} | {pct(m['max_dd_pct'])} | {num(m['sharpe'])} | "
            f"{num(m['sortino'])} | {m['trades']} | {pct(m['avg_trade_pct'], 2)} | {pct(m['win_rate_pct'], 0)} | "
            f"{num(m['profit_factor'])} | {pct(m['time_in_market_pct'], 0)} |")


MH = ("| Aday | Net getiri | Yıllık (CAGR) | Maks. düşüş | Sharpe | Sortino | İşlem | Ort. işlem | Kazanma | Kâr faktörü | Piyasada |\n"
      "|---|---|---|---|---|---|---|---|---|---|---|")


def render(sel, fin):
    b = sel["boundaries_ms"]
    L = []
    w = L.append
    w("# Strateji araştırması v2: BTC/TRY için alternatif strateji doğrulaması\n")
    accepted = fin["accepted"]
    if accepted:
        w(f"## Sonuç\n\n**Tüm kabul kapılarını geçen aday: {', '.join(FAM[a] for a in accepted)}.** "
          "Bu bir kâr vaadi değildir; yalnızca ön kayıtlı doğrulama kapılarını geçmiştir. Çoklu deneme ve tek test "
          "döneminin sınırları aşağıda belirtilmiştir. Canlı bota entegrasyon için ayrı plan ve kullanıcı onayı gerekir.\n")
    else:
        w("## Sonuç\n\n**Şimdilik doğrulanmış strateji yok.** Hiçbir aday ön kayıtlı kabul kapılarının tamamını "
          "dokunulmamış test döneminde geçemedi. Yeni strateji seçilmedi.\n")
    w("> Bu rapor kârlılık kanıtı değildir. Kurallar, adaylar, bölme ve kapılar sonuçlardan önce commit edilmiştir "
      f"(`{(fin.get('base_sha') or 'bilinmiyor')[:7]}`, `strategy_lab/spec.py`).\n")
    ch = fin.get("active_bot_changed_files")
    w("## Aktif botta değişiklik teyidi\n")
    if ch is None:
        w("Kontrol yapılamadı (git geçmişi alınamadı).\n")
    elif not ch:
        w(f"Ön kayıt commit'i ile bu çalıştırma arasında `paper/`, `bot.py`, `scripts/`, `bot.yml` ve `watchdog.yml` dosyalarında "
          "**değişiklik yok**. Araştırma yalnızca geçmiş veri ve kâğıt işlem mantığı kullandı; gerçek emir gönderilmedi.\n")
    else:
        w("**UYARI: aktif bot dosyalarında değişiklik var:** " + ", ".join(ch) + "\n")
    w("## Dönemler ve veri\n")
    w(f"- Değerlendirme: {dt(b['eval_start'])} → {dt(b['data_end'])} (ısınma verisi {spec.FETCH_START}'den).")
    w(f"- **Eğitim:** {dt(b['eval_start'])} – {dt(b['train_end'])} | **Doğrulama:** {dt(b['train_end'])} – {dt(b['val_end'])} | "
      f"**Test (dokunulmamış):** {dt(b['val_end'])} – {dt(b['data_end'])}")
    w(f"- Seçim aşaması test verisini hiç indirmedi; seçim `{sel['created_utc']}` anında dondu (`selection.json`). "
      "Geçmiş verinin parmak izi final aşamasında yeniden doğrulandı.")
    w(f"- Aday sayısı: **{sel['candidate_count']}** (ön kayıt). Çok sayıda aday denendiği için en iyi sonucun tesadüfen "
      "iyi çıkma olasılığı vardır; ayrıca A stratejisinin 27 komşu ayarı önceki analizde görüldü.")
    w("- Bulaşma: önceki rapor 2022–2026'da BTC/TRY'nin TRY'de güçlü yükseldiğini gösterdi; trend takibi adaylarına yapısal avantaj olabilir.\n")
    w("| Veri | Mum | İlk | Son | Eksik mum | En uzun boşluk | Yinelenen | Hatalı OHLC | Izgara dışı |")
    w("|---|---|---|---|---|---|---|---|---|")
    for r in fin["integrity_final"]:
        w(f"| {r['tf']} | {r['bars']} | {r['first'][:10]} | {r['last'][:10]} | {r['missing_bars']} ({pct(r['missing_pct'], 2)}) | "
          f"{r['longest_gap_bars']} | {r['duplicates']} | {r['bad_ohlc']} | {r['off_grid']} |")
    w("\nKaynak: Binance Spot genel verisi (data-api.binance.vision), zaman damgaları UTC. Parmak izleri `selection.json` içinde.\n")
    w("## Varsayımlar\n")
    w("- Sinyal kapanmış mumda; dolum **bir sonraki mum açılışında** (gecikmeli senaryo: bir mum daha geç).")
    w("- Stop muhafazakâr: mum düşüğü stopa değdiyse tetiklenmiş sayılır (giriş mumu dahil), dolum = min(stop, açılış) ve ardından aleyhte slipaj + komisyon.")
    w("- Temel maliyet %0,10 komisyon + %0,05 slipaj (her yön); yüksek %0,15 + %0,10; çok yüksek %0,20 + %0,20. Ücretler gerçek hesapta doğrulanmadı.")
    w("- Tüm sermaye tek pozisyonda, short/kaldıraç/borç yok. Açık pozisyon çıkış maliyetleri düşülerek değerlenir.")
    w("- Sharpe/Sortino: günlük getiri, risksiz oran 0, yıllıklandırma √365; Sortino yalnızca negatif günlük sapma. "
      "TRY faizi yüksek olduğundan Sharpe iyimserdir. CAGR = (son/ilk)^(365,25/gün) − 1.")
    w("- Enflasyon/kur etkisi düzeltilmedi (gerçek veri olmadan uydurma yapılmaz); TRY'deki fiyat artışı kısmen kur kaymasıdır.\n")
    w("## Eğitim + doğrulama: tüm adaylar (temel maliyet)\n")
    w("| Aday | Eğitim net | Eğitim Sharpe | Doğrulama net | Doğrulama Sharpe | İşlem (E+D) | Skor | Uygun | Seçildi |")
    w("|---|---|---|---|---|---|---|---|---|")
    chosen_names = set(v for v in sel["chosen"].values() if v)
    for t in sel["candidates"]:
        tr, va = t["train"], t["val"]
        w(f"| {t['name']} | {pct(tr['net_return_pct']) if tr else '-'} | {num(tr['sharpe']) if tr else '-'} | "
          f"{pct(va['net_return_pct']) if va else '-'} | {num(va['sharpe']) if va else '-'} | {t['trades_trainval']} | "
          f"{num(t['score']) if t['score'] is not None else '-'} | {'evet' if t['eligible'] else 'hayır'} | "
          f"{'✔' if t['name'] in chosen_names else ''} |")
    w("\nSeçim kuralı: aile içinde en yüksek skor (= min(eğitim Sharpe, doğrulama Sharpe)), en az 20 işlem. "
      f"Seçilenler: {', '.join(f'{k}: {v}' for k, v in sel['chosen'].items())}.\n")
    w("## Test dönemi (dokunulmamış): temel maliyet\n")
    w(MH)
    for nm in fin["show"]:
        w(mrow(nm, fin["metrics"][nm]["temel"]))
    bh = fin["metrics"][fin["e_name"]]["temel"]
    w(f"\nAl-tut (test): net {pct(bh['net_return_pct'])}, maks. düşüş {pct(bh['max_dd_pct'])}, Sharpe {num(bh['sharpe'])}.\n")
    w("### Maliyet etkisi ve senaryolar (test)\n")
    w("| Aday | Maliyetsiz | Temel | Yüksek | Çok yüksek | Gecikmeli | Maliyet etkisi (puan) |")
    w("|---|---|---|---|---|---|---|")
    for nm in fin["show"]:
        m = fin["metrics"][nm]
        w(f"| {nm} | {pct(m['sifir']['net_return_pct'])} | {pct(m['temel']['net_return_pct'])} | "
          f"{pct(m['yuksek']['net_return_pct'])} | {pct(m['cok_yuksek']['net_return_pct'])} | "
          f"{pct(m['gecikmeli']['net_return_pct'])} | {m['sifir']['net_return_pct'] - m['temel']['net_return_pct']:.1f} |")
    w("\n## Kabul kapıları (seçilen adaylar, test dönemi)\n")
    for fam, g in fin["gates"].items():
        nm = fin["frozen"][fam]
        w(f"**{FAM[fam]} → `{nm}`**\n")
        w("| Kapı | Koşul | Sonuç | Ayrıntı |")
        w("|---|---|---|---|")
        for k, (ok, txt) in g.items():
            w(f"| {k} | {spec.GATES[k]} | {'GEÇTİ' if ok else 'KALDI'} | {txt} |")
        w(f"\nGenel: **{'KABUL' if fam in accepted else 'RED'}**\n")
    for fam in ("B", "C", "D"):
        if not fin["frozen"].get(fam):
            w(f"**{FAM[fam]}**: eğitim+doğrulamada uygun (≥{spec.MIN_TRADES} işlem) ayar çıkmadı; aday seçilmedi.\n")
    w("## Piyasa rejimine ve yıllara göre (temel maliyet)\n")
    names = {"yukselis": "Yükseliş", "yatay": "Yatay", "dusus": "Düşüş"}
    for nm in fin["show"]:
        w(f"**{nm}**\n")
        for key, title in (("test", "Test dönemi"), ("all", "Tüm değerlendirme dönemi")):
            w(f"{title}:\n")
            w("| Rejim | Gün | Strateji | Al-tut | Piyasada kalma |")
            w("|---|---|---|---|---|")
            for rg, x in fin["regimes"][nm][key].items():
                w(f"| {names[rg]} | {x['days']:.0f} | {pct(x['strategy_pct'])} | {pct(x['buyhold_pct'])} | {pct(x['time_in_market_pct'], 0)} |")
            w("")
        w("Yıllara göre: " + "; ".join(f"{y['year']}: {pct(y['net_return_pct'])} (işlem {y['trades']})" for y in fin["yearly"][nm]) + "\n")
    w("## Walk-forward (yalnızca eğitim+doğrulama, ileri-örneklem)\n")
    w("| Aile | Pencere | İleri-örneklem net | Sharpe | Seçilen ayarlar |")
    w("|---|---|---|---|---|")
    for fam, x in sel["walk_forward"].items():
        picks = ", ".join(sorted({(p['pick'] or 'nakit') for p in x["picks"]}))
        w(f"| {fam} | {x['folds']} | {pct(x['oos_return_pct']) if x['oos_return_pct'] is not None else '-'} | "
          f"{num(x['oos_sharpe']) if x['oos_sharpe'] is not None else '-'} | {picks} |")
    w("\nWalk-forward ayar geçişlerinde pozisyon devri modellenmez (günlük getiriler birleştirilir).\n")
    w("## Ek: tüm adaylar test döneminde (yalnızca sağlamlık; seçimi DEĞİŞTİRMEZ)\n")
    w("| Aday | Test net (temel) |")
    w("|---|---|")
    for nm, v in fin["test_all_base"].items():
        w(f"| {nm} | {pct(v)} |")
    w("\n## Sınırlar\n")
    w("- Tek bir test dönemi, tek bir piyasa rejimi dizisi; sonuçlar başka dönemlere genellenmeyebilir.")
    w("- Çoklu deneme: 26 aday ve 8 kapı; geçen bir aday bile tesadüf olabilir, geçemeyen aday kesin işe yaramaz demek değildir.")
    w("- Gerçek slipaj, emir defteri derinliği, vergi ve minimum emir kuralları modellenmedi; ücretler varsayımdır.")
    w("- Al-tut TRY'de büyük ölçüde kur kaymasından yararlandı; bu bir 'strateji becerisi' değildir.")
    return "\n".join(L) + "\n"
