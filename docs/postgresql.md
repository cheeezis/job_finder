# PostgreSQL lokal betreiben

PostgreSQL ist der einzige Laufzeitspeicher für den Stellenbestand, Entscheidungen,
Bewerbungsverläufe, Empfehlungen, Discord-Versandstatus und Quellencaches.
SQLite wird nur noch beim ausdrücklichen Altimport gelesen. Dokumentinhalte bleiben
separate Dateien, ihre Metadaten und Zuordnung stehen in PostgreSQL.

## Einrichtung

Aus dem Repository-Stamm in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/setup_postgres.py
docker compose --env-file .env.postgres up -d --wait
.\.venv\Scripts\python.exe -m job_finder.db init
```

Der Setup-Befehl erzeugt einmalig ein zufälliges Passwort in `.env.postgres`.
Die Datei ist von Git und Docker-Builds ausgeschlossen. Vorhandene Einstellungen
werden nicht überschrieben. Die Anwendung lädt sie automatisch; bereits gesetzte
Umgebungsvariablen haben Vorrang. Im Cloud-Betrieb wird `JOBFINDER_DATABASE_URL`
als Secret bereitgestellt. Für Azure ist später TLS mit Zertifikatsprüfung
einzurichten; diese lokale Umstellung erzeugt keine neuen Azure-Ressourcen.

Die Datenbank ist nur unter `127.0.0.1:55432` erreichbar. Der separate Compose-Name
`jobfinder` vermeidet Konflikte mit anderen Projekten. Das Docker-Volume
`jobfinder_postgres_data` übersteht das Ersetzen des Containers. PostgreSQL 18
verwendet dafür den Mount `/var/lib/postgresql`. `docker compose down` erhält
das Volume; `down -v` würde es löschen und gehört nicht zum normalen Ablauf.

## Vorhandene Daten übernehmen

Finder und Review vorher beenden, damit niemand mehr die alten Dateien ändert:

```powershell
.\.venv\Scripts\python.exe -m job_finder.db migrate
.\.venv\Scripts\python.exe -m job_finder.db check
```

Die Migration erstellt unter `data/backups/pre-postgres-…` eine Kopie der
JSON-Dateien und Dokumente sowie eine konsistente SQLite-Sicherung. Sie übernimmt
den SQLite-Zustand und die JSON-Dateien direkt unter `data/internal` und
`data/output`. Die durch SQLite ersetzte `seen_jobs.json` bleibt nur als Altdaten-
sicherung erhalten. Vorhandene zusätzliche JSON-Daten, etwa ein alter LLM-Cache,
werden ohne Reaktivierung der früheren Funktion mit gesichert und übernommen.

Das Ziel muss leer sein. Vor dem Commit werden alle übernommenen Inhalte
verglichen und alle referenzierten Dokumente anhand ihrer Prüfsummen geprüft.
Ein Fehler rollt die Datenübernahme zurück. Derselbe erfolgreich migrierte
Quellstand wird beim erneuten Aufruf nicht noch einmal über laufende Daten
geschrieben. Der Bericht liegt in der jeweiligen Quellsicherung.
Alte Dateien bleiben unverändert erhalten; der laufende Finder liest seine
Standarddaten anschließend aus PostgreSQL. JSON-Dateien an ausdrücklich anderen
Pfaden bleiben als Import-/Exportformat und für Offline-Testfixtures nutzbar.

## Finder und Review starten

```powershell
.\.venv\Scripts\python.exe run_finder.py
.\.venv\Scripts\python.exe -m job_finder.review --no-browser
```

Die Befehle laufen in getrennten Terminals. Die Review ist unter
`http://127.0.0.1:8765` erreichbar. Discord wird bei jedem Finder-Lauf direkt
versendet, sofern `DISCORD_WEBHOOK_URL` gesetzt ist. Ein PostgreSQL-Lock
verhindert zwei gleichzeitig laufende Finder.

Dokumente liegen standardmäßig weiterhin unter `data/internal/application_documents`.
`JOBFINDER_DOCUMENTS_DIR` kann vor dem Start auf eine andere dauerhafte Ablage
zeigen. In Containern muss diese Ablage gemountet werden. Ein lokaler Dateipfad
allein macht die Dokumente noch nicht in Azure verfügbar.

## Datenmodell und gleichzeitige Zugriffe

- `job_state`: feste Spalten für ID, Titel, Firma, Status, Aktivität, Fehl-Läufe,
  Gehaltsvorstellung, persönliche Bewertung und Notiz; weitere Attribute als JSONB.
- `workflow_history`: geordnete Ereignisse mit Status, Datum und Gesprächstermin.
- `application_documents`: Metadaten und Referenzen, keine Dokumentbytes.
- `jobs` / `recommendations`: eigene Datensätze pro Snapshot-Eintrag mit festen
  Kernspalten. Mehrere Quellendarstellungen derselben kanonischen Job-ID bleiben
  erhalten; die Position ist Teil des Schlüssels.
- `notifications`: getrennte ausstehende und bereits versendete Einträge.
- `manual_sources`: dauerhaft hinzugefügte URLs und ihre Quelldaten. Ein veralteter
  Cache-Schreibstand entfernt diese Eingaben nicht.
- `source_cache`: einzeln gespeicherte Cache-Inhalte mit JSONB und Speicherzeit.
- `datasets`: kleine Header und Formatangaben für die rekonstruierten Ansichten.

Einzelne Review-Änderungen sperren die betroffene Stelle. Größere Finder-Abgleiche
laden weiterhin den Bestand zur bestehenden Deduplizierung, sperren dabei die
Bestandsänderungen und schreiben nur veränderte Datensätze zurück. Status und
Verlauf werden gemeinsam gespeichert. Der manuelle Import schreibt Quellen,
Gedächtnis und Ergebnisse in einer Transaktion; der HTTP-Abruf erfolgt davor.
Die Veröffentlichung der beiden Ergebnisansichten ist ebenfalls atomar.

## Caches

Aktualisierungs- und Fehlerersatzfristen bleiben wie bisher nach Datenart
unterschiedlich. Die Migration löscht keine Cache-Inhalte. Optional lassen sich
automatische Cache-Einträge aufräumen, die seit mindestens 30 Tagen nicht mehr
neu gespeichert oder geändert wurden:

```powershell
.\.venv\Scripts\python.exe -m job_finder.db prune-cache --days 30
```

Unverändertes erneutes Schreiben setzt diese Frist nicht zurück. Manuelle Quellen,
Stellen, Bewerbungen, Versandstatus und Dokumente sind von diesem Aufräumen
ausgeschlossen. Aufbewahrung verlängert nicht die Gültigkeit alter Quelldaten.

## Backup und Wiederherstellung

```powershell
.\.venv\Scripts\python.exe -m job_finder.db backup
```

Das ZIP enthält einen konsistenten PostgreSQL-Anwendungsstand, alle referenzierten
Dokumentdateien und Prüfsummen. Vor jedem echten Finder-Lauf wird ebenfalls ein
solches Backup erzeugt; die automatische Rotation behält sieben Archive.
Die Quellsicherungen vor der Migration sind davon ausgenommen.

Eine Wiederherstellung benötigt eine leere, separat konfigurierte Datenbank und
ein leeres Dokumentziel. Zuerst `JOBFINDER_DATABASE_URL` auf dieses Ziel setzen:

```powershell
.\.venv\Scripts\python.exe -m job_finder.db restore "PFAD_ZUM_BACKUP.zip" --documents-dir "PFAD_ZUM_LEEREN_DOKUMENTORDNER"
```

Die Anwendung prüft die Prüfsummen und vergleicht die zurückgeschriebenen Daten.
Bestehende Daten werden nicht überschrieben. Das ist ein Anwendungsbackup, kein
Ersatz für spätere Azure-Serverbackups, Rollen- oder Infrastruktur-Sicherungen.

## Tests

Einmalig die getrennte Testdatenbank anlegen:

```powershell
docker compose --env-file .env.postgres exec postgres createdb -U jobfinder jobfinder_test
```

Danach:

```powershell
.\.venv\Scripts\python.exe scripts/test_postgres.py
.\.venv\Scripts\python.exe -m ruff check job_finder scripts tests run_finder.py
.\.venv\Scripts\python.exe -m ruff format --check job_finder scripts tests run_finder.py
node --test tests/frontend.test.cjs
```

Der Teststarter verlangt `JOBFINDER_TEST_DATABASE_URL` mit einem eigenen
Datenbanknamen auf `_test`, verweigert den konfigurierten Produktivnamen und leert
nur die Anwendungstabellen dieser ausdrücklich ausgewählten Testdatenbank.
Die CI führt die Python-Suite mit einem PostgreSQL-Service auf Linux aus;
derselbe Teststarter funktioniert lokal unter Windows.
