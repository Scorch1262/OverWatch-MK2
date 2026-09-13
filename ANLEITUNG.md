# OverWatchMK2 – Anleitung

## 1. Installation & Start

1. Release-Zip von der [Releases-Seite](../../releases) herunterladen
   und an einen beliebigen Ort entpacken (z.B. `C:\OverWatchMK2\` oder
   `~/OverWatchMK2/`).
2. **Windows:** `OverWatchMK2.exe` doppelklicken. Ein Konsolenfenster
   öffnet sich und zeigt die Netzwerkadresse an.
3. **macOS:** `OverWatchMK2.app` per Rechtsklick -> "Öffnen" starten
   (siehe Abschnitt 6 zu Gatekeeper -- ein normaler Doppelklick schlägt
   beim allerersten Start fehl, da das Programm nicht kostenpflichtig
   von Apple signiert ist). Ein Terminalfenster öffnet sich mit
   Diagnose-/Log-Ausgaben.
4. Die im Konsolen-/Terminalfenster angezeigte Adresse
   (z.B. `http://192.168.1.42:8080`) im Browser öffnen -- funktioniert
   von JEDEM Gerät im selben Netzwerk aus, nicht nur vom Rechner, auf
   dem OverWatchMK2 läuft.
5. Zum Beenden das Konsolen-/Terminalfenster schließen oder `Strg+C`
   (macOS: `Cmd+C`) drücken.

## 2. Konfiguration (`config.yaml`)

Liegt direkt neben der `.exe`/`.app` und wird bei einem Versions-Update
NIE überschrieben -- eigene Anpassungen bleiben erhalten. Wichtige
Optionen:

- `adsb.provider`: welcher Internet-Anbieter für Flugzeugdaten genutzt
  wird (`adsbfi` als Standard braucht keinen Account; `opensky` ist
  ohne Account stark rate-limitiert).
- `sms.enabled` / `sms.port` / `sms.baudrate`: siehe Abschnitt 4.
- `map.default_lat` / `map.default_lon`: Startposition der Karte.

Nach jeder Änderung muss OverWatchMK2 neu gestartet werden.

## 3. Offline-Karte

Eine beliebige `.mbtiles`-Datei (Standard-SQLite-basiertes Format für
Kartenkacheln) wird automatisch erkannt, wenn sie **direkt neben** der
`.exe`/`.app` liegt (Dateiname ist egal) -- oder in einem Unterordner
`offline_map/` daneben (dieser Unterordner ist im Release-Zip bereits
als leerer Platzhalter enthalten). Nach dem Ablegen der Datei
OverWatchMK2 neu starten; in der Sidebar erscheint dann unter
"Offline-Karte statt Online" der Hinweis, dass Kacheln gefunden wurden.

Eine eigene `.mbtiles`-Datei lässt sich z.B. mit folgenden
Open-Source-Werkzeugen erzeugen (nicht Teil dieses Projekts, jeweils
eigene Nutzungsbedingungen der Kartenanbieter beachten):

- [MOBAC](https://mobac.sourceforge.io/) (einfache Desktop-Oberfläche)
- [TileMill](https://tilemill-project.github.io/tilemill/) /
  [Maperitive](http://maperitive.net/)
- Ein selbst gehosteter OSM-Tile-Server (z.B.
  `overv/openstreetmap-tile-server` auf Docker Hub) für vollständige,
  im Detailgrad identische Karten eines größeren Gebiets.

## 4. SMS-Ortung einrichten

### 4.1 Hardware anschließen

Ein USB-UART-Adapter (z.B. CP2102, CH340, FT232) verbindet den PC/Mac
mit einem GSM-Modem (SIM800L/SIM800C/SIM900/SIM7600-kompatibel). Eine
aktive SIM-Karte mit SMS-Empfang wird benötigt.

### 4.2 Seriellen Port herausfinden

- **Windows:** Geräte-Manager -> "Anschlüsse (COM & LPT)" -> der
  Adapter erscheint z.B. als `COM3`.
- **macOS:** Terminal öffnen, `ls /dev/cu.*` ausführen -- der Adapter
  erscheint z.B. als `/dev/cu.usbserial-0001` oder `/dev/cu.SLAB_USBtoUART`.
- **Linux (Entwicklung):** `ls /dev/ttyUSB*` bzw. `ls /dev/ttyACM*`.

### 4.3 config.yaml anpassen

```yaml
sms:
  enabled: true
  port: "COM3"          # bzw. "/dev/cu.usbserial-0001" auf macOS
  baudrate: 9600         # SIM800-Standard, ggf. 115200 bei manchen Klonen
  poll_interval_sec: 5
  delete_after_read: true
  pin: ""                # SIM-PIN, falls eine gesetzt ist
```

OverWatchMK2 neu starten. In der Sidebar unter "SMS-Ortung" sollte der
Status-Punkt nach kurzer Zeit grün werden.

### 4.4 Koordinatenformat einmalig anlernen

Jedes Tracker-Gerät formatiert seine Positions-SMS unterschiedlich.
OverWatchMK2 versucht automatisch mehrere gängige Formate zu erkennen,
liefert aber erst nach einmaliger Bestätigung ("Anlernen") zuverlässige,
eindeutige Ergebnisse:

1. Warten, bis die erste echte Positions-SMS eingetroffen ist (oder
   Sidebar -> "SMS-Ortung" -> "Empfangene SMS anzeigen", falls schon
   eine da ist).
2. Sidebar -> "SMS-Ortung" -> "Format anlernen" klicken.
3. Entweder den Beispieltext von Hand einfügen, oder in der Inbox-
   Ansicht bei der gewünschten Nachricht auf "Als Beispiel für Anlernen
   nutzen" klicken.
4. "Erkennung testen" klicken -- alle passenden Formate werden mit den
   erkannten Koordinaten angezeigt.
5. Den richtigen Treffer anklicken, um ihn zu bestätigen.

Ab sofort werden alle künftigen SMS mit diesem Format sofort und
eindeutig eingezeichnet. Passt keines der eingebauten Formate, kann
unter "Fortgeschritten: eigene Regex" ein eigener regulärer Ausdruck
mit den benannten Gruppen `(?P<lat>...)` und `(?P<lon>...)` (jeweils in
Dezimalgrad) eingegeben werden.

Zum erneuten Anlernen (z.B. bei einem neuen, anders formatierenden
Tracker-Gerät): "Angelerntes Format zurücksetzen" klicken.

### 4.5 Anzeige & Verwaltung

Jede Absendernummer erhält automatisch eine eigene, dauerhaft
zugewiesene Farbe. Alle ihre empfangenen Positionen werden als farbige
Punktkette in Empfangsreihenfolge (verbindende Linie) auf der Karte
angezeigt. In der Sidebar unter "SMS-Ortung" lässt sich die Punktkette
einer einzelnen Nummer über das "✕"-Symbol vollständig löschen.

### 4.6 Ohne Hardware testen

Für Entwicklung/Vorführung ohne angeschlossenes Modem steht folgender
Test-Endpunkt bereit (z.B. mit `curl`):

```bash
curl -X POST http://localhost:8080/api/sms/simulate \
  -H "Content-Type: application/json" \
  -d '{"sender":"+491701234567","text":"Standort: 52.5200,13.4050"}'
```

## 5. Fehlerbehebung

- **"Keine Ergebnisse" bei der Adresssuche / Karte lädt nicht:**
  Adresssuche und die Online-Kartenkacheln benötigen eine
  Internetverbindung. Koordinateneingaben (z.B. `52.52, 13.40`) und die
  Offline-Karte funktionieren auch ohne Internet.
- **ADS-B/FLARM-Status bleibt rot/grau:** Meist eine blockierte
  Firewall/kein Internetzugang. Die genaue Fehlermeldung steht im
  Log (`overwatchmk2.log`, neben der exe/.app).
- **SMS-Status bleibt rot:** Prüfen, ob der in `config.yaml`
  eingetragene `port` tatsächlich existiert (siehe Abschnitt 4.2) und
  ob ein anderes Programm (z.B. ein SMS-Terminal-Tool) den Port
  gerade blockiert -- ein serieller Port kann immer nur von einem
  Programm gleichzeitig geöffnet werden.

## 6. macOS Gatekeeper

Da `OverWatchMK2.app` nicht mit einem kostenpflichtigen Apple-
Entwicklerzertifikat signiert ist, blockiert macOS beim allerersten
Start die Ausführung ("kann nicht geöffnet werden, da der Entwickler
nicht verifiziert werden kann"). Abhilfe (nur beim ersten Start nötig):
Rechtsklick auf `OverWatchMK2.app` -> "Öffnen" -> im Dialog erneut
"Öffnen" bestätigen. Ab dann startet die App auch per normalem
Doppelklick.
