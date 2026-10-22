# BTC/TRY PAPER (sanal) işlem botu

**Gerçek para yok, gerçek emir yok.** Bot yalnızca Binance Spot'un anahtarsız genel piyasa verisini
okur (GET) ve sanal bir portföyde işlem simüle eder. Stratejinin kârlı olduğu varsayılmaz; sonuçlar
ölçülür ve hiçbir kârlılık garantisi yoktur.

## Dosyalar
| Dosya | Görev |
|---|---|
| `bot.py` | Giriş noktası (workflow bunu çalıştırır) |
| `paper/indicators.py` | Bollinger(20,2), SMA50, RSI(14) ve sinyal kuralı (eski bot ile aynı formül) |
| `paper/binance.py` | Binance Spot genel veri istemcisi, kapanmış mum filtresi, veri doğrulama |
| `paper/portfolio.py` | Sanal portföy, komisyon/slipaj, K/Z, drawdown |
| `paper/state.py` | Atomik JSON state, doğrulama, tek örnek kilidi |
| `paper/notify.py` | ntfy bildirimleri (mevcut topic/format korunur) |
| `paper/report.py` | Bildirim ve günlük rapor metinleri |
| `paper/runner.py` | Çalıştırma akışı (`--init`, `--loop`, `--dry-run`) |
| `tests/` | 69 birim testi (internetsiz, sahte veri) |
| `.github/workflows/bot.yml` | Her 5 dk çalışır, state'i `data` dalına kaydeder |
| `.github/workflows/test.yml` | Her push'ta testleri çalıştırır |

## İşlem kuralları
- **Strateji (değişmedi):** ALIM = kapanış ≤ alt bant **ve** RSI < 48 **ve** kapanış > SMA50.
  SATIŞ = kapanış ≥ üst bant. Yalnızca **kapanmış** 5 dk mumlar kullanılır; göstergeler hazır değilse sinyal yok.
- **Spot:** short yok. Pozisyon yokken SATIŞ sinyali yok sayılır; pozisyon varken ALIM sinyali yok sayılır.
- **Alış:** tüm sanal TRY ile. Fiyat = sinyal mumu kapanışı × (1 + slipaj). Komisyon alım tutarından.
- **Satış:** tüm BTC. Fiyat = kapanış × (1 − slipaj). Komisyon satış tutarından.
- **Maliyet varsayımları (gerçek borsa ücreti DEĞİL, değiştirilebilir):** komisyon alış %0,10 / satış %0,10,
  slipaj her yönde %0,05 aleyhte. Değiştirmek için `PAPER_FEE_BUY`, `PAPER_FEE_SELL`,
  `PAPER_SLIPPAGE_BUY`, `PAPER_SLIPPAGE_SELL` ortam değişkenleri (0.001 = %0,10).
- **Değerleme:** açık pozisyon, tahmini satış komisyonu ve slipajı düşülerek değerlenir.
  Toplam net K/Z = portföy değeri − başlangıç. Toplam = gerçekleşmiş + gerçekleşmemiş.
- **Bayat sinyal:** 30 dakikadan eski kapanmış mumdaki sinyalle işlem açılmaz/kapanmaz (`PAPER_MAX_SIGNAL_AGE_S`).
  Çünkü GitHub zamanlayıcısı geç kalabilir ve eski fiyatla işlem gerçekçi olmaz.

## Başlangıç bakiyesi ve kur
İlk kurulumda 1000 USDT, o andaki Binance `USDTTRY` fiyatıyla sanal TRY'ye çevrilir (ör. 40,00 → ₺40.000).
Strateji performansı **TRY bazlıdır**. Raporlardaki USDT değerleri **başlangıç kuruyla** gösterilir; güncel kurla
değer ve "kur etkisi" ayrı bir satırda verilir, böylece USDT/TRY değişimi strateji sonucuna karışmaz.

## Bildirimler (ntfy)
Topic, eskisi gibi `NTFY_TOPIC` (GitHub Secret) ile gelir; koda/loga yazılmaz. Gönderilenler: kurulum
("PAPER mod aktif"), sanal alış, pozisyon kapanışı (net K/Z, getiri %), günlük rapor (21:00 TSİ, günde bir kez,
kaçırılırsa sonraki çalışmada), kritik veri/state hatası (aynı hata en fazla 6 saatte bir).
Gönderim hatası botu durdurmaz; bildirim state'te bekler ve sonraki çalışmada yeniden denenir.
**Kaldırılanlar:** her sinyalde gelen ALIM/SATIŞ uyarısı ve 6 saatlik sağlık raporu (yerini sanal işlem
bildirimleri ve günlük rapor aldı); Kraken/BTCTurk yedekleri (istenildiği için).

## Çalıştırma ve test
```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -t . -v       # testler
export NTFY_TOPIC=...                              # (isteğe bağlı) bildirim için
python bot.py --init --dry-run                     # kuru deneme: bildirim ve state yazmaz
python bot.py --init                               # ilk kurulum: sanal portföy oluşturur
python bot.py                                      # tek çalıştırma
python bot.py --loop                               # sürekli çalışma (kendi bilgisayar/VPS)
```
State yerelde `paper_data/paper_state.json` (git'e girmez). State yoksa `--init` olmadan bot **durur**.

## GitHub Actions'ta kalıcılık
State repoda `data` dalında `paper_state.json` olarak tutulur (ana dal kirlenmez). Her çalıştırma:
1. `scripts/prepare_data.sh` `data` dalını çeker. State hiç oluşmamışsa (dal yok ya da dalda state'in geçmişi yok)
   ilk kurulum için `--init` verilir. State bir zamanlar vardıysa ama kaybolduysa veya GitHub'a erişilemezse
   iş durur, portföy yanlışlıkla sıfırlanmaz.
2. Botu çalıştırır: state'i atomik yazar, **sonra** bildirimleri gönderir.
3. `scripts/save_state.sh`: `paper_state.json` varsa ve değiştiyse commit + push eder (çakışmada yeniden dener).
   State yokken (ör. Binance hatası) hiçbir şey commit edilmez; kilit dosyaları depoya girmez.
Aynı anda iki çalıştırma olmaması için `concurrency` grubu vardır. Mum işlenmişliği state içindeki
`last_candle_open_ms` ile izlenir; aynı mum iki kez işlenmez.
`data` dalı public repoda **herkese açıktır** (sanal portföy verisi; sır içermez).

## Eksik mum, sağlık takibi ve bekçi

- **Eksik mum:** Her çalışmada son işlenen mumla Binance'ten gelen mumlar arasında boşluk aranır.
  Boşluk varsa eksik mumlar `klines` (startTime/endTime) ile tamamlanır ve sırayla işlenir.
  Telafi isteği hata verirse çalışma güvenle durur (işlem yok, state ilerlemez, uyarı gider).
  Borsada o mumlar gerçekten yoksa boşluktaki mumlar için sinyal üretilmez; bot yalnızca boşluk
  sonrası ardışık (>= 60) mumla devam eder ve ntfy ile uyarır. Açık pozisyon varken boşlukta bir
  satış sinyali kaçmış olabilir. Boşluklar state'te `gaps` listesinde tutulur.
- **Sağlık:** state içinde `health`: çalışma sayısı, hata sayısı, ardışık hata, son başarılı çalışma,
  son işlenen mum, veri gecikmesi, son hata. Günlük raporda bir satır olarak görünür. 3+ ardışık
  hatadan sonra düzelince "toparlandı" mesajı gider.
- **Bekçi:** `watchdog.yml` saatte bir state'i okur; son başarılı çalışma 120 dakikadan eskiyse
  aynı ntfy kanalına acil öncelikli "BEKCI ALARM" gönderir (6 saatte bir tekrar). Sınır, repo
  değişkeni `PAPER_WATCHDOG_MAX_AGE_MIN` ile değiştirilir (en az 30). Bekçi de GitHub Actions'ta
  çalıştığı için GitHub'ın tamamı kesilirse o da susar; bunu yakalamak için bot dışında bir
  hizmet gerekir.
- **Test kapısı:** `bot.yml`, kodu çekmeden önce `Tests` workflow'unun BAŞARIYLA bitirdiği son
  `main` commit'ini seçer ve botu o sürümle çalıştırır. Testi başarısız bir commit devreye girmez,
  bot önceki sağlam sürümde kalır.

## Bilinen sınırlar / varsayımlar
- **Binance, GitHub'ın ABD sunucularından HTTP 451/403 ile engellenebilir.** Bu durumda bot güvenle bekler ve
  hata bildirimi gönderir. Çözüm seçenekleri: repo ayarlarında `BINANCE_BASE_URL` Variable'ı
  (ör. Binance'in resmi genel veri adresi `https://data-api.binance.vision`; test edilmedi) veya botu kendi
  bilgisayarında/VPS'te `--loop` ile çalıştırmak. Otomatik yedek borsa yoktur.
- GitHub `*/5` zamanlayıcısı pratikte 15–25 dk gecikebilir; bayat sinyaller atlanır. Hassas simülasyon için `--loop` daha uygundur.
- Slipaj sabit yüzdedir; gecikme, likidite ve emir defteri modellenmez. Komisyon oranları kullandığınız gerçek ücretlerle uyuşmayabilir.
- Başlangıç kurunun alındığı an ile ilk işlem arasındaki kur farkı raporlanır ama stratejiden ayrı tutulur.
- Test edilemeyenler: gerçek Binance bağlantısı ve gerçek ntfy gönderimi (geliştirme ortamında internet erişimi yoktu); ilk gerçek doğrulama `workflow_dispatch` ile `dry_run` seçilerek yapılmalıdır.
- Kaydedilen state commit'i push edilemezse o çalıştırmanın sonucu kaybolur ve sonraki çalıştırmada aynı mumlar yeniden işlenir
  (işlem mükerrer olmaz, ama bildirim tekrar gelebilir).
- Binance'in minimum emir tutarı ve miktar adımı (lot size) modellenmedi; sanal alım/satım her zaman tam dolar.
- Kısmi dolum yok: her işlem tüm nakitle alım / tüm BTC ile satım.
