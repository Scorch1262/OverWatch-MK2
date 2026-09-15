# OverWatchMK2

Reiner Online-Anzeiger für ADS-B (Flugzeuge), FLARM/OGN (Segelflieger/
Kleinflugzeuge) und Starlink-Satelliten, plus SMS-basierten
Ortungsempfang über ein per USB-UART angeschlossenes GSM-Modem
(SIM800/900/7600-kompatibel). Läuft als eigenständiges Windows-`.exe`
oder macOS-`.app` und startet dabei eine im lokalen Netzwerk erreichbare
Weboberfläche (Leaflet-Karte + WebSocket-Live-Updates) -- kein separater
Server, keine Installation nötig.

Abgewandeltes Nachfolgeprojekt von OverWatchMK1: sämtliche lokale
Funk-/Sensor-Ortung (Drohnen-Remote-ID, lokaler ADS-B-/FLARM-Empfang per
RTL-SDR, lokaler C-ITS-Empfang) wurde entfernt -- siehe
[CHANGELOG.md](CHANGELOG.md) für die vollständige Begründung jeder
Änderung.

## Funktionen

- **ADS-B** -- Live-Flugzeugpositionen über öffentliche Internet-APIs
  (adsb.fi / airplanes.live / adsb.lol / OpenSky Network).
- **FLARM/OGN** -- Segelflieger/Kleinflugzeuge über das echte,
  weltweite Open-Glider-Network (`aprs.glidernet.org`).
- **Starlink** -- Live-Satellitenpositionen aus öffentlichen
  TLE-Bahnelementen (CelesTrak/Space-Track), lokal per SGP4 berechnet.
- **SMS-Ortung** -- ein angeschlossenes GSM-Modem wird auf eingehende
  SMS abgefragt; enthaltene Koordinaten werden automatisch erkannt
  (nach einmaligem "Anlernen" des SMS-Textformats über die
  Weboberfläche) und als farbige, nach Absendernummer getrennte
  Punktketten auf der Karte eingezeichnet.
- **Offline-Karte** -- eine `.mbtiles`-Datei direkt neben der
  exe/.app wird automatisch erkannt und als Offline-Kartenebene
  angeboten.
- **Dunkles Kartendesign** und Kartendarstellung/Symbole für ADS-B und
  FLARM identisch zum Vorgängerprojekt OverWatchMK1 übernommen --
  inklusive Flugbahn-/Prognose-Linien, Satelliten-Bodenspuren und
  einzeln ein-/ausblendbarer Ebenen (ADS-B, FLARM, Starlink,
  SMS-Ortung, No-Fly-Zonen).
- **No-Fly-Zonen** (Deutschland, dipul/DFS) und **Adress-/
  Koordinatensuche** (Nominatim).
- Im gesamten lokalen Netzwerk erreichbar (Handy, Tablet, anderer PC).

## Schnellstart

1. Neuestes Release unter [Releases](../../releases) herunterladen
   (`OverWatchMK2-windows.zip` bzw. `OverWatchMK2-macos.zip`).
2. Entpacken, `OverWatchMK2.exe` (Windows) bzw. `OverWatchMK2.app`
   (macOS) starten.
3. Browser öffnet sich nicht automatisch -- die Konsolenausgabe zeigt
   die Adresse an, z.B. `http://192.168.1.42:8080`. Diese Adresse im
   Browser öffnen (auch von einem anderen Gerät im selben Netzwerk aus).
4. Einstellungen (ADS-B-Anbieter, SMS-Port, ...) in der `config.yaml`
   neben der exe/.app anpassen und das Programm neu starten.

Ausführliche Anleitung inkl. SMS-Ortung, Offline-Karte und
Fehlerbehebung: siehe [ANLEITUNG.md](ANLEITUNG.md).

## Selbst bauen

```bash
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --clean --noconfirm OverWatchMK2.spec
```

Das GitHub-Actions-Workflow (`.github/workflows/build.yml`) baut beide
Plattformen automatisch bei jedem Push nach `main` bzw. bei jedem
`v*.*.*`-Tag (dann inkl. automatischem GitHub Release).

## Entwicklung

```bash
pip install -r requirements.txt
python main.py --debug
```

Nützliche Kommandozeilen-Flags: `--no-adsb`, `--no-starlink`,
`--no-flarm`, `--no-sms`, `--test-starlink`, `--version`, `--debug`.

Zum Testen der SMS-Ortung OHNE angeschlossenes Modem steht der
Entwicklungsendpunkt `POST /api/sms/simulate` zur Verfügung (siehe
CHANGELOG.md und ANLEITUNG.md).

## Lizenz

MIT, siehe [LICENSE](LICENSE).
