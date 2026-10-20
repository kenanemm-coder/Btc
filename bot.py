"""BTC/TRY PAPER (sanal) işlem botu — giriş noktası.

Gerçek emir GÖNDERİLMEZ. Veri: yalnızca Binance Spot genel piyasa verisi.
Ayrıntı: PAPER_TRADING.md   |   Mantık: paper/ klasörü
Eski sinyal stratejisi (Bollinger + RSI + SMA50) paper/indicators.py içinde aynen korunmuştur.

Kullanım:
  python bot.py --init --dry-run       # kuru deneme (bildirim/state yok)
  python bot.py --init                 # ilk kurulum (sanal portföy oluşturur)
  python bot.py                        # tek çalıştırma (GitHub Actions bunu çağırır)
  python bot.py --loop                 # sürekli çalışma (kendi bilgisayarında/VPS)
"""
import sys

from paper.runner import main

if __name__ == "__main__":
    sys.exit(main())
