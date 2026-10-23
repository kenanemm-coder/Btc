# Strateji araştırması v2: BTC/TRY için alternatif strateji doğrulaması

## Sonuç

**Şimdilik doğrulanmış strateji yok.** Hiçbir aday ön kayıtlı kabul kapılarının tamamını dokunulmamış test döneminde geçemedi. Yeni strateji seçilmedi.

> Bu rapor kârlılık kanıtı değildir. Kurallar, adaylar, bölme ve kapılar sonuçlardan önce commit edilmiştir (`39ea75f`, `strategy_lab/spec.py`).

## Aktif botta değişiklik teyidi

Ön kayıt commit'i ile bu çalıştırma arasında `paper/`, `bot.py`, `scripts/`, `bot.yml` ve `watchdog.yml` dosyalarında **değişiklik yok**. Araştırma yalnızca geçmiş veri ve kâğıt işlem mantığı kullandı; gerçek emir gönderilmedi.

## Dönemler ve veri

- Değerlendirme: 01.01.2021 → 23.10.2026 (ısınma verisi 2020-10-01'den).
- **Eğitim:** 01.01.2021 – 26.06.2024 | **Doğrulama:** 26.06.2024 – 24.08.2025 | **Test (dokunulmamış):** 24.08.2025 – 23.10.2026
- Seçim aşaması test verisini hiç indirmedi; seçim `2026-10-23T04:28:01+00:00` anında dondu (`selection.json`). Geçmiş verinin parmak izi final aşamasında yeniden doğrulandı.
- Aday sayısı: **26** (ön kayıt). Çok sayıda aday denendiği için en iyi sonucun tesadüfen iyi çıkma olasılığı vardır; ayrıca A stratejisinin 27 komşu ayarı önceki analizde görüldü.
- Bulaşma: önceki rapor 2022–2026'da BTC/TRY'nin TRY'de güçlü yükseldiğini gösterdi; trend takibi adaylarına yapısal avantaj olabilir.

| Veri | Mum | İlk | Son | Eksik mum | En uzun boşluk | Yinelenen | Hatalı OHLC | Izgara dışı |
|---|---|---|---|---|---|---|---|---|
| 5m | 637105 | 2020-10-01 | 2026-10-23 | 287 (0,05%) | 56 | 0 | 0 | 0 |
| 1h | 53096 | 2020-10-01 | 2026-10-23 | 20 (0,04%) | 4 | 0 | 0 | 0 |
| 4h | 13279 | 2020-10-01 | 2026-10-23 | 0 (0,00%) | 0 | 0 | 0 | 0 |

Kaynak: Binance Spot genel verisi (data-api.binance.vision), zaman damgaları UTC. Parmak izleri `selection.json` içinde.

## Varsayımlar

- Sinyal kapanmış mumda; dolum **bir sonraki mum açılışında** (gecikmeli senaryo: bir mum daha geç).
- Stop muhafazakâr: mum düşüğü stopa değdiyse tetiklenmiş sayılır (giriş mumu dahil), dolum = min(stop, açılış) ve ardından aleyhte slipaj + komisyon.
- Temel maliyet %0,10 komisyon + %0,05 slipaj (her yön); yüksek %0,15 + %0,10; çok yüksek %0,20 + %0,20. Ücretler gerçek hesapta doğrulanmadı.
- Tüm sermaye tek pozisyonda, short/kaldıraç/borç yok. Açık pozisyon çıkış maliyetleri düşülerek değerlenir.
- Sharpe/Sortino: günlük getiri, risksiz oran 0, yıllıklandırma √365; Sortino yalnızca negatif günlük sapma. TRY faizi yüksek olduğundan Sharpe iyimserdir. CAGR = (son/ilk)^(365,25/gün) − 1.
- Enflasyon/kur etkisi düzeltilmedi (gerçek veri olmadan uydurma yapılmaz); TRY'deki fiyat artışı kısmen kur kaymasıdır.

## Eğitim + doğrulama: tüm adaylar (temel maliyet)

| Aday | Eğitim net | Eğitim Sharpe | Doğrulama net | Doğrulama Sharpe | İşlem (E+D) | Skor | Uygun | Seçildi |
|---|---|---|---|---|---|---|---|---|
| B[1h N=20 k=2.0 chan10] | 5,7% | 0,20 | -33,7% | -1,30 | 484 | -1,30 | evet |  |
| B[1h N=20 k=2.0 trail] | -54,3% | -0,75 | -49,7% | -2,69 | 598 | -2,69 | evet |  |
| B[1h N=20 k=3.0 chan10] | -5,7% | 0,11 | -23,5% | -0,77 | 455 | -0,77 | evet |  |
| B[1h N=20 k=3.0 trail] | 10,7% | 0,25 | -27,6% | -1,02 | 450 | -1,02 | evet |  |
| B[1h N=40 k=2.0 chan10] | 37,5% | 0,46 | -31,3% | -1,34 | 381 | -1,34 | evet |  |
| B[1h N=40 k=2.0 trail] | -37,4% | -0,48 | -40,8% | -2,23 | 464 | -2,23 | evet |  |
| B[1h N=40 k=3.0 chan10] | 13,0% | 0,26 | -25,6% | -0,96 | 361 | -0,96 | evet |  |
| B[1h N=40 k=3.0 trail] | 5,4% | 0,19 | -29,3% | -1,18 | 365 | -1,18 | evet |  |
| C[1h EMA50/200] | 187,0% | 0,96 | 40,1% | 1,06 | 110 | 0,96 | evet |  |
| C[1h EMA25/100] | 74,9% | 0,60 | 44,2% | 1,14 | 229 | 0,60 | evet |  |
| D[1h mom20/50] | -82,6% | -1,14 | -71,3% | -3,12 | 1558 | -3,12 | evet |  |
| D[1h mom10/30] | -99,2% | -3,13 | -74,2% | -3,39 | 2271 | -3,39 | evet |  |
| B[4h N=20 k=2.0 chan10] | 279,2% | 1,27 | 15,3% | 0,61 | 127 | 0,61 | evet |  |
| B[4h N=20 k=2.0 trail] | 113,2% | 0,94 | -4,6% | -0,08 | 166 | -0,08 | evet |  |
| B[4h N=20 k=3.0 chan10] | 234,2% | 1,13 | 14,5% | 0,57 | 119 | 0,57 | evet |  |
| B[4h N=20 k=3.0 trail] | 129,0% | 0,92 | 19,0% | 0,72 | 127 | 0,72 | evet |  |
| B[4h N=40 k=2.0 chan10] | 366,8% | 1,51 | 31,4% | 1,11 | 97 | 1,11 | evet |  |
| B[4h N=40 k=2.0 trail] | 150,5% | 1,19 | -1,5% | 0,04 | 132 | 0,04 | evet |  |
| B[4h N=40 k=3.0 chan10] | 319,9% | 1,37 | 32,6% | 1,11 | 92 | 1,11 | evet |  |
| B[4h N=40 k=3.0 trail] | 215,2% | 1,30 | 34,9% | 1,20 | 97 | 1,20 | evet | ✔ |
| C[4h EMA50/200] | 227,9% | 1,06 | 80,1% | 1,73 | 26 | 1,06 | evet |  |
| C[4h EMA25/100] | 668,0% | 1,62 | 72,5% | 1,66 | 50 | 1,62 | evet | ✔ |
| D[4h mom20/50] | 286,0% | 1,17 | 14,6% | 0,55 | 357 | 0,55 | evet | ✔ |
| D[4h mom10/30] | 93,0% | 0,69 | -8,4% | -0,10 | 541 | -0,10 | evet |  |
| A[5m Bollinger+RSI+SMA50] | -52,2% | -1,71 | -42,6% | -3,86 | 711 | -3,86 | evet |  |
| E[al-tut 4h] | 825,1% | 1,38 | 128,4% | 1,80 | 0 | 1,38 | hayır |  |

Seçim kuralı: aile içinde en yüksek skor (= min(eğitim Sharpe, doğrulama Sharpe)), en az 20 işlem. Seçilenler: B: B[4h N=40 k=3.0 trail], C: C[4h EMA25/100], D: D[4h mom20/50].

## Test dönemi (dokunulmamış): temel maliyet

| Aday | Net getiri | Yıllık (CAGR) | Maks. düşüş | Sharpe | Sortino | İşlem | Ort. işlem | Kazanma | Kâr faktörü | Piyasada |
|---|---|---|---|---|---|---|---|---|---|---|
| B[4h N=40 k=3.0 trail] | 10,7% | 9,2% | 16,0% | 0,56 | 1,00 | 21 | 0,59% | 38% | 1,51 | 23% |
| C[4h EMA25/100] | 1,0% | 0,8% | 30,1% | 0,16 | 0,24 | 17 | 0,44% | 29% | 1,23 | 48% |
| D[4h mom20/50] | -16,2% | -14,1% | 33,8% | -0,52 | -0,80 | 77 | -0,18% | 26% | 0,83 | 38% |
| A[5m Bollinger+RSI+SMA50] | -37,5% | -33,3% | 37,7% | -3,72 | -3,89 | 174 | -0,27% | 35% | 0,30 | 6% |
| E[al-tut 4h] | -12,1% | -10,5% | 48,0% | -0,06 | -0,09 | 0 | - | - | - | 100% |

Al-tut (test): net -12,1%, maks. düşüş 48,0%, Sharpe -0,06.

### Maliyet etkisi ve senaryolar (test)

| Aday | Maliyetsiz | Temel | Yüksek | Çok yüksek | Gecikmeli | Maliyet etkisi (puan) |
|---|---|---|---|---|---|---|
| B[4h N=40 k=3.0 trail] | 17,9% | 10,7% | 6,2% | -0,3% | 7,3% | 7.2 |
| C[4h EMA25/100] | 6,6% | 1,0% | -2,6% | -7,7% | 5,8% | 5.6 |
| D[4h mom20/50] | 5,6% | -16,2% | -28,2% | -43,0% | -5,0% | 21.8 |
| A[5m Bollinger+RSI+SMA50] | 5,3% | -37,5% | -55,9% | -73,8% | -40,6% | 42.8 |
| E[al-tut 4h] | -12,1% | -12,1% | -12,1% | -12,1% | -12,1% | 0.0 |

## Kabul kapıları (seçilen adaylar, test dönemi)

**B (Donchian + SMA200 + ATR stopu) → `B[4h N=40 k=3.0 trail]`**

| Kapı | Koşul | Sonuç | Ayrıntı |
|---|---|---|---|
| G1 | Temel maliyette TEST net getirisi > 0 | GEÇTİ | temel TEST net getiri 10.7% |
| G2 | Yüksek maliyet senaryosunda TEST net getirisi > -%10 (çökmemeli) | GEÇTİ | yüksek maliyet TEST net getiri 6.2% |
| G3 | Gecikmeli dolum senaryosunda TEST net getirisi > 0 | GEÇTİ | gecikmeli TEST net getiri 7.3% |
| G4 | TEST'te en az 10 işlem ve en iyi tek işlem çıkarılınca net getiri > 0 | KALDI | TEST işlem 21, en iyi işlem çıkarılınca -7.2% |
| G5 | TEST'i 4 eşit zamana bölünce en az 2 parça pozitif | GEÇTİ | 4 parçadan 2 pozitif: 6.2%, -2.5%, -5.2%, 12.8% |
| G6 | Aynı aile+zaman diliminde ön kayıtlı ayarların en az %50'si TEST'te temel maliyette pozitif | GEÇTİ | komşu ayarların %88'i TEST'te pozitif |
| G7 | Walk-forward ileri-örneklem (eğitim+doğrulama) bileşik getirisi > 0 | GEÇTİ | walk-forward ileri-örneklem 171.3% |
| G8 | Al-tuta göre TEST'te Sharpe VEYA maksimum düşüş avantajı (düşüş <= al-tutun yarısı ve net getiri > 0) | GEÇTİ | Sharpe 0.56 (al-tut -0.06), maks. düşüş 16.0% (al-tut 48.0%) |

Genel: **RED**

**C (EMA kesişimi) → `C[4h EMA25/100]`**

| Kapı | Koşul | Sonuç | Ayrıntı |
|---|---|---|---|
| G1 | Temel maliyette TEST net getirisi > 0 | GEÇTİ | temel TEST net getiri 1.0% |
| G2 | Yüksek maliyet senaryosunda TEST net getirisi > -%10 (çökmemeli) | GEÇTİ | yüksek maliyet TEST net getiri -2.6% |
| G3 | Gecikmeli dolum senaryosunda TEST net getirisi > 0 | GEÇTİ | gecikmeli TEST net getiri 5.8% |
| G4 | TEST'te en az 10 işlem ve en iyi tek işlem çıkarılınca net getiri > 0 | KALDI | TEST işlem 17, en iyi işlem çıkarılınca -12.9% |
| G5 | TEST'i 4 eşit zamana bölünce en az 2 parça pozitif | GEÇTİ | 4 parçadan 2 pozitif: -10.6%, -10.0%, 7.8%, 16.4% |
| G6 | Aynı aile+zaman diliminde ön kayıtlı ayarların en az %50'si TEST'te temel maliyette pozitif | GEÇTİ | komşu ayarların %100'i TEST'te pozitif |
| G7 | Walk-forward ileri-örneklem (eğitim+doğrulama) bileşik getirisi > 0 | GEÇTİ | walk-forward ileri-örneklem 721.3% |
| G8 | Al-tuta göre TEST'te Sharpe VEYA maksimum düşüş avantajı (düşüş <= al-tutun yarısı ve net getiri > 0) | GEÇTİ | Sharpe 0.16 (al-tut -0.06), maks. düşüş 30.1% (al-tut 48.0%) |

Genel: **RED**

**D (20/50 momentum) → `D[4h mom20/50]`**

| Kapı | Koşul | Sonuç | Ayrıntı |
|---|---|---|---|
| G1 | Temel maliyette TEST net getirisi > 0 | KALDI | temel TEST net getiri -16.2% |
| G2 | Yüksek maliyet senaryosunda TEST net getirisi > -%10 (çökmemeli) | KALDI | yüksek maliyet TEST net getiri -28.2% |
| G3 | Gecikmeli dolum senaryosunda TEST net getirisi > 0 | KALDI | gecikmeli TEST net getiri -5.0% |
| G4 | TEST'te en az 10 işlem ve en iyi tek işlem çıkarılınca net getiri > 0 | KALDI | TEST işlem 77, en iyi işlem çıkarılınca -32.1% |
| G5 | TEST'i 4 eşit zamana bölünce en az 2 parça pozitif | GEÇTİ | 4 parçadan 2 pozitif: -4.3%, -20.5%, 2.6%, 7.5% |
| G6 | Aynı aile+zaman diliminde ön kayıtlı ayarların en az %50'si TEST'te temel maliyette pozitif | KALDI | komşu ayarların %0'i TEST'te pozitif |
| G7 | Walk-forward ileri-örneklem (eğitim+doğrulama) bileşik getirisi > 0 | GEÇTİ | walk-forward ileri-örneklem 186.7% |
| G8 | Al-tuta göre TEST'te Sharpe VEYA maksimum düşüş avantajı (düşüş <= al-tutun yarısı ve net getiri > 0) | KALDI | Sharpe -0.52 (al-tut -0.06), maks. düşüş 33.8% (al-tut 48.0%) |

Genel: **RED**

## Piyasa rejimine ve yıllara göre (temel maliyet)

**B[4h N=40 k=3.0 trail]**

Test dönemi:

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma |
|---|---|---|---|---|
| Yükseliş | 90 | -2,7% | -2,7% | 30% |
| Yatay | 181 | 14,2% | -10,6% | 26% |
| Düşüş | 154 | -0,4% | 0,3% | 14% |

Tüm değerlendirme dönemi:

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma |
|---|---|---|---|---|
| Yükseliş | 1020 | 172,1% | 1103,3% | 32% |
| Yatay | 705 | 95,6% | -8,7% | 26% |
| Düşüş | 395 | -11,6% | 69,2% | 13% |

Yıllara göre: 2021: 34,3% (işlem 27); 2022: -5,6% (işlem 14); 2023: 84,1% (işlem 21); 2024: 59,7% (işlem 22); 2025: 21,1% (işlem 16); 2026: 4,3% (işlem 18)

**C[4h EMA25/100]**

Test dönemi:

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma |
|---|---|---|---|---|
| Yükseliş | 90 | -11,0% | -2,7% | 75% |
| Yatay | 181 | 17,6% | -10,6% | 46% |
| Düşüş | 154 | -3,5% | 0,3% | 35% |

Tüm değerlendirme dönemi:

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma |
|---|---|---|---|---|
| Yükseliş | 1020 | 453,5% | 1103,3% | 70% |
| Yatay | 705 | 87,2% | -8,7% | 51% |
| Düşüş | 395 | 29,1% | 69,2% | 33% |

Yıllara göre: 2021: 159,2% (işlem 8); 2022: -29,3% (işlem 12); 2023: 192,1% (işlem 10); 2024: 114,3% (işlem 10); 2025: -1,9% (işlem 16); 2026: 18,9% (işlem 11)

**D[4h mom20/50]**

Test dönemi:

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma |
|---|---|---|---|---|
| Yükseliş | 90 | -12,7% | -2,7% | 38% |
| Yatay | 181 | 20,2% | -10,6% | 42% |
| Düşüş | 154 | -20,2% | 0,3% | 34% |

Tüm değerlendirme dönemi:

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma |
|---|---|---|---|---|
| Yükseliş | 1020 | 118,0% | 1103,3% | 48% |
| Yatay | 705 | 42,8% | -8,7% | 44% |
| Düşüş | 395 | 19,0% | 69,2% | 31% |

Yıllara göre: 2021: 72,6% (işlem 69); 2022: -10,7% (işlem 65); 2023: 69,5% (işlem 97); 2024: 82,3% (işlem 66); 2025: -20,5% (işlem 84); 2026: -2,2% (işlem 53)

**A[5m Bollinger+RSI+SMA50]**

Test dönemi:

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma |
|---|---|---|---|---|
| Yükseliş | 91 | -12,3% | -4,3% | 6% |
| Yatay | 181 | -22,0% | -5,8% | 8% |
| Düşüş | 153 | -8,6% | -2,2% | 5% |

Tüm değerlendirme dönemi:

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma |
|---|---|---|---|---|
| Yükseliş | 1021 | -51,7% | 988,8% | 7% |
| Yatay | 704 | -57,0% | -21,1% | 7% |
| Düşüş | 395 | -17,3% | 117,2% | 5% |

Yıllara göre: 2021: -17,8% (işlem 171); 2022: -20,2% (işlem 141); 2023: -25,5% (işlem 133); 2024: -29,2% (işlem 155); 2025: -39,4% (işlem 169); 2026: -18,2% (işlem 116)

**E[al-tut 4h]**

Test dönemi:

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma |
|---|---|---|---|---|
| Yükseliş | 90 | -2,7% | -2,7% | 100% |
| Yatay | 181 | -10,6% | -10,6% | 100% |
| Düşüş | 154 | 0,3% | 0,3% | 100% |

Tüm değerlendirme dönemi:

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma |
|---|---|---|---|---|
| Yükseliş | 1020 | 1103,3% | 1103,3% | 100% |
| Yatay | 705 | -8,7% | -8,7% | 100% |
| Düşüş | 395 | 69,2% | 69,2% | 100% |

Yıllara göre: 2021: 183,6% (işlem 0); 2022: -49,1% (işlem 0); 2023: 307,5% (işlem 0); 2024: 158,1% (işlem 0); 2025: 13,6% (işlem 0); 2026: 7,8% (işlem 0)

## Walk-forward (yalnızca eğitim+doğrulama, ileri-örneklem)

| Aile | Pencere | İleri-örneklem net | Sharpe | Seçilen ayarlar |
|---|---|---|---|---|
| A | 6 | -58,1% | -3,25 | A[5m Bollinger+RSI+SMA50] |
| B | 6 | 171,3% | 1,55 | B[1h N=40 k=3.0 trail], B[4h N=40 k=2.0 chan10], B[4h N=40 k=2.0 trail], B[4h N=40 k=3.0 chan10] |
| C | 6 | 721,3% | 2,44 | C[1h EMA50/200], C[4h EMA25/100] |
| D | 6 | 186,7% | 1,40 | D[4h mom20/50] |
| E | 6 | 1389,0% | 2,47 | E[al-tut 4h] |

Walk-forward ayar geçişlerinde pozisyon devri modellenmez (günlük getiriler birleştirilir).

## Ek: tüm adaylar test döneminde (yalnızca sağlamlık; seçimi DEĞİŞTİRMEZ)

| Aday | Test net (temel) |
|---|---|
| B[1h N=20 k=2.0 chan10] | -27,6% |
| B[1h N=20 k=2.0 trail] | -40,1% |
| B[1h N=20 k=3.0 chan10] | -30,4% |
| B[1h N=20 k=3.0 trail] | -28,5% |
| B[1h N=40 k=2.0 chan10] | -34,4% |
| B[1h N=40 k=2.0 trail] | -38,4% |
| B[1h N=40 k=3.0 chan10] | -32,5% |
| B[1h N=40 k=3.0 trail] | -33,2% |
| C[1h EMA50/200] | -2,5% |
| C[1h EMA25/100] | -20,2% |
| D[1h mom20/50] | -55,5% |
| D[1h mom10/30] | -86,5% |
| B[4h N=20 k=2.0 chan10] | 2,8% |
| B[4h N=20 k=2.0 trail] | -0,3% |
| B[4h N=20 k=3.0 chan10] | 2,7% |
| B[4h N=20 k=3.0 trail] | 5,6% |
| B[4h N=40 k=2.0 chan10] | 2,5% |
| B[4h N=40 k=2.0 trail] | 2,9% |
| B[4h N=40 k=3.0 chan10] | 9,7% |
| B[4h N=40 k=3.0 trail] | 10,7% |
| C[4h EMA50/200] | 20,8% |
| C[4h EMA25/100] | 1,0% |
| D[4h mom20/50] | -16,2% |
| D[4h mom10/30] | -4,2% |
| A[5m Bollinger+RSI+SMA50] | -37,5% |
| E[al-tut 4h] | -12,1% |

## Sınırlar

- Tek bir test dönemi, tek bir piyasa rejimi dizisi; sonuçlar başka dönemlere genellenmeyebilir.
- Çoklu deneme: 26 aday ve 8 kapı; geçen bir aday bile tesadüf olabilir, geçemeyen aday kesin işe yaramaz demek değildir.
- Gerçek slipaj, emir defteri derinliği, vergi ve minimum emir kuralları modellenmedi; ücretler varsayımdır.
- Al-tut TRY'de büyük ölçüde kur kaymasından yararlandı; bu bir 'strateji becerisi' değildir.
