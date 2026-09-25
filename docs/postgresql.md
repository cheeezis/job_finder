# PostgreSQL betreiben

PostgreSQL ist der einzige Laufzeitspeicher für den Stellenbestand, Entscheidungen,
Bewerbungsverläufe, Empfehlungen, Discord-Versandstatus und Quellencaches.
SQLite liest die Anwendung nicht mehr. Dokumentinhalte bleiben separate Dateien
(lokal oder im Blob Storage), ihre Metadaten und Zuordnung stehen in PostgreSQL.

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
als Secret bereitgestellt.

Die Datenbank ist nur unter `127.0.0.1:55432` erreichbar. Der separate Compose-Name
`jobfinder` vermeidet Konflikte mit anderen Projekten. Das Docker-Volume
`jobfinder_postgres_data` übersteht das Ersetzen des Containers. PostgreSQL 18
verwendet dafür den Mount `/var/lib/postgresql`. `docker compose down` erhält
das Volume; `down -v` würde es löschen und gehört nicht zum normalen Ablauf.

Worker und Review verbinden sich nicht mit dem Admin-Benutzer `jobfinder`,
sondern über die eigens angelegte Rolle `jobfinder_app` mit eingeschränkten
Rechten (kein Zugriff auf `schema_version`). Einmalig einrichten:

```powershell
.\.venv\Scripts\python.exe scripts/create_app_role.py
```

Der Befehl legt die Rolle an beziehungsweise aktualisiert ihre Rechte, setzt
`JOBFINDER_DATABASE_URL` in `.env.postgres` auf die neue Rolle und ist beliebig
oft wiederholbar. Passwörter werden dabei nie ausgegeben.

## Frühere Datenübernahme

Die einmalige Übernahme der alten SQLite- und JSON-Daten nach PostgreSQL ist
abgeschlossen; der Befehl `db migrate` wurde danach entfernt. Die Quellsicherung
liegt weiter unter `data/backups/pre-postgres-…`, die alten Dateien bleiben
unverändert erhalten. Der frühere Code ist im Git-Verlauf abrufbar:

```powershell
git show d8a829c:job_finder/persistence/migration.py
```

JSON-Dateien an ausdrücklich anderen Pfaden bleiben als Import-/Exportformat
und für Offline-Testfixtures nutzbar.

## Finder und Review starten

```powershell
.\.venv\Scripts\python.exe run_finder.py
.\.venv\Scripts\python.exe -m job_finder.review --no-browser
```

Die Befehle laufen in getrennten Terminals. Die Review ist unter
`http://127.0.0.1:8765` erreichbar. Discord wird bei jedem Finder-Lauf direkt
versendet, sofern `DISCORD_WEBHOOK_URL` gesetzt ist. Ein PostgreSQL-Lock
verhindert zwei gleichzeitig laufende Finder.

Dokumente liegen standardmäßig unter `data/internal/application_documents`.
`JOBFINDER_DOCUMENTS_DIR` kann vor dem Start auf eine andere dauerhafte Ablage
zeigen. Mit `JOBFINDER_DOCUMENTS_BACKEND=blob` liegen sie stattdessen in dem
Blob-Container, den `JOBFINDER_STORAGE_ACCOUNT` und
`JOBFINDER_STORAGE_CONTAINER` benennen; so arbeiten Worker, Review und der
lokale Hybrid-Lauf.

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
unterschiedlich. Optional lassen sich
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

In Containern (Azure-Worker, lokaler Hybrid-Lauf) entfällt dieses Backup
(`JOBFINDER_SKIP_RUN_BACKUP=1`): Ihr Dateisystem überdauert den Lauf nicht, das
ZIP wäre sofort wieder weg. Dort sichern der Point-in-Time-Restore des
Postgres-Servers (sieben Tage) und die Versionierung samt Soft Delete im
Blob Storage (14 Tage) die Daten.

Eine Wiederherstellung benötigt eine leere, separat konfigurierte Datenbank und
ein leeres Dokumentziel. Zuerst `JOBFINDER_DATABASE_URL` auf dieses Ziel setzen:

```powershell
.\.venv\Scripts\python.exe -m job_finder.db restore "PFAD_ZUM_BACKUP.zip" --documents-dir "PFAD_ZUM_LEEREN_DOKUMENTORDNER"
```

Die Anwendung prüft die Prüfsummen und vergleicht die zurückgeschriebenen Daten.
Bestehende Daten werden nicht überschrieben. Das ist ein Anwendungsbackup, kein
Ersatz für spätere Azure-Serverbackups, Rollen- oder Infrastruktur-Sicherungen.

## Azure

`infrastructure/postgresql.tf` verwaltet den produktiven Server: einen PostgreSQL-
Flexible-Server (`B_Standard_B1ms`, 32 GiB, France Central), die leere Datenbank
`jobfinder`, eine Firewallregel für genau eine öffentliche IPv4 und
`require_secure_transport`. Worker und Review melden sich dort mit Passwort
als Rolle `jobfinder_app` an. Die Verbindungs-URL samt Passwort liegt im
Key-Vault-Secret `JobfinderDatabaseUrl`, das beide über ihre Managed Identity
lesen (`infrastructure/keyvault.tf`, `main.tf`, `review.tf`); die folgenden
Schritte betreffen nur den administrativen Zugriff von einem lokalen Rechner aus.

Admin-Passwort und die freizugebende IP liegen lokal in
`infrastructure/postgres.auto.tfvars.json` (von Git ausgeschlossen). Planen und
anwenden aus dem Repository-Stamm:

```powershell
terraform -chdir=infrastructure fmt -check
terraform -chdir=infrastructure validate
terraform -chdir=infrastructure plan "-out=postgresql.tfplan"

$env:TF_VAR_postgres_admin_password = (Get-Content infrastructure/postgres.auto.tfvars.json -Raw | ConvertFrom-Json).postgres_admin_password
try {
    terraform -chdir=infrastructure apply "postgresql.tfplan"
} finally {
    Remove-Item Env:TF_VAR_postgres_admin_password -ErrorAction SilentlyContinue
}
```

Ändert sich die eigene Internetverbindung, muss `postgres_client_ipv4` in
`infrastructure/postgres.auto.tfvars.json` aktualisiert und neu geplant/angewendet
werden; eine IP-Freigabe ersetzt weder Passwort noch TLS.

Lesender Zugriffstest mit Zertifikatsprüfung (`sslmode=verify-full`), ohne lokale
Konfiguration oder Datenbestände zu verändern:

```powershell
.\.venv\Scripts\python.exe scripts/check_azure_postgres.py
```

Die App-Rolle für Azure einrichten beziehungsweise aktualisieren (analog zur
lokalen Rolle oben, aber gegen den Azure-Server):

```powershell
.\.venv\Scripts\python.exe scripts/create_app_role.py --azure
```

Neue Tabellen und Spalten legt ausschließlich `job_finder.db init` an, die
Anwendung selbst ändert das Schema nie. Nach einer Schemaerweiterung (zuletzt
für den KI-Agenten: `agent_fact_sheets` und die Spalte `web_searches` in
`agent_usage`) läuft der Befehl deshalb einmal gegen Azure, mit den
Verbindungsdaten aus `.env.postgres-azure`, die nur für diesen Aufruf gesetzt
werden. `init` legt nur Fehlendes an und lässt vorhandene Daten unverändert;
`check` zählt danach als App-Rolle die Zeilen und zeigt so, dass sie die neuen
Tabellen lesen darf:

```powershell
$azure = Get-Content .env.postgres-azure -Raw | ConvertFrom-StringData
try {
    $env:JOBFINDER_ADMIN_DATABASE_URL = $azure.JOBFINDER_ADMIN_DATABASE_URL
    $env:JOBFINDER_DATABASE_URL = $azure.JOBFINDER_DATABASE_URL
    .\.venv\Scripts\python.exe -m job_finder.db init
    .\.venv\Scripts\python.exe -m job_finder.db check
} finally {
    Remove-Item Env:JOBFINDER_ADMIN_DATABASE_URL, Env:JOBFINDER_DATABASE_URL -ErrorAction SilentlyContinue
}
```

Laufende Kosten: B1MS-Rechenleistung und Standardspeicher zusammen etwa
15 EUR/Monat (France Central, Stand der letzten Preisabfrage), vor Steuern und
weiteren Ressourcen wie Registry oder Logs. Der Server läuft auch außerhalb von
Finder-Läufen weiter. Pausieren spart nur Rechenleistung, nicht den Speicher:

```powershell
$jobfinderPostgresServer = terraform -chdir=infrastructure output -raw postgres_server_name
az postgres flexible-server stop --resource-group rg-jobfinder --name $jobfinderPostgresServer
# Für die Weiterarbeit:
az postgres flexible-server start --resource-group rg-jobfinder --name $jobfinderPostgresServer
```

Ein gestoppter Server startet nach sieben Tagen automatisch wieder. Ein
späteres Löschen benötigt vorher eine geprüfte Datensicherung;
`terraform destroy` im Ordner betrifft die gesamte dort verwaltete
Infrastruktur, nicht nur PostgreSQL. Server und Storage-Account tragen
zusätzlich eine Löschsperre (`no-delete`): Jedes Löschen, auch per
`terraform destroy`, scheitert, bis die Sperre bewusst lokal mit
Owner-Rechten entfernt wurde. Stoppen und Starten sind davon nicht betroffen.

## Lokaler Hybrid-Lauf (StepStone/Remotely)

StepStone und Remotely liefern aus Azure heraus keine Treffer; sie laufen
stattdessen einmal täglich über den lokalen Windows-Task gegen dieselbe
Azure-Datenbank (`scripts/run_local_hybrid.py`, Überblick in der README unter
„Betrieb“). Der Task startet genau das Image, das der Azure-Worker gerade
nutzt: Er fragt es bei jedem Start über die lokale `az`-Anmeldung ab, meldet
sich an der Registry an und holt es per `docker pull`. Beide Hälften laufen
so immer mit derselben Code-Version; Voraussetzung sind eine gültige
`az`-Anmeldung und ein laufendes Docker Desktop. Die persönlichen
Sucheinstellungen gibt das Skript aus `user_settings.local.yaml` als
`JOBFINDER_USER_SETTINGS` an den Container weiter; im Image stehen nur die
Beispielwerte.

Eine native Windows-Verbindung (`.venv`) lieferte zeitweise veraltete
Lesezustände gegenüber Azure, ein Snapshot von Stunden zuvor. Im Container
trat das einmal ebenfalls auf, danach nicht mehr. Die Ursache ist ungeklärt;
der Container-Betrieb umgeht das Problem nur, er erklärt es nicht.

Ohne Managed Identity oder interaktive `az`-Anmeldung im Container braucht
das einen eigenen, eng begrenzten Service Principal für den Blob-Zugriff
(nur `Storage Blob Data Contributor` auf dem Dokument-Container
`application-documents`, nicht auf dem `tfstate`-Container daneben, siehe
`infrastructure/storage.tf`,
`storage_blob_data_contributor_local_docker`). Einmalig einrichten:

```powershell
az ad app create --display-name "jobfinder-local-docker"
az ad sp create --id <appId aus dem vorigen Befehl>
az ad app credential reset --id <appId> --display-name "local-docker-worker" --years 2
```

Die drei Werte (`appId`, `tenant`, `password`) in `.env.docker-local`
speichern (von Git ausgeschlossen, nicht dieselben Werte wie
`.env.postgres-azure`):

```text
AZURE_CLIENT_ID=<appId>
AZURE_TENANT_ID=<tenant>
AZURE_CLIENT_SECRET=<password>
```

Danach in `infrastructure/variables.tf` die `object_id` des neuen Service
Principals (`az ad sp show --id <appId> --query id`) als
`local_docker_sp_object_id` eintragen und die Rollenzuweisung anwenden.

## Tests

Einmalig die getrennte Testdatenbank anlegen:

```powershell
docker compose --env-file .env.postgres exec postgres createdb -U jobfinder jobfinder_test
```

Danach:

```powershell
.\.venv\Scripts\python.exe scripts/test_postgres.py
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
node --test tests/frontend.test.cjs
```

Der Teststarter verlangt `JOBFINDER_TEST_DATABASE_URL` mit einem eigenen
Datenbanknamen auf `_test`, verweigert den konfigurierten Produktivnamen und leert
nur die Anwendungstabellen dieser ausdrücklich ausgewählten Testdatenbank.
Die CI führt die Python-Suite mit einem PostgreSQL-Service auf Linux aus;
derselbe Teststarter funktioniert lokal unter Windows.
