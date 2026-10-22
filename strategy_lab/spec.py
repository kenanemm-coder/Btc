"""ÖN KAYIT: bu dosya, herhangi bir sonuç görülmeden ÖNCE commit edilir.

Sonuçlara bakıp bu değerleri değiştirmek araştırmayı geçersiz kılar; değişiklik gerekirse
yeni bir ön kayıt commit'i ve raporda açıklaması gerekir.
"""

SYMBOL = "BTCTRY"
BASE_URL_DEFAULT = "https://data-api.binance.vision"  # anahtarsız genel piyasa verisi

# --- Veri dönemi -----------------------------------------------------------------
# Isınma (SMA200, 90 günlük rejim için) için veri 2020-10-01'den çekilir; işlem/değerlendirme
# 2021-01-01'den başlar. Bitiş: indirme anındaki sunucu saati, 4 saatlik sınıra yuvarlanır ve manifestte sabitlenir.
FETCH_START = "2020-10-01"
EVAL_START = "2021-01-01"

# Kronolojik bölme (değerlendirme dönemi süresine göre): ilk %60 eğitim, sonraki %20 doğrulama, son %20 TEST.
SPLIT = (0.60, 0.20, 0.20)

# --- Maliyet ve dolum modeli ------------------------------------------------------
# Sinyal, KAPANMIŞ mumda üretilir; dolum en erken BİR SONRAKİ mumun AÇILIŞINDA olur.
COSTS = {
    "temel":      {"fee_buy": 0.0010, "fee_sell": 0.0010, "slip_buy": 0.0005, "slip_sell": 0.0005, "delay_bars": 0},
    "yuksek":     {"fee_buy": 0.0015, "fee_sell": 0.0015, "slip_buy": 0.0010, "slip_sell": 0.0010, "delay_bars": 0},
    "cok_yuksek": {"fee_buy": 0.0020, "fee_sell": 0.0020, "slip_buy": 0.0020, "slip_sell": 0.0020, "delay_bars": 0},
    "gecikmeli":  {"fee_buy": 0.0010, "fee_sell": 0.0010, "slip_buy": 0.0005, "slip_sell": 0.0005, "delay_bars": 1},
}
# Stop muhafazakâr: mum içi sıralama bilinmediği için bir mumun düşüğü stop seviyesine
# değdiyse stop o mumda tetiklenmiş sayılır (giriş mumu dahil); dolum = min(stop, mum açılışı),
# ardından aleyhte slipaj ve komisyon. Takip eden stop seviyesi yalnızca KAPANMIŞ mumlarla güncellenir.

# --- Adaylar ---------------------------------------------------------------------
TIMEFRAMES = ("1h", "4h")
ATR_PERIOD = 14
TREND_SMA = 200

# B: Donchian kırılımı + SMA200 trend filtresi + ATR stopu.
#   Giriş: kapanış > önceki N mumun en yüksek 'high'ı VE kapanış > SMA200.
#   Başlangıç stopu: giriş açılışı − k·ATR (ATR sinyal mumunda).
#   Çıkış 'chan10': kapanış < önceki 10 mumun en düşük 'low'ı.
#   Çıkış 'trail': takip eden stop = (girişten beri en yüksek kapanış) − k·ATR.
B_GRID = {"entry_n": (20, 40), "atr_k": (2.0, 3.0), "exit": ("chan10", "trail")}
# C: EMA kesişimi. Giriş: hızlı EMA yavaşı yukarı keser. Çıkış: hızlı EMA yavaşın altına iner. Stop yok.
C_GRID = {"pair": ((50, 200), (25, 100))}
# D: momentum. Giriş: a mumluk VE b mumluk getiri > 0. Çıkış: a mumluk getiri <= 0. Stop yok.
D_GRID = {"pair": ((20, 50), (10, 30))}
# A (referans): mevcut Bollinger+RSI+SMA stratejisi, 5 dk mum, aynı yeni dolum modeliyle.
# E (kontrol): basit al-tut (değerlendirme başında alış, sonda değerleme), aynı maliyetle.

# --- Seçim kuralı (yalnızca eğitim+doğrulama verisiyle) -----------------------------
# Aile içinde (zaman dilimi dahil) en yüksek SKOR seçilir. SKOR = min(Sharpe_eğitim, Sharpe_doğrulama),
# temel maliyet. Eğitim+doğrulamada < MIN_TRADES işlemi olan ayar uygun değildir.
MIN_TRADES = 20
ANNUALIZATION_DAYS = 365          # günlük getiri, risksiz oran = 0
# Walk-forward: ilk 730 gün ısınma; sonra 180 günlük ileri pencereler (yalnızca eğitim+doğrulama içinde).
WF_FIRST_DAYS = 730
WF_STEP_DAYS = 180

# --- Rejim (stratejiden bağımsız, yalnızca geçmişe bakar) ---------------------------
REGIME_LOOKBACK_DAYS = 90
REGIME_UP, REGIME_DOWN = 0.15, -0.15

# --- Kabul kapıları: seçilen (dondurulmuş) aday için, TEST döneminde, TÜMÜ gerekir ------
GATES = {
    "G1": "Temel maliyette TEST net getirisi > 0",
    "G2": "Yüksek maliyet senaryosunda TEST net getirisi > -%10 (çökmemeli)",
    "G3": "Gecikmeli dolum senaryosunda TEST net getirisi > 0",
    "G4": "TEST'te en az 10 işlem ve en iyi tek işlem çıkarılınca net getiri > 0",
    "G5": "TEST'i 4 eşit zamana bölünce en az 2 parça pozitif",
    "G6": "Aynı aile+zaman diliminde ön kayıtlı ayarların en az %50'si TEST'te temel maliyette pozitif",
    "G7": "Walk-forward ileri-örneklem (eğitim+doğrulama) bileşik getirisi > 0",
    "G8": "Al-tuta göre TEST'te Sharpe VEYA maksimum düşüş avantajı (düşüş <= al-tutun yarısı ve net getiri > 0)",
}
# Hiçbir aday tüm kapıları geçmezse: "şimdilik doğrulanmış strateji yok".
