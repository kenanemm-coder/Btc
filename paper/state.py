"""Kalıcı state: atomik JSON yazma, doğrulama, tek örnek kilidi.

Bozuk / beklenmeyen sürümdeki state ASLA sessizce sıfırlanmaz: StateError atılır.
State dosyası hiç yoksa da otomatik oluşturulmaz (yalnızca açık --init ile).
"""
import json
import math
import os
import tempfile

from . import MODE, STATE_VERSION

try:  # Linux/macOS
    import fcntl
except ImportError:  # pragma: no cover (Windows)
    fcntl = None


class StateError(Exception):
    pass


class StateMissing(StateError):
    pass


class LockError(Exception):
    pass


_REQUIRED = {
    "version": int, "mode": str, "symbol": str, "initial": dict, "cash_try": (int, float),
    "btc": (int, float), "trades": list, "realized_pnl_try": (int, float),
    "last_candle_open_ms": int, "last_price": (int, float), "peak_equity_try": (int, float),
    "max_drawdown_pct": (int, float), "daily_equity": list, "last_report_date": str,
    "outbox": list, "next_outbox_id": int,
}


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_state(state, cfg):
    if not isinstance(state, dict):
        raise StateError("State bir JSON nesnesi değil")
    if state.get("version") != STATE_VERSION:
        raise StateError(f"Beklenmeyen state sürümü: {state.get('version')!r} (beklenen {STATE_VERSION})")
    for key, typ in _REQUIRED.items():
        if key not in state or not isinstance(state[key], typ) or isinstance(state[key], bool):
            raise StateError(f"State alanı eksik/geçersiz: {key}")
    if "position" not in state:
        raise StateError("State alanı eksik: position")
    if state["mode"] != MODE:
        raise StateError(f"State modu {state['mode']!r}, beklenen {MODE!r}")
    if state["symbol"] != cfg.symbol:
        raise StateError(f"State paritesi {state['symbol']!r} ≠ ayar {cfg.symbol!r}")
    ini = state["initial"]
    for k in ("usdt", "usdt_try_rate", "try"):
        if not _finite(ini.get(k)) or ini[k] <= 0:
            raise StateError(f"State başlangıç alanı geçersiz: initial.{k}")
    for k in ("cash_try", "btc", "realized_pnl_try", "last_price", "peak_equity_try",
              "max_drawdown_pct"):
        if not _finite(state[k]):
            raise StateError(f"State sayısı geçersiz: {k}")
    if state["cash_try"] < 0 or state["btc"] < 0 or state["last_price"] <= 0:
        raise StateError("State bakiyeleri geçersiz (negatif)")
    if "health" in state and not isinstance(state["health"], dict):
        raise StateError("State alanı geçersiz: health")
    if "gaps" in state and not isinstance(state["gaps"], list):
        raise StateError("State alanı geçersiz: gaps")
    pos = state["position"]
    if pos is None:
        if state["btc"] != 0:
            raise StateError("Pozisyon yok ama BTC miktarı sıfır değil")
    else:
        if not isinstance(pos, dict) or not _finite(pos.get("qty")) or pos["qty"] <= 0:
            raise StateError("Açık pozisyon kaydı geçersiz")
        if abs(pos["qty"] - state["btc"]) > 1e-12 * max(1.0, pos["qty"]):
            raise StateError("Pozisyon miktarı ile BTC bakiyesi tutarsız")
    return state


def load_state(path, cfg):
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        raise StateMissing(f"State dosyası yok: {path}")
    except OSError as e:
        raise StateError(f"State okunamadı: {type(e).__name__}")
    try:
        state = json.loads(raw)
    except ValueError:
        raise StateError("State dosyası bozuk (JSON okunamadı)")
    return validate_state(state, cfg)


def save_state(path, state):
    """Atomik yazma: geçici dosyaya yaz, fsync, os.replace. NaN/Inf reddedilir."""
    data = json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False)
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class FileLock:
    """Aynı makinede tek örnek garantisi (flock). GitHub Actions'ta ayrıca
    workflow `concurrency` grubu kullanılır."""

    def __init__(self, path):
        self.path = path
        self._fh = None

    def __enter__(self):
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        self._fh = open(self.path, "a+")
        if fcntl is not None:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self._fh.close()
                self._fh = None
                raise LockError("Başka bir bot örneği çalışıyor (kilit alınamadı)")
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            if fcntl is not None:
                try:
                    fcntl.flock(self._fh, fcntl.LOCK_UN)
                except OSError:
                    pass
            self._fh.close()
            self._fh = None
        return False
