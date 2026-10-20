"""Ayarlar. Tüm maliyet varsayımları buradan (veya PAPER_* ortam değişkenlerinden)
değiştirilebilir. Varsayılanlar VARSAYIMDIR, gerçek borsa ücretleri değildir."""
import math
import os
from dataclasses import dataclass, replace


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    symbol: str = "BTCTRY"          # Binance Spot paritesi (BTC/TRY)
    fx_symbol: str = "USDTTRY"      # başlangıç bakiyesi dönüşümü için
    interval: str = "5m"
    interval_ms: int = 300_000
    initial_usdt: float = 1000.0
    fee_buy: float = 0.001          # %0,10 (varsayım)
    fee_sell: float = 0.001         # %0,10 (varsayım)
    slippage_buy: float = 0.0005    # %0,05 aleyhte (varsayım)
    slippage_sell: float = 0.0005   # %0,05 aleyhte (varsayım)
    klines_limit: int = 300
    min_candles: int = 60           # SMA50 + RSI için gereken sürekli veri
    max_signal_age_s: int = 1800    # bundan eski sinyalde işlem açılmaz (varsayım)
    stale_data_s: int = 900         # son kapanmış mum bundan eskiyse veri "bayat"
    report_hour: int = 21           # günlük rapor saati, TSİ (varsayım)
    tz: str = "Europe/Istanbul"
    error_notify_interval_h: float = 6.0
    base_url: str = "https://api.binance.com"

    def validate(self):
        for name in ("fee_buy", "fee_sell", "slippage_buy", "slippage_sell"):
            v = getattr(self, name)
            if not (isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v < 0.05):
                raise ConfigError(f"{name} 0 ile 0.05 arasında olmalı: {v!r}")
        if not (math.isfinite(self.initial_usdt) and self.initial_usdt > 0):
            raise ConfigError("initial_usdt > 0 olmalı")
        if not (0 <= self.report_hour <= 23):
            raise ConfigError("report_hour 0-23 olmalı")
        if self.interval_ms <= 0 or self.min_candles < 51:
            raise ConfigError("interval_ms > 0 ve min_candles >= 51 olmalı")
        if not self.base_url.startswith("https://"):
            raise ConfigError("base_url https:// ile başlamalı")
        return self


_FLOATS = {
    "PAPER_FEE_BUY": "fee_buy",
    "PAPER_FEE_SELL": "fee_sell",
    "PAPER_SLIPPAGE_BUY": "slippage_buy",
    "PAPER_SLIPPAGE_SELL": "slippage_sell",
    "PAPER_INITIAL_USDT": "initial_usdt",
    "PAPER_ERROR_NOTIFY_HOURS": "error_notify_interval_h",
}
_INTS = {
    "PAPER_REPORT_HOUR": "report_hour",
    "PAPER_MAX_SIGNAL_AGE_S": "max_signal_age_s",
}


def from_env(env=None):
    env = os.environ if env is None else env
    changes = {}
    try:
        for key, field in _FLOATS.items():
            if env.get(key, "").strip():
                changes[field] = float(env[key])
        for key, field in _INTS.items():
            if env.get(key, "").strip():
                changes[field] = int(env[key])
    except ValueError as e:
        raise ConfigError(f"Geçersiz ortam değişkeni değeri: {e}")
    base = env.get("BINANCE_BASE_URL", "").strip().rstrip("/")
    if base:
        changes["base_url"] = base
    return replace(Config(), **changes).validate()
