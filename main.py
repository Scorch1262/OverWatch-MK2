#!/usr/bin/env python3
"""
OverWatchMK2 – Online-Luftraum-/Ortungsanzeige + SMS-Tracker
=============================================================

Abgewandeltes Nachfolgeprojekt von OverWatchMK1. Zeigt AUSSCHLIESSLICH
Online-Daten an -- jede Form von lokaler Funk-/Sensor-Ortung (Drohnen-
Remote-ID per WLAN/Bluetooth, lokaler ADS-B-Empfang per RTL-SDR, lokaler
FLARM-Empfang per zweitem RTL-SDR, lokaler C-ITS-Empfang per ESP32-Board)
wurde bewusst entfernt, siehe CHANGELOG.md [1.0.0] für die vollständige
Begründung. Als reiner, plattformübergreifender Desktop-Client (Windows-
.exe / macOS-.app, siehe OverWatchMK2.spec + .github/workflows/build.yml)
konzipiert, der weiterhin dieselbe im Netzwerk erreichbare Weboberfläche
startet wie das Vorgängerprojekt.

Neu in OverWatchMK2: SMS-Ortungsempfang über ein per USB-UART
angeschlossenes GSM-Modem (SIM800/900/7600-kompatibel), siehe
sms_gateway.py.
"""

OVERWATCH_VERSION = "1.0.0"
OVERWATCH_BUILD_NOTE = "initial-release-online-only-plus-sms-tracking"

import sys, os, json, time, math, socket, logging, threading, sqlite3
import argparse, platform, traceback, requests, uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from logging.handlers import RotatingFileHandler

# ── Absturz-Handler ─────────────────────────────────────────────
def _external_dir() -> str:
    """Liefert den Ordner, in dem der Nutzer 'die exe' erwartet:
    - Entwicklung (python main.py):            Ordner von main.py
    - Windows-EXE (PyInstaller onefile):        Ordner der .exe
    - macOS-.app (PyInstaller BUNDLE):          Ordner, der die .app
                                                 ENTHÄLT (nicht die
                                                 .app selbst -- die ist
                                                 ein Bundle, in das
                                                 Nutzer nicht hinein-
                                                 schauen), analog zu
                                                 "neben der exe" unter
                                                 Windows.
    Wird für config.yaml, Logs, offline_map/, sms_*.json etc. genutzt --
    NIEMALS für gebündelte Ressourcen (templates/static), dafür siehe
    resource_path().
    """
    if getattr(sys, "frozen", False):
        exe_path = os.path.abspath(sys.executable)
        if sys.platform == "darwin" and ".app/Contents/MacOS/" in exe_path:
            # .../Irgendwo/OverWatchMK2.app/Contents/MacOS/OverWatchMK2
            # -> drei Ebenen hoch = "Irgendwo" (Ordner NEBEN der .app)
            app_macos_dir = os.path.dirname(exe_path)
            contents_dir = os.path.dirname(app_macos_dir)
            app_bundle_dir = os.path.dirname(contents_dir)
            return os.path.dirname(app_bundle_dir)
        return os.path.dirname(exe_path)
    return os.path.dirname(os.path.abspath(__file__))


def _crash_handler(exc_type, exc_value, exc_tb):
    _crash_path = os.path.join(_external_dir(), "overwatchmk2_crash.log")
    _msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        with open(_crash_path, "w", encoding="utf-8") as f:
            f.write(f"=== OverWatchMK2 Crash {datetime.now()} ===\n\n{_msg}")
    except Exception:
        pass
    print("\n" + "=" * 60)
    print("FEHLER – OverWatchMK2 konnte nicht starten:")
    print("=" * 60)
    print(_msg)
    print(f"\nCrash-Log: {_crash_path}")
    print("\nDrücke ENTER zum Beenden...")
    try:
        input()
    except Exception:
        pass


sys.excepthook = _crash_handler


# ── PyInstaller Ressourcenpfad (NUR für gebündelte templates/static) ─
def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, rel)


def external_path(rel: str) -> str:
    """Pfad für Dateien, die NEBEN der exe/.app liegen sollen (config,
    Logs, persistente SMS-/Kartenmarkierungsdaten, offline_map/)."""
    return os.path.join(_external_dir(), rel)


# ── Startdiagnose ────────────────────────────────────────────────
def _print_startup_diagnostics():
    frozen = getattr(sys, "frozen", False)
    meipass = getattr(sys, "_MEIPASS", None)
    print(f"[DIAG] OverWatchMK2 Version: {OVERWATCH_VERSION} ({OVERWATCH_BUILD_NOTE})")
    print(f"[DIAG] Frozen:      {frozen}")
    print(f"[DIAG] _MEIPASS:    {meipass}")
    print(f"[DIAG] CWD:         {os.getcwd()}")
    print(f"[DIAG] Externer Ordner (config/Logs/offline_map): {_external_dir()}")
    print(f"[DIAG] Python:      {sys.version.split()[0]}")
    print(f"[DIAG] Platform:    {sys.platform}")
    sys.stdout.flush()


# ── Config ───────────────────────────────────────────────────────
_CFG_LOADED_FROM = None
_DEFAULT_CFG = {
    "server": {"host": "0.0.0.0", "port": 8080, "secret_key": "overwatchmk2-default"},
    "adsb": {"enabled": True, "provider": "adsbfi",
             "api_url": "https://opensky-network.org/api/states/all",
             "radius_deg": 0.5, "radius_nm": 30, "poll_interval_sec": 15,
             "opensky": {"username": "", "password": ""}},
    "flarm": {"enabled": True, "internet_call": "OVERWATCH2",
              "internet_radius_km": 50, "station_timeout_sec": 120},
    "starlink": {"enabled": True,
                 "tle_url": "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle",
                 "tle_refresh_hours": 6, "position_update_sec": 5,
                 "max_displayed": 300, "viewport_padding_deg": 5.0,
                 "spacetrack": {"username": "", "password": ""}},
    "sms": {"enabled": False, "port": "", "baudrate": 9600,
            "poll_interval_sec": 5, "delete_after_read": True, "pin": ""},
    "map": {"default_lat": 51.1657, "default_lon": 10.4515, "default_zoom": 8,
            "adsb_timeout_sec": 60,
            "tile_url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            "satellite_url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"},
    "offline_map": {"enabled": True},
    "noflyzones": {"enabled": True, "wms_url": "https://uas-betrieb.de/geoservices/dipul/wms",
                   "wms_version": "1.3.0",
                   "layers": "dipul:flugplaetze,dipul:flughaefen,dipul:modellflugplaetze,dipul:temporaere_betriebseinschraenkungen",
                   "attribution": "Quelle Geodaten: DFS, BKG"},
    "logging": {"level": "INFO", "file": "overwatchmk2.log", "max_size_mb": 10, "backup_count": 3},
}

try:
    import yaml
    # Externe config.yaml (neben exe/.app) hat IMMER Vorrang vor der beim
    # Build mitgelieferten Version -- sonst könnte der Nutzer nie eigene
    # Einstellungen (ADS-B-Provider, SMS-Port, ...) dauerhaft ändern, ohne
    # dass ein Update sie stillschweigend wieder verwirft.
    _cfg_candidates = [
        external_path("config.yaml"),
        os.path.abspath("config.yaml"),
        resource_path("config.yaml"),
    ]
    CFG = None
    for _p in _cfg_candidates:
        if os.path.exists(_p):
            with open(_p, encoding="utf-8") as f:
                CFG = yaml.safe_load(f)
            _CFG_LOADED_FROM = os.path.abspath(_p)
            print(f"[DIAG] Config geladen: {_CFG_LOADED_FROM}")
            break
    if CFG is None:
        raise FileNotFoundError(f"config.yaml nicht gefunden. Gesuchte Pfade: {_cfg_candidates}")

    # Fehlende Top-Level-Abschnitte einer älteren config.yaml ergänzen
    # (analog zu deploy/migrate_config.py bei OverWatchMK1) -- verhindert
    # KeyErrors, falls der Nutzer eine config.yaml aus einer älteren
    # OverWatchMK2-Version weiterverwendet, in der neue Abschnitte
    # (z.B. 'sms') noch fehlen.
    def _deep_merge_defaults(cfg, defaults):
        for k, v in defaults.items():
            if k not in cfg:
                cfg[k] = v
            elif isinstance(v, dict) and isinstance(cfg.get(k), dict):
                _deep_merge_defaults(cfg[k], v)
    _deep_merge_defaults(CFG, _DEFAULT_CFG)
except Exception as e:
    print(f"WARNUNG: config.yaml nicht geladen ({e}), nutze Standardwerte.")
    _CFG_LOADED_FROM = f"FALLBACK (config.yaml nicht gefunden: {e})"
    CFG = json.loads(json.dumps(_DEFAULT_CFG))  # tiefe Kopie


# ── Logging ──────────────────────────────────────────────────────
def setup_logging():
    level = getattr(logging, CFG["logging"].get("level", "INFO"), logging.INFO)
    handlers = [logging.StreamHandler()]
    logfile = CFG["logging"].get("file")
    if logfile:
        try:
            handlers.append(RotatingFileHandler(
                external_path(logfile),
                maxBytes=CFG["logging"].get("max_size_mb", 10) * 1024 * 1024,
                backupCount=CFG["logging"].get("backup_count", 3), encoding="utf-8"))
        except Exception:
            pass
    logging.basicConfig(level=level,
        format="%(asctime)s [%(levelname)-8s] %(name)s – %(message)s",
        datefmt="%H:%M:%S", handlers=handlers)


setup_logging()
log = logging.getLogger("OverWatchMK2")

# ── FLARM/OGN (Internet-Netzwerk) ─────────────────────────────────
import ogn_receiver
from ogn_receiver import OGNInternetClient
ogn_receiver.set_logger(log)

# ── SMS/GSM-Ortungsempfang ─────────────────────────────────────────
import sms_gateway
from sms_gateway import SMSReceiver, SMSFormatStore, SMSTrackStore, SMSInboxLog, \
    try_parse_coordinates, custom_pattern_from_regex, COORD_PATTERNS
sms_gateway.set_logger(log)

# ── Flask / SocketIO ─────────────────────────────────────────────
try:
    from flask import Flask, render_template, jsonify, request, make_response
    from flask_socketio import SocketIO, emit
    import engineio.async_drivers.threading as _eat  # noqa – wichtig für PyInstaller
except ImportError as e:
    print(f"FEHLER: {e}")
    print("Bitte: pip install flask flask-socketio")
    input("ENTER zum Beenden...")
    sys.exit(1)


# ════════════════════════════════════════════════════════════════
# ADS-B (nur Internet-Anbieter -- kein lokaler RTL-SDR-Empfang mehr)
# ════════════════════════════════════════════════════════════════

class ADSBReceiver(threading.Thread):
    """
    Holt öffentliche ADS-B Daten. Unterstützt mehrere Anbieter:

      opensky        – OpenSky Network (anonym stark rate-limitiert)
      adsbfi         – adsb.fi (community, kein Account nötig)
      airplaneslive  – airplanes.live (community, kein Account nötig)
      adsblol        – adsb.lol (community, kein Account nötig)

    Diese drei community-Anbieter sind aus genau diesem Grund entstanden:
    OpenSky hat die anonyme Nutzung seiner API stark eingeschränkt.
    Achtung: Diese Drittanbieter-Dienste werden nicht von uns betrieben,
    ihre Verfügbarkeit/Rate-Limits können sich jederzeit ändern.
    """

    _RADIUS_PROVIDERS = {"adsbfi", "airplaneslive", "adsblol"}
    _PROVIDER_URLS = {
        "adsbfi":        "https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{radius_nm}",
        "airplaneslive": "https://api.airplanes.live/v2/point/{lat}/{lon}/{radius_nm}",
        "adsblol":       "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius_nm}",
    }

    def __init__(self):
        super().__init__(daemon=True, name="ADSBReceiver")
        self.running = False
        self._data = []
        self._lock = threading.Lock()
        self._center_lat = CFG["map"]["default_lat"]
        self._center_lon = CFG["map"]["default_lon"]
        self.timeout = CFG["map"].get("adsb_timeout_sec", 60)
        self._radius_deg = CFG["adsb"].get("radius_deg", 0.5)
        self._radius_nm = CFG["adsb"].get("radius_nm", 30)
        self._bbox = None
        self._refresh_requested = threading.Event()
        self.provider = CFG["adsb"].get("provider", "adsbfi").lower()
        self._backoff_until = 0
        self._consecutive_failures = 0
        self.last_success_ts = None
        self.last_error = None
        self.last_attempt_ts = None

    @staticmethod
    def _safe_float(val, default=0.0):
        if val is None:
            return default
        if isinstance(val, (int, float)):
            return float(val)
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def _normalize_dump1090(self, aircraft, source_label):
        out = []
        skipped = 0
        for a in aircraft:
            try:
                lat = a.get("lat"); lon = a.get("lon")
                if lat is None or lon is None:
                    continue
                lat = self._safe_float(lat, None)
                lon = self._safe_float(lon, None)
                if lat is None or lon is None:
                    continue
                alt_raw = self._safe_float(a.get("alt_baro") or a.get("altitude"), 0.0)
                spd_ms = self._safe_float(a.get("gs") or a.get("speed"), 0.0)
                heading = self._safe_float(a.get("track") or a.get("heading"), None)
                vert_rate = self._safe_float(a.get("baro_rate") or a.get("vert_rate"), None)
                out.append({"icao": str(a.get("hex", "")).upper(),
                            "callsign": str(a.get("flight", "") or "").strip(),
                            "lat": lat, "lon": lon,
                            "alt_ft": alt_raw or None,
                            "alt_m": round(alt_raw * 0.3048, 0),
                            "speed_kts": round(spd_ms, 0),
                            "speed_kmh": round(spd_ms * 1.852, 0),
                            "heading": heading, "vert_rate": vert_rate,
                            "squawk": str(a.get("squawk", "") or ""),
                            "on_ground": a.get("mlat", []) != [],
                            "category": str(a.get("category", "") or "").upper() or None,
                            "source": source_label, "last_seen": time.time()})
            except Exception as e:
                skipped += 1
                log.debug("Flugzeug übersprungen (unerwartetes Format): %s", e)
        if skipped:
            log.warning("%s: %d von %d Flugzeugen wegen unerwartetem Datenformat übersprungen.",
                       source_label, skipped, len(aircraft))
        return out

    _OPENSKY_CATEGORY_MAP = {
        2: "A1", 3: "A2", 4: "A3", 5: "A4", 6: "A5", 7: "A6", 8: "A7",
        9: "B1", 10: "B2", 11: "B3", 12: "B4", 14: "B6", 15: "B7",
        16: "C1", 17: "C2", 18: "C3", 19: "C4", 20: "C5",
    }

    def _normalize_opensky(self, states):
        out = []
        skipped = 0
        for s in states:
            try:
                if len(s) < 17:
                    continue
                lat = s[6]; lon = s[5]
                if lat is None or lon is None:
                    continue
                lat = self._safe_float(lat, None)
                lon = self._safe_float(lon, None)
                if lat is None or lon is None:
                    continue
                alt_m = self._safe_float(s[7] if s[7] is not None else s[13], 0.0)
                spd_ms = self._safe_float(s[9], 0.0)
                category = None
                if len(s) > 17 and s[17] is not None:
                    try:
                        category = self._OPENSKY_CATEGORY_MAP.get(int(s[17]))
                    except (TypeError, ValueError):
                        category = None
                out.append({"icao": str(s[0] or "").upper().strip(),
                            "callsign": str(s[1] or "").strip(),
                            "lat": lat, "lon": lon,
                            "alt_ft": round(alt_m * 3.28084, 0),
                            "alt_m": round(alt_m, 0),
                            "speed_kts": round(spd_ms * 1.944, 0),
                            "speed_kmh": round(spd_ms * 3.6, 0),
                            "heading": self._safe_float(s[10], None),
                            "vert_rate": self._safe_float(s[11], None),
                            "squawk": str(s[14] or ""), "on_ground": s[8],
                            "category": category, "source": "OpenSky Network",
                            "last_seen": time.time()})
            except Exception as e:
                skipped += 1
                log.debug("OpenSky State-Vektor übersprungen: %s", e)
        if skipped:
            log.warning("OpenSky: %d von %d Flugzeugen übersprungen.", skipped, len(states))
        return out

    def _fetch_opensky(self):
        cfg = CFG["adsb"]
        with self._lock:
            bbox = dict(self._bbox) if self._bbox else None
            r_deg = self._radius_deg
            la, lo = self._center_lat, self._center_lon
        params = bbox if bbox else {"lamin": la - r_deg, "lamax": la + r_deg,
                                     "lomin": lo - r_deg, "lomax": lo + r_deg}
        osuser = cfg.get("opensky", {}).get("username", "")
        ospw = cfg.get("opensky", {}).get("password", "")
        auth = (osuser, ospw) if osuser else None
        try:
            r = requests.get(cfg["api_url"], params=params, auth=auth, timeout=10)
            if r.status_code == 429:
                msg = ("OpenSky Rate-Limit erreicht (HTTP 429). Alternativen ohne Account: "
                      "adsb.provider auf 'adsbfi', 'airplaneslive' oder 'adsblol' setzen.")
                log.warning(msg); self.last_error = msg; self._register_failure(); return None
            if r.status_code != 200:
                msg = f"OpenSky HTTP {r.status_code}. Body: {r.text[:200]!r}"
                log.warning(msg); self.last_error = msg; self._register_failure(); return None
            j = r.json()
            states = j.get("states")
            if states is None:
                msg = "OpenSky Antwort enthält kein 'states' Feld."
                log.warning(msg); self.last_error = msg; self._register_failure(); return None
            result = self._normalize_opensky(states)
            self._register_success()
            return result
        except requests.exceptions.Timeout:
            self.last_error = "OpenSky Anfrage: Timeout nach 10s."; log.warning(self.last_error)
            self._register_failure(); return None
        except requests.exceptions.ConnectionError as e:
            self.last_error = f"OpenSky Verbindungsfehler ({e})."; log.warning(self.last_error)
            self._register_failure(); return None
        except Exception as e:
            self.last_error = f"OpenSky API Fehler ({type(e).__name__}): {e}"
            log.warning(self.last_error); self._register_failure(); return None

    def _fetch_community_provider(self):
        cfg = CFG["adsb"]
        with self._lock:
            r_nm = self._radius_nm
            la, lo = self._center_lat, self._center_lon
        url_template = self._PROVIDER_URLS.get(self.provider)
        if not url_template:
            self.last_error = f"Unbekannter ADS-B Provider '{self.provider}'."
            log.error(self.last_error); return None
        r_nm = min(max(r_nm, 1), 250)
        url = url_template.format(lat=la, lon=lo, radius_nm=r_nm)
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "OverWatchMK2/1.0"})
            if r.status_code == 429:
                self.last_error = f"{self.provider}: Rate-Limit erreicht (HTTP 429)."
                log.warning(self.last_error); self._register_failure(); return None
            if r.status_code != 200:
                self.last_error = f"{self.provider}: HTTP {r.status_code}. Body: {r.text[:200]!r}"
                log.warning(self.last_error); self._register_failure(); return None
            j = r.json()
            aircraft = None
            for key in ("aircraft", "ac"):
                if isinstance(j, dict) and key in j:
                    aircraft = j[key]; break
            if aircraft is None:
                self.last_error = f"{self.provider}: unerwartetes Antwortformat."
                log.warning(self.last_error); self._register_failure(); return None
            label = {"adsbfi": "adsb.fi", "airplaneslive": "airplanes.live",
                     "adsblol": "adsb.lol"}.get(self.provider, self.provider)
            result = self._normalize_dump1090(aircraft, source_label=label)
            self._register_success()
            return result
        except requests.exceptions.Timeout:
            self.last_error = f"{self.provider}: Timeout nach 10s."; log.warning(self.last_error)
            self._register_failure(); return None
        except requests.exceptions.ConnectionError as e:
            self.last_error = f"{self.provider}: Verbindungsfehler ({e})."; log.warning(self.last_error)
            self._register_failure(); return None
        except Exception as e:
            self.last_error = f"{self.provider} API Fehler ({type(e).__name__}): {e}"
            log.warning(self.last_error); self._register_failure(); return None

    def _register_success(self):
        self.last_success_ts = time.time(); self.last_error = None
        self._consecutive_failures = 0; self._backoff_until = 0

    def _register_failure(self):
        self._consecutive_failures += 1
        wait = min(10 * (2 ** (self._consecutive_failures - 1)), 300)
        self._backoff_until = time.time() + wait
        if self._consecutive_failures >= 2:
            log.info("ADS-B: %d Fehler in Folge – nächster Versuch in %ds.",
                     self._consecutive_failures, wait)

    def update_viewport(self, lat, lon, radius_km=None, bbox=None):
        with self._lock:
            self._center_lat = lat; self._center_lon = lon
            if radius_km is not None:
                self._radius_deg = max(min(radius_km / 111.32, 20), 0.05)
                self._radius_nm = max(min(radius_km / 1.852, 250), 1)
            self._bbox = bbox
        self._refresh_requested.set()

    def get_all(self):
        cutoff = time.time() - self.timeout
        with self._lock:
            return [a for a in self._data if a.get("last_seen", 0) > cutoff]

    def run(self):
        self.running = True
        interval = CFG["adsb"].get("poll_interval_sec", 15)
        log.info("ADS-B Receiver gestartet (Provider: %s, Intervall: %ds)", self.provider, interval)
        while self.running:
            self.last_attempt_ts = time.time()
            if time.time() < self._backoff_until:
                self._refresh_requested.wait(timeout=min(interval, 2))
                self._refresh_requested.clear()
                continue
            try:
                if self.provider == "opensky":
                    fetched = self._fetch_opensky()
                elif self.provider in self._RADIUS_PROVIDERS:
                    fetched = self._fetch_community_provider()
                else:
                    log.error("Unbekannter ADS-B Provider '%s'.", self.provider)
                    fetched = None
                if fetched is not None:
                    with self._lock:
                        self._data = fetched
                    log.info("ADS-B: %d Flugzeuge empfangen (%s)", len(fetched), self.provider)
            except Exception as e:
                self.last_error = f"ADS-B unerwarteter Fehler ({type(e).__name__}): {e}"
                log.error(self.last_error); log.debug(traceback.format_exc())
                self._register_failure()
            self._refresh_requested.wait(timeout=interval)
            self._refresh_requested.clear()

    def stop(self):
        self.running = False


# ════════════════════════════════════════════════════════════════
# STARLINK SATELLITEN-TRACKER (SGP4-Bahnberechnung aus TLE-Daten)
# ════════════════════════════════════════════════════════════════
# Unverändert reiner Online-Dienst (TLE-Bahnelemente von CelesTrak/
# Space-Track, lokal per SGP4 propagiert) -- übernommen aus
# OverWatchMK1, keine lokale Hardware beteiligt.

class StarlinkReceiver(threading.Thread):
    WGS84_A = 6378.137
    WGS84_F = 1 / 298.257223563
    WGS84_E2 = 2 * WGS84_F - WGS84_F * WGS84_F

    TLE_URL_FALLBACKS = [
        "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle",
        "https://celestrak.org/NORAD/elements/starlink.txt",
    ]
    REQUEST_HEADERS = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        "Accept": "text/plain,text/html,*/*",
    }
    GROUND_TRACK_MINUTES = 8
    GROUND_TRACK_STEP_SEC = 30

    def __init__(self):
        super().__init__(daemon=True, name="StarlinkReceiver")
        self.running = False
        self._lock = threading.Lock()
        self._satellites = []
        self._data = []
        self._center_lat = CFG["map"]["default_lat"]
        self._center_lon = CFG["map"]["default_lon"]
        self._bbox = None
        self._refresh_requested = threading.Event()
        cfg = CFG.get("starlink", {})
        self.tle_refresh_hours = cfg.get("tle_refresh_hours", 6)
        self.position_update_sec = cfg.get("position_update_sec", 5)
        self.max_displayed = cfg.get("max_displayed", 300)
        self.viewport_padding_deg = cfg.get("viewport_padding_deg", 5.0)
        self._tle_last_fetch_ts = 0
        self._tle_backoff_until = 0
        self._tle_consecutive_failures = 0
        self.last_success_ts = None
        self.last_error = None
        self.satellite_count = 0
        self._tle_cache_path = external_path(cfg.get("tle_cache_path", "starlink_tle_cache.json"))

    def _load_tle_cache_if_fresh(self):
        try:
            with open(self._tle_cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            age_sec = time.time() - cache.get("fetched_at", 0)
            if age_sec > self.tle_refresh_hours * 3600:
                return False
            sats = self._parse_tle_text(cache["raw_tle"])
            if not sats:
                return False
            with self._lock:
                self._satellites = sats; self.satellite_count = len(sats)
            self._tle_last_fetch_ts = cache["fetched_at"]
            self.last_success_ts = time.time(); self.last_error = None
            log.info("Starlink: %d Satelliten aus lokalem Zwischenspeicher geladen (Alter: %.0f min).",
                     len(sats), age_sec / 60)
            return True
        except FileNotFoundError:
            return False
        except Exception as e:
            log.debug("Starlink: TLE-Zwischenspeicher nicht lesbar (%s).", e)
            return False

    def _save_tle_cache(self, raw_tle_text):
        try:
            tmp = self._tle_cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"fetched_at": time.time(), "raw_tle": raw_tle_text}, f)
            os.replace(tmp, self._tle_cache_path)
        except Exception as e:
            log.debug("Starlink: TLE-Zwischenspeicher konnte nicht geschrieben werden (%s).", e)

    def _register_tle_success(self):
        self._tle_consecutive_failures = 0; self._tle_backoff_until = 0

    def _register_tle_failure(self):
        self._tle_consecutive_failures += 1
        wait = min(60 * (2 ** (self._tle_consecutive_failures - 1)), 1200)
        self._tle_backoff_until = time.time() + wait
        log.info("Starlink TLE-Abruf: %d Fehlschläge – nächster Versuch in %d Minuten.",
                 self._tle_consecutive_failures, wait // 60)

    def _fetch_from_spacetrack(self):
        creds = CFG.get("starlink", {}).get("spacetrack", {})
        user = creds.get("username", ""); pw = creds.get("password", "")
        if not user or not pw:
            return None
        session = requests.Session()
        login_url = "https://www.space-track.org/ajaxauth/login"
        query_url = ("https://www.space-track.org/basicspacedata/query/class/gp/"
                    "OBJECT_NAME/~~STARLINK/orderby/NORAD_CAT_ID/format/3le")
        try:
            r = session.post(login_url, data={"identity": user, "password": pw}, timeout=20)
            if r.status_code != 200 or "fail" in r.text.lower()[:200]:
                self.last_error = f"Space-Track Login fehlgeschlagen (HTTP {r.status_code})."
                log.warning(self.last_error); return False
            r2 = session.get(query_url, timeout=30)
            if r2.status_code != 200:
                self.last_error = f"Space-Track Datenabruf: HTTP {r2.status_code}."
                log.warning(self.last_error); return False
            sats = self._parse_tle_text(r2.text)
            if not sats:
                self.last_error = "Space-Track: Antwort enthielt keine gültigen Bahnelemente."
                log.warning(self.last_error); return False
            with self._lock:
                self._satellites = sats; self.satellite_count = len(sats)
            self._tle_last_fetch_ts = time.time(); self.last_error = None
            self._register_tle_success()
            log.info("Starlink: %d Bahnelemente von Space-Track.org geladen.", len(sats))
            return True
        except Exception as e:
            self.last_error = f"Space-Track Fehler ({type(e).__name__}): {e}"
            log.warning(self.last_error); return False

    def _fetch_tle_catalog(self):
        st_result = self._fetch_from_spacetrack()
        if st_result is True:
            return True
        if st_result is False:
            log.info("Space-Track fehlgeschlagen, versuche CelesTrak als Fallback...")
        cfg_url = CFG.get("starlink", {}).get("tle_url")
        urls = ([cfg_url] if cfg_url else []) + [u for u in self.TLE_URL_FALLBACKS if u != cfg_url]
        last_error = None
        for url in urls:
            try:
                r = requests.get(url, timeout=20, headers=self.REQUEST_HEADERS)
                if r.status_code != 200:
                    last_error = f"Starlink TLE-Abruf: HTTP {r.status_code} von {url}."
                    log.warning(last_error); continue
                sats = self._parse_tle_text(r.text)
                if not sats:
                    last_error = f"Starlink TLE-Abruf von {url}: keine gültigen Bahnelemente."
                    log.warning(last_error); continue
                with self._lock:
                    self._satellites = sats; self.satellite_count = len(sats)
                self._tle_last_fetch_ts = time.time(); self.last_error = None
                self._register_tle_success(); self._save_tle_cache(r.text)
                log.info("Starlink: %d Bahnelemente geladen von %s", len(sats), url)
                return True
            except Exception as e:
                last_error = f"Starlink TLE-Abruf von {url} Fehler ({type(e).__name__}): {e}"
                log.warning(last_error)
        self.last_error = last_error or "Starlink TLE-Abruf: unbekannter Fehler."
        self._register_tle_failure()
        return False

    @staticmethod
    def _parse_tle_text(text):
        from sgp4.api import Satrec
        lines = [l.rstrip("\n").rstrip("\r") for l in text.strip().split("\n")]
        sats = []; i = 0
        while i < len(lines) - 2:
            name = lines[i].strip(); l1, l2 = lines[i + 1], lines[i + 2]
            if l1.startswith("1 ") and l2.startswith("2 "):
                try:
                    sat = Satrec.twoline2rv(l1, l2)
                    sats.append({"name": name, "norad_id": sat.satnum, "sat": sat})
                except Exception:
                    pass
                i += 3
            else:
                i += 1
        return sats

    @staticmethod
    def _gmst_rad(dt):
        jd = (367 * dt.year - int(7 * (dt.year + int((dt.month + 9) / 12)) / 4)
              + int(275 * dt.month / 9) + dt.day + 1721013.5)
        jd += (dt.hour + dt.minute / 60 + dt.second / 3600) / 24
        t = (jd - 2451545.0) / 36525.0
        gmst_sec = (67310.54841 + (876600 * 3600 + 8640184.812866) * t
                    + 0.093104 * t * t - 6.2e-6 * t * t * t)
        gmst_deg = (gmst_sec % 86400) / 240.0
        return math.radians(gmst_deg % 360)

    @classmethod
    def _eci_to_geodetic(cls, x, y, z, dt):
        theta = cls._gmst_rad(dt)
        xe = x * math.cos(theta) + y * math.sin(theta)
        ye = -x * math.sin(theta) + y * math.cos(theta)
        ze = z
        a, e2 = cls.WGS84_A, cls.WGS84_E2
        lon = math.atan2(ye, xe)
        p = math.sqrt(xe * xe + ye * ye)
        lat = math.atan2(ze, p * (1 - e2))
        for _ in range(6):
            sin_lat = math.sin(lat)
            N = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
            alt = p / math.cos(lat) - N
            lat = math.atan2(ze, p * (1 - e2 * N / (N + alt)))
        sin_lat = math.sin(lat)
        N = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
        alt = p / math.cos(lat) - N
        return math.degrees(lat), math.degrees(lon), alt

    def _compute_ground_track(self, sat, start_dt):
        from sgp4.api import jday
        segments = [[]]
        n_steps = int((self.GROUND_TRACK_MINUTES * 60) / self.GROUND_TRACK_STEP_SEC)
        for i in range(1, n_steps + 1):
            t = start_dt + timedelta(seconds=i * self.GROUND_TRACK_STEP_SEC)
            jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute, t.second)
            try:
                err, pos, vel = sat.sgp4(jd, fr)
                if err != 0:
                    break
                lat, lon, _ = self._eci_to_geodetic(pos[0], pos[1], pos[2], t)
            except Exception:
                break
            cur_seg = segments[-1]
            if cur_seg:
                prev_lon = cur_seg[-1][1]
                if abs(lon - prev_lon) > 180:
                    segments.append([]); cur_seg = segments[-1]
            cur_seg.append([round(lat, 4), round(lon, 4)])
        return [seg for seg in segments if len(seg) >= 2]

    def _propagate_all(self):
        from sgp4.api import jday
        with self._lock:
            sats = list(self._satellites)
            bbox = dict(self._bbox) if self._bbox else None
            clat, clon = self._center_lat, self._center_lon
        if not sats:
            return []
        now = datetime.now(timezone.utc)
        jd, fr = jday(now.year, now.month, now.day, now.hour, now.minute, now.second)
        pad = self.viewport_padding_deg
        if bbox:
            lamin, lamax = bbox["lamin"] - pad, bbox["lamax"] + pad
            lomin, lomax = bbox["lomin"] - pad, bbox["lomax"] + pad
        else:
            lamin, lamax = clat - pad, clat + pad
            lomin, lomax = clon - pad, clon + pad
        results = []
        for entry in sats:
            try:
                err, pos, vel = entry["sat"].sgp4(jd, fr)
                if err != 0:
                    continue
                lat, lon, alt_km = self._eci_to_geodetic(pos[0], pos[1], pos[2], now)
                if not (lamin <= lat <= lamax and lomin <= lon <= lomax):
                    continue
                speed_kms = math.sqrt(vel[0] ** 2 + vel[1] ** 2 + vel[2] ** 2)
                results.append({"norad_id": entry["norad_id"], "name": entry["name"],
                                 "lat": round(lat, 5), "lon": round(lon, 5),
                                 "alt_km": round(alt_km, 1), "speed_kms": round(speed_kms, 2),
                                 "speed_kmh": round(speed_kms * 3600, 0), "last_seen": time.time()})
            except Exception:
                continue
        if len(results) > self.max_displayed:
            results = results[:self.max_displayed]
        sats_by_id = {e["norad_id"]: e["sat"] for e in sats}
        for r in results:
            sat = sats_by_id.get(r["norad_id"])
            if sat is not None:
                try:
                    r["ground_track"] = self._compute_ground_track(sat, now)
                except Exception:
                    r["ground_track"] = []
        return results

    def update_viewport(self, lat, lon, bbox=None):
        with self._lock:
            self._center_lat = lat; self._center_lon = lon; self._bbox = bbox
        self._refresh_requested.set()

    def get_all(self):
        with self._lock:
            return list(self._data)

    def run(self):
        self.running = True
        log.info("Starlink Tracker gestartet (TLE-Refresh: %dh, Positions-Update: %ds)",
                 self.tle_refresh_hours, self.position_update_sec)
        if not self._load_tle_cache_if_fresh():
            self._fetch_tle_catalog()
        while self.running:
            due_for_refresh = (time.time() - self._tle_last_fetch_ts > self.tle_refresh_hours * 3600)
            backoff_active = time.time() < self._tle_backoff_until
            if due_for_refresh and not backoff_active:
                self._fetch_tle_catalog()
            try:
                positions = self._propagate_all()
                with self._lock:
                    self._data = positions
                if positions or self._satellites:
                    self.last_success_ts = time.time(); self.last_error = None
            except Exception as e:
                self.last_error = f"Starlink Propagation Fehler ({type(e).__name__}): {e}"
                log.error(self.last_error); log.debug(traceback.format_exc())
            self._refresh_requested.wait(timeout=self.position_update_sec)
            self._refresh_requested.clear()

    def stop(self):
        self.running = False


# ════════════════════════════════════════════════════════════════
# KARTENMARKIERUNGEN (vom Nutzer angelegt, persistent)
# ════════════════════════════════════════════════════════════════

class MapAnnotationsStore:
    _VALID_TYPES = {"marker", "circle", "rectangle"}

    def __init__(self, path):
        import re
        self._color_re = re.compile(r"^#[0-9a-fA-F]{6}$")
        self.path = path
        self._lock = threading.Lock()
        self._data = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            log.info("Kartenmarkierungen: %d Eintrag/Einträge aus %s geladen", len(self._data), self.path)
        except FileNotFoundError:
            self._data = {}
        except Exception as e:
            log.warning("Kartenmarkierungen: Konnte %s nicht laden (%s).", self.path, e)
            self._data = {}

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, self.path)
        except Exception as e:
            log.warning("Kartenmarkierungen: Konnte %s nicht speichern: %s", self.path, e)

    def add(self, kind, color, geometry, label=""):
        if kind not in self._VALID_TYPES:
            raise ValueError(f"Ungültiger Markierungstyp: {kind}")
        if not self._color_re.match(color or ""):
            raise ValueError(f"Ungültige Farbe (erwartet #RRGGBB): {color}")
        aid = uuid.uuid4().hex[:12]
        entry = {"id": aid, "type": kind, "color": color, "geometry": geometry,
                  "label": (label or "")[:200], "created_at": datetime.now(timezone.utc).isoformat()}
        with self._lock:
            self._data[aid] = entry; self._save()
        return entry

    def remove(self, aid):
        with self._lock:
            existed = aid in self._data
            if existed:
                del self._data[aid]; self._save()
        return existed

    def all_entries(self):
        with self._lock:
            return list(self._data.values())


# ════════════════════════════════════════════════════════════════
# FLASK / SOCKETIO APP
# ════════════════════════════════════════════════════════════════

app = Flask(__name__, template_folder=resource_path("templates"), static_folder=resource_path("static"))
app.config["SECRET_KEY"] = CFG["server"]["secret_key"]
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading",
                    logger=False, engineio_logger=False, ping_timeout=20, ping_interval=10)

adsb_rx: Optional[ADSBReceiver] = None
starlink_rx: Optional[StarlinkReceiver] = None
flarm_internet_rx: Optional[OGNInternetClient] = None
sms_rx: Optional[SMSReceiver] = None

map_annotations_store = MapAnnotationsStore(external_path("map_annotations.json"))
sms_format_store = SMSFormatStore(external_path("sms_format.json"))
sms_track_store = SMSTrackStore(external_path("sms_tracks.json"))
sms_inbox_log = SMSInboxLog()


def get_local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80)); return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _on_sms_point(sender, track, inbox_entry):
    """Callback aus SMSReceiver: broadcastet den neuen Punkt SOFORT an
    alle verbundenen Clients, statt bis zum nächsten Sekundentakt der
    ohnehin laufenden _broadcast_loop zu warten -- SMS-Ortungsmeldungen
    sind selten genug, dass sofortiges Feedback im Frontend spürbar
    wertvoller ist als der minimale Zusatzaufwand."""
    try:
        socketio.emit("sms_point", {"sender": sender, "track": track, "inbox_entry": inbox_entry})
    except Exception as e:
        log.debug("SMS-Broadcast fehlgeschlagen: %s", e)


@app.route("/")
def index():
    resp = make_response(render_template(
        "index.html", local_ip=get_local_ip(), port=CFG["server"]["port"],
        map_cfg=CFG["map"], dw_version=OVERWATCH_VERSION, dw_build_note=OVERWATCH_BUILD_NOTE))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/api/adsb")
def api_adsb():
    return jsonify(adsb_rx.get_all() if adsb_rx else [])


@app.route("/api/starlink")
def api_starlink():
    return jsonify(starlink_rx.get_all() if starlink_rx else [])


@app.route("/api/flarm")
def api_flarm():
    return jsonify(flarm_internet_rx.get_all() if flarm_internet_rx else [])


@app.route("/api/viewport", methods=["POST"])
def api_viewport():
    """Wird vom Frontend bei jedem Kartenschwenk/-zoom aufgerufen -- passt
    Mittelpunkt/Radius/Bounding-Box der ADS-B- und Starlink-Abfragen an
    den sichtbaren Kartenausschnitt an."""
    data = request.get_json(silent=True) or {}
    lat = data.get("lat"); lon = data.get("lon")
    if lat is None or lon is None:
        return jsonify({"ok": False, "error": "lat/lon erforderlich"}), 400
    radius_km = data.get("radius_km")
    bbox = data.get("bbox")
    if adsb_rx:
        adsb_rx.update_viewport(lat, lon, radius_km=radius_km, bbox=bbox)
    if starlink_rx:
        starlink_rx.update_viewport(lat, lon, bbox=bbox)
    return jsonify({"ok": True})


@app.route("/api/stats")
def api_stats():
    return jsonify({
        "version": OVERWATCH_VERSION, "build_note": OVERWATCH_BUILD_NOTE,
        "adsb_status": _get_adsb_status(), "starlink_status": _get_starlink_status(),
        "flarm_status": _get_flarm_status(), "sms_status": _get_sms_status(),
    })


def _get_adsb_status():
    if not adsb_rx:
        return {"active": False, "last_success_ts": None, "last_error": None}
    return {"active": adsb_rx.running, "last_success_ts": adsb_rx.last_success_ts,
            "last_error": adsb_rx.last_error, "provider": adsb_rx.provider}


def _get_starlink_status():
    if not starlink_rx:
        return {"active": False, "satellite_count": 0, "last_success_ts": None, "last_error": None}
    return {"active": starlink_rx.running, "satellite_count": starlink_rx.satellite_count,
            "last_success_ts": starlink_rx.last_success_ts, "last_error": starlink_rx.last_error}


def _get_flarm_status():
    if not flarm_internet_rx:
        return {"active": False, "last_success_ts": None, "last_error": None, "packets_received": 0}
    return {"active": flarm_internet_rx.running, "last_success_ts": flarm_internet_rx.last_success_ts,
            "last_error": flarm_internet_rx.last_error,
            "packets_received": flarm_internet_rx.packets_received}


def _get_sms_status():
    if not sms_rx:
        return {"enabled": CFG.get("sms", {}).get("enabled", False), "connected": False,
                "last_error": "SMS-Empfang nicht gestartet.", "last_success_ts": None,
                "messages_received": 0, "messages_with_coords": 0}
    return sms_rx.status()


# ── Kartenmarkierungen ────────────────────────────────────────────
@app.route("/api/annotations")
def api_annotations_list():
    return jsonify(map_annotations_store.all_entries())


@app.route("/api/annotations", methods=["POST"])
def api_annotations_create():
    data = request.get_json(silent=True) or {}
    try:
        entry = map_annotations_store.add(data.get("type"), data.get("color"),
                                          data.get("geometry"), data.get("label", ""))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    socketio.emit("annotations_update", map_annotations_store.all_entries())
    return jsonify({"ok": True, "entry": entry})


@app.route("/api/annotations/<aid>", methods=["DELETE"])
def api_annotations_delete(aid):
    existed = map_annotations_store.remove(aid)
    socketio.emit("annotations_update", map_annotations_store.all_entries())
    return jsonify({"ok": existed})


# ── SMS-Ortung: Anlernen, Status, Punktketten ────────────────────
@app.route("/api/sms/status")
def api_sms_status():
    return jsonify(_get_sms_status())


@app.route("/api/sms/format")
def api_sms_format():
    return jsonify(sms_format_store.get())


@app.route("/api/sms/format", methods=["DELETE"])
def api_sms_format_reset():
    sms_format_store.reset()
    return jsonify({"ok": True})


@app.route("/api/sms/inbox")
def api_sms_inbox():
    return jsonify(sms_inbox_log.all())


@app.route("/api/sms/tracks")
def api_sms_tracks():
    return jsonify(sms_track_store.all_tracks())


@app.route("/api/sms/tracks/<sender>", methods=["DELETE"])
def api_sms_tracks_delete(sender):
    existed = sms_track_store.clear_sender(sender)
    return jsonify({"ok": existed})


@app.route("/api/sms/learn", methods=["POST"])
def api_sms_learn():
    """Probiert einen vom Nutzer eingegebenen Beispieltext gegen ALLE
    eingebauten Koordinaten-Muster (siehe sms_gateway.COORD_PATTERNS) UND
    optional eine eigene Regex. Speichert NICHTS -- das übernimmt erst
    /api/sms/learn/confirm, nachdem der Nutzer den richtigen Treffer aus
    den Kandidaten ausgewählt hat."""
    data = request.get_json(silent=True) or {}
    text = (data.get("sample_text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "sample_text erforderlich"}), 400
    candidates = try_parse_coordinates(text)
    custom_regex = (data.get("custom_regex") or "").strip()
    if custom_regex:
        try:
            pat = custom_pattern_from_regex(custom_regex)
            m = pat["regex"].search(text)
            if m:
                lat, lon = pat["convert"](m)
                candidates.append({"pattern_id": "custom", "label": "Eigene Regex",
                                    "lat": round(lat, 6), "lon": round(lon, 6),
                                    "matched_text": m.group(0), "custom_regex": custom_regex})
        except Exception as e:
            return jsonify({"ok": False, "error": f"Eigene Regex ungültig: {e}"}), 400
    return jsonify({"ok": True, "candidates": candidates})


@app.route("/api/sms/learn/confirm", methods=["POST"])
def api_sms_learn_confirm():
    data = request.get_json(silent=True) or {}
    pattern_id = data.get("pattern_id")
    label = data.get("label") or pattern_id
    example_text = data.get("example_text") or ""
    custom_regex = data.get("custom_regex")
    if not pattern_id:
        return jsonify({"ok": False, "error": "pattern_id erforderlich"}), 400
    sms_format_store.confirm(pattern_id, label, example_text, custom_regex=custom_regex)
    return jsonify({"ok": True, "format": sms_format_store.get()})


@app.route("/api/sms/simulate", methods=["POST"])
def api_sms_simulate():
    """Entwicklungs-/Test-Hilfsmittel: injiziert eine simulierte SMS ohne
    angeschlossenes Modem -- ermöglicht es, den kompletten Anlern- und
    Anzeige-Ablauf (Koordinaten-Erkennung, Punktkette, Farbzuweisung)
    ohne echte SIM800-Hardware durchzutesten. Nutzt DIESELBE
    Verarbeitungslogik wie ein echter SMSReceiver (SMSReceiver._process_message
    wird direkt genutzt, falls ein SMSReceiver läuft; sonst wird nur
    geparst und ohne Modem-Zustand in die Tracks übernommen)."""
    data = request.get_json(silent=True) or {}
    sender = (data.get("sender") or "+490000000000").strip()
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "text erforderlich"}), 400
    fake_msg = {"sender": sender, "text": text, "timestamp": None, "index": -1}
    if sms_rx:
        sms_rx._process_message(fake_msg)
    else:
        # Kein Receiver aktiv (SMS in config.yaml deaktiviert) -- trotzdem
        # dieselbe Parsing-/Speicher-Logik nutzen, damit die Anlern-
        # Oberfläche auch ohne aktivierte SMS-Funktion getestet werden kann.
        results = try_parse_coordinates(text)
        if results:
            hit = results[0]
            sms_track_store.add_point(sender=sender, lat=hit["lat"], lon=hit["lon"],
                                      raw_text=text, confirmed=False, pattern_label=hit["label"])
    return jsonify({"ok": True, "tracks": sms_track_store.all_tracks()})


# ── Offline-Kartenkacheln (MBTiles, direkt neben exe/.app) ───────
def _find_mbtiles_path() -> Optional[str]:
    base = _external_dir()
    candidates = []
    try:
        candidates += [os.path.join(base, f) for f in os.listdir(base) if f.lower().endswith(".mbtiles")]
    except Exception:
        pass
    offline_dir = os.path.join(base, "offline_map")
    if os.path.isdir(offline_dir):
        try:
            candidates += [os.path.join(offline_dir, f) for f in os.listdir(offline_dir)
                          if f.lower().endswith(".mbtiles")]
        except Exception:
            pass
    return candidates[0] if candidates else None


@app.route("/api/offline_tiles/status")
def api_offline_tiles_status():
    path = _find_mbtiles_path()
    return jsonify({"available": bool(path) and CFG.get("offline_map", {}).get("enabled", True),
                    "path": path})


@app.route("/api/offline_tiles/<int:z>/<int:x>/<int:y>.<ext>")
def api_offline_tile(z, x, y, ext):
    path = _find_mbtiles_path()
    if not path or not CFG.get("offline_map", {}).get("enabled", True):
        return "", 404
    tms_y = (2 ** z - 1) - y
    try:
        uri = f"file:{path}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        cur = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (z, x, tms_y))
        row = cur.fetchone()
        conn.close()
        if row is None:
            return "", 404
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "application/octet-stream")
        resp = make_response(row[0])
        resp.headers["Content-Type"] = mime
        resp.headers["Cache-Control"] = "public, max-age=604800"
        return resp
    except Exception as e:
        log.warning("Offline-Tile-Abfrage fehlgeschlagen (z=%d x=%d y=%d): %s", z, x, y, e)
        return "", 500


# ── No-Fly-Zonen (reine Konfigurations-Weitergabe, siehe config.yaml) ─
@app.route("/api/noflyzones/config")
def api_noflyzones_config():
    nfz_cfg = CFG.get("noflyzones", {})
    return jsonify({"enabled": nfz_cfg.get("enabled", False), "wms_url": nfz_cfg.get("wms_url", ""),
                    "wms_version": nfz_cfg.get("wms_version", "1.3.0"),
                    "layers": nfz_cfg.get("layers", ""), "attribution": nfz_cfg.get("attribution", "")})


# ── Adress-/Straßensuche (Geocoding über Nominatim) ──────────────
_geocode_cache: Dict[str, dict] = {}
_geocode_cache_lock = threading.Lock()
_last_geocode_request_ts = 0.0
_GEOCODE_MIN_INTERVAL_SEC = 1.0


@app.route("/api/geocode")
def api_geocode():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"ok": False, "error": "Kein Suchbegriff angegeben."}), 400
    cache_key = query.lower()
    with _geocode_cache_lock:
        cached = _geocode_cache.get(cache_key)
    if cached is not None:
        return jsonify(cached)
    global _last_geocode_request_ts
    with _geocode_cache_lock:
        wait = _GEOCODE_MIN_INTERVAL_SEC - (time.time() - _last_geocode_request_ts)
        if wait > 0:
            time.sleep(wait)
        _last_geocode_request_ts = time.time()
    try:
        resp = requests.get("https://nominatim.openstreetmap.org/search",
                            params={"q": query, "format": "jsonv2", "limit": 1,
                                    "accept-language": "de"},
                            headers={"User-Agent": f"OverWatchMK2/{OVERWATCH_VERSION}"}, timeout=6)
        resp.raise_for_status()
        results = resp.json()
        if not results:
            result = {"ok": False, "error": f"Keine Ergebnisse für „{query}“."}
        else:
            r = results[0]
            result = {"ok": True, "lat": float(r["lat"]), "lon": float(r["lon"]),
                       "display_name": r.get("display_name", query)}
        with _geocode_cache_lock:
            _geocode_cache[cache_key] = result
            if len(_geocode_cache) > 200:
                _geocode_cache.pop(next(iter(_geocode_cache)))
        return jsonify(result)
    except requests.exceptions.RequestException as e:
        log.warning("Geocoding-Anfrage fehlgeschlagen: %s", e)
        return jsonify({"ok": False, "error": "Adresssuche nicht erreichbar -- benötigt Internet."}), 502
    except Exception as e:
        log.warning("Geocoding-Anfrage fehlgeschlagen: %s", e)
        return jsonify({"ok": False, "error": f"Fehler bei der Adresssuche: {e}"}), 500


@socketio.on("connect")
def ws_connect():
    log.info("WebSocket Client verbunden: %s", request.sid)
    emit("full_update", {
        "adsb": adsb_rx.get_all() if adsb_rx else [],
        "adsb_status": _get_adsb_status(),
        "starlink": starlink_rx.get_all() if starlink_rx else [],
        "starlink_status": _get_starlink_status(),
        "flarm": flarm_internet_rx.get_all() if flarm_internet_rx else [],
        "flarm_status": _get_flarm_status(),
        "sms_status": _get_sms_status(),
        "sms_tracks": sms_track_store.all_tracks(),
        "sms_format": sms_format_store.get(),
        "annotations": map_annotations_store.all_entries(),
    })


def _broadcast_loop():
    while True:
        time.sleep(1)
        try:
            socketio.emit("full_update", {
                "adsb": adsb_rx.get_all() if adsb_rx else [],
                "adsb_status": _get_adsb_status(),
                "starlink": starlink_rx.get_all() if starlink_rx else [],
                "starlink_status": _get_starlink_status(),
                "flarm": flarm_internet_rx.get_all() if flarm_internet_rx else [],
                "flarm_status": _get_flarm_status(),
                "sms_status": _get_sms_status(),
            })
        except Exception as e:
            log.debug("Broadcast: %s", e)


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

def main():
    global adsb_rx, starlink_rx, flarm_internet_rx, sms_rx
    _print_startup_diagnostics()

    log.info("=" * 60)
    log.info("OverWatchMK2 Version: %s (%s)", OVERWATCH_VERSION, OVERWATCH_BUILD_NOTE)
    log.info("config.yaml Quelle: %s", _CFG_LOADED_FROM)
    log.info("Externer Ordner (config/Logs/offline_map): %s", _external_dir())
    log.info("=" * 60)

    parser = argparse.ArgumentParser(description="OverWatchMK2 – Online-Luftraum-/Ortungsanzeige")
    parser.add_argument("--port", type=int, default=CFG["server"]["port"])
    parser.add_argument("--host", default=CFG["server"]["host"])
    parser.add_argument("--no-adsb", action="store_true", help="ADS-B Empfang deaktivieren")
    parser.add_argument("--no-starlink", action="store_true", help="Starlink-Tracker deaktivieren")
    parser.add_argument("--no-flarm", action="store_true", help="FLARM/OGN Empfang deaktivieren")
    parser.add_argument("--no-sms", action="store_true", help="SMS-Ortungsempfang deaktivieren")
    parser.add_argument("--version", action="store_true", help="Version anzeigen und beenden")
    parser.add_argument("--test-starlink", action="store_true",
                        help="Nur TLE-Abruf testen (einmalig) und Ergebnis anzeigen, dann beenden.")
    parser.add_argument("--debug", action="store_true", help="Ausführliches Logging aktivieren")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        log.info("Debug-Logging aktiviert (--debug)")

    if args.version:
        print(f"OverWatchMK2 {OVERWATCH_VERSION} ({OVERWATCH_BUILD_NOTE})")
        sys.exit(0)

    if args.test_starlink:
        print("Teste TLE-Abruf (einmalig, ignoriert Backoff-Wartezeit)...\n")
        test_rx = StarlinkReceiver()
        result = test_rx._fetch_tle_catalog()
        if result:
            print(f"ERFOLG: {test_rx.satellite_count} Satelliten-Bahnelemente geladen.")
        else:
            print(f"FEHLGESCHLAGEN: {test_rx.last_error}")
        sys.exit(0 if result else 1)

    local_ip = get_local_ip()
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║      OverWatchMK2  –  Online-Luftraum-/Ortungsanzeige              ║
╠══════════════════════════════════════════════════════════════════╣
║  Web-Interface:  http://{local_ip}:{args.port:<38}║
║  Lokale Adresse: http://localhost:{args.port:<37}║
╚══════════════════════════════════════════════════════════════════╝
    """)

    workers = []

    if CFG["adsb"]["enabled"] and not args.no_adsb:
        adsb_rx = ADSBReceiver(); adsb_rx.start(); workers.append(adsb_rx)
    else:
        log.info("ADS-B Empfang deaktiviert.")

    if CFG.get("starlink", {}).get("enabled", True) and not args.no_starlink:
        try:
            import sgp4 as _sgp4_check  # noqa
            starlink_rx = StarlinkReceiver(); starlink_rx.start(); workers.append(starlink_rx)
        except ImportError:
            log.error("Starlink-Tracker NICHT gestartet: Paket 'sgp4' fehlt. pip install sgp4")
    else:
        log.info("Starlink-Tracker deaktiviert.")

    if CFG.get("flarm", {}).get("enabled", True) and not args.no_flarm:
        flarm_internet_rx = OGNInternetClient(CFG.get("flarm", {}),
                                              center_lat=CFG["map"]["default_lat"],
                                              center_lon=CFG["map"]["default_lon"])
        flarm_internet_rx.start(); workers.append(flarm_internet_rx)
    else:
        log.info("FLARM/OGN Empfang deaktiviert.")

    if CFG.get("sms", {}).get("enabled", False) and not args.no_sms:
        sms_rx = SMSReceiver(CFG.get("sms", {}), sms_format_store, sms_track_store,
                             sms_inbox_log, on_new_point=_on_sms_point)
        sms_rx.start(); workers.append(sms_rx)
    else:
        log.info("SMS-Ortungsempfang deaktiviert (sms.enabled: false, oder --no-sms).")

    threading.Thread(target=_broadcast_loop, daemon=True).start()

    log.info("Webserver startet auf http://%s:%d", local_ip, args.port)
    try:
        socketio.run(app, host=args.host, port=args.port, debug=False,
                     use_reloader=False, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        log.info("Beende OverWatchMK2...")
    finally:
        for w in workers:
            w.stop()


if __name__ == "__main__":
    main()
