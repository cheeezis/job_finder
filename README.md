# Job Finder

Ein lokal betriebener Python-Job-Finder für IT-Einstiegsstellen. Er sammelt
Anzeigen aus mehreren Quellen, vereinheitlicht und dedupliziert sie, verwirft
klare Fehlgriffe regelbasiert und unterstützt die persönliche Sichtung bis zur
Bewerbungsnachverfolgung. Der vollständige Stellenbestand sowie Bewerbungs-
und Dokumentdaten verbleiben auf dem eigenen Rechner. Bei aktiviertem
Discord-Versand werden ausschließlich die dafür vorgesehenen kompakten
Stellenkarten und Laufstatistiken an Discord übertragen.

## Funktionen

- öffentliche Jobportale, offene Feeds und ausgewählte direkte Karriereseiten
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
  Gehaltsvorstellung (Eingabe pro Monat oder Jahr, gespeichert als Jahresbrutto) und Statistik; die Antwortquote
  bezieht sich nur auf abgeschlossene Bewerbungen
- kompakte Discord-Karten für neue Stellen sowie
  eine strukturierte Laufstatistik, die Fundmenge, Vorfilter, tatsächlich
  versendete Karten und die im Standard-Review sichtbare Anzahl trennt
- isolierte Quellenfehler, Laufprotokolle und rotierende Backups wichtiger
  lokaler Zustände
- dynamische Fortschrittsanzeigen je Quelle und Detailabruf; im Terminal wird
  eine kompakte Zeile mit Zähler, Prozent und Laufzeit aktualisiert; eine
  geschätzte Restzeit erscheint nur bei längeren laufenden Abrufen. Keine 1/1-Balken.
  Ohne interaktives Terminal werden zeitgestempelte Zwischenstände höchstens
  alle 30 Sekunden je Vorgang ausgegeben; Start und Abschluss bleiben sichtbar.

Der Vorfilter-Score ist keine persönliche Eignungsprognose. Er macht nur
transparent, warum eine Stelle den regelbasierten Filter passiert hat. Die
endgültige Bewertung bleibt bewusst beim Nutzer.

## Quellen

| Gruppe | Quellen |
| --- | --- |
| Jobportale | Arbeitsagentur, StepStone, get-in-IT |
| Feeds und Aggregatoren | Arbeitnow, GermanTechJobs, Himalayas, Jobicy, Remotely, Startup Jobs, StudySmarter |
| Direkte Karriereseiten | Compose IT, bytewerk, RhönEnergie, JUMO, EDAG, CSS, Proemion, NETHINKS |
| Eigene Einträge | manueller Import einer öffentlichen Stellen-URL |

Startup Jobs ist optional und wird nur mit `STARTUP_JOBS_API_KEY` aktiviert.
GermanTechJobs wird über den öffentlichen XML-Feed eingelesen. Die dort
angegebenen Gehaltsspannen werden als Euro brutto pro Jahr übernommen; wie bei
allen automatisch gefundenen Quellen greift der 60-Tage-Filter auf das im Feed
ausgewiesene Veröffentlichungsdatum.
Einzelne Quellen können vorübergehend nur Teilergebnisse liefern, etwa bei
Rate-Limits oder nicht erreichbaren Detailseiten. Der Lauf isoliert solche
Fehler und kennzeichnet sie in Konsole, Log und Discord-Zusammenfassung.
Sind mehr als die Hälfte der Quellen nicht verwendbar, endet der Lauf dagegen
als fehlgeschlagen und lässt den vorherigen Job- und Review-Stand unverändert.

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
Lokale Geheimnisse gehören nicht in YAML-Dateien oder ins Repository.

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

Alternativ kann die Oberfläche direkt als Python-Modul gestartet werden:

```powershell
.\.venv\Scripts\python.exe -m job_finder.review
```

Danach stehen zur Verfügung:

- `http://127.0.0.1:8765/` – Startseite und manueller Import
- `http://127.0.0.1:8765/review` – Stellen prüfen
- `http://127.0.0.1:8765/applications` – Bewerbungen und Statistik

Der Review startet mit „Neu“ und zeigt dort alle noch nicht eingestuften Stellen,
unabhängig vom Fundlauf. Nach einer Entscheidung verschwindet die Stelle aus
diesem Filter. Internationale Anzeigen und
Junior-Hybrid-Sonderfälle sind eigene, standardmäßig deaktivierte Filter.

## Ablauf

1. Die Quellen liefern Suchtreffer und Detaildaten.
2. URLs und inhaltlich gleiche Stellen werden zusammengeführt.
3. Das lokale Gedächtnis erkennt neue, bekannte und inaktive Jobs.
4. Der Vorfilter schließt klare Konflikte sowie automatisch gefundene Anzeigen
   aus, deren bekanntes Veröffentlichungsdatum mehr als 60 Tage zurückliegt.
   Anzeigen ohne verlässliches Datum bleiben nach der Verfügbarkeitsprüfung
   zulässig. Manuell eingereichte alte Anzeigen bleiben mit Warnung prüfbar.
   Die übrigen Stellen erhalten nachvollziehbare Kategorien für IT-Bereich,
   Einstieg und Standort.
5. Alle durchgelassenen Stellen erscheinen im Review. Neue Stellen können
   zusätzlich an Discord gesendet werden. Nachträgliche Textänderungen werden
   eingelesen, lösen aber weder eine erneute Benachrichtigung noch ein erneutes
   Auftauchen unter „Neu“ aus.
6. Bewerbungen werden getrennt vom Stellen-Review dauerhaft nachverfolgt.

Interessante Stellen bleiben auch bei fehlenden Suchtreffern vorgemerkt. Ein
Finder-Lauf prüft ausschließlich die URLs fehlender interessanter Stellen, wenn
alle bekannten Quellen vollständig erfolgreich abgeschlossen wurden. Aktuelle
Treffer werden übersprungen; veraltete Cache-Treffer gelten als fehlend. Nur wenn
alle bekannten URLs eindeutig geschlossen sind (HTTP 404/410 oder expliziter
Schließungshinweis), wechselt die Stelle automatisch auf „Nicht interessant“.
Fehlende Suchtreffer, Login-Weiterleitungen und Abruffehler reichen dafür nicht.
Bestehende Bewerbungen bleiben davon ausgenommen. Der automatische Wechsel wird
mit Datum und Grund gespeichert.

Der veränderliche Stellen- und Bewerbungszustand liegt transaktional in
`data/internal/job_finder.sqlite3`. Beim ersten Zugriff wird eine vorhandene
`seen_jobs.json` einmalig importiert und als unveränderte Rückfallkopie
beibehalten. `jobs.json` und `recommendations.json` bleiben bewusst lesbare,
neu erzeugbare Ausgaben. Bewerbungsunterlagen liegen als eigenständige Dateien
in `data/internal/application_documents`; die Zustandsbackups enthalten diese
Dokumentordner nicht. Für eine vollständige Sicherung den gesamten `data`-Ordner
bei beendeter Anwendung separat sichern. SQLite ist der einzige schreibbare Speicher für den
Stellen- und Bewerbungszustand; JSON wird dafür nur noch beim Altimport gelesen.

```text
data/internal/job_finder.sqlite3  Status, Entscheidungen und Bewerbungsverlauf
data/internal/jobs.json          letzter deduplizierter Quellensnapshot
data/internal/*_cache.json       lokale Quellencaches
data/internal/notifications.json Discord-Versandstatus
data/output/recommendations.json aktuelle Ausgabe für die Review-Oberfläche
data/logs/                        Laufprotokolle
data/backups/                     rotierende Zustandsbackups
```

Direkte Arbeitnow-Anzeigen mit vollständigem Text bleiben unverändert. Nur bei
dem bekannten Platzhaltertext wird nach bestandenem Vorfilter die verlinkte
Originalanzeige geladen. Review und Discord verwenden anschließend bevorzugt
deren URL.

Remotely übernimmt ausschließlich Anzeigen aus einem rollierenden
Sieben-Tage-Fenster. Alte hervorgehobene Anzeigen und bereits vergebene Stellen
werden verworfen; jeder Lauf liest die Listenansicht bis zur alten
Trefferfront. Detailseiten werden sieben Tage lokal gecacht. Bei Kandidaten mit
LinkedIn als Originalquelle wird zusätzlich geprüft, ob dort noch Bewerbungen
angenommen werden; geschlossene Anzeigen gelangen nicht ins Review.

get-in-IT liefert zunächst kompakte Suchdaten. Vollständige Detailseiten werden
nur für Stellen geladen, die den bewusst großzügigen ersten Vorfilter bestehen;
erfolgreich geladene Details bleiben sieben Tage im Cache.

StudySmarter wird lokal im konfigurierten Radius und deutschlandweit nach
vollständig remote möglichen Einstiegsrollen durchsucht. Detailseiten werden
erst nach dem ersten Vorfilter geladen und anschließend sieben Tage gecacht.

Detaildaten gelten sieben Tage als frisch. Bei einem vorübergehenden
Netzwerkfehler darf ein höchstens 14 Tage alter Cache-Eintrag als sichtbar
markierter Fallback erscheinen; ältere Einträge werden nicht mehr übernommen.
Ein teilweise fehlgeschlagenes Suchsegment darf keine alten Stellen dieser
Quelle automatisch inaktiv setzen.

Jede Quelle erhält eine Ergebniszeile mit Treffern, Dauer und gegebenenfalls
Teilergebnis oder Fehler. Im Terminal werden laufende Meldungen ersetzt.
Konsole und Laufprotokoll zeigen außerdem die Dauer ihrer
Detailanreicherung und der Pipeline-Schritte einschließlich Offline-Prüfung.
Auch abgebrochene Schritte melden ihre bis dahin verstrichene Zeit. Die
Offline-Prüfung zeigt erledigte und insgesamt geplante eindeutige URLs, ohne
URLs oder Stelleninhalte auszugeben. Verschachtelte Zeiten überlappen und dürfen
nicht zur Gesamtlaufzeit addiert werden.

Die Review-Diagnose trennt erstmals gespeicherte und bekannte Treffer, passende
und ausgeschlossene neue Treffer sowie den Status Neu vom Standardfilter Neu.
Letzterer zeigt unbearbeitete Stellen mit Status Neu unabhängig vom Fundlauf;
internationale und Junior-Hybrid-Sonderfälle sind standardmäßig ausgeblendet.
Das Erstfund-Merkmal bleibt für Laufstatistik und Benachrichtigungen bestehen.
Ein Abbruch nach dem Speichern des Gedächtnisses und anschließender Neustart
entfernt unbearbeitete Stellen deshalb nicht mehr aus dem Filter Neu. Die
Offline-Prüfung bearbeitet höchstens 200 URLs pro Lauf und startet nach zwei
Minuten keine weitere Anfrage; eine laufende Anfrage darf noch fertig werden.
Unbearbeitete Stellen mit Status Neu werden nicht zusätzlich geprüft.
Prüfergebnisse einschließlich unklarer
Antworten werden 24 Stunden berücksichtigt, bevor die URL erneut geprüft wird.
Offene Prüfungen werden auf spätere Läufe verteilt; sie ändern den Status nicht.
Bei mehreren Anzeigen-URLs müssen alle innerhalb der letzten 24 Stunden eindeutig
als geschlossen bestätigt worden sein. Die Anfragen bleiben sequenziell.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Die gemeinsamen Browser-Helfer lassen sich zusätzlich mit Node.js (ab Version 18)
ohne weitere Pakete prüfen:

```powershell
node --test tests/frontend.test.cjs
```

Node.js wird nur für diese Tests benötigt, nicht für den Betrieb. Die drei
Oberflächen teilen sich `app.js` und `app.css`; ihre jeweiligen Abläufe bleiben
direkt in den Seiten.

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
user_settings.example.yaml  dokumentierte, anonymisierte Konfigurationsvorlage
data/                   ausschließlich lokale Laufdaten (nicht versioniert)
```

## Lizenz

Dieses Projekt steht unter der MIT-Lizenz. Details enthält `LICENSE`.
