"""Çalışma sağlığı kaydı (state içinde 'health'). Bekçi (watchdog) bunu okur.

Zamanlar duvar saati (ms, UTC) ile tutulur; veri gecikmesi borsa saatine göre hesaplanır.
"""

FIELDS = {
    "runs": 0,
    "failures": 0,
    "consecutive_failures": 0,
    "max_consecutive_failures": 0,
    "last_run_ms": None,
    "last_success_ms": None,
    "last_candle_open_ms": None,
    "data_lag_s": None,
    "last_error": None,
    "last_error_ms": None,
}


def ensure(state):
    """Eski state'lere eksik sağlık alanlarını ekler (mevcut değerleri bozmaz)."""
    h = state.get("health")
    if not isinstance(h, dict):
        h = {}
    for k, v in FIELDS.items():
        h.setdefault(k, v)
    state["health"] = h
    if not isinstance(state.get("gaps"), list):
        state["gaps"] = []
    return h


def record_success(state, wall_ms, server_ms, last_candle_open_ms, interval_ms):
    """Başarılı çalışma. Dönüş: bu çalışmadan önceki ardışık hata sayısı."""
    h = ensure(state)
    prev = h["consecutive_failures"]
    h["runs"] += 1
    h["last_run_ms"] = wall_ms
    h["last_success_ms"] = wall_ms
    h["consecutive_failures"] = 0
    h["last_candle_open_ms"] = last_candle_open_ms
    h["data_lag_s"] = max(0.0, (server_ms - (last_candle_open_ms + interval_ms)) / 1000.0)
    return prev


def record_failure(state, wall_ms, text):
    h = ensure(state)
    h["runs"] += 1
    h["failures"] += 1
    h["consecutive_failures"] += 1
    h["max_consecutive_failures"] = max(h["max_consecutive_failures"], h["consecutive_failures"])
    h["last_run_ms"] = wall_ms
    h["last_error"] = str(text)[:200]
    h["last_error_ms"] = wall_ms
    return h["consecutive_failures"]


def last_good_ms(state, interval_ms):
    """Son 'iyi' an: başarılı çalışma; yoksa son işlenen mumun kapanışı (eski state)."""
    h = state.get("health") or {}
    if isinstance(h.get("last_success_ms"), int):
        return h["last_success_ms"]
    c = state.get("last_candle_open_ms")
    return c + interval_ms if isinstance(c, int) else None
