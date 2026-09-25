# Job Finder

Ein Python-Job-Finder für IT-Einstiegsstellen. Er sammelt Anzeigen aus
mehreren Quellen, vereinheitlicht und dedupliziert sie, verwirft klare
Fehlgriffe regelbasiert und unterstützt die persönliche Sichtung bis zur
Bewerbungsnachverfolgung. Er läuft lokal oder in Azure (siehe
[Betrieb](#betrieb)); Stellenbestand, Bewerbungs- und Dokumentdaten liegen
entsprechend auf dem eigenen Rechner oder in der eigenen Azure-Subscription.
Bei aktiviertem Discord-Versand werden ausschließlich die dafür vorgesehenen
kompakten Stellenkarten und Laufstatistiken an Discord übertragen.

## Funktionen

- öffentliche Jobportale, offene Feeds und ausgewählte direkte Karriereseiten
- ein einheitliches Jobmodell und quellenübergreifende Deduplizierung
- Detail-Caches und ein Gedächtnis für bekannte und inaktive Stellen
- regelbasierter Vorfilter für Standort, Remote-Anteil, Erfahrungsniveau,
  Beschäftigungsart, Reisetätigkeit und grobe IT-Eignung
- sichtbare Junior-Hybrid-Sonderfälle und internationale Stellen, die sich im
  Review bei Bedarf zuschalten lassen
- manueller Import einer einzelnen Stellenanzeige per URL
- Review-Oberfläche mit Interessant-, Rückfrage-, Ignorieren- und
  Bewerben-Workflow
- Bewerbungsübersicht mit Verlauf, Gesprächsterminen, optional gespeicherter
  Gehaltsvorstellung (Eingabe pro Monat oder Jahr, gespeichert als Jahresbrutto) und Statistik; die Antwortquote
  bezieht sich nur auf abgeschlossene Bewerbungen
- kompakte Discord-Karten für neue Stellen sowie
  eine strukturierte Laufstatistik, die Fundmenge, Vorfilter, tatsächlich
  versendete Karten und die im Standard-Review sichtbare Anzahl trennt und
  Kandidaten ohne ladbare Detailseite ausweist
- isolierte Quellenfehler, Laufprotokolle und bei lokalen Läufen rotierende
  Backups des Datenbestands
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

Vor dem ersten Start PostgreSQL einrichten:
[PostgreSQL-Anleitung](docs/postgresql.md). Die Datenbank ist für Finder und
Review erforderlich.

Persönliche Sucheinstellungen anlegen:

```powershell
Copy-Item user_settings.example.yaml user_settings.local.yaml
```

`user_settings.local.yaml` enthält unter anderem Suchort, Suchradius,
Pendlerorte und fachliche Stichwörter. Die Datei wird von Git ignoriert. Ohne
lokale Datei wird die anonymisierte Beispielkonfiguration verwendet.

Die Einstellungen werden beim Start geladen. Nach Änderungen die laufende
Review-Anwendung neu starten; ein Neuladen der Browserseite genügt nicht.

Der Finder bewertet und sortiert Stellen eigenständig, auch ohne KI-Stufe.
Der Vorfilter verwendet wieder die bewährte Punkteverteilung: bis zu 30 für
die Rolle, 25 für Technologien, 25 für Einstiegseignung, 15 für den Standort
und fünf für den Bezug zu Projekten oder Weiterbildungen. Abzüge für
Arbeitsbedingungen werden danach angewendet. Die Rollen- und Technologiegewichte
stehen getrennt von den Erkennungsregeln in `job_finder/matching/ranking_weights.py`.
`matching.profile_domain_keywords` steuert den einmaligen Stichwortbonus.
Suchradius, Ortsliste und Pendlergrenzen kommen unverändert aus den aktuellen
persönlichen Einstellungen. Der Score ist eine regelbasierte Sortierhilfe,
kein Nachweis persönlicher Eignung.

Das Wort „Weiterbildung“ löst im Beschreibungstext keinen Ausbildungsabzug
mehr aus, damit reguläre Stellen mit Weiterbildungsangeboten nicht schlechter
abschneiden. Ausbildungsstellen und Weiterbildungstitel werden weiterhin erkannt.

`job_finder/matching/matching_rules.py` enthält die Erkennungs- und Ausschlussregeln,
`job_finder/matching/scoring.py` setzt daraus die Bewertung zusammen. Ein optionales
`profile.local.yaml` dient als persönliche Faktenbasis für eine spätere
agentische Stufe und wird vom aktuellen Finder nicht geladen. Die frühere
Python-Datei `job_finder/profile.py` wurde durch diese Trennung abgelöst.
Neue Bewertungen erscheinen beim nächsten Finder-Lauf; bereits gespeicherte
Review-Ergebnisse werden durch einen Neustart allein nicht neu bewertet.

Für Discord kann `DISCORD_WEBHOOK_URL` als Umgebungsvariable gesetzt werden.
Lokale Geheimnisse gehören nicht in YAML-Dateien oder ins Repository.

Ist zusätzlich `JOBFINDER_REVIEW_HOST` gesetzt (der Hostname der Review-Seite,
z. B. `jobfinder-review.ashyisland-3b6e9522.francecentral.azurecontainerapps.io`),
enthält jede Discord-Benachrichtigung einen Direktlink zur passenden Stelle
in der Review.

## Nutzung

Finder starten (sendet neue Treffer immer per Discord, sofern konfiguriert):

```powershell
.\.venv\Scripts\python.exe run_finder.py
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

## Betrieb

- **Lokal:** Finder und Review laufen wie oben beschrieben auf dem eigenen
  Rechner, PostgreSQL kommt aus Docker Compose. Dokumente liegen unter
  `data/internal/application_documents`; vor jedem Finder-Lauf entsteht ein
  rotierendes Backup.
- **Azure:** Der Finder läuft als Container-Apps-Job täglich um 06:00 und
  16:00 UTC, ohne StepStone und Remotely. Die Review ist eine Container App
  hinter einer Entra-ID-Anmeldung, die nur das eigene Konto zulässt. Daten
  liegen in einem Azure-PostgreSQL-Server, Dokumente im Blob Storage;
  Zugangsdaten kommen aus dem Key Vault. Statt der ZIP-Backups sichern
  Point-in-Time-Restore und Blob-Versionierung.
- **Hybrid:** StepStone und Remotely liefern aus Azure keine Treffer. Ein
  lokaler Windows-Task startet sie einmal täglich in Docker, mit dem Image des
  Azure-Workers und gegen dieselbe Azure-Datenbank
  (`scripts/run_local_hybrid.py`).

Einrichtung und Zugriffswege beschreiben [PostgreSQL betreiben](docs/postgresql.md)
und [Netzwerkpfade](docs/networking.md).

## Ablauf

1. Die Quellen liefern Suchtreffer und Detaildaten.
2. URLs und inhaltlich gleiche Stellen werden zusammengeführt.
3. Der Vorfilter schließt klare Konflikte sowie automatisch gefundene Anzeigen
   aus, deren bekanntes Veröffentlichungsdatum mehr als 60 Tage zurückliegt.
   Ein fehlendes Datum allein führt nicht zum Ausschluss. Zusätzliche Detail-
   und Verfügbarkeitsprüfungen hängen von der jeweiligen Quelle ab.
   Manuell eingereichte alte Anzeigen bleiben mit Warnung prüfbar.
   Die übrigen Stellen erhalten nachvollziehbare Kategorien für IT-Bereich,
   Einstieg und Standort.
4. Für passende Kandidaten werden je nach Quelle vollständige Details ergänzt.
   Die endgültige Bewertung erfolgt vor dem Speichern des Gedächtnisses.
   Anschließend werden neue, bekannte und inaktive Stellen zugeordnet sowie
   fehlende interessante Anzeigen unter den unten beschriebenen Bedingungen
   auf Verfügbarkeit geprüft.
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

Der Stellen- und Bewerbungszustand, Empfehlungen, Versandstatus und Quellencaches
liegen in PostgreSQL. Bewerbungsunterlagen bleiben separate Dateien, lokal
unter `data/internal/application_documents`, in Azure im Blob Storage; ihre
Zuordnung steht in der Datenbank.

Einrichtung, Backups und Wiederherstellung sind in
[PostgreSQL betreiben](docs/postgresql.md) beschrieben. Die alten SQLite-
und JSON-Dateien bleiben nach der Migration als Sicherung erhalten und werden
vom normalen Betrieb nicht mehr aktualisiert.

Direkte Arbeitnow-Anzeigen mit vollständigem Text bleiben unverändert. Nur bei
dem bekannten Platzhaltertext wird nach bestandenem Vorfilter die verlinkte
Originalanzeige geladen. Review und Discord verwenden anschließend bevorzugt
deren URL.

Remotely übernimmt ausschließlich Anzeigen aus einem rollierenden
Sieben-Tage-Fenster. Alte hervorgehobene Anzeigen und bereits vergebene Stellen
werden verworfen; jeder Lauf liest die Listenansicht bis zur alten
Trefferfront. Detailseiten werden sieben Tage gecacht. Bei Kandidaten mit
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
.\.venv\Scripts\python.exe scripts/test_postgres.py
```

Die gemeinsamen Browser-Helfer lassen sich zusätzlich mit Node.js (ab Version 18)
ohne weitere Pakete prüfen:

```powershell
node --test tests/frontend.test.cjs
```

Node.js wird nur für diese Tests benötigt, nicht für den Betrieb. Die drei
Oberflächen teilen sich `app.js` und `app.css`; ihre jeweiligen Abläufe bleiben
in den zugehörigen `landing.js`, `review.js` und `applications.js`.

Die Tests bleiben absichtlich im Repository: Sie dokumentieren die Regeln und
schützen insbesondere Deduplizierung, Quellenadapter, Review-Workflow und
Bewerbungsverlauf vor Regressionen.

## Projektstruktur

Die [Entwickleranleitung](docs/development.md) erklärt den Datenfluss, das
Ergänzen von Quellen, den Umgang mit lokalem Zustand sowie die Python- und
Docstring-Konventionen. Entwicklungswerkzeuge installieren und prüfen:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Der Workflow in `.github/workflows/checks.yml` führt Stilprüfungen, Python-
und Frontend-Tests bei Pull Requests und Pushes auf `main` aus. Er startet
keinen Finder-Lauf und verschickt keine Discord-Nachrichten. Bei Pull Requests
zeigt er zusätzlich einen `terraform plan`, der nur mit dem gespeicherten State
vergleicht (`-refresh=false`); nach erfolgreichem Test-Durchlauf auf `main`
baut er das Docker-Image, pusht es nach ACR, wendet die Infrastruktur per
`terraform apply` an und rollt danach das neue Image auf Worker und Review-App
aus. Dieser letzte Job wartet auf manuelle Freigabe im GitHub-Environment
`production`; ein neuerer Deploy bricht einen älteren, noch wartenden
automatisch ab. Nach der Freigabe rollt er nur aus, wenn sein Commit noch der
aktuelle `main`-Stand ist. Terraform selbst verwaltet die Image-Version nicht,
ein lokales `terraform apply` setzt die App also nie zurück (siehe
[infrastructure/cicd.tf](infrastructure/cicd.tf) und
[Netzwerkpfade](docs/networking.md)).

```text
job_finder/             Kernlogik, Quellen, Review und Bewerbungsverwaltung
job_finder/sources/     einzelne Quellenadapter
tests/                  automatisierte Tests
docs/development.md     Architektur, Quellenvertrag und Entwicklungsablauf
requirements-dev.txt    zusätzliche Werkzeuge für die Entwicklung
run_finder.py           produktiver Kommandozeilen-Einstieg
review_jobs.bat         Start der lokalen Weboberfläche
user_settings.example.yaml  dokumentierte, anonymisierte Konfigurationsvorlage
data/                   ausschließlich lokale Laufdaten (nicht versioniert)
```

## Lizenz

Dieses Projekt steht unter der MIT-Lizenz. Details enthält `LICENSE`.
