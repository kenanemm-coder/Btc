# Strateji dayanıklılık raporu (BTCTRY, 5 dk)

> Bu rapor **kârlılık kanıtı değildir**. Geçmiş sonuçlar geleceği garanti etmez. Botun mevcut sabit parametreleri bu veriye göre ayarlanmadı; yalnızca farklı dönem ve piyasa koşullarında nasıl davrandığı ölçüldü.

## Veri

- Dönem: 01.01.2022 – 22.10.2026; 505.662 mum; eksik mum 16 (0.00%); en uzun boşluk 16 mum
- Kaynak: Binance Spot genel verisi. Sinyal mum kapanışında görülür; maliyet varsayımları gerçek ücret değildir (komisyon %0,10/yön, slipaj %0,05/yön).
- Dolum gecikmesi: `gecikme=0` iyimserdir (sinyal anındaki kapanışta dolum). GitHub Actions gecikmesi için `gecikme=5` (25 dk) de gösterilir.

## Genel sonuç

| Senaryo | İşlem | Net getiri | Maks. düşüş | Kazanma | Ort. işlem | Kâr faktörü |
|---|---|---|---|---|---|---|
| maliyetsiz / gecikme=0 | 714 | 88,5% | 16,2% | 70% | 0,09% | 1,40 |
| temel / gecikme=0 | 714 | -77,9% | 78,1% | 43% | -0,21% | 0,42 |
| temel / gecikme=1 | 714 | -80,4% | 80,5% | 38% | -0,22% | 0,39 |
| temel / gecikme=5 | 714 | -77,9% | 78,2% | 34% | -0,21% | 0,44 |
| cift_maliyet / gecikme=0 | 714 | -97,4% | 97,4% | 19% | -0,51% | 0,10 |

Al-tut (aynı dönem): maliyetsiz 550,7%, maliyetli 548,7%. TRY'deki kur kayması BTC/TRY fiyatını yukarı iter; bu yüzden al-tut karşılaştırması TRY bazında enflasyon/kur etkisini de içerir.

## İstatistiksel güven

- İşlem başı ortalama getiri -0,21%; %95 güven aralığı [-0,27%; -0,15%].
- Aynı sayıda/sürede **rastgele girişli** işlemler: medyan -86,9%, 5–95. yüzdelik [-91,4%; -79,6%]. Strateji -77,9%; rastgele denemelerin %2'i stratejiden iyi veya eşit.

## Piyasa rejimine göre (son 90 gün getirisine göre sınıflama)

Rejim, stratejiden bağımsız ve yalnızca geçmişe bakar: ±%15 eşik (ana), ±%10 ve ±%25 (duyarlılık). Mum bazlı getiriler karşılaştırılabilirdir; az işlemli rejimlerde sonuçlar istatistiksel olarak zayıftır.


**Eşik ±%15, gecikme=0**

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma | İşlem | Kazanma | Ort. işlem |
|---|---|---|---|---|---|---|---|
| Yükseliş | 739 | -43,1% | 370,2% | 6% | 294 | 43% | -0,18% |
| Yatay | 636 | -52,0% | 19,3% | 7% | 271 | 40% | -0,25% |
| Düşüş | 290 | -18,3% | 6,8% | 5% | 104 | 45% | -0,25% |

**Eşik ±%15, gecikme=5**

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma | İşlem | Kazanma | Ort. işlem |
|---|---|---|---|---|---|---|---|
| Yükseliş | 739 | -40,8% | 370,2% | 6% | 296 | 34% | -0,17% |
| Yatay | 636 | -48,5% | 19,3% | 7% | 269 | 32% | -0,24% |
| Düşüş | 290 | -26,1% | 6,8% | 5% | 104 | 29% | -0,29% |

**Eşik ±%10, gecikme=0**

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma | İşlem | Kazanma | Ort. işlem |
|---|---|---|---|---|---|---|---|
| Yükseliş | 834 | -48,7% | 377,0% | 6% | 341 | 40% | -0,20% |
| Yatay | 410 | -40,0% | 7,8% | 7% | 169 | 43% | -0,23% |
| Düşüş | 422 | -27,5% | 16,5% | 5% | 159 | 44% | -0,26% |

**Eşik ±%10, gecikme=5**

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma | İşlem | Kazanma | Ort. işlem |
|---|---|---|---|---|---|---|---|
| Yükseliş | 834 | -44,7% | 377,0% | 6% | 339 | 33% | -0,18% |
| Yatay | 410 | -38,0% | 7,8% | 7% | 168 | 36% | -0,21% |
| Düşüş | 422 | -34,3% | 16,5% | 5% | 162 | 28% | -0,31% |

**Eşik ±%25, gecikme=0**

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma | İşlem | Kazanma | Ort. işlem |
|---|---|---|---|---|---|---|---|
| Yükseliş | 600 | -28,9% | 324,6% | 6% | 226 | 46% | -0,14% |
| Yatay | 981 | -68,3% | -4,1% | 7% | 415 | 38% | -0,27% |
| Düşüş | 85 | -1,1% | 47,2% | 4% | 28 | 68% | -0,07% |

**Eşik ±%25, gecikme=5**

| Rejim | Gün | Strateji | Al-tut | Piyasada kalma | İşlem | Kazanma | Ort. işlem |
|---|---|---|---|---|---|---|---|
| Yükseliş | 600 | -29,2% | 324,6% | 6% | 226 | 36% | -0,14% |
| Yatay | 981 | -67,1% | -4,1% | 6% | 415 | 31% | -0,27% |
| Düşüş | 85 | -3,3% | 47,2% | 4% | 28 | 36% | -0,10% |

## Dönemlere göre (gecikme=0)

| Dönem | Strateji | Al-tut (maliyetsiz) | Maks. düşüş | İşlem | Kazanma |
|---|---|---|---|---|---|
| 2022 | -18,1% | -49,2% | 21,3% | 141 | 54% |
| 2023 | -24,4% | 307,5% | 25,6% | 133 | 36% |
| 2024 | -28,4% | 158,1% | 29,9% | 155 | 45% |
| 2025 | -38,9% | 13,6% | 41,1% | 169 | 39% |
| 2026 | -18,2% | 7,1% | 19,8% | 116 | 40% |
| 1. yarı | -40,4% | 260,8% | 41,6% | 330 | 46% |
| 2. yarı | -62,9% | 80,4% | 63,9% | 384 | 40% |

Yıllık: 5 yılın 0'inde strateji pozitif, 1'inde al-tutu geçti.

## Dönemlere göre (gecikme=5)

| Dönem | Strateji | Al-tut (maliyetsiz) | Maks. düşüş | İşlem | Kazanma |
|---|---|---|---|---|---|
| 2022 | -26,0% | -49,2% | 28,2% | 141 | 35% |
| 2023 | -27,4% | 307,5% | 28,4% | 133 | 26% |
| 2024 | -27,3% | 158,1% | 32,1% | 155 | 43% |
| 2025 | -31,1% | 13,6% | 34,7% | 169 | 33% |
| 2026 | -18,0% | 7,1% | 19,4% | 116 | 30% |
| 1. yarı | -48,1% | 260,8% | 48,6% | 330 | 32% |
| 2. yarı | -57,5% | 80,4% | 58,9% | 384 | 35% |

Yıllık: 5 yılın 0'inde strateji pozitif, 1'inde al-tutu geçti.

## Parametre duyarlılığı (en iyiyi seçmek için DEĞİL)

RSI eşiği {44, 48, 52} × Bollinger çarpanı {1,8; 2; 2,2} × SMA {40, 50, 60} = 27 kombinasyon, temel maliyet, gecikme=0.

- Net getiri: en düşük -98,0%, medyan -71,9%, en yüksek -24,5%; 0/27 kombinasyon pozitif.
- Mevcut ayar (-77,9%) 27 içinde 18. sırada. Komşu ayarlarla sonuç çok değişiyorsa strateji parametreye duyarlı/kırılgandır.

## Sınırlar

- Rejim ve dönem kesitleri çok sayıdadır; çoklu karşılaştırmada rastgele iyi görünen bir kesit çıkması beklenir.
- İşlem sayısı az olan kesitlerde yüzdeler anlamsızdır. Küçük örneklem uyarısı için işlem sütununa bakın.
- Emir defteri derinliği, gerçek slipaj, vergi ve Binance minimum emir kuralları modellenmedi.
- Strateji parametreleri önceki bir çalışmadan geliyor; o çalışmada hangi verilerin kullanıldığı bilinmiyor.
