# Job Finder

Ein schlanker, lokal betriebener Job Finder für IT-Einstiegsstellen. Er sammelt
Stellen aus mehreren Quellen, vereinheitlicht und dedupliziert sie, verwirft
klare Fehlgriffe regelbasiert und legt die übrigen Stellen zur persönlichen
Entscheidung vor.

## Funktionen

- Quellen: Arbeitsagentur, StepStone, get-in-IT, Arbeitnow, Remotely,
  StudySmarter, Himalayas, Jobicy, Startup Jobs sowie ausgewählte direkte
  Karriereseiten
- ein einheitliches Jobmodell und quellenübergreifende Deduplizierung
- lokale Detail-Caches und ein Gedächtnis für bekannte und inaktive Stellen
- regelbasierter Vorfilter für Standort, Remote-Anteil, Erfahrungsniveau,
  Beschäftigungsart, Reisetätigkeit und grobe IT-Eignung
- sichtbare Junior-Hybrid-Sonderfälle und internationale Stellen, die sich im
  Review bei Bedarf zuschalten lassen
- manueller Import einer einzelnen Stellenanzeige per URL
- lokale Review-Oberfläche mit Interessant-, Rückfrage-, Ignorieren- und
  Bewerben-Workflow
- Bewerbungsübersicht mit Verlauf, Gesprächsterminen, optional gespeicherter
  Gehaltsvorstellung und Statistik; die Antwortquote bezieht sich nur auf
  abgeschlossene Bewerbungen
- kompakte Discord-Karten für neue oder inhaltlich geänderte Stellen sowie
  eine strukturierte Laufstatistik, die Fundmenge, Vorfilter, tatsächlich
  versendete Karten und die im Standard-Review sichtbare Anzahl trennt
- isolierte Quellenfehler, Laufprotokolle und rotierende Backups wichtiger
  lokaler Zustände
- dynamische Fortschrittsanzeigen je Quelle und Detailabruf; im Terminal wird
  eine tqdm-artige Zeile mit Prozent, Zähler, Laufzeit, Restzeit und Rate
  aktualisiert, im Laufprotokoll bleibt nur der Endstand

Der Vorfilter-Score ist keine persönliche Eignungsprognose. Er macht nur
transparent, warum eine Stelle den regelbasierten Filter passiert hat. Die
endgültige Bewertung bleibt bewusst beim Nutzer.

## Einrichtung

Virtuelle Projektumgebung anlegen und Abhängigkeiten installieren:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Unterstützt wird Python 3.11 oder neuer. Die Projektmetadaten stehen zusätzlich
in `pyproject.toml`; eine bearbeitbare Installation ist mit
`.\.venv\Scripts\python.exe -m pip install -e .` möglich.

Persönliche Sucheinstellungen anlegen:

```powershell
Copy-Item user_settings.example.yaml user_settings.local.yaml
```

`user_settings.local.yaml` enthält unter anderem Suchort, Suchradius,
Pendlerorte und fachliche Stichwörter. Die Datei wird von Git ignoriert. Ohne
lokale Datei wird die anonymisierte Beispielkonfiguration verwendet.

Für Discord kann `DISCORD_WEBHOOK_URL` als Umgebungsvariable gesetzt werden.
Die Quelle Startup Jobs ist optional und wird nur mit gesetztem
`STARTUP_JOBS_API_KEY` aktiviert.

## Nutzung

Finder ohne Discord-Versand starten:

```powershell
.\.venv\Scripts\python.exe run_finder.py
```

Finder mit Discord-Versand starten:

```powershell
.\.venv\Scripts\python.exe run_finder.py --notify
```

Lokale Oberfläche öffnen:

```powershell
review_jobs.bat
```

Danach stehen zur Verfügung:

- `http://127.0.0.1:8765/` – Startseite und manueller Import
- `http://127.0.0.1:8765/review` – Stellen prüfen
- `http://127.0.0.1:8765/applications` – Bewerbungen und Statistik

## Ablauf

1. Die Quellen liefern Suchtreffer und Detaildaten.
2. URLs und inhaltlich gleiche Stellen werden zusammengeführt.
3. Das lokale Gedächtnis erkennt neue, bekannte, geänderte und inaktive Jobs.
4. Der Vorfilter schließt klare Konflikte sowie automatisch gefundene Anzeigen
   aus, deren bekanntes Veröffentlichungsdatum mehr als 60 Tage zurückliegt.
   Anzeigen ohne verlässliches Datum bleiben nach der Verfügbarkeitsprüfung
   zulässig. Manuell eingereichte alte Anzeigen bleiben mit Warnung prüfbar.
   Die übrigen Stellen erhalten nachvollziehbare Kategorien für IT-Bereich,
   Einstieg und Standort.
5. Alle durchgelassenen Stellen erscheinen im Review. Neue oder geänderte
   Stellen können zusätzlich an Discord gesendet werden. Der Standardfilter
   „Neu oder aktualisiert“ zeigt dieselbe Art von Stellen; eine erkannte
   Aktualisierung bleibt dort sichtbar, bis sie im Review entschieden wurde.
   Die Discord-Laufstatistik weist separat aus, wie viele Stellen im Lauf neu
   oder geändert, benachrichtigungsfähig, tatsächlich versendet und nach den
   standardmäßigen Sichtbarkeitsfiltern direkt im Review sichtbar sind.
6. Bewerbungen werden getrennt vom Stellen-Review dauerhaft nachverfolgt.

Der veränderliche Stellen- und Bewerbungszustand liegt transaktional in
`data/internal/job_finder.sqlite3`. Beim ersten Zugriff wird eine vorhandene
`seen_jobs.json` einmalig importiert und als unveränderte Rückfallkopie
beibehalten. `jobs.json` und `recommendations.json` bleiben bewusst lesbare,
neu erzeugbare Ausgaben.

Direkte Arbeitnow-Anzeigen mit vollständigem Text bleiben unverändert. Nur bei
dem bekannten Platzhaltertext wird nach bestandenem Vorfilter die verlinkte
Originalanzeige geladen. Review und Discord verwenden anschließend bevorzugt
deren URL.

Remotely übernimmt ausschließlich Anzeigen aus einem rollierenden
Sieben-Tage-Fenster. Alte hervorgehobene Anzeigen und bereits vergebene Stellen
werden verworfen; jeder Lauf liest die Listenansicht bis zur alten
Trefferfront. Detailseiten werden einen Tag lokal gecacht. Bei Kandidaten mit
LinkedIn als Originalquelle wird zusätzlich geprüft, ob dort noch Bewerbungen
angenommen werden; geschlossene Anzeigen gelangen nicht ins Review.

StudySmarter wird lokal im konfigurierten Radius und deutschlandweit nach
vollständig remote möglichen Einstiegsrollen durchsucht. Detailseiten werden
erst nach dem ersten Vorfilter geladen und anschließend sieben Tage gecacht.

Detaildaten gelten sieben Tage als frisch. Bei einem vorübergehenden
Netzwerkfehler darf ein höchstens 14 Tage alter Cache-Eintrag als sichtbar
markierter Fallback erscheinen; ältere Einträge werden nicht mehr übernommen.
Ein teilweise fehlgeschlagenes Suchsegment darf keine alten Stellen dieser
Quelle automatisch inaktiv setzen.

## Lokale Daten und Datenschutz

Persönliche Einstellungen, gefundene Stellen, Bewerbungsverläufe, Caches,
Benachrichtigungsstatus und Laufprotokolle liegen unter ignorierten lokalen
Dateien beziehungsweise unter `data/`. Sie gehören nicht ins Repository.
Zugangsdaten werden ausschließlich über Umgebungsvariablen erwartet.

Die Weboberfläche bindet ausschließlich an `127.0.0.1` und ist als persönliche
Ein-Nutzer-Anwendung gedacht. Host-, Origin- und Inhaltstypprüfungen schützen
die lokalen Schreib- und Dokumentendpunkte. Der manuelle Import akzeptiert nur
öffentliche HTTP(S)-Ziele und prüft auch DNS-Auflösung sowie Weiterleitungen;
interne Dienste und lokale Dateien sind nicht zulässig.

Bewerbungsunterlagen werden pro Job in einem technisch eindeutigen lokalen
Ordner gespeichert. Sie sind nicht Bestandteil der rotierenden Zustandsbackups
und sollten bei Bedarf separat verschlüsselt gesichert und nach Ende der
gewünschten Aufbewahrungsdauer gelöscht werden.

Vor einer Veröffentlichung empfiehlt sich:

```powershell
git status --ignored
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Die Tests bleiben absichtlich im Repository: Sie dokumentieren die Regeln und
schützen insbesondere Deduplizierung, Quellenadapter, Review-Workflow und
Bewerbungsverlauf vor Regressionen.

## Projektstruktur

```text
job_finder/             Kernlogik, Quellen, Review und Bewerbungsverwaltung
job_finder/sources/     einzelne Quellenadapter
tests/                  automatisierte Tests
run_finder.py           produktiver Kommandozeilen-Einstieg
review_jobs.bat         Start der lokalen Weboberfläche
user_settings.example.yaml  anonymisierte Konfigurationsvorlage
data/                   ausschließlich lokale Laufdaten (nicht versioniert)
```

## Lizenz

Dieses Projekt steht unter der MIT-Lizenz. Details enthält `LICENSE`.
