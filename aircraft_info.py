# -*- coding: utf-8 -*-
"""
OverWatchMK2 – Flugzeug-Zusatzinfos (ab v1.1.0)

Liefert beim Anklicken eines Flugzeugs:
  * Flugzeugtyp / Hersteller / Registrierung / Betreiber  (adsbdb.com, per ICAO-Hex)
  * Start- und Zielflughafen                               (adsbdb.com, per Callsign)
  * komplette bisherige Flugbahn                           (adsb.lol tar1090-Trace-Dateien)

Alles ist reine Online-Abfrage (kein lokaler Empfang) mit Cache, Timeouts und
Fehlertoleranz: schlägt eine Quelle fehl, werden die übrigen Felder trotzdem geliefert.
"""
import re
import time
import threading
import logging
import requests

log = logging.getLogger("overwatch.aircraft_info")

ADSBDB_AIRCRAFT = "https://api.adsbdb.com/v0/aircraft/{icao}"
ADSBDB_CALLSIGN = "https://api.adsbdb.com/v0/callsign/{callsign}"
ADSBLOL_TRACE = "https://adsb.lol/data/traces/{tail}/trace_full_{icao}.json"

_HEX_RE = re.compile(r"^[0-9a-f]{6}$")
_CALLSIGN_RE = re.compile(r"^[A-Z0-9]{2,8}$")

INFO_TTL_OK = 24 * 3600        # erfolgreiche Treffer lange cachen
INFO_TTL_MISS = 15 * 60        # "unbekannt" kürzer (Daten können nachgepflegt werden)
INFO_TTL_ERR = 60              # Netzwerkfehler nur kurz
TRACE_TTL = 20                 # Flugbahn alle 20 s neu holen (tar1090 schreibt ~alle 15-30 s)
TRACE_MAX_POINTS = 1500
LEG_GAP_SEC = 30 * 60          # Lücke > 30 min = neuer Flug in derselben Datei
CACHE_MAX = 2000


class _TTLCache:
    def __init__(self):
        self._d = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            v = self._d.get(key)
            if v and v[0] > time.time():
                return v[1]
            return None

    def put(self, key, value, ttl):
        with self._lock:
            if len(self._d) >= CACHE_MAX:
                now = time.time()
                for k in [k for k, v in self._d.items() if v[0] <= now]:
                    del self._d[k]
                if len(self._d) >= CACHE_MAX:
                    self._d.clear()
            self._d[key] = (time.time() + ttl, value)


_cache = _TTLCache()
_UA = {"User-Agent": "OverWatchMK2/1.1 (+https://github.com)"}


def _get_json(url, timeout=8):
    """Gibt (status, json|None, fehlertext|None) zurück. 404 = 'unbekannt', kein Fehler."""
    try:
        r = requests.get(url, timeout=timeout, headers=_UA)
    except requests.exceptions.RequestException as e:
        return None, None, f"{type(e).__name__}"
    if r.status_code == 404:
        return 404, None, None
    if r.status_code != 200:
        return r.status_code, None, f"HTTP {r.status_code}"
    try:
        return 200, r.json(), None
    except ValueError:
        return 200, None, "Ungültiges JSON"


def _airport(d):
    if not isinstance(d, dict):
        return None
    return {
        "name": d.get("name") or "",
        "iata": d.get("iata_code") or "",
        "icao": d.get("icao_code") or "",
        "city": d.get("municipality") or "",
        "country": d.get("country_name") or "",
        "lat": d.get("latitude"),
        "lon": d.get("longitude"),
    }


def lookup_aircraft(icao):
    """Typ/Registrierung per ICAO-Hex (6 Hex-Zeichen)."""
    icao = (icao or "").strip().lower()
    if not _HEX_RE.match(icao):
        return {"ok": False, "error": "ungültige ICAO-Adresse"}
    key = ("ac", icao)
    c = _cache.get(key)
    if c is not None:
        return c
    status, js, err = _get_json(ADSBDB_AIRCRAFT.format(icao=icao))
    if err:
        res = {"ok": False, "error": err}
        _cache.put(key, res, INFO_TTL_ERR)
        return res
    ac = ((js or {}).get("response") or {}).get("aircraft") if js else None
    if not isinstance(ac, dict):
        res = {"ok": True, "found": False}
        _cache.put(key, res, INFO_TTL_MISS)
        return res
    res = {"ok": True, "found": True,
           "type": ac.get("type") or "",
           "icao_type": ac.get("icao_type") or "",
           "manufacturer": ac.get("manufacturer") or "",
           "registration": ac.get("registration") or "",
           "owner": ac.get("registered_owner") or "",
           "owner_country": ac.get("registered_owner_country_name") or "",
           "photo": ac.get("url_photo_thumbnail") or ac.get("url_photo") or ""}
    _cache.put(key, res, INFO_TTL_OK)
    return res


def lookup_route(callsign):
    """Start/Ziel per Callsign."""
    cs = (callsign or "").strip().upper()
    if not _CALLSIGN_RE.match(cs):
        return {"ok": True, "found": False}
    key = ("rt", cs)
    c = _cache.get(key)
    if c is not None:
        return c
    status, js, err = _get_json(ADSBDB_CALLSIGN.format(callsign=cs))
    if err:
        res = {"ok": False, "error": err}
        _cache.put(key, res, INFO_TTL_ERR)
        return res
    fr = ((js or {}).get("response") or {}).get("flightroute") if js else None
    if not isinstance(fr, dict):
        res = {"ok": True, "found": False}
        _cache.put(key, res, INFO_TTL_MISS)
        return res
    al = fr.get("airline") or {}
    res = {"ok": True, "found": True,
           "origin": _airport(fr.get("origin")),
           "destination": _airport(fr.get("destination")),
           "airline": al.get("name") or "",
           "callsign_iata": fr.get("callsign_iata") or ""}
    _cache.put(key, res, INFO_TTL_OK)
    return res


def get_info(icao, callsign=""):
    ac = lookup_aircraft(icao)
    rt = lookup_route(callsign) if callsign else {"ok": True, "found": False}
    return {"icao": (icao or "").upper(), "callsign": (callsign or "").strip().upper(),
            "aircraft": ac, "route": rt}


def parse_trace(js, now=None):
    """tar1090-Trace -> {'points': [[lat,lon,alt_ft|None],...], 'start_ts': float}
    Nimmt nur den letzten Flug (Lücke > LEG_GAP_SEC oder Leg-Flag)."""
    if not isinstance(js, dict) or not isinstance(js.get("trace"), list):
        return None
    base = float(js.get("timestamp") or 0)
    legs, cur, last_t = [], [], None
    for p in js["trace"]:
        try:
            t = base + float(p[0]); lat = float(p[1]); lon = float(p[2])
        except (TypeError, ValueError, IndexError):
            continue
        alt = p[3] if len(p) > 3 else None
        alt = None if (alt is None or isinstance(alt, str)) else alt
        flags = p[6] if len(p) > 6 and isinstance(p[6], int) else 0
        new_leg = (last_t is not None and (t - last_t > LEG_GAP_SEC)) or (flags & 2 and cur)
        if new_leg:
            legs.append(cur); cur = []
        cur.append((t, lat, lon, alt))
        last_t = t
    if cur:
        legs.append(cur)
    if not legs:
        return None
    leg = legs[-1]
    if len(leg) > TRACE_MAX_POINTS:
        step = len(leg) / float(TRACE_MAX_POINTS)
        leg = [leg[int(i * step)] for i in range(TRACE_MAX_POINTS - 1)] + [leg[-1]]
    return {"points": [[round(x[1], 5), round(x[2], 5), x[3]] for x in leg],
            "start_ts": leg[0][0], "end_ts": leg[-1][0]}


def get_track(icao):
    icao = (icao or "").strip().lower()
    if not _HEX_RE.match(icao):
        return {"ok": False, "error": "ungültige ICAO-Adresse"}
    key = ("tr", icao)
    c = _cache.get(key)
    if c is not None:
        return c
    url = ADSBLOL_TRACE.format(tail=icao[-2:], icao=icao)
    status, js, err = _get_json(url, timeout=12)
    if err:
        res = {"ok": False, "error": err}
        _cache.put(key, res, 10)
        return res
    tr = parse_trace(js) if js else None
    if not tr:
        res = {"ok": True, "found": False, "points": []}
        _cache.put(key, res, 60)
        return res
    res = {"ok": True, "found": True, "source": "adsb.lol", **tr}
    _cache.put(key, res, TRACE_TTL)
    return res
