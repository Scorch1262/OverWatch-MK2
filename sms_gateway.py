"""
OverWatchMK2 – SMS/GSM-Ortungsempfang über ein serielles Modem (z.B. SIM800)
============================================================================

Ziel: Ein per USB-UART-Adapter angeschlossenes GSM-Modem (SIM800/SIM900/
kompatibel) wird per AT-Befehlen abgefragt und liest eingehende SMS aus.
Enthält eine eingehende Nachricht Koordinaten, werden diese auf der Karte
eingezeichnet -- als fortlaufende, per Linie verbundene Punktkette PRO
ABSENDERNUMMER, jede Nummer in einer eigenen, stabil zugewiesenen Farbe.

Architektur-Entscheidung: POLLING statt AT-Kommando-URCs (+CMTI)
------------------------------------------------------------------
Es wird bewusst NICHT auf die unaufgeforderten "+CMTI"-Benachrichtigungen
mancher Modems gesetzt, die eine neue SMS ankündigen, sobald sie eintrifft.
Grund: Das genaue Verhalten (ob/wie zuverlässig ein Modem diese URCs sendet,
in welchem CNMI-Modus) unterscheidet sich stark zwischen SIM800-Klonen und
Firmware-Ständen -- in der Praxis erwies sich reines Abfragen (Polling) per
AT+CMGL="ALL" in festem Intervall als der robustere, über praktisch jedes
SIM800/900/7600-kompatible Modem hinweg funktionierende Ansatz. Nachteil:
etwas höhere Latenz (bis zu poll_interval_sec), was für SMS-basierte
Ortungsmeldungen (typischerweise alle paar Minuten, nicht in Echtzeit)
unkritisch ist.

Koordinaten-Format-Erkennung ("Anlernen")
------------------------------------------
Tracker-Geräte, die Positionen per SMS senden, nutzen sehr unterschiedliche
Textformate (reine Dezimalgrad, Grad/Minuten, Google-Maps-Links, "geo:"-URIs,
beschriftete Felder wie "Lat: ... Lon: ..." usw.). Statt für jedes Gerät neuen
Code zu schreiben, probiert `try_parse_coordinates()` eine Bibliothek
eingebauter Muster (siehe COORD_PATTERNS) der Reihe nach durch. Bevor ein
Muster nicht wenigstens einmal über die Weboberfläche BESTÄTIGT ("angelernt")
wurde, werden ankommende SMS zwar bestmöglich geparst und vorläufig
("automatisch erkannt, unbestätigt") auf der Karte angezeigt, damit keine
Positionsmeldung verloren geht -- sobald ein Muster einmal bestätigt wurde,
wird DIESES Muster für alle künftigen Nachrichten bevorzugt verwendet
(schneller, eindeutiger, kein Rätselraten mehr zwischen mehreren
theoretisch passenden Mustern).
"""

import json
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

log = None  # wird von main.py per set_logger() gesetzt


def set_logger(logger):
    global log
    log = logger


# ════════════════════════════════════════════════════════════════
# KOORDINATEN-MUSTERBIBLIOTHEK
# ════════════════════════════════════════════════════════════════
#
# Jedes Muster liefert bei Treffer (lat, lon) in Dezimalgrad (WGS84).
# 'convert' bekommt das re.Match-Objekt und muss (lat, lon) als float
# zurückgeben oder None werfen/zurückgeben, falls der Treffer inhaltlich
# unplausibel ist (z.B. außerhalb -90..90 / -180..180).

def _dm_to_decimal(deg: float, minutes: float, hemisphere: str) -> float:
    val = deg + minutes / 60.0
    if hemisphere.upper() in ("S", "W"):
        val = -val
    return val


def _conv_decimal_pair(m: "re.Match") -> Tuple[float, float]:
    return float(m.group("lat")), float(m.group("lon"))


def _conv_hemisphere_decimal(m: "re.Match") -> Tuple[float, float]:
    lat = float(m.group("lat"))
    lon = float(m.group("lon"))
    if m.group("ns").upper() == "S":
        lat = -lat
    if m.group("ew").upper() == "W":
        lon = -lon
    return lat, lon


def _conv_dm_hemisphere(m: "re.Match") -> Tuple[float, float]:
    lat = _dm_to_decimal(float(m.group("latd")), float(m.group("latm")), m.group("ns"))
    lon = _dm_to_decimal(float(m.group("lond")), float(m.group("lonm")), m.group("ew"))
    return lat, lon


def _conv_dms_hemisphere(m: "re.Match") -> Tuple[float, float]:
    lat = float(m.group("latd")) + float(m.group("latm")) / 60.0 + float(m.group("lats")) / 3600.0
    lon = float(m.group("lond")) + float(m.group("lonm")) / 60.0 + float(m.group("lons")) / 3600.0
    if m.group("ns").upper() == "S":
        lat = -lat
    if m.group("ew").upper() == "W":
        lon = -lon
    return lat, lon


# Reihenfolge ist relevant: spezifischere/eindeutigere Muster zuerst,
# damit z.B. ein Google-Maps-Link nicht zuerst versehentlich vom
# allgemeinen "zwei Dezimalzahlen"-Muster angeknabbert wird.
COORD_PATTERNS: List[dict] = [
    {
        "id": "geo_uri",
        "label": "geo:-URI (geo:52.5200,13.4050)",
        "regex": re.compile(r"geo:\s*(?P<lat>-?\d{1,3}\.\d+)\s*,\s*(?P<lon>-?\d{1,3}\.\d+)", re.IGNORECASE),
        "convert": _conv_decimal_pair,
    },
    {
        "id": "gmaps_link",
        "label": "Google-Maps-Link (?q=52.52,13.40 / @52.52,13.40)",
        "regex": re.compile(
            r"(?:[?&]q=|@)\s*(?P<lat>-?\d{1,3}\.\d+)\s*,\s*(?P<lon>-?\d{1,3}\.\d+)",
            re.IGNORECASE,
        ),
        "convert": _conv_decimal_pair,
    },
    {
        "id": "labeled_latlon",
        "label": "Beschriftete Felder (Lat/Breite: .. Lon/Länge: ..)",
        "regex": re.compile(
            r"(?:lat|breite)\D{0,4}(?P<lat>-?\d{1,3}\.\d+)"
            r".{0,20}?(?:lon|lng|long|l\u00e4nge)\D{0,4}(?P<lon>-?\d{1,3}\.\d+)",
            re.IGNORECASE | re.DOTALL,
        ),
        "convert": _conv_decimal_pair,
    },
    {
        "id": "dms_hemisphere",
        "label": "Grad/Minute/Sekunde mit Himmelsrichtung (52°30'15\"N 13°15'10\"E)",
        "regex": re.compile(
            r"(?P<latd>\d{1,2})[°\s]+(?P<latm>\d{1,2})['\u2019\s]+(?P<lats>\d{1,2}(?:\.\d+)?)[\"\u201d]?\s*(?P<ns>[NS])"
            r"[,;\s]+"
            r"(?P<lond>\d{1,3})[°\s]+(?P<lonm>\d{1,2})['\u2019\s]+(?P<lons>\d{1,2}(?:\.\d+)?)[\"\u201d]?\s*(?P<ew>[EW])",
            re.IGNORECASE,
        ),
        "convert": _conv_dms_hemisphere,
    },
    {
        "id": "dm_hemisphere",
        "label": "Grad/Dezimalminute mit Himmelsrichtung (52°30.25'N 13°15.10'E)",
        "regex": re.compile(
            r"(?P<latd>\d{1,2})[°\s]+(?P<latm>\d{1,2}\.\d+)['\u2019]?\s*(?P<ns>[NS])"
            r"[,;\s]+"
            r"(?P<lond>\d{1,3})[°\s]+(?P<lonm>\d{1,2}\.\d+)['\u2019]?\s*(?P<ew>[EW])",
            re.IGNORECASE,
        ),
        "convert": _conv_dm_hemisphere,
    },
    {
        "id": "decimal_hemisphere",
        "label": "Dezimalgrad mit Himmelsrichtung (52.5200N 13.4050E)",
        "regex": re.compile(
            r"(?P<lat>\d{1,3}\.\d+)\s*(?P<ns>[NS])[,;\s]+(?P<lon>\d{1,3}\.\d+)\s*(?P<ew>[EW])",
            re.IGNORECASE,
        ),
        "convert": _conv_hemisphere_decimal,
    },
    {
        "id": "decimal_pair",
        "label": "Reines Dezimalgrad-Zahlenpaar (52.5200, 13.4050)",
        "regex": re.compile(r"(?P<lat>-?\d{1,3}\.\d{2,})[,;\s]+(?P<lon>-?\d{1,3}\.\d{2,})"),
        "convert": _conv_decimal_pair,
    },
]

_PATTERNS_BY_ID = {p["id"]: p for p in COORD_PATTERNS}


def _plausible(lat: float, lon: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def try_parse_coordinates(text: str, only_pattern_id: Optional[str] = None) -> List[dict]:
    """Probiert Koordinaten-Muster gegen den Text. Gibt eine Liste aller
    TREFFER zurück (typischerweise 0 oder 1, bei mehrdeutigem Text ggf.
    auch mehrere Muster gleichzeitig -- die Aufrufer-Seite entscheidet,
    was damit geschieht: main.py nimmt beim automatischen Empfang immer
    den ersten Treffer, das Anlern-UI zeigt dem Nutzer ALLE Treffer zur
    Auswahl an.

    only_pattern_id: falls gesetzt, wird NUR dieses eine (bereits
    angelernte) Muster probiert -- schneller und eindeutig.
    """
    candidates = ([_PATTERNS_BY_ID[only_pattern_id]] if only_pattern_id
                  and only_pattern_id in _PATTERNS_BY_ID else COORD_PATTERNS)
    results = []
    for pat in candidates:
        m = pat["regex"].search(text)
        if not m:
            continue
        try:
            lat, lon = pat["convert"](m)
        except Exception:
            continue
        if not _plausible(lat, lon):
            continue
        results.append({
            "pattern_id": pat["id"],
            "label": pat["label"],
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "matched_text": m.group(0),
        })
    return results


def custom_pattern_from_regex(regex_str: str) -> dict:
    """Baut ein Muster-Dict aus einer vom Nutzer selbst angegebenen Regex
    (fortgeschrittene Option im Anlern-Dialog, falls kein eingebautes
    Muster passt). Erwartet zwingend benannte Gruppen (?P<lat>...) und
    (?P<lon>...) in Dezimalgrad -- andere Konventionen (Himmelsrichtung,
    Grad/Minuten) sind über die eingebauten Muster abgedeckt und für
    eine frei eingegebene Regex zu fehleranfällig, um sie hier generisch
    zu unterstützen."""
    compiled = re.compile(regex_str, re.IGNORECASE)
    if "lat" not in compiled.groupindex or "lon" not in compiled.groupindex:
        raise ValueError("Die Regex muss die benannten Gruppen (?P<lat>...) "
                          "und (?P<lon>...) enthalten.")
    return {"id": "custom", "label": "Eigene Regex", "regex": compiled,
            "convert": _conv_decimal_pair}


# ════════════════════════════════════════════════════════════════
# PERSISTENZ: Angelerntes Format
# ════════════════════════════════════════════════════════════════

class SMSFormatStore:
    """Speichert das EINMALIG angelernte, bestätigte Koordinatenformat.
    Solange kein Format bestätigt wurde, ist active_pattern None und
    main.py probiert bei jeder eingehenden SMS die volle Musterbibliothek
    durch (siehe try_parse_coordinates ohne only_pattern_id)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._data = {"pattern_id": None, "custom_regex": None,
                       "label": None, "trained_at": None, "example_text": None}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data.update(json.load(f))
            if log:
                log.info("SMS: Angelerntes Koordinatenformat aus %s geladen (%s)",
                          self.path, self._data.get("pattern_id"))
        except FileNotFoundError:
            pass
        except Exception as e:
            if log:
                log.warning("SMS: Konnte %s nicht laden (%s) -- kein Format angelernt.",
                            self.path, e)

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            if log:
                log.warning("SMS: Konnte Koordinatenformat nicht speichern: %s", e)

    def get(self) -> dict:
        with self._lock:
            return dict(self._data)

    def confirm(self, pattern_id: str, label: str, example_text: str,
                custom_regex: Optional[str] = None):
        with self._lock:
            self._data = {
                "pattern_id": pattern_id,
                "custom_regex": custom_regex,
                "label": label,
                "example_text": example_text[:400],
                "trained_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save()
        if log:
            log.info("SMS: Koordinatenformat angelernt/bestätigt: %s (%s)", label, pattern_id)

    def reset(self):
        with self._lock:
            self._data = {"pattern_id": None, "custom_regex": None,
                           "label": None, "trained_at": None, "example_text": None}
            self._save()
        if log:
            log.info("SMS: Angelerntes Koordinatenformat zurückgesetzt.")


# ════════════════════════════════════════════════════════════════
# PERSISTENZ: Punktketten je Absendernummer
# ════════════════════════════════════════════════════════════════

# Bewusst eine feste, gut unterscheidbare Palette statt zufälliger Farben --
# zufällige Farben könnten sich zwischen zwei Absendern zu ähnlich sein oder
# mit den ADS-B/FLARM/Starlink-Kartenfarben kollidieren.
SENDER_COLOR_PALETTE = [
    "#f97316",  # orange
    "#22d3ee",  # cyan
    "#a3e635",  # lime
    "#f472b6",  # pink
    "#facc15",  # gelb
    "#c084fc",  # violett
    "#fb7185",  # rosa-rot
    "#34d399",  # smaragd
    "#60a5fa",  # blau
    "#fdba74",  # apricot
    "#e879f9",  # fuchsia
    "#4ade80",  # grün
]

_PHONE_RE = re.compile(r"[^0-9+]")


def normalize_phone(raw: str) -> str:
    return _PHONE_RE.sub("", raw or "").strip() or "UNBEKANNT"


class SMSTrackStore:
    """Persistiert je Absendernummer eine Liste chronologisch empfangener
    Punkte (lat/lon/Zeitstempel/Rohtext) plus eine stabil zugewiesene
    Farbe. Wird auf der Karte als farbige Punktkette + verbindende Linie
    (in Empfangsreihenfolge) dargestellt."""

    def __init__(self, path: str, max_points_per_sender: int = 500):
        self.path = path
        self.max_points_per_sender = max_points_per_sender
        self._lock = threading.Lock()
        self._data: Dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            if log:
                log.info("SMS: %d Absender-Punktketten aus %s geladen",
                          len(self._data), self.path)
        except FileNotFoundError:
            self._data = {}
        except Exception as e:
            if log:
                log.warning("SMS: Konnte %s nicht laden (%s) -- starte leer.", self.path, e)
            self._data = {}

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            if log:
                log.warning("SMS: Konnte Punktketten nicht speichern: %s", e)

    def _color_for_new_sender(self) -> str:
        used = {v["color"] for v in self._data.values()}
        for c in SENDER_COLOR_PALETTE:
            if c not in used:
                return c
        # Palette erschöpft (>12 verschiedene Absender) -- deterministisch
        # aus der Anzahl bereits bekannter Absender ableiten, damit
        # Farben bei einem Neustart stabil bleiben statt zufällig zu sein.
        return SENDER_COLOR_PALETTE[len(self._data) % len(SENDER_COLOR_PALETTE)]

    def add_point(self, sender: str, lat: float, lon: float, raw_text: str,
                  confirmed: bool, pattern_label: str, sms_timestamp: Optional[str] = None) -> dict:
        sender = normalize_phone(sender)
        with self._lock:
            entry = self._data.get(sender)
            if entry is None:
                entry = {"sender": sender, "color": self._color_for_new_sender(), "points": []}
                self._data[sender] = entry
            point = {
                "id": uuid.uuid4().hex[:12],
                "lat": lat, "lon": lon,
                "received_at": datetime.now(timezone.utc).isoformat(),
                "sms_timestamp": sms_timestamp,
                "raw_text": (raw_text or "")[:400],
                "confirmed": confirmed,
                "pattern_label": pattern_label,
            }
            entry["points"].append(point)
            if len(entry["points"]) > self.max_points_per_sender:
                entry["points"] = entry["points"][-self.max_points_per_sender:]
            self._save()
            return dict(entry)

    def all_tracks(self) -> Dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._data.items()}

    def clear_sender(self, sender: str) -> bool:
        sender = normalize_phone(sender)
        with self._lock:
            existed = sender in self._data
            if existed:
                del self._data[sender]
                self._save()
            return existed


# ════════════════════════════════════════════════════════════════
# ROHNACHRICHTEN-LOG (für das Anlern-UI: letzte N SMS zum Auswählen)
# ════════════════════════════════════════════════════════════════

class SMSInboxLog:
    """Hält die letzten N tatsächlich empfangenen SMS im Speicher (NICHT
    persistiert -- reines Arbeitsgedächtnis für die Anlern-Oberfläche,
    verliert seinen Inhalt bewusst bei jedem Neustart, da alte Roh-SMS
    nach erfolgreicher Verarbeitung kein dauerhafter Mehrwert sind; die
    verarbeiteten Koordinaten selbst liegen dauerhaft in SMSTrackStore)."""

    def __init__(self, max_entries: int = 50):
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: List[dict] = []

    def add(self, entry: dict):
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self.max_entries:
                self._entries = self._entries[-self.max_entries:]

    def all(self) -> List[dict]:
        with self._lock:
            return list(reversed(self._entries))


# ════════════════════════════════════════════════════════════════
# AT-KOMMANDO SMS-EMPFANG (SIM800/900/7600-kompatibel)
# ════════════════════════════════════════════════════════════════

# Antwortzeile von AT+CMGL="ALL":
#   +CMGL: 3,"REC UNREAD","+491701234567",,"26/09/13,10:15:32+08"
# gefolgt vom Nachrichtentext in der/den nächsten Zeile(n), bis zur
# nächsten "+CMGL:"-Zeile oder "OK".
_CMGL_HEADER_RE = re.compile(
    r'^\+CMGL:\s*(?P<index>\d+)\s*,\s*"(?P<status>[^"]*)"\s*,\s*"(?P<sender>[^"]*)"'
    r'\s*,\s*"?(?P<sender_alpha>[^",]*)"?\s*,\s*"(?P<timestamp>[^"]*)"'
)


def _parse_cmgl_response(raw: str) -> List[dict]:
    """Zerlegt die komplette Antwort von AT+CMGL="ALL" in einzelne
    Nachrichten. Robust gegen leicht abweichende Zeilenenden/Whitespace,
    wie sie zwischen verschiedenen SIM800-Klonen/Treibern vorkommen."""
    messages = []
    lines = raw.replace("\r", "").split("\n")
    current = None
    body_lines: List[str] = []
    for line in lines:
        m = _CMGL_HEADER_RE.match(line.strip())
        if m:
            if current is not None:
                current["text"] = "\n".join(body_lines).strip()
                messages.append(current)
            current = {"index": int(m.group("index")), "status": m.group("status"),
                       "sender": m.group("sender"), "timestamp": m.group("timestamp")}
            body_lines = []
        elif current is not None:
            stripped = line.strip()
            if stripped in ("OK", "") and not body_lines:
                continue
            if stripped == "OK":
                continue
            body_lines.append(line)
    if current is not None:
        current["text"] = "\n".join(body_lines).strip()
        messages.append(current)
    return messages


class SMSReceiver(threading.Thread):
    """Fragt ein per USB-UART angeschlossenes GSM-Modem (SIM800/900/7600-
    kompatibel) per AT-Kommandos regelmäßig auf neue SMS ab (Polling,
    siehe Moduldocstring zur Begründung), extrahiert enthaltene
    Koordinaten und legt sie in SMSTrackStore ab.

    on_new_point: Callback(sender, track_dict) -- wird aufgerufen, sobald
    ein neuer Punkt hinzugefügt wurde, damit main.py sofort per WebSocket
    an alle verbundenen Clients broadcasten kann (statt bis zum nächsten
    Sekundentakt der ohnehin laufenden _broadcast_loop zu warten -- SMS-
    Empfang ist selten genug, dass sofortiges Feedback spürbar wertvoller
    ist als der minimal höhere Aufwand eines Extra-Broadcasts)."""

    RECONNECT_DELAY_SEC = 10
    AT_TIMEOUT_SEC = 5

    def __init__(self, cfg: dict, format_store: SMSFormatStore,
                 track_store: SMSTrackStore, inbox_log: SMSInboxLog,
                 on_new_point: Optional[Callable[[str, dict, dict], None]] = None):
        super().__init__(daemon=True, name="SMSReceiver")
        self.running = False
        self.enabled = cfg.get("enabled", False)
        self.port = cfg.get("port", "")
        self.baudrate = cfg.get("baudrate", 9600)
        self.poll_interval_sec = cfg.get("poll_interval_sec", 5)
        self.delete_after_read = cfg.get("delete_after_read", True)
        self.pin = str(cfg.get("pin", "") or "")

        self.format_store = format_store
        self.track_store = track_store
        self.inbox_log = inbox_log
        self.on_new_point = on_new_point

        self._ser = None
        self._lock = threading.Lock()
        self.connected = False
        self.last_error: Optional[str] = None
        self.last_success_ts: Optional[float] = None
        self.messages_received = 0
        self.messages_with_coords = 0

    # ── Low-Level AT-Kommunikation ──────────────────────────────
    def _send_at(self, cmd: str, wait_sec: Optional[float] = None) -> str:
        wait_sec = wait_sec or self.AT_TIMEOUT_SEC
        self._ser.reset_input_buffer()
        self._ser.write((cmd + "\r\n").encode("utf-8", "ignore"))
        deadline = time.time() + wait_sec
        buf = b""
        while time.time() < deadline:
            chunk = self._ser.read(self._ser.in_waiting or 1)
            if chunk:
                buf += chunk
                if b"OK" in buf or b"ERROR" in buf:
                    # kurze Nachlese -- manche Modems senden nach OK/ERROR
                    # noch ein paar Bytes Nachzügler (z.B. Zeilenumbrüche)
                    time.sleep(0.1)
                    buf += self._ser.read(self._ser.in_waiting or 0)
                    break
            else:
                time.sleep(0.05)
        return buf.decode("utf-8", "ignore")

    def _connect(self):
        import serial  # lazy import -- pyserial muss nicht zwingend
                        # installiert sein, wenn SMS-Empfang deaktiviert ist
        self._ser = serial.Serial(self.port, self.baudrate, timeout=1)
        time.sleep(0.3)
        resp = self._send_at("AT")
        if "OK" not in resp:
            raise ConnectionError(f"Modem antwortet nicht auf 'AT' (Antwort: {resp!r})")
        if self.pin:
            pin_status = self._send_at("AT+CPIN?")
            if "READY" not in pin_status:
                pin_resp = self._send_at(f'AT+CPIN="{self.pin}"', wait_sec=8)
                if "OK" not in pin_resp:
                    raise ConnectionError(f"PIN-Eingabe fehlgeschlagen: {pin_resp!r}")
        cmgf = self._send_at("AT+CMGF=1")  # Textmodus statt PDU-Modus
        if "OK" not in cmgf:
            raise ConnectionError(f"AT+CMGF=1 fehlgeschlagen (Textmodus nicht gesetzt): {cmgf!r}")
        self._send_at('AT+CSCS="GSM"')
        self.connected = True
        self.last_error = None
        if log:
            log.info("SMS: Modem auf %s (%d Baud) verbunden und im Textmodus konfiguriert.",
                     self.port, self.baudrate)

    def _disconnect(self):
        self.connected = False
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        self._ser = None

    # ── Nachrichtenverarbeitung ──────────────────────────────────
    def _process_message(self, msg: dict):
        sender = msg.get("sender", "")
        text = msg.get("text", "")
        timestamp = msg.get("timestamp")
        self.messages_received += 1

        fmt = self.format_store.get()
        confirmed = False
        used_label = None
        results = []
        if fmt.get("pattern_id"):
            if fmt["pattern_id"] == "custom" and fmt.get("custom_regex"):
                try:
                    pat = custom_pattern_from_regex(fmt["custom_regex"])
                    m = pat["regex"].search(text)
                    if m:
                        lat, lon = pat["convert"](m)
                        if _plausible(lat, lon):
                            results = [{"pattern_id": "custom", "label": fmt.get("label") or "Eigene Regex",
                                        "lat": round(lat, 6), "lon": round(lon, 6), "matched_text": m.group(0)}]
                except Exception as e:
                    if log:
                        log.warning("SMS: Angelernte eigene Regex fehlgeschlagen (%s) -- "
                                    "versuche eingebaute Musterbibliothek als Rückfallebene.", e)
            else:
                results = try_parse_coordinates(text, only_pattern_id=fmt["pattern_id"])
            if results:
                confirmed = True
                used_label = fmt.get("label")
            elif log:
                log.warning("SMS: Angelerntes Format '%s' passte NICHT auf eine eingehende "
                           "Nachricht von %s -- versuche automatische Erkennung als Rückfallebene.",
                           fmt.get("label"), sender)

        if not results:
            results = try_parse_coordinates(text)
            confirmed = False

        inbox_entry = {
            "id": uuid.uuid4().hex[:12],
            "sender": normalize_phone(sender),
            "sender_raw": sender,
            "timestamp": timestamp,
            "text": text,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "parsed": bool(results),
            "confirmed_format": confirmed,
        }
        self.inbox_log.add(inbox_entry)

        if results:
            hit = results[0]
            self.messages_with_coords += 1
            track = self.track_store.add_point(
                sender=sender, lat=hit["lat"], lon=hit["lon"], raw_text=text,
                confirmed=confirmed, pattern_label=used_label or hit["label"],
                sms_timestamp=timestamp)
            if log:
                log.info("SMS: Koordinate von %s erkannt (%.5f, %.5f) via '%s'%s",
                         normalize_phone(sender), hit["lat"], hit["lon"], hit["label"],
                         "" if confirmed else " [unbestätigt]")
            if self.on_new_point:
                try:
                    self.on_new_point(normalize_phone(sender), track, inbox_entry)
                except Exception as e:
                    if log:
                        log.debug("SMS: on_new_point-Callback fehlgeschlagen: %s", e)
        else:
            if log:
                log.info("SMS: Nachricht von %s enthielt keine erkennbaren Koordinaten "
                         "(im Anlern-Bereich der Weboberfläche einsehbar).",
                         normalize_phone(sender))

    def _poll_once(self):
        raw = self._send_at('AT+CMGL="ALL"', wait_sec=8)
        messages = _parse_cmgl_response(raw)
        for msg in messages:
            try:
                self._process_message(msg)
            except Exception as e:
                if log:
                    log.error("SMS: Fehler bei Verarbeitung einer Nachricht (%s): %s",
                             type(e).__name__, e)
            if self.delete_after_read:
                try:
                    self._send_at(f'AT+CMGD={msg["index"]}')
                except Exception:
                    pass
        if messages:
            self.last_success_ts = time.time()
            self.last_error = None

    def run(self):
        self.running = True
        if not self.enabled:
            if log:
                log.info("SMS-Empfang: deaktiviert (sms.enabled: false in config.yaml).")
            return
        if not self.port:
            self.last_error = "Kein serieller Port konfiguriert (sms.port in config.yaml)."
            if log:
                log.warning("SMS-Empfang NICHT gestartet: %s", self.last_error)
            return
        try:
            import serial  # noqa -- nur Verfügbarkeit prüfen
        except ImportError:
            self.last_error = "Paket 'pyserial' nicht installiert -- pip install pyserial"
            if log:
                log.error("SMS-Empfang NICHT gestartet: %s", self.last_error)
            return

        while self.running:
            try:
                if not self.connected:
                    self._connect()
                self._poll_once()
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                if log:
                    log.warning("SMS-Empfang: Fehler (%s) -- neuer Verbindungsversuch in %ds.",
                               self.last_error, self.RECONNECT_DELAY_SEC)
                self._disconnect()
                time.sleep(self.RECONNECT_DELAY_SEC)
                continue
            time.sleep(self.poll_interval_sec)

    def stop(self):
        self.running = False
        self._disconnect()

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "port": self.port,
            "last_error": self.last_error,
            "last_success_ts": self.last_success_ts,
            "messages_received": self.messages_received,
            "messages_with_coords": self.messages_with_coords,
        }
