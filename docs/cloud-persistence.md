# Phase 7: Datenbank und Dateien cloudfähig machen

Arbeitsnotizen zur aktualisierten Roadmap `Job_Finder_Cloud_Roadmap_aktualisiert.docx`.
Stand: 18. September 2026. Die Bestandsaufnahme beschreibt den aktuellen Code,
keine bereits umgesetzte PostgreSQL-Anbindung.

## Vereinbarte Richtung

- PostgreSQL wird der einzige unterstützte Datenbanktyp: lokal in Docker für
  Entwicklung und Tests, in Azure für den tatsächlichen Betrieb. Die Datenbestände
  bleiben getrennt.
- SQLite bleibt zunächst als Migrationsquelle und Sicherung erhalten. Die
  SQLite-Unterstützung wird erst nach erfolgreicher Übernahme und Prüfung entfernt.
- Bewerbungsdokumente werden separat gespeichert. Zuordnung und Speicherreferenzen
  sollen in PostgreSQL liegen. Der konkrete Dateispeicher ist noch auszuwählen.
- Feste Spalten und Tabellen bilden die Grundlage für die Kerndaten. `jsonb`
  kommt gezielt für flexible Zusatz- oder Cachedaten infrage. Das genaue Schema
  ist noch nicht festgelegt.
- Die Review bleibt in Phase 7 zunächst lokal und erhält geschützten Zugriff auf
  den gemeinsamen Cloud-Datenbestand. Ihre Veröffentlichung folgt in Phase 8.
- Quellencaches sollen über Worker-Läufe und Containerwechsel hinweg gespeichert
  bleiben. Gewünscht ist eine Aufbewahrung über mehrere Wochen; die genaue Frist
  ist noch offen. Aufbewahrung, Aktualisierung und zulässige Nutzung alter Daten
  bei Abruffehlern werden getrennt festgelegt.

## Schritt 1: Bestandsaufnahme der Datenzugriffe

Die Pfade sind in `job_finder/paths.py` definiert. Angaben zu Lesern und Schreibern
beziehen sich auf den Anwendungscode, nicht auf Tests oder manuelle Dateibearbeitung.

| Ablage unter `data/` | Inhalt und Zweck | Finder | Review |
| --- | --- | --- | --- |
| `internal/job_finder.sqlite3` | Bekannte Stellen, Entscheidungen, Bewerbungsstatus und Historie sowie Dokumentmetadaten | Liest und aktualisiert Bestand und Verfügbarkeit | Liest Bestand; ändert Entscheidungen, Bewerbungsdaten und Historie |
| `internal/jobs.json` | Aktueller gesammelter Stellenbestand | Schreibt den Bestand nach einem Lauf | Manueller Import liest und ergänzt den Bestand |
| `output/recommendations.json` | Aufbereitete Empfehlungen mit Bewertung | Schreibt die Ergebnisse | Liest Empfehlungen; manueller Import ergänzt sie |
| `internal/notifications.json` | Bereits versendete und ausstehende Discord-Nachrichten | Liest und aktualisiert den Versandstatus | Kein direkter Zugriff festgestellt |
| Quellencaches unter `internal/` | Abgerufene Stellendetails und Zeitinformationen; teilweise spezielle Prüfergebnisse | Liest und aktualisiert die Quellendaten | Manueller Import schreibt den manuellen Quellenbestand |
| `internal/application_documents/` | Bewerbungsunterlagen als Dateien | Kein direkter Zugriff festgestellt | Speichert und liefert Dokumente aus; Bereinigung bei fehlgeschlagenen Vorgängen |

Weitere Ablagen:

- `internal/seen_jobs.json`: altes Gedächtnisformat, das beim erstmaligen Anlegen
  der SQLite-Datenbank importiert werden kann.
- `logs/`: Laufprotokolle; der Finder schreibt zugleich auf die Konsole.
- `backups/`: lokale ZIP-Sicherungen. Der Finder übergibt aktuell nur die
  SQLite-Datei und `notifications.json` an die Sicherungsfunktion. Dokumente und
  Ergebnisdateien sind damit nicht abgedeckt. Das Backup-Verfahren muss im
  Migrationsschritt einschließlich konsistenter Sicherung und Wiederherstellung
  geprüft werden.

## Erkenntnisse für die folgenden Schritte

1. Die Review kombiniert Empfehlungen aus JSON mit dem Zustand aus SQLite.
   Allein der Austausch von SQLite stellt daher noch keine vollständige
   gemeinsame Datenanbindung her.
2. Beide Prozesse schreiben: Auch die Review verändert beim manuellen Import
   den Stellenbestand, Empfehlungen und den manuellen Cache.
3. `manual_jobs_cache.json` enthält die ausdrücklich hinzugefügten Stellen und
   dient dem Finder als Quelle. Es ist nicht pauschal ein entbehrlicher Cache.
4. SQLite speichert aktuell pro Job einen JSON-Text in `job_state.payload_json`.
   `edit_memory` lädt den gesamten Bestand und schreibt ihn unter einer
   SQLite-Schreibsperre vollständig zurück. Dieses Verfahren muss beim späteren
   Umbau für gemeinsame Schreibzugriffe bewusst überprüft werden.
5. Der manuelle Import schreibt mehrere Ablagen nacheinander. Diese Änderungen
   bilden aktuell keine gemeinsame Transaktion.

Nachweise im Code: `run_finder.py`, `job_finder/memory.py`,
`job_finder/review.py`, `job_finder/review_data.py`, `job_finder/review_actions.py`,
`job_finder/manual_import.py`, `job_finder/notifications.py`,
`job_finder/application_documents.py`, `job_finder/sources/manual.py`,
`job_finder/sources/common.py` und `job_finder/operations.py`.

## Schritt 2: Aufbewahrung und gemeinsamer Zugriff

"Erneuerbar" bedeutet, dass Quelldaten grundsätzlich erneut abgerufen werden
können, solange die Quelle sie noch anbietet. Es bedeutet nicht, Caches nach
jedem Lauf zu löschen. Wiederverwendung soll unnötige Abrufe vermeiden.

Behandlung manueller Eingaben: Die manuell hinzugefügte Stelle
und ihre URL werden als dauerhafter Bestandteil des Stellenbestands mit Herkunft
"manuell" geführt. Zwischengespeicherte Webseitendetails erhalten eigene
Aktualisierungsregeln. Ein abgelaufener Cache oder eine verschwundene Anzeige
darf die gespeicherte Eingabe und Bewerbungsdaten nicht löschen.
Diese Trennung ist die vorgesehene Richtung, noch keine Implementierung.

Festgehaltene Einordnung:

- Bekannte Stellen, manuelle Eingaben, Bewerbungsdaten und Historie bleiben
  dauerhaft erhalten und werden von Finder und Review gemeinsam genutzt.
- Aktueller Stellenbestand und Empfehlungen werden gemeinsam und dauerhaft
  bereitgestellt. Eine vollständige Historie aller Suchläufe ist damit noch
  nicht beschlossen.
- Der Discord-Versandstatus bleibt über Läufe und Containerwechsel erhalten.
- Dokumente bleiben dauerhaft in einer separaten Dateiablage erhalten.
- Quellencaches bleiben über Läufe erhalten. Gemeinsame Regeln werden nach
  Datenart festgelegt; begründete Unterschiede bleiben bestehen.

Die bisherigen Aktualisierungs- und Fehlerersatzfristen bleiben beim Umbau
zunächst erhalten: viele Stellendetails 7 Tage bis zur Aktualisierung und
maximal 14 Tage seit erfolgreichem Abruf für Fehlerersatz; GermanTechJobs-Feed
Abruf bei jedem Lauf und maximal 3 Tage alter Fehlerersatz; LinkedIn-Prüfstatus
maximal 1 Tag wiederverwendbar. Dies sind Beispiele, keine vollständige Liste
aller Quellenregeln. Aufbewahrung bedeutet nicht automatisch weitere Nutzbarkeit
als aktuelles Ergebnis. Die genaue Aufräumfrist wird in Schritt 9 festgelegt;
die gewünschte Größenordnung beträgt mehrere Wochen.

Stand: Schritte 1 und 2 besprochen. Als Nächstes folgt Schritt 3:
Datenzugriff abstrahieren. Tabellenstruktur und Implementierung werden gemeinsam
schrittweise erarbeitet; eine SQLite-Alternative ist für das Zielsystem nicht
vorgesehen.
