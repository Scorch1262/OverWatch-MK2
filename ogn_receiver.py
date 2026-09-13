"""
OverWatchMK2 – FLARM/OGN-Empfang über das öffentliche OGN-Internet-Netzwerk
============================================================================

OverWatchMK2 zeigt AUSSCHLIESSLICH Online-Daten an (siehe CHANGELOG [1.0.0]:
lokaler RTL-SDR-Empfang inkl. `LocalAPRSServer` wurde bewusst komplett
entfernt, ebenso wie lokale Drohnen-/ADS-B-/C-ITS-Ortung). Dieses Modul
enthält deshalb nur noch `OGNInternetClient`: einen ganz normalen APRS-IS-
Client, der sich zum echten, weltweiten Open-Glider-Network anmeldet
(`aprs.glidernet.org:14580`) und Positionsmeldungen von Segelflugzeugen,
Kleinflugzeugen und FLARM/ADS-L-ausgerüsteten Luftfahrzeugen in der Nähe
empfängt. Passwort "-1" bedeutet laut APRS-IS-Protokoll "nur lesend, keine
eigenen Daten senden" -- kein echtes, personengebundenes APRS-Passwort
nötig.

Das APRS-Positionsformat und die OGN-spezifische Kommentar-Erweiterung
(id/climb/rot/...) sind vom OGN-Projekt öffentlich dokumentiert (siehe
deren `aprs.txt`) -- hier nicht neu erfunden, sondern nach dieser
Dokumentation nachgebaut.

HINWEIS ZUR GENAUIGKEIT: Die Aufschlüsselung des OGN-ID-Bytes in
Flugzeugtyp/Stealth/No-Track-Flags folgt der öffentlich dokumentierten
Standard-Belegung, kann sich aber je nach OGN-Protokollversion in
Details unterscheiden -- wo unklar, wird der Rohwert zusätzlich
mitgeliefert, damit nichts verloren geht.
"""

import math
import re
import socket
import threading
import time
from typing import Optional, Dict

log = None  # wird von main.py per set_logger() gesetzt


def set_logger(logger):
    global log
    log = logger


# Bit 7 (0x80) = Stealth-Modus, Bit 6 (0x40) = No-Track gewünscht,
# Bits 5-2 = Flugzeugtyp (4 bits), Bits 1-0 = Adresstyp (öffentlich
# dokumentierte OGN-Standardbelegung, siehe glidernet/OGN-Wiki
# "aprs.txt"). WICHTIG: Diese Bit-Aufteilung konnte hier nicht gegen
# eine echte laufende ogn-decode-Instanz verifiziert werden (kein
# RTL-SDR in dieser Entwicklungsumgebung) -- der Rohwert (`id_flags_raw`)
# wird deshalb IMMER mitgeliefert, damit bei Abweichungen nichts
# verloren geht und sich die Zuordnung notfalls im Nachhinein per
# Vergleich mit bekannten Flugzeugen korrigieren lässt.
OGN_AIRCRAFT_TYPES = {
    0x1: "Segelflugzeug", 0x2: "Schleppflugzeug", 0x3: "Helikopter",
    0x4: "Fallschirm", 0x5: "Flugzeug (frei)", 0x6: "Hängegleiter",
    0x7: "Gleitschirm", 0x8: "Motorflugzeug", 0x9: "Jet",
    0xA: "UFO/unbekannt", 0xB: "Ballon", 0xC: "Luftschiff",
    0xD: "UAV/Drohne", 0xF: "Sonstiges",
}

# APRS-Positionszeile (unkomprimiertes Format), z.B.:
# FLRDDA5BA>APRS,qAS,LFMX:/074548h4415.41N/00600.03E'091/091/A=002088 id0ADDA5BA -454fpm +0.0rot 8.8dB 0e -6.6kHz
_POS_RE = re.compile(
    r"^(?P<src>[\w-]+)>(?P<dst>[\w,*]+):[/=@](?P<time>\d{6})h"
    r"(?P<lat>\d{2})(?P<latmin>\d{2}\.\d+)(?P<ns>[NS])."
    r"(?P<lon>\d{3})(?P<lonmin>\d{2}\.\d+)(?P<ew>[EW])."
    r"(?P<course>\d{3})/(?P<speed>\d{3})"
    r"(?:/A=(?P<alt>\d{6}))?\s*(?P<comment>.*)$"
)

# OGN-Kommentar-Erweiterung, z.B.: "id0ADDA5BA -454fpm +0.0rot 8.8dB 0e -6.6kHz"
_COMMENT_RE = re.compile(
    r"id(?P<flags>[0-9A-Fa-f]{2})(?P<addr>[0-9A-Fa-f]{6})"
    r"(?:\s+(?P<climb>[+-]\d+)fpm)?"
    r"(?:\s+(?P<rot>[+-][\d.]+)rot)?"
    r"(?:\s+(?P<snr>[\d.]+)dB)?"
)


# Plausibilitätsgrenzen für einzelne FLARM/OGN-Positionsmeldungen --
# SEIT 1.10.3 (Nutzermeldung nach echtem Feldtest): Bei tatsächlicher
# Hochfrequenz-Reception am Rand der Reichweite/mit Rauschen können
# einzelne APRS-Zeilen trotz syntaktisch korrektem Format inhaltlich
# fehlerhaft sein (Bitfehler bei der Dekodierung, die dennoch ein
# gültiges Format ergeben) -- beobachtetes Symptom: eine einzelne
# Meldung mit absurder Höhe (>60000m, physikalisch für FLARM-Verkehr
# unmöglich), gefolgt von einer Position, die mehrere hundert
# Kilometer vom vorherigen, plausiblen Standort desselben Flugzeugs
# entfernt liegt. Die Parser-Mathematik selbst (Grad/Minuten-
# Umrechnung, Regex) wurde unabhängig mit sauberen Testdaten
# verifiziert und ist korrekt -- hier geht es NICHT um einen
# Parsing-Fehler, sondern um das Verwerfen inhaltlich unplausibler,
# aber syntaktisch valider Rohdaten, bevor sie angezeigt werden
# (dasselbe Prinzip wie die bereits vorhandene 0,0-Verwerfung bei
# Drohnen-Positionen ohne GPS-Fix).
_MAX_PLAUSIBLE_ALT_M = 15000       # deutlich über jeder realistischen
                                    # FLARM-Verkehrshöhe (Segler/GA/Drohnen)
_MAX_PLAUSIBLE_SPEED_MS = 250      # ~900 km/h, großzügig über allem was
                                    # FLARM-ausgerüstete Luftfahrzeuge
                                    # real erreichen -- verhindert das
                                    # Anzeigen physikalisch unmöglicher
                                    # Sprünge, ohne echte schnelle
                                    # Bewegung fälschlich zu verwerfen


def parse_aprs_position(line: str) -> Optional[dict]:
    """
    Parst eine einzelne APRS-Positionszeile im von ogn-decode gesendeten
    Format. Gibt None zurück bei Nicht-Positionszeilen (Kommentare,
    Server-Statuszeilen mit '#', Login-Antworten) oder bei nicht
    interpretierbaren Zeilen -- wirft NIE eine Exception nach außen,
    damit eine einzelne unerwartet formatierte Zeile nie den gesamten
    Empfang zum Stehen bringt.
    """
    if not line or line.startswith("#"):
        return None
    m = _POS_RE.match(line)
    if not m:
        return None
    try:
        lat = int(m.group("lat")) + float(m.group("latmin")) / 60.0
        if m.group("ns") == "S":
            lat = -lat
        lon = int(m.group("lon")) + float(m.group("lonmin")) / 60.0
        if m.group("ew") == "W":
            lon = -lon
        alt_ft = int(m.group("alt")) if m.group("alt") else None
        rec = {
            "src": m.group("src"),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "course_deg": int(m.group("course")),
            "speed_kt": int(m.group("speed")),
            "alt_m": round(alt_ft * 0.3048, 1) if alt_ft is not None else None,
        }
        cm = _COMMENT_RE.search(m.group("comment") or "")
        if cm:
            flags = int(cm.group("flags"), 16)
            aircraft_type_code = (flags >> 2) & 0x0F
            rec["ogn_id"] = cm.group("addr").upper()
            rec["id_flags_raw"] = cm.group("flags").upper()
            rec["stealth"] = bool(flags & 0x80)
            rec["no_track"] = bool(flags & 0x40)
            rec["aircraft_type"] = OGN_AIRCRAFT_TYPES.get(
                aircraft_type_code, f"Unbekannt (0x{aircraft_type_code:X})")
            if cm.group("climb"):
                rec["climb_fpm"] = int(cm.group("climb"))
            if cm.group("rot"):
                rec["rotation"] = float(cm.group("rot"))
            if cm.group("snr"):
                rec["snr_db"] = float(cm.group("snr"))
        else:
            rec["ogn_id"] = m.group("src")
            rec["aircraft_type"] = "Unbekannt"
        return rec
    except (ValueError, TypeError, IndexError):
        return None

class OGNInternetClient(threading.Thread):
    """
    Zusätzliche FLARM/OGN-Datenquelle aus dem ECHTEN, weltweiten OGN-
    Netzwerk (Open Glider Network) -- analog zum bereits vorhandenen
    Muster bei ADS-B (lokaler Empfänger UND Internet-Anbieter parallel,
    farblich unterschieden, siehe main.py ADSBReceiver). Verbindet sich
    dafür als ganz normaler APRS-IS-CLIENT (nicht als Server wie
    LocalAPRSServer, das dort ja `ogn-decode` täuscht) zum echten
    öffentlichen Server `aprs.glidernet.org:14580`, meldet sich mit
    einem Positions-/Radius-Filter an (nur Verkehr in der Nähe, nicht
    das gesamte weltweite Netzwerk) und liest die Positionszeilen mit
    genau demselben, bereits vorhandenen Parser (`parse_aprs_position`)
    wie beim lokalen Empfang.

    Passwort "-1" bedeutet laut APRS-IS-Protokoll "nur lesend, keine
    eigenen Daten senden" -- dafür ist kein echtes, personengebundenes
    APRS-Passwort nötig (das bräuchte man nur, wenn man selbst Daten
    ins Netzwerk EINSPEISEN wollte).
    """

    APRS_HOST = "aprs.glidernet.org"
    APRS_PORT = 14580
    RECONNECT_DELAY_SEC = 15

    def __init__(self, cfg: dict, center_lat: float, center_lon: float):
        super().__init__(daemon=True, name="OGNInternetClient")
        self.running = False
        self._lock = threading.Lock()
        self._aircraft: Dict[str, dict] = {}

        self.enabled = cfg.get("internet_enabled", True)
        self.call = cfg.get("internet_call", "OVERWATCH1")
        self.center_lat = cfg.get("internet_lat", center_lat)
        self.center_lon = cfg.get("internet_lon", center_lon)
        self.radius_km = cfg.get("internet_radius_km", 50)
        self.station_timeout_sec = cfg.get("station_timeout_sec", 120)

        self.last_success_ts: Optional[float] = None
        self.last_error: Optional[str] = None
        self.packets_received = 0
        self._sock: Optional[socket.socket] = None

    def _purge_stale(self):
        now = time.time()
        with self._lock:
            self._aircraft = {
                k: v for k, v in self._aircraft.items()
                if now - v["last_seen"] < self.station_timeout_sec
            }

    def get_all(self):
        self._purge_stale()
        with self._lock:
            return list(self._aircraft.values())

    def run(self):
        self.running = True
        if not self.enabled:
            if log:
                log.info("FLARM/OGN-Internet: deaktiviert (flarm.internet_enabled: false)")
            return
        while self.running:
            try:
                self._connect_and_read()
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                if log:
                    log.warning("FLARM/OGN-Internet: Verbindung zu %s:%d fehlgeschlagen "
                               "(%s) -- neuer Versuch in %ds",
                               self.APRS_HOST, self.APRS_PORT, self.last_error,
                               self.RECONNECT_DELAY_SEC)
            if not self.running:
                break
            time.sleep(self.RECONNECT_DELAY_SEC)

    def _connect_and_read(self):
        sock = socket.create_connection((self.APRS_HOST, self.APRS_PORT), timeout=15)
        self._sock = sock
        sock.settimeout(60)  # großzügiger als beim lokalen Server -- das
                             # echte OGN-Netzwerk sendet eigene, uns
                             # unbekannt getaktete Keepalives, kein Grund
                             # für uns, selbst aktiv welche zu erwarten
        try:
            # Radius-Filter: nur Verkehr in der Nähe der eigenen Station,
            # nicht das gesamte weltweite Netzwerk (Format laut
            # APRS-IS-Protokoll: "r/Breitengrad/Längengrad/RadiusKm").
            login = (f"user {self.call} pass -1 vers OverWatchMK2 1.0 "
                    f"filter r/{self.center_lat}/{self.center_lon}/{self.radius_km}\r\n")
            sock.sendall(login.encode("utf-8"))
            # FIX (analog zu OpenTrafficMapClient, Nutzermeldung 08.09.2026):
            # last_error erst bei erfolgreicher Positionsverarbeitung
            # zurückzusetzen ließ einen alten Fehler aus einem vorherigen
            # Verbindungsversuch irreführend lange im Status stehen, obwohl
            # die Verbindung selbst längst wieder stand.
            self.last_error = None
            if log:
                log.info("FLARM/OGN-Internet: verbunden mit %s:%d (Filter: "
                        "%.4f/%.4f, Radius %dkm)", self.APRS_HOST, self.APRS_PORT,
                        self.center_lat, self.center_lon, self.radius_km)
            buf = b""
            while self.running:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionError("Verbindung vom Server geschlossen")
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("utf-8", "ignore").strip("\r\n")
                    if not line:
                        continue
                    rec = parse_aprs_position(line)
                    self.packets_received += 1
                    if rec:
                        rec["last_seen"] = time.time()
                        rec["source"] = "Internet (OGN Netzwerk)"
                        with self._lock:
                            previous = self._aircraft.get(rec["ogn_id"])
                            ok, reason = is_plausible_update(rec, previous)
                            if ok:
                                self._aircraft[rec["ogn_id"]] = rec
                            elif log:
                                log.info("FLARM/OGN-Internet: Meldung für %s verworfen "
                                        "(%s).", rec["ogn_id"], reason)
                        self.last_success_ts = time.time()
                        self.last_error = None
        finally:
            try:
                sock.close()
            except Exception:
                pass
            self._sock = None

    def stop(self):
        self.running = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
