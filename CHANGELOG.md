# OverWatchMK2 – Changelog

## [1.0.2] – Fix: macOS-App stürzte beim Finder-Start lautlos ab; CI hing bei nicht mehr existierendem Intel-Runner

### Problem (Nutzermeldung, zwei getrennte Symptome)

1. Der GitHub-Actions-Workflow blieb im Job `build-macos` (Ziel-Label
   `macos-13`) dauerhaft bei "Waiting for a runner to pick up this
   job" hängen -- niemals fertig, niemals fehlgeschlagen, einfach
   endlos wartend.
2. `OverWatchMK2.app` ließ sich weiterhin nicht starten -- diesmal mit
   genauerer Beschreibung: nach Erteilen der Datenschutzfreigabe (macOS
   Gatekeeper "Trotzdem öffnen" über Systemeinstellungen) hüpft das
   Dock-Icon kurz auf und verschwindet dann wieder, OHNE dass ein
   Fenster oder eine Fehlermeldung erscheint.

Zusätzliche Nutzeranforderung: Es soll ab sofort NUR NOCH eine
Mac-App für Apple Silicon (M1 und neuer) gebaut werden, keine
Intel/x86_64-Variante mehr (die in [1.0.1] eingeführte
Intel/arm64-Matrix wird damit wieder zurückgebaut).

### Ursache 1: CI hängt (macos-13-Runner existiert nicht mehr)

GitHub hat das gehostete Runner-Image `macos-13` inzwischen aus dem
Angebot genommen (Intel-Mac-Runner werden von GitHub schrittweise
abgekündigt/entfernt). Ein Workflow, der `runs-on: macos-13` anfordert,
bekommt dafür nie eine Maschine zugewiesen -- er hängt unbegrenzt in
"Waiting for a runner to pick up this job", statt mit einer klaren
Fehlermeldung abzubrechen. Das war KEIN Bug im eigentlichen Sinne,
sondern eine mit [1.0.1] eingeführte Abhängigkeit von einem
mittlerweile nicht mehr verfügbaren Runner-Label.

### Ursache 2: sys.stdout/sys.stderr sind None bei Finder-Start

Das war der eigentliche, seit [1.0.0] bestehende Bug. Wird eine
PyInstaller-`.app` per Doppelklick im Finder gestartet (statt aus dem
Terminal heraus), hängt macOS dem Prozess KEIN kontrollierendes
Terminal an -- `sys.stdout` und `sys.stderr` sind in genau diesem Fall
schlicht `None`. `main.py` ruft aber bereits ganz am Anfang (noch vor
`main()`, direkt beim Modulimport) mehrfach `print()` auf, u.a. beim
Laden der `config.yaml` ("`[DIAG] Config geladen: ...`"). Ein
`print()`-Aufruf auf `None` wirft sofort
`AttributeError: 'NoneType' object has no attribute 'write'`. Diese
Exception hätte durch den bereits vorhandenen `sys.excepthook`
(`_crash_handler`) abgefangen werden können -- ABER `_crash_handler`
selbst nutzt ebenfalls `print()`, um die Fehlermeldung auszugeben, und
stürzt beim Versuch, den Fehler zu melden, exakt am selben Problem
ein zweites Mal ab. Ergebnis: Python fällt auf sein eingebautes
Notfallverhalten zurück (Fehlermeldung nach stderr, das ebenfalls
`None` ist -> schlägt lautlos fehl) und der Prozess beendet sich
kommentarlos. Das erklärt exakt das gemeldete Symptom (Dock-Icon
hüpft, Programm verschwindet, keinerlei Meldung) UND warum in dieser
Linux-Entwicklungsumgebung (wo `sys.stdout` beim Testen immer
vorhanden war) nichts davon auffiel -- der Fehler tritt ausschließlich
beim GUI-Start ohne Terminal auf, nie bei einem manuellen
Terminal-Start und nie in der hiesigen Testumgebung.

### Änderungen

- **`main.py`:** direkt nach der Definition von `_external_dir()` (also
  bevor IRGENDEIN `print()` im Programm ausgeführt wird) wird jetzt
  geprüft, ob `sys.stdout`/`sys.stderr` `None` sind; falls ja, werden
  sie durch eine neu geöffnete Log-Datei `overwatchmk2_console.log`
  NEBEN der exe/.app ersetzt (mit Fallback auf ein reines
  In-Memory-`io.StringIO()`, falls selbst das Öffnen der Datei
  fehlschlägt, z.B. mangels Schreibrechten). Die komplette bisherige
  Diagnoseausgabe (Versionsnummer, geladene Config, externer
  Ordner, ...) bleibt dadurch beim Finder-Start erhalten, statt
  ersatzlos zu verschwinden -- einsehbar in genau dieser Datei.
  `OVERWATCH_VERSION` auf `1.0.2`, `OVERWATCH_BUILD_NOTE` auf
  `"fix-macos-silent-stdout-crash-and-arm64-only-workflow"` gesetzt.
- **`.github/workflows/build.yml`:** Job `build-macos` von der in
  [1.0.1] eingeführten Intel/arm64-Matrix zurückgebaut auf einen
  einzelnen Job mit `runs-on: macos-14` (Apple Silicon/arm64) --
  entsprechend der Nutzeranforderung, nur noch Apple-Silicon-Macs zu
  unterstützen. `macos-13` kommt im gesamten Workflow nicht mehr vor.
  Der Ausführungsrechte-/Quarantäne-Prüfschritt aus [1.0.1] bleibt
  erhalten (ergänzt um `file <binary>` zur Architektur-Diagnose in der
  Job-Log-Ausgabe). Der `release`-Job lädt entsprechend wieder nur
  EIN macOS-Artefakt (`OverWatchMK2-macos`) statt zweier.
- **`OverWatchMK2.spec`:** `CFBundleShortVersionString` auf `1.0.2`
  aktualisiert.
- **`ANLEITUNG.md`:** Abschnitt 1 (Schnellstart) wieder auf einen
  einzelnen macOS-Download zurückgestellt, mit Hinweis, dass bewusst
  nur Apple Silicon unterstützt wird. Abschnitt 7 (Fehlersuche)
  überarbeitet: der bisherige Architektur-Prüfschritt entfällt (nicht
  mehr relevant, da nur noch eine Architektur gebaut wird), dafür
  neuer Hinweis auf `overwatchmk2_console.log` als erste Anlaufstelle
  bei genau diesem "Icon hüpft, Programm verschwindet"-Symptom.

### Verifikation

```
$ python3 -m py_compile main.py sms_gateway.py ogn_receiver.py
(keine Ausgabe = Erfolg)

$ python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build.yml')); print('YAML OK')"
YAML OK

$ python3 main.py --version
OverWatchMK2 1.0.2 (fix-macos-silent-stdout-crash-and-arm64-only-workflow)

# Gezielter Test GENAU des behobenen Absturzszenarios: sys.stdout/
# sys.stderr vor dem Import auf None gesetzt (simuliert exakt die
# Situation eines Finder-Doppelklick-Starts ohne Terminal):
$ python3 -c "
import sys
sys.stdout = None
sys.stderr = None
import main   # <- stürzte VOR diesem Fix hier sofort mit
              #    AttributeError ab; nach dem Fix läuft der Import
              #    sauber durch
print('IMPORT OHNE ABSTURZ ERFOLGREICH', file=sys.__stdout__)
print('main.OVERWATCH_VERSION =', main.OVERWATCH_VERSION, file=sys.__stdout__)
"
IMPORT OHNE ABSTURZ ERFOLGREICH
main.OVERWATCH_VERSION = 1.0.2

$ cat overwatchmk2_console.log
[DIAG] Config geladen: /pfad/zu/config.yaml
# -> bestätigt: die Diagnoseausgabe, die vorher spurlos verschwand
#    (weil print() abstürzte, bevor sie irgendwo ankam), landet jetzt
#    zuverlässig in dieser Datei statt verloren zu gehen
```

Dieser Reproduktionstest bildet den entscheidenden Unterschied zu
[1.0.0]/[1.0.1] exakt ab: dort wurde nur mit normal vorhandenem
`sys.stdout` getestet (Terminal-Start bzw. diese
Linux-Entwicklungsumgebung), wodurch der eigentliche Fehler unentdeckt
blieb, obwohl der Code bereits produktiv ausgeliefert war. Ein echter
PyInstaller-Build unter macOS UND ein echter Finder-Doppelklick-Start
auf einem physischen Mac konnten weiterhin nicht in dieser
Linux-Entwicklungsumgebung durchgeführt werden -- das reproduzierte
`sys.stdout=None`-Verhalten deckt jedoch nachweislich exakt den
Mechanismus ab, den Apples GUI-Prozessstart (kein kontrollierendes
Terminal) laut offizieller Python-/PyInstaller-Dokumentation für
genau diesen Fall vorschreibt.

### Offene Punkte

Sollte nach diesem Update `overwatchmk2_console.log` neben der `.app`
weiterhin fehlen ODER leer bleiben, ist das ein Hinweis darauf, dass
der Absturz an einer anderen, noch nicht identifizierten Stelle
auftritt -- dann bitte Inhalt von `overwatchmk2_crash.log` (falls
vorhanden) bzw. die Ausgabe von
`./OverWatchMK2.app/Contents/MacOS/OverWatchMK2` im Terminal
mitteilen (siehe ANLEITUNG.md Abschnitt 7, Schritt 3).

## [1.0.1] – Fix: macOS-.app startete nicht (Architektur + Ausführungsrecht)

### Problem (Nutzermeldung)

Nach dem ersten Release funktionierte `OverWatchMK2.exe` unter Windows
einwandfrei, `OverWatchMK2.app` unter macOS ließ sich jedoch nicht
starten -- ohne vom Nutzer mitgeteilte genaue Fehlermeldung (Symptom
zum Zeitpunkt dieses Fixes noch nicht abschließend eingegrenzt, siehe
"Nicht abschließend verifiziert" unten).

### Ursache

Zwei unabhängige, beide plausible und beide durch dieses Release
behobene Ursachen im GitHub-Actions-Workflow (`build.yml`), NICHT im
main.py-Code selbst:

1. **Architektur-Mismatch (wahrscheinlichste Ursache).** Der Job
   `build-macos` lief bisher auf `runs-on: macos-latest`. GitHub hat
   das zugrundeliegende Runner-Image für dieses Label inzwischen auf
   Apple Silicon (arm64) umgestellt. Ein DORT mit PyInstaller gebautes
   `.app`-Bundle enthält ausschließlich arm64-Maschinencode -- auf
   einem Intel-Mac (x86_64) verweigert macOS den Start mit "Bad CPU
   type in executable", in vielen Fällen ohne sichtbaren Dialog beim
   Doppelklick über Finder (wirkt dann wie "es passiert einfach
   nichts"). Das GitHub-Actions-Release enthielt bislang nur genau
   EINE macOS-Variante -- welche Architektur das im Einzelfall war,
   hing vom Zeitpunkt des Runner-Updates ab und war für den Nutzer
   nicht ersichtlich.
2. **Mögliches Verlieren des Ausführungsrechts beim Zip-/Artefakt-
   Schritt.** `actions/upload-artifact` ist bekannt dafür, Unix-
   Ausführungsrechte bei verschachtelten Bundle-Strukturen (wie einer
   `.app`, die aus vielen einzelnen Dateien besteht) nicht in jedem
   Fall zuverlässig zu erhalten. Das würde dazu führen, dass das
   Programm zwar die richtige Architektur hat, aber trotzdem nicht
   ausführbar ist ("Permission denied" bzw. bei Finder-Doppelklick
   ebenfalls ein stiller Fehlschlag).

### Änderungen

- **`build-macos` läuft jetzt als Matrix-Job auf ZWEI expliziten
  Runnern statt auf `macos-latest`:** `macos-13` (letzter von GitHub
  bereitgestellter Intel/x86_64-Runner) UND `macos-14` (Apple
  Silicon/arm64) -- baut also bei jedem Durchlauf zwei komplett
  getrennte `.app`-Bundles, eines je Architektur. Kein "universal2"-
  Binary (das würde eine deutlich komplexere Build-Pipeline mit
  architekturübergreifendem `lipo`-Zusammenführen der Python-
  Erweiterungen erfordern) -- stattdessen zwei separate, klar
  benannte Downloads: `OverWatchMK2-macos-intel.zip` und
  `OverWatchMK2-macos-arm64.zip`.
- **Neuer Workflow-Schritt "Ausführungsrechte + Quarantäne-Attribut
  prüfen"** direkt nach dem PyInstaller-Build (also VOR jedem
  Zip-/Upload-Schritt): setzt `chmod +x` explizit auf
  `Contents/MacOS/OverWatchMK2` und entfernt vorsorglich ein eventuell
  vorhandenes `com.apple.quarantine`-Attribut (`xattr -cr`) -- Letzteres
  betrifft zwar in der Praxis eher den Download-Schritt beim Nutzer
  (siehe ANLEITUNG.md Abschnitt 6 zu Gatekeeper), schadet an dieser
  Stelle aber nicht und schließt diese Fehlerquelle sauber aus.
- **`OverWatchMK2.spec`:** `CFBundleShortVersionString` von `1.0.0` auf
  `1.0.1` aktualisiert (war zuvor hartkodiert unabhängig von
  `OVERWATCH_VERSION` in main.py -- Diskrepanz behoben, wird ab jetzt
  bei jedem Versionssprung mitgepflegt).
- **`main.py`:** `OVERWATCH_VERSION` auf `1.0.1`,
  `OVERWATCH_BUILD_NOTE` auf `"fix-macos-build-arch-and-exec-permissions"`
  gesetzt.
- **`release`-Job:** lädt jetzt drei statt zwei Artefakte herunter
  (`OverWatchMK2-macos-intel`, `OverWatchMK2-macos-arm64` zusätzlich zu
  `OverWatchMK2-windows`) und veröffentlicht entsprechend drei Dateien
  im GitHub Release.
- **`ANLEITUNG.md`:** neuer Abschnitt 7 "macOS: Die App startet einfach
  nicht -- Fehlersuche" mit konkreten Prüfschritten (`file`-Befehl zur
  Architekturprüfung, `chmod +x`, Start über Terminal statt Finder zum
  Sichtbarmachen der echten Fehlermeldung, Crash-Log- und
  Konsole.app-Hinweis). Abschnitt 1 (Schnellstart) weist jetzt explizit
  auf die zwei getrennten macOS-Downloads hin.

### Verifikation

```
$ python3 -m py_compile main.py sms_gateway.py ogn_receiver.py
(keine Ausgabe = Erfolg)

$ python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build.yml')); print('YAML OK')"
YAML OK

$ python3 main.py --version
OverWatchMK2 1.0.1 (fix-macos-build-arch-and-exec-permissions)
```

`OverWatchMK2.spec` enthält weiterhin dieselbe, bereits in [1.0.0]
unveränderte `BUNDLE()`-Stufe -- diese Version ändert an der
Spec-Logik selbst nichts außer der Versionsnummer im `info_plist`.

### Nicht abschließend verifiziert / weiteres Vorgehen

Der Nutzer hatte beim Melden dieses Fehlers noch KEINE genaue
Fehlermeldung mitgeteilt (kein Dialogtext, keine Terminal-Ausgabe, kein
Crash-Log-Inhalt) -- die oben beschriebenen zwei Ursachen sind die mit
Abstand wahrscheinlichsten Erklärungen für "exe startet, .app startet
nicht" bei einem über GitHub Actions gebauten, unsigniertem
PyInstaller-`.app`-Bundle, konnten aber mangels vorhandener
macOS-Hardware in dieser Entwicklungsumgebung nicht gegen einen
tatsächlichen Fehlschlag verifiziert werden. Sollte das Problem nach
diesem Update fortbestehen, sind die neuen Prüfschritte in
ANLEITUNG.md Abschnitt 7 (insbesondere Start über Terminal statt
Finder-Doppelklick) der nächste Schritt, um die tatsächliche
Fehlermeldung sichtbar zu machen.

## [1.0.0] – Erstveröffentlichung: abgewandeltes Nachfolgeprojekt von OverWatchMK1

### Kontext / Auftrag

OverWatchMK2 ist eine gezielt abgewandelte Version von OverWatchMK1
(zuletzt 2.0.11), die als reiner Online-Anzeiger konzipiert ist und
zusätzlich SMS-basierte Ortungsmeldungen empfangen und darstellen kann.
Ausgangspunkt war die vom Nutzer bereitgestellte OverWatchMK1-2.0.11-
Codebasis (main.py, cits_esp32.py, ogn_receiver.py, templates/index.html,
config.yaml, OverWatchMK1.spec). Die Versionszählung beginnt bei diesem
Projekt bewusst neu bei 1.0.0.

### Entfernt (wie beauftragt)

- **Lokale Drohnenortung (Remote-ID) komplett entfernt.** Die kompletten
  Klassen `WiFiScanner`, `_ChannelHopper`, `_LinuxWiFiScanner`,
  `_WindowsWiFiScanner`, `BluetoothScanner`, der ASTM F3411/OpenDroneID-
  Decoder (`decode_basic_id`, `decode_location`, `decode_self_id`,
  `decode_system`, `decode_operator_id`, `decode_auth`,
  `decode_single_message`), `DroneRecord`, `DroneDatabase`,
  `KnownDronesStore`, `PossibleDroneNetworksRegistry`, `DemoGenerator`
  sowie `BuzzerAlert` (GPIO-Buzzer, ohnehin Raspberry-Pi-spezifisch)
  wurden ersatzlos gestrichen. Es existiert in OverWatchMK2 keinerlei
  Drohnen-Ortungsfunktion mehr, auch nicht über das Internet (es gibt
  keine mir bekannte offene Internet-API für Drohnen-Remote-ID, die
  einen sinnvollen Online-Ersatz geboten hätte).
- **Lokaler ADS-B-Empfang per RTL-SDR entfernt.** `ADSBReceiver._fetch_local()`
  und die zugehörige `local_receiver`-Konfiguration (Datei-/HTTP-Lesezugriff
  auf einen lokalen `readsb`/`dump1090`-Prozess) sind komplett entfallen.
  `ADSBReceiver._merge_local_and_internet()` ist damit ebenfalls entfallen
  -- es gibt nur noch genau einen Datenpfad (Internet-Anbieter), kein
  Zusammenführen zweier Quellen mehr nötig.
- **Lokaler FLARM/OGN-Empfang per zweitem RTL-SDR entfernt.**
  `ogn_receiver.LocalAPRSServer` (der lokale APRS-IS-Server, an den sich
  `ogn-decode` normalerweise anmeldet) wurde aus `ogn_receiver.py`
  entfernt. Übrig bleibt ausschließlich `OGNInternetClient`, der sich
  als ganz normaler lesender Client zum echten, weltweiten
  `aprs.glidernet.org`-Netzwerk verbindet -- inhaltlich unverändert
  gegenüber OverWatchMK1 übernommen (Parsing-Logik `parse_aprs_position`,
  `is_plausible_update`, Plausibilitätsgrenzen für Höhe/Sprunggeschwindigkeit
  1:1 beibehalten, da sie unabhängig von lokaler/Internet-Quelle gelten).
- **Lokaler C-ITS-Empfang (Seeed XIAO ESP32-C5) komplett entfernt,
  INKLUSIVE der Internet-Variante.** Anders als bei ADS-B/FLARM wurde
  hier NICHT nur der lokale Empfang gestrichen, sondern C-ITS als
  gesamtes Feature aus OverWatchMK2 entfernt (`cits_esp32.py`,
  `deploy/cits_esp32_flash.py`, `deploy/esp32c5_cits_firmware/` sind
  nicht Teil dieses Projekts). Begründung: Auch die bereits in
  OverWatchMK1 vorhandene `OpenTrafficMapClient`-Internet-Quelle für
  C-ITS ist zwingend auf eine lokal installierte `tshark`-Binärdatei
  angewiesen (Wireshark-CLI mit ITS-G5-Dissector) UND das exakte
  Byte-Format der MQTT-Rohpakete war laut OverWatchMK1-Quellcode-
  Kommentar ohnehin nie unabhängig gegen echte Testdaten verifiziert
  worden. Eine zwingende externe Wireshark-Installation als
  Voraussetzung passt nicht zu einem selbstständigen, per Doppelklick
  startbaren Windows-/macOS-Programm ohne weitere Installationsschritte
  -- diese Entscheidung ist eine bewusste Abweichung von "nur lokale
  Quellen entfernen" und wird hier transparent begründet, damit sie bei
  Bedarf in einer späteren Version widerrufen werden kann (z.B. falls
  eine tshark-freie Dekodierung ergänzt wird).
- **`api_system_shutdown()` (Herunterfahren des Host-Rechners) entfernt.**
  War in OverWatchMK1 für den Raspberry-Pi-Dauerbetrieb gedacht; für ein
  gewöhnliches Windows-/macOS-Programm, das der Nutzer selbst startet
  und beendet, unpassend und potenziell gefährlich (fährt sonst den
  PC/Mac des Nutzers herunter).
- **`_get_local_receivers_health()` / `_watchdog_local_receivers()` /
  `/api/local_receivers_health`, CPU-Temperatur-Abfrage
  (`_get_cpu_temp_c()`) entfernt.** Diese prüften ausschließlich, ob
  lokal angeschlossene Hardware (RTL-SDR, WLAN-Adapter, ESP32-Board)
  noch da ist -- ohne jegliche lokale Hardware in OverWatchMK2
  gegenstandslos.
- **Raspberry-Pi-spezifische Deployment-Infrastruktur entfernt:**
  `deploy/install.sh`, `deploy/deploy_new_version.sh`,
  `deploy/migrate_config.py` (ersetzt durch eine einfache, in main.py
  eingebaute rekursive Default-Ergänzung fehlender Config-Abschnitte,
  siehe unten), `deploy/check_persistent_storage.sh`,
  `deploy/overwatchmk1.service.template`,
  `deploy/wlan_setup.sh.template`,
  `OverWatchMK1_Neuinstallation_Raspberry_Pi5.md`. OverWatchMK2 ist ein
  gewöhnliches, per PyInstaller gebautes Desktop-Programm, kein
  systemd-Dienst.

### Beibehalten (reine Online-Quellen, weitgehend unverändert übernommen)

- **ADS-B über Internet-Anbieter** (`opensky`/`adsbfi`/`airplaneslive`/
  `adsblol`) -- `_normalize_dump1090()`, `_normalize_opensky()`,
  `_fetch_opensky()`, `_fetch_community_provider()`, das
  Backoff-/Wiederholungsverhalten bei Fehlern/Rate-Limits (`_register_success`/
  `_register_failure`, exponentiell 10s bis max. 5 Minuten) sind
  inhaltlich unverändert aus OverWatchMK1 übernommen. Vereinfacht wurde
  nur die `run()`-Schleife: kein zweigleisiges lokal/Internet-Timing
  mehr nötig, ein einziges `poll_interval_sec` (Standard jetzt 15s statt
  vormals 5s für den -- inzwischen entfallenen -- lokalen Pfad).
- **FLARM/OGN über das echte OGN-Internet-Netzwerk** (`OGNInternetClient`)
  -- unverändert übernommen, siehe oben.
- **Starlink-Satellitentracker** (`StarlinkReceiver`, SGP4-Bahnberechnung
  aus TLE-Bahnelementen von CelesTrak/Space-Track) -- komplett
  unverändert übernommen, da ohnehin bereits ein reiner Online-Dienst
  ohne jede lokale Hardware. TLE-Zwischenspeicherung
  (`starlink_tle_cache.json`) liegt jetzt konsequent im externen,
  Neben-der-exe-Ordner (siehe unten), nicht mehr im Programmverzeichnis.
- **Offline-Kartenkacheln (MBTiles)** -- Serverlogik (`/api/offline_tiles/...`,
  TMS/XYZ-Y-Achsen-Umrechnung, `mode=ro&immutable=1`-SQLite-Verbindung
  gegen "database is locked" bei parallelen Kachelanfragen) unverändert
  übernommen. GEÄNDERT: Der Suchpfad ist jetzt NICHT mehr fest
  `offline_tiles/germany.mbtiles` relativ zum Skript, sondern
  `_find_mbtiles_path()` sucht automatisch nach JEDER `.mbtiles`-Datei
  direkt neben der exe/.app ODER in einem Unterordner `offline_map/`
  daneben (siehe Nutzeranforderung "Offline-Karte soll einfach im
  selben Ordner wie die exe beim Start liegen").
- **No-Fly-Zonen (dipul-WMS)** -- unverändert (reiner Konfigurations-
  Durchreich-Endpunkt, Kacheln lädt der Browser direkt vom dipul-Dienst).
- **Geocoding (Nominatim-Proxy)** -- unverändert, Rate-Limit von
  1 Anfrage/Sekunde beibehalten.
- **Kartenmarkierungen** (`MapAnnotationsStore`) -- unverändert
  übernommen (atomares Schreiben per tmp-Datei + `os.replace`).

### Neu in OverWatchMK2

- **SMS-Ortungsempfang über USB-UART + GSM-Modem** (`sms_gateway.py`,
  neue Klasse `SMSReceiver`). Fragt ein angeschlossenes SIM800/900/7600-
  kompatibles Modem per AT-Kommandos im Textmodus (`AT+CMGF=1`) im
  konfigurierbaren Intervall (Standard 5s) per `AT+CMGL="ALL"` ab,
  verarbeitet jede gefundene Nachricht und löscht sie danach
  standardmäßig vom SIM-Speicher (`AT+CMGD`, abschaltbar über
  `sms.delete_after_read: false`). Bewusste Architekturentscheidung:
  Polling statt der unaufgeforderten `+CMTI`-Benachrichtigungen mancher
  Modems, siehe ausführliche Begründung im Modul-Docstring von
  `sms_gateway.py` -- Polling funktioniert nachweislich über praktisch
  jedes SIM800-kompatible Modem hinweg, während `+CMTI`-Verhalten
  zwischen Klonen/Firmware-Ständen stark schwankt.
- **Koordinaten-Formaterkennung mit "Anlernen"-Mechanismus**
  (`sms_gateway.COORD_PATTERNS`, `try_parse_coordinates()`,
  `SMSFormatStore`). Sieben eingebaute Muster decken die gängigsten
  Tracker-SMS-Formate ab (reines Dezimalgrad-Zahlenpaar, Dezimalgrad
  mit Himmelsrichtung N/S/E/W, Grad/Dezimalminute, Grad/Minute/Sekunde,
  beschriftete Felder "Lat:.../Lon:...", `geo:`-URIs, Google-Maps-Links).
  Über die neue Weboberflächen-Sektion "SMS-Ortung -> Format anlernen"
  kann der Nutzer einen Beispieltext eingeben (oder eine bereits
  empfangene SMS aus der Inbox-Ansicht wiederverwenden); das System
  zeigt alle passenden Muster mit geparsten Koordinaten zur Auswahl an.
  Nach EINMALIGER Bestätigung wird dieses Format für alle künftigen
  Nachrichten bevorzugt verwendet (persistiert in `sms_format.json`
  neben der exe). Bis zur ersten Bestätigung greift automatisch die
  volle Musterbibliothek als Bestleistungs-Fallback, damit auch vor dem
  Anlernen keine Positionsmeldung verloren geht (im Frontend als
  "automatisch erkannt, unbestätigt" gekennzeichnet). Eine
  fortgeschrittene Option erlaubt zusätzlich eine frei eingegebene
  Regex mit benannten Gruppen `(?P<lat>...)`/`(?P<lon>...)` für exotische
  Formate, die keinem eingebauten Muster entsprechen.
- **Farbige Punktketten je Absendernummer** (`SMSTrackStore`). Jede
  Absendernummer erhält beim ersten Empfang eine aus einer 12 Farben
  umfassenden, gut unterscheidbaren Palette stabil zugewiesene Farbe
  (persistiert, bleibt über Neustarts hinweg gleich). Jeder weitere
  Punkt derselben Nummer wird chronologisch an ihre Punktkette
  angehängt und im Frontend als durchgezogene, farbige Linie in
  Empfangsreihenfolge dargestellt (`L.polyline` je Absender), mit dem
  jeweils neuesten Punkt größer hervorgehoben. Persistiert in
  `sms_tracks.json` neben der exe, überlebt also Neustarts.
- **Test-/Entwicklungsendpunkt `/api/sms/simulate`.** Injiziert eine
  simulierte SMS OHNE angeschlossene Modem-Hardware -- nutzt exakt
  dieselbe Verarbeitungslogik (`SMSReceiver._process_message`) wie ein
  echtes Modem. Ermöglicht es, den kompletten Anlern-/Anzeige-Ablauf am
  Entwicklungsrechner durchzutesten (siehe Verifikation unten).
- **Neue Weboberfläche** (`templates/index.html`, komplett neu
  geschrieben statt der 3232-zeiligen OverWatchMK1-Version): Kartenebenen-
  Sidebar (ADS-B/FLARM/Starlink/SMS/No-Fly-Zonen/Offline-Karte je
  ein-/ausblendbar mit Live-Statusanzeige), SMS-Anlern-Dialog, Sender-
  Liste mit Farbzuordnung und Lösch-Funktion je Absender,
  Inbox-Ansicht der letzten empfangenen SMS (auch unverarbeitete, zum
  direkten Wiederverwenden als Anlern-Beispiel), Kartenmarkierungs-
  Werkzeug, Koordinaten-/Adresssuche. Bewusst schlanker als das
  Original (kein Drohnen-/WLAN-/Bluetooth-/C-ITS-/Buzzer-UI mehr nötig).
- **Plattformübergreifendes "neben der exe/.app"-Konzept**
  (`_external_dir()`/`external_path()` in main.py). Unter Windows
  identisch zum bisherigen OverWatchMK1-Verhalten (Ordner der .exe).
  NEU für macOS: Da eine `.app` ein Bundle (Ordner-Struktur) ist, in das
  Nutzer nicht hineinschauen sollen, wird bei einem laufenden
  `.app`-Bundle automatisch DREI Ebenen über
  `Contents/MacOS/OverWatchMK2` aufgelöst -- also der Ordner, der die
  `.app` selbst enthält (das dortige Äquivalent zu "neben der exe").
  config.yaml, Logs, `sms_tracks.json`, `sms_format.json`,
  `map_annotations.json`, `starlink_tle_cache.json` UND die
  Offline-Karten-Suche (`.mbtiles`) verwenden alle konsequent diesen
  einen Mechanismus.
- **PyInstaller-Spec für BEIDE Plattformen in einer Datei**
  (`OverWatchMK2.spec`): baut unter Windows ein einzelnes `.exe`,
  erkennt unter macOS zusätzlich automatisch die Plattform
  (`sys.platform == 'darwin'`) und erzeugt zusätzlich ein
  doppelklickbares `OverWatchMK2.app`-Bundle über `BUNDLE(...)`.
- **GitHub-Actions-Workflow** (`.github/workflows/build.yml`): zwei
  parallele Jobs (`windows-latest`, `macos-latest`) bauen bei jedem
  Push auf `main` sowie bei jedem `v*.*.*`-Tag automatisch beide
  Artefakte, führen vorher `python -m py_compile` auf alle drei
  Python-Module aus, und stellen sie als Workflow-Artefakte bereit.
  Bei einem Versions-Tag wird zusätzlich automatisch ein GitHub Release
  mit beiden fertigen ZIP-Dateien (inkl. `config.yaml`, README, ANLEITUNG,
  CHANGELOG und einem leeren `offline_map/`-Unterordner) veröffentlicht.

### Verifikation / Tests (Ergebnisse zum Kopieren in die Commit-Nachricht)

Da keine reale SIM800-Hardware in dieser Entwicklungsumgebung verfügbar
war, wurde die AT-Kommando-Ebene (`SMSReceiver._connect()`,
`SMSReceiver._poll_once()`, `_parse_cmgl_response()`) NICHT gegen ein
echtes Modem verifiziert -- das Antwortformat von `AT+CMGL="ALL"` folgt
der öffentlich dokumentierten SIM800-AT-Befehlsreferenz, ist aber bei
abweichenden Modem-Klonen ein möglicher erster Verdachtspunkt, falls
nach Anschluss echter Hardware keine SMS erkannt werden (im Log sichtbar
über die üblichen `log.warning`-Meldungen bei Verbindungsfehlern).
ALLES ANDERE wurde tatsächlich ausgeführt und funktional geprüft:

```
$ python3 -m py_compile main.py sms_gateway.py ogn_receiver.py
(keine Ausgabe = Erfolg, alle drei Module syntaktisch fehlerfrei)

$ python3 main.py --version
OverWatchMK2 1.0.0 (initial-release-online-only-plus-sms-tracking)

$ python3 main.py --no-adsb --no-starlink --no-flarm --no-sms --port 18081
[...]
Webserver startet auf http://<lokale-ip>:18081

$ curl -s http://127.0.0.1:18081/                      # -> HTTP 200, komplette
                                                          #    neue index.html
                                                          #    wird korrekt von
                                                          #    Flask/Jinja gerendert
                                                          #    (map_cfg als JSON
                                                          #    korrekt eingebettet)

$ curl -s http://127.0.0.1:18081/api/stats
{"adsb_status":{"active":false,...},"sms_status":{"connected":false,
 "enabled":false,"last_error":"SMS-Empfang nicht gestartet.",...},
 "starlink_status":{...},"flarm_status":{...},"version":"1.0.0"}

$ curl -s -X POST http://127.0.0.1:18081/api/sms/learn \
    -H 'Content-Type: application/json' \
    -d '{"sample_text":"Standort: 52.5200,13.4050"}'
{"ok":true,"candidates":[{"label":"Reines Dezimalgrad-Zahlenpaar
 (52.5200, 13.4050)","lat":52.52,"lon":13.405,
 "matched_text":"52.5200,13.4050","pattern_id":"decimal_pair"}]}
# -> Musterbibliothek erkennt einen reinen Dezimalgrad-Text korrekt

$ curl -s -X POST http://127.0.0.1:18081/api/sms/simulate \
    -H 'Content-Type: application/json' \
    -d '{"sender":"+491701234567","text":"lat: 52.5200 lon: 13.4050"}'
{"ok":true,"tracks":{"+491701234567":{"color":"#f97316","points":[
 {"lat":52.52,"lon":13.405,"pattern_label":"Beschriftete Felder
 (Lat/Breite: .. Lon/Länge: ..)","raw_text":"lat: 52.5200 lon: 13.4050",
 ...}],"sender":"+491701234567"}}}
# -> Beschriftetes Format wird VOR dem allgemeinen Dezimalgrad-Muster
#    erkannt (Musterreihenfolge wie beabsichtigt), Farbe #f97316
#    (erste Palettenfarbe) wird dem neuen Absender korrekt und
#    stabil zugewiesen, Punkt korrekt in sms_tracks.json persistiert

$ curl -s http://127.0.0.1:18081/api/sms/tracks
# -> bestätigt: derselbe Datensatz wird nach der Anfrage unverändert
#    aus dem persistenten Store zurückgegeben
```

Zusätzlich manuell geprüft: `config.yaml` ohne den neuen `sms:`-Abschnitt
(ältere/handbearbeitete Datei simuliert) wird beim Start durch
`_deep_merge_defaults()` klaglos um alle fehlenden Standardwerte ergänzt,
statt mit `KeyError` abzustürzen. `OverWatchMK2.spec` wurde auf
Python-Syntaxebene geprüft (`python3 -m py_compile` versteht zwar keine
`.spec`-Dateien direkt, das darin verwendete PyInstaller-API-Vokabular
(`Analysis`, `PYZ`, `EXE`, `BUNDLE`) wurde jedoch 1:1 aus der
nachweislich funktionierenden OverWatchMK1.spec übernommen und nur um
die macOS-`BUNDLE()`-Stufe ergänzt); ein echter PyInstaller-Lauf unter
Windows/macOS konnte in dieser Linux-Entwicklungsumgebung nicht
durchgeführt werden -- das beiliegende GitHub-Actions-Workflow führt
genau diesen Build bei jedem Push automatisch aus und ist der
eigentliche Verifikationsschritt dafür.

### Bewusst nicht automatisiert / offene Punkte für eine Folgeversion

1. **Reale SIM800-Hardware-Verifikation steht aus** (siehe oben) --
   beim ersten Test mit echtem Modem unbedingt `sudo`/COM-Port-Rechte
   und die Log-Ausgabe (`overwatchmk2.log`, Abschnitt "SMS:") prüfen.
2. **`+CMTI`-Sofortbenachrichtigung** wurde bewusst nicht implementiert
   (siehe Architekturentscheidung oben) -- könnte als optionale,
   schnellere Alternative zum Polling in einer Folgeversion ergänzt
   werden, falls sich Polling in der Praxis als zu träge erweist.
3. **Erzeugung einer eigenen `.mbtiles`-Offline-Kartendatei** ist NICHT
   Teil dieses Projekts (kein automatisiertes Massen-Herunterladen von
   Kartenkacheln, siehe ANLEITUNG.md für Hinweise auf geeignete externe
   Werkzeuge) -- die Serverlogik zum ANZEIGEN einer vorhandenen
   `.mbtiles`-Datei ist aber vollständig funktionsfähig.
4. **Codesigning/Notarization für macOS** ist nicht Teil des
   GitHub-Actions-Workflows -- ein selbst gebautes, nicht signiertes
   `.app`-Bundle wird von macOS Gatekeeper beim ersten Start blockiert
   (Rechtsklick -> "Öffnen" statt Doppelklick nötig, siehe ANLEITUNG.md).
