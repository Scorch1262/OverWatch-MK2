# Offline-Kartendaten – Quellenangabe

**Datei:** `germany_bundeslaender.geo.json`
**Inhalt:** Vereinfachte administrative Grenzen der 16 deutschen
Bundesländer (GeoJSON, niedrige Detailstufe für kompakte Dateigröße).
**Quelle:** [isellsoap/deutschlandGeoJSON](https://github.com/isellsoap/deutschlandGeoJSON)
(archiviertes, aber weiterhin öffentlich verfügbares Community-Projekt),
zugrundeliegende Geodaten: [DIVA-GIS](http://www.diva-gis.org/gdata).

**Zweck in OverWatchMK1:** Rein geografische Referenzdarstellung
(Bundesländer-Umrisse) als Offline-Fallback, wenn keine Internet-
verbindung zum Online-Kartenserver (OpenStreetMap-Tiles) besteht.
Kein Ersatz für eine detaillierte Straßenkarte -- zeigt nur grobe
Orientierung (in welchem Bundesland befindet sich ein Kontakt).

**Kein Ersatz für die volle Online-Karte:** Diese Datei enthält
ausschließlich Verwaltungsgrenzen, keine Straßen, Gebäude, Gewässer
o.ä. Sobald wieder eine Internetverbindung besteht, schaltet
OverWatchMK1 automatisch zurück auf die vollständigen OpenStreetMap-
Kacheln.
