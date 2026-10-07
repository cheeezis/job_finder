# Betrieb

Einrichtung der Datenbank, Backups, Azure, der lokale Hybrid-Lauf und die
Zugriffswege. Bedienung steht in [Bedienung](bedienung.md), Aufbau und
Entwicklung in der [Entwickleranleitung](development.md).

PostgreSQL ist der einzige Laufzeitspeicher für Stellenbestand, Entscheidungen,
Bewerbungsverläufe, Empfehlungen, Discord-Versandstatus, Quellencaches und die
Steckbriefe des Agenten. Dokumentinhalte bleiben separate Dateien (lokal oder im
Blob Storage), ihre Metadaten und Zuordnung stehen in PostgreSQL.

## Lokale Datenbank

Aus dem Repository-Stamm in PowerShell:

```powershell
uv sync
uv run python scripts/setup_postgres.py
docker compose --env-file .env.postgres up -d --wait
uv run python -m job_finder.db init
```

Der Setup-Befehl erzeugt einmalig ein zufälliges Passwort in `.env.postgres`.
Die Datei ist von Git und Docker-Builds ausgeschlossen. Vorhandene Einstellungen
werden nicht überschrieben. Die Anwendung lädt sie automatisch; bereits gesetzte
Umgebungsvariablen haben Vorrang.

Die Datenbank ist nur unter `127.0.0.1:55432` erreichbar. Der separate Compose-Name
`jobfinder` vermeidet Konflikte mit anderen Projekten. Das Docker-Volume
`jobfinder_postgres_data` (Mount `/var/lib/postgresql`) übersteht das Ersetzen
des Containers. `docker compose down` erhält das Volume; `down -v` würde es
löschen und gehört nicht zum normalen Ablauf.

Worker und Review verbinden sich nicht mit dem Admin-Benutzer `jobfinder`,
sondern über die Rolle `jobfinder_app` mit eingeschränkten Rechten (kein Zugriff
auf `schema_version` oder `alembic_version`). Einmalig einrichten:

```powershell
.\.venv\Scripts\python.exe scripts/create_app_role.py
```

Der Befehl legt die Rolle an beziehungsweise aktualisiert ihre Rechte, setzt
`JOBFINDER_DATABASE_URL` in `.env.postgres` auf die neue Rolle und ist beliebig
oft wiederholbar. Passwörter werden dabei nie ausgegeben.

Für die Tests einmalig die getrennte Testdatenbank anlegen:

```powershell
docker compose --env-file .env.postgres exec postgres createdb -U jobfinder jobfinder_test
```

Der Teststarter `scripts/test_postgres.py` verlangt `JOBFINDER_TEST_DATABASE_URL`
mit einem eigenen Datenbanknamen auf `_test`, verweigert den konfigurierten
Produktivnamen und leert nur die Anwendungstabellen dieser Testdatenbank.

## Daten, Dokumente und Caches

- `job_state`: feste Spalten für ID, Titel, Firma, Status, Aktivität, Fehlläufe,
  Gehaltsvorstellung, persönliche Bewertung und Notiz; weitere Attribute als JSONB.
- `workflow_history`: geordnete Ereignisse mit Status, Datum und Gesprächstermin.
- `application_documents`: Metadaten und Referenzen, keine Dokumentbytes.
- `jobs` / `recommendations`: eigene Datensätze pro Snapshot-Eintrag mit festen
  Kernspalten; mehrere Quellendarstellungen einer Job-ID bleiben erhalten, die
  Position ist Teil des Schlüssels.
- `notifications`: getrennte ausstehende und bereits versendete Einträge.
- `manual_sources`: dauerhaft hinzugefügte URLs und ihre Quelldaten; ein
  veralteter Cache-Schreibstand entfernt sie nicht.
- `source_cache`: einzeln gespeicherte Cache-Inhalte mit JSONB und Speicherzeit.
- `datasets`: kleine Header und Formatangaben für die rekonstruierten Ansichten.
- `agent_usage` / `agent_fact_sheets`: Kostenbuch und Steckbriefe des Agenten.

Einzelne Review-Änderungen sperren die betroffene Stelle; Status und Verlauf
werden gemeinsam gespeichert. Finder-Abgleiche sperren die Bestandsänderungen
und schreiben nur veränderte Datensätze zurück; ein Lock verhindert zwei
gleichzeitige Finder. Der manuelle Import schreibt
Quellen, Gedächtnis und Ergebnisse in einer Transaktion, der HTTP-Abruf erfolgt
davor. Der Finder schreibt Gedächtnis, bestätigte Schließungen, beide
Ergebnisansichten und Discord-Aufträge gemeinsam in einer Transaktion. Versand
und Quittierung folgen nach Commit. Die bestehende Tabelle `notifications`
enthält die Kartendaten, einen stabilen Ereignisschlüssel und den Versandstatus;
für diese Outbox ist keine Schemaänderung erforderlich.

Offene Aufträge werden im nächsten erfolgreichen Finder-Lauf auch ohne erneuten
Fund wiederholt. Quellen-Ausfälle und geteilte Zeitpläne entfernen sie nicht.
Eine neu gesetzte Review-Entscheidung oder ein Vorfilterausschluss kann den
Auftrag verwerfen. Erfolgreiche Teilversände werden einzeln gespeichert. Bei
einem Abbruch nach Discord-Erfolg, aber vor Quittierung ist ein doppelter Hinweis
möglich. Die Laufstatistik wird weiterhin unmittelbar gesendet. Einzelheiten
und Abbruchtests: [Entwickler-Doku](development.md#veröffentlichung-und-discord-outbox).

Dokumente liegen standardmäßig unter `data/internal/application_documents`;
`JOBFINDER_DOCUMENTS_DIR` kann auf eine andere dauerhafte Ablage zeigen. Mit
`JOBFINDER_DOCUMENTS_BACKEND=blob` liegen sie in dem Blob-Container, den
`JOBFINDER_STORAGE_ACCOUNT` und `JOBFINDER_STORAGE_CONTAINER` benennen; so
arbeiten Worker, Review und Hybrid-Lauf.

Beim Start einer Bewerbung schreibt die Review die Dokumente zuerst unter neuen
Schlüsseln und trägt sie danach in einer kurzen Transaktion ein; scheitert diese,
löscht sie die neuen Dateien wieder. Nur ein Absturz genau dazwischen hinterlässt
eine Datei ohne Verweis. Solche Dateien, die älter als 24 Stunden sind, listet
der folgende Befehl; gelöscht wird nur mit `--delete`. Gegen Azure mit denselben
Umgebungsvariablen wie die Review (`JOBFINDER_DOCUMENTS_BACKEND=blob`, Konto,
Container) und einer Datenbank-URL mit Lesezugriff:

```powershell
.\.venv\Scripts\python.exe -m job_finder.db orphaned-documents
.\.venv\Scripts\python.exe -m job_finder.db orphaned-documents --delete
```

Automatische Cache-Einträge, die seit mindestens 30 Tagen nicht neu gespeichert
oder geändert wurden, lassen sich aufräumen; unverändertes erneutes Schreiben
setzt die Frist nicht zurück. Manuelle Quellen, Stellen, Bewerbungen,
Versandstatus und Dokumente bleiben davon unberührt:

```powershell
.\.venv\Scripts\python.exe -m job_finder.db prune-cache --days 30
```

## Backup und Wiederherstellung

```powershell
.\.venv\Scripts\python.exe -m job_finder.db backup
```

Das ZIP enthält einen konsistenten Anwendungsstand samt Steckbriefen und
Kostenbuch des Agenten, alle referenzierten Dokumentdateien und Prüfsummen. Vor
jedem lokalen Finder-Lauf entsteht ebenfalls ein solches Backup; die Rotation
behält sieben Archive. In Containern (Azure-Worker, Hybrid-Lauf) entfällt es
(`JOBFINDER_SKIP_RUN_BACKUP=1`), weil ihr Dateisystem den Lauf nicht überdauert.
Dort sichern der Point-in-Time-Restore des Servers (14 Tage) und die
Versionierung samt Soft Delete im Blob Storage (14 Tage).

Eine Wiederherstellung braucht eine leere, separat konfigurierte Datenbank und
ein leeres Dokumentziel. Zuerst `JOBFINDER_DATABASE_URL` auf dieses Ziel setzen:

```powershell
.\.venv\Scripts\python.exe -m job_finder.db restore "PFAD_ZUM_BACKUP.zip" --documents-dir "PFAD_ZUM_LEEREN_DOKUMENTORDNER"
```

Die Anwendung prüft die Prüfsummen, vergleicht die zurückgeschriebenen Daten und
überschreibt nichts. Das ist ein Anwendungsbackup, kein Ersatz für die
Azure-Serverbackups, Rollen- oder Infrastruktur-Sicherungen.

Die gemeinsame Planung für Datenbank und historische Dokumentversionen steht
unter [Backup und Wiederherstellung](backup-recovery.md). Eine Probe mit
erfundenen Daten auf dem lokalen PostgreSQL-Testcontainer läuft mit
`python scripts/restore_drill.py`; sie erstellt und entfernt eigene
Testdatenbanken und prüft die Dokumentverweise. Sie ersetzt keine Azure-PITR-
und Blob-Restore-Abnahme; diese ist am 05.10.2026 bestanden
([Ergebnis](backup-recovery.md#ergebnis-der-azure-probe)).

## Azure

`infrastructure/postgresql.tf` verwaltet den produktiven Server: einen
PostgreSQL-Flexible-Server (`B_Standard_B1ms`, 32 GiB, France Central), die
Datenbank `jobfinder`, die Firewallregeln und `require_secure_transport`.
Im F09-Modus `split` nutzen Worker, Review und Hybrid eigene eingeschränkte
DB-Rollen; die Cloud-URLs liegen in den jeweiligen Worker-/Review-DB-Secrets,
die jede Komponente mit ihrer eigenen Managed Identity liest. Nur der bisherige
Modus `legacy` verwendet gemeinsam `jobfinder_app` und `JobfinderDatabaseUrl`.
Rechte und Phasen: [Laufzeitzugänge](runtime-access.md). Die gesonderte Umstellung
auf Token-Anmeldung ist in [Datenbankanmeldung mit Entra](database-auth.md)
beschrieben; Standard bleibt Passwortauthentifizierung. Die folgenden Schritte
betreffen den administrativen Zugriff vom eigenen Rechner.

Admin-Passwort und die freizugebende IP liegen lokal in
`infrastructure/postgres.auto.tfvars.json`, weitere private Werte in
`infrastructure/terraform.tfvars` (beide von Git ausgeschlossen). Planen und
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

Ändert sich die eigene öffentliche IP, `postgres_client_ipv4` anpassen und neu
planen und anwenden; die Firewallregel `local-review` lässt genau diese Adresse
zu, auch für den Hybrid-Lauf. Lesender Zugriffstest mit Zertifikatsprüfung, der
nichts verändert:

```powershell
.\.venv\Scripts\python.exe scripts/check_azure_postgres.py
```

Den gemeinsamen Zugang nur für die Ersteinrichtung im Modus `legacy` anlegen.
Getrennte Worker-/Review-/Hybrid-Zugänge, Rechte-Matrix und der Ablauf mit zwei Etappen
stehen in [runtime-access.md](runtime-access.md). Nach `split` deren Wartungsweg
verwenden, nicht den folgenden alten App-Rollen-Befehl erneut ausführen.

```powershell
.\.venv\Scripts\python.exe scripts/create_app_role.py --azure
```

Neue Tabellen und Spalten werden ausschließlich mit expliziten Alembic-Migrationen
über `job_finder.db migrate` angelegt; `init` ist ein kompatibler Alias für denselben
Pfad. Worker und Review ändern das Schema nie. Eine leere Datenbank erhält das Schema
über die unveränderte Baseline-Revision `0001_baseline` und die folgenden Revisionen
bis `head`. Eine bestehende Datenbank ohne Alembic-Stand
wird erst nach Prüfung der vollständigen Baseline-Struktur und des bisherigen
Versionsmarkers übernommen. Abweichungen führen zum Abbruch; fehlende Tabellen oder
Spalten werden bei der Übernahme nicht automatisch repariert.
Die bekannte alte Import-Protokolltabelle `migration_runs` darf mit ihrer ursprünglichen,
ebenfalls geprüften Struktur vorhanden sein. Sie und ihre Daten bleiben erhalten;
frische Datenbanken bekommen sie seit der abgeschlossenen Datenübernahme nicht mehr.

`schema-status` prüft Struktur und Migrationsstand mit dem Admin-Zugang, technisch
schreibgeschützt. Es zeigt `empty`, `legacy`, `current` oder `outdated`, den aktuellen
Stand und das Ziel `head`, ohne Zeilenzahlen oder Verbindungsdaten. `migrate` übernimmt
Prüfung, Baseline-Markierung, Upgrade und Schutz der Versionstabellen in einer
Transaktion unter der bisherigen Schema-Sperre. Auch ererbte Tabellenrechte werden
für die Versionstabellen entzogen; Anwendungstabellen behalten ihre Rechte.

Vor einer Produktionsmigration Bestand sichern und `schema-status` prüfen. Bei einer
Abweichung Ursache und tatsächlichen Bestand untersuchen; kein ungeprüftes `alembic
stamp`. Die Baseline erhält die bisherige `schema_version = 2` und alle vorhandenen
Daten. Ein Rollback auf das vorherige App-Image benötigt keinen Schema-Downgrade;
ein Baseline-Downgrade wird bewusst abgelehnt, weil er den gesamten Bestand löschen
würde. Neue Revisionen brauchen einen eigenen Kompatibilitäts- und Rückkehrplan.
`0003_agent_fact_sheet_state` ergänzt nur Spalten mit Standardwerten an
`agent_fact_sheets` (Wiederholung, Versuche, Grundlage, veraltete Teile): Das
vorherige Image schreibt weiter wie bisher, ein Image-Rollback braucht also keinen
Downgrade. Die Migration läuft vor dem Merge des passenden Releases, weil das neue
Image die Spalten liest.
`0004_review_lookup_indexes` legt zwei GIN-Indizes auf `job_state.extra` an (Listen-URLs und
verknüpfte IDs), über die die Review gemerkte Stellen einer Anzeige findet; ohne sie liest
PostgreSQL für jede Stellenliste die ganze Tabelle. Die Strukturprüfung erwartet genau diese
beiden Definitionen. Auch diese Migration läuft vor dem Merge des passenden Releases.
`0005_job_listings` (F17, Etappe 1) legt die Tabellen `job_listings` (Anzeigen einer
Stelle: Position, URL, Quelle) und `job_links` (verknüpfte Stellen) sowie die Spalten
`first_seen_at`, `last_seen_at` und `locations` an `job_state` an, übernimmt die Werte
aus `job_state.extra` und gibt den Tabellen dieselben Rechte wie `job_state`. Bis zum
Abbau in einer späteren Etappe bleiben die JSON-Felder die Quelle: Jedes Speichern
leitet Tabellen und Spalten daraus neu ab, ein älteres Image läuft also unverändert
weiter. Abweichungen, etwa nach einem Rollback, meldet `listing_drift` in der
Laufzeile `run_summary`; prüfen und reparieren:

```powershell
.\.venv\Scripts\python.exe -m job_finder.db listings-drift
.\.venv\Scripts\python.exe -m job_finder.db listings-drift --repair
```
Migrationen laufen bewusst getrennt vom Deploy (siehe [Deploy und Rollback](#deploy-und-rollback)).

Nach einer freigegebenen Schemaänderung läuft der Befehl einmal gegen Azure, mit
den nur für diesen Aufruf gesetzten Verbindungsdaten aus `.env.postgres-azure`.
`check` zählt danach als App-Rolle die Zeilen und prüft damit den Datenzugriff:

```powershell
$azure = Get-Content .env.postgres-azure -Raw | ConvertFrom-StringData
try {
    $env:JOBFINDER_ADMIN_DATABASE_URL = $azure.JOBFINDER_ADMIN_DATABASE_URL
    $env:JOBFINDER_DATABASE_URL = $azure.JOBFINDER_DATABASE_URL
    .\.venv\Scripts\python.exe -m job_finder.db schema-status
    .\.venv\Scripts\python.exe -m job_finder.db migrate
    .\.venv\Scripts\python.exe -m job_finder.db check
} finally {
    Remove-Item Env:JOBFINDER_ADMIN_DATABASE_URL, Env:JOBFINDER_DATABASE_URL -ErrorAction SilentlyContinue
}
```

Der Server läuft auch zwischen den Finder-Läufen (Preise unter
[Kosten](#kosten)). Pausieren spart nur Rechenleistung, nicht den Speicher; ein
gestoppter Server startet nach sieben Tagen von selbst wieder:

```powershell
$jobfinderPostgresServer = terraform -chdir=infrastructure output -raw postgres_server_name
az postgres flexible-server stop --resource-group rg-jobfinder --name $jobfinderPostgresServer
# Für die Weiterarbeit:
az postgres flexible-server start --resource-group rg-jobfinder --name $jobfinderPostgresServer
```

Server und Storage-Account tragen eine Löschsperre (`no-delete`): Jedes
Löschen, auch per `terraform destroy`, scheitert, bis die Sperre bewusst lokal
mit Owner-Rechten entfernt wurde. `terraform destroy` betrifft die gesamte
Infrastruktur im Ordner und braucht vorher eine geprüfte Datensicherung.

Was Daten oder den Zugangsschutz trägt, schützt Terraform zusätzlich mit
`prevent_destroy`: Server und Datenbank, Storage-Account und Dokumente-Container,
den Key Vault sowie die Review-App und ihre Anmeldekonfiguration. Ein Plan, der
eines davon löschen oder neu anlegen würde, bricht vor dem Apply ab, auch
`terraform destroy`; ein bewusster Abbau braucht erst eine Codeänderung.

Eine neu angelegte Review ist zunächst nur intern erreichbar; öffentlich
schaltet sie erst `review_public`, nachdem die Anmeldung steht. Für einen
bewussten Neuaufbau `prevent_destroy` an App und Anmeldekonfiguration lokal
entfernen und die drei Teile zusammen ersetzen, mit denselben lokalen Werten
wie oben:

```powershell
terraform -chdir=infrastructure apply -replace=azurerm_container_app.review -replace=azapi_resource.review_auth -replace=azapi_resource_action.review_public
```

## Deploy und Rollback

Ein Deploy nach dem Merge läuft in drei Stufen:

1. **Plan:** Der Job „Release plan“ erstellt den Terraform-Plan gegen den echten
   Azure-Zustand und legt ihn privat im State-Container ab (`release-plans/<commit>`).
   Pläne können geheime Werte enthalten; die Zusammenfassung im Lauf nennt deshalb
   nur Ressourcen und Aktionen. Das Environment `production-plan` ist auf `main`
   beschränkt und braucht keine Freigabe.
2. **Freigabe und Apply:** Nach der Freigabe in `production` wendet der Apply genau
   diesen Plan an, nach Prüfung seiner Prüfsumme. Ein veralteter Plan (State seither
   geändert) bricht ab; dann den Lauf neu starten. Der Plan wird danach gelöscht.
3. **Ausrollen und prüfen:** Die Zusammenfassung notiert unter „Rückweg“ das
   bisherige Image. Danach rollt der Job das neue Image per Digest aus, prüft, dass
   die Review ohne Anmeldung nur mit 302 (Umleitung zum Login) oder 401 antwortet,
   und wartet, bis Worker und neueste Review-Revision auf dem neuen Image stehen und
   die Revision gesund läuft ([verify_rollout.sh](../.github/scripts/verify_rollout.sh)).

**Rollback:** Unter Actions den Workflow „Rollback“ von `main` starten und das unter
„Rückweg“ notierte Image (`…/jobfinder@sha256:…`) eintragen. Er läuft ebenfalls erst
nach Freigabe in `production`, setzt nur das Image von Worker und Review zurück und
führt dieselben Prüfungen aus. Terraform und Datenbank bleiben unverändert.

Das geht nur, solange das Schema zum alten Image passt. Migrationen sind deshalb
erweiternd (neue Tabellen und Spalten, nichts entfernen) und laufen vor dem Deploy
getrennt, wie oben unter Azure beschrieben; Aufräummigrationen folgen erst, wenn
kein Rückweg mehr auf ein älteres Image nötig ist. Nach einer inkompatiblen
Schemaänderung ist ein altes Image kein Rückweg mehr; dann gilt die Wiederherstellung
aus [Backup und Wiederherstellung](backup-recovery.md).

## Lokaler Hybrid-Lauf (StepStone/Remotely)

StepStone und Remotely liefern aus Azure keine Treffer; sie laufen einmal
täglich über einen lokalen Windows-Task gegen dieselbe Azure-Datenbank
(`scripts/run_local_hybrid.py`). Der Task startet genau das Image, das der
Azure-Worker gerade nutzt: Er fragt es über die lokale `az`-Anmeldung ab, meldet
sich an der Registry an und holt es per `docker pull`. Antwortet Docker nicht,
startet das Skript Docker Desktop und beendet es nach dem Lauf wieder; lief es
schon, bleibt es an. Die Datenbank-URL der App-Rolle liegt in
`.env.postgres-azure`. `user_settings.local.yaml` und `profile.local.yaml` gibt es als
`JOBFINDER_USER_SETTINGS` und `JOBFINDER_PROFILE` an den Container weiter; mit
der Modell-Adresse aus `.env.docker-local` schreibt der Agent danach die
Steckbriefe der neuen Stellen. Eine native Windows-Verbindung lieferte zeitweise
veraltete Lesezustände gegenüber Azure; der Container umgeht das, die Ursache
ist ungeklärt.

Die Ausgabe jedes Laufs landet in `data/logs/hybrid-<Zeitpunkt>.log` (14 Tage
aufbewahrt, ohne die geheimen Startparameter). Scheitert ein Schritt, etwa weil
Docker nicht startet oder die `az`-Anmeldung abgelaufen ist, meldet das Skript
Grund und Logdatei in Discord (`DISCORD_WEBHOOK_URL` als Benutzervariable).

Im Container gibt es weder Managed Identity noch `az`-Anmeldung. Dafür gibt es
einen eigenen Service Principal mit genau zwei Rollen: `Storage Blob Data
Contributor` nur auf dem Container `application-documents`
(`infrastructure/storage.tf`) und `Cognitive Services OpenAI User` für den
Agenten (`infrastructure/openai.tf`). Einmalig einrichten:

```powershell
az ad app create --display-name "jobfinder-local-docker"
az ad sp create --id <appId aus dem vorigen Befehl>
az ad app credential reset --id <appId> --display-name "local-docker-worker" --years 2
```

Das Geheimnis gilt zwei Jahre und wird mit dem letzten Befehl erneuert. Die
Werte kommen in `.env.docker-local` (von Git ausgeschlossen):

```text
AZURE_CLIENT_ID=<appId>
AZURE_TENANT_ID=<tenant>
AZURE_CLIENT_SECRET=<password>
JOBFINDER_REVIEW_HOST=<Hostname der Review-App, für Direktlinks in Discord>
JOBFINDER_OPENAI_ENDPOINT=<Adresse des Azure-OpenAI-Kontos, für den Agenten>
```

Danach die Objekt-ID des Service Principals (`az ad sp show --id <appId> --query id`)
als `local_docker_sp_object_id` in `infrastructure/variables.tf` eintragen und
die Rollenzuweisungen anwenden.

## Logs und Traces

Jeder Lauf schreibt JSON-Zeilen mit derselben `run_id` (`job_finder/console.py`);
im Log-Analytics-Workspace stehen sie in `ContainerAppConsoleLogs_CL`, Spalte
`Log_s`. Der Agent ergänzt je Stelle eine Zeile `agent_job`: Stellen-ID,
Ergebnis (`fertig`, `abgebrochen`, `gestoppt`), Abbruchgrund als festes Wort
(etwa `job_cost`, `incomplete`, `rejected`), Versuch und ob ein weiterer folgt,
Fazit-Stufe, Modell- und
Werkzeugaufrufe, Websuchen, Tokens, Kosten und Sekunden. Teuerste Stellen und
Abbruchgründe der letzten sieben Tage:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(7d) and Log_s startswith "{"
| extend e = parse_json(Log_s)
| where e.event == "agent_job"
| summarize Stellen = count(), Kosten = sum(todouble(e.cost_eur)), Websuchen = sum(toint(e.web_searches))
    by Ergebnis = tostring(e.outcome), Grund = tostring(e.reason), Fazit = tostring(e.verdict)
| order by Kosten desc
```

Dieselben Zahlen gehen als Traces nach Application Insights
(`appi-jobfinder`, `job_finder/telemetry.py`): ein Baum aus `agent_run`, je
Stelle `agent_job` und darunter `model_call` und `tool_call` mit Dauer. Die
Attribute der Modellaufrufe folgen den OpenTelemetry-Namen für generative KI
(`gen_ai.usage.input_tokens` usw.). Im Portal zeigt „Transaktionssuche“ den Baum
eines Laufs; im Workspace liegen die Spans in `AppDependencies`:

```kusto
AppDependencies
| where TimeGenerated > ago(7d) and Name == "model_call"
| summarize Aufrufe = count(), Median_ms = percentile(DurationMs, 50), P95_ms = percentile(DurationMs, 95),
    Ausgabe = sum(toint(Properties["gen_ai.usage.output_tokens"])) by bin(TimeGenerated, 1d)
```

Was erfasst wird, legt `span()` in `job_finder/telemetry.py` fest: nur Zahlen,
Wahrheitswerte und kurze feste Wörter; bei einem Fehler nur der Typname der
Ausnahme. Profil, Prompt, Anzeigentext, Notizen, Titel, Firma und Antworten des
Modells fehlen; ein Test in `tests/test_agent_runner.py` prüft das. Application
Insights nimmt nur Daten mit Entra-ID-Anmeldung an (die Managed Identitys von
Worker und Review), bewahrt sie 30 Tage auf und nimmt höchstens 0,1 GB am Tag an. Ohne
`APPLICATIONINSIGHTS_CONNECTION_STRING`, also lokal, in Tests und Evals, sendet
nichts.

Die Review schickt je API-Anfrage einen Span `review_request` mit Route, Status,
Dauer und `jobfinder.first_request` (erste Anfrage nach dem Start, also
Kaltstart). Darunter hängen die Schritte `db_connect` (Verbindung samt
Entra-Token), `read_recommendations`, `read_memory`, `build_cards`,
`read_fact_sheets` und `read_document`, mit Zeilen- und Kartenzahl, aber ohne
IDs oder Inhalte. Wo die Ladezeit der Stellenliste bleibt:

```kusto
AppDependencies
| where TimeGenerated > ago(7d) and Name in ("review_request", "db_connect", "read_recommendations",
    "read_memory", "build_cards", "read_fact_sheets", "read_document")
| summarize Anzahl = count(), Median_ms = percentile(DurationMs, 50), P95_ms = percentile(DurationMs, 95)
    by Name, Route = tostring(Properties["jobfinder.route"]), Kaltstart = tostring(Properties["jobfinder.first_request"])
| order by Median_ms desc
```

Jeder Finder-Lauf trägt sich außerdem in die Tabelle `runs` ein (Revision `0006_runs`):
beim Start mit Quellen und Ort (`cloud` aus dem Worker-Job, `hybrid` aus dem lokalen
Hybrid-Lauf, sonst `local`, gesetzt über `JOBFINDER_RUNNER`), am Ende mit Ergebnis
(`finished` oder `failed`) und den Kennzahlen. So erscheinen auch die Hybrid-Läufe, die
keine Logs nach Azure senden. Die Startseite der Review zeigt den letzten Lauf je Ort
(`/api/runs`).

Am Ende jedes Laufs, nach dem Agenten, steht eine Zeile `run_summary`: Dauer,
Stellen, neue Stellen gesamt und je Quelle (`new_by_source`), neue Review-Karten,
teilweise oder ganz gescheiterte Quellen, gesendete und fehlgeschlagene
Discord-Nachrichten sowie der Rückstand danach: offene Discord-Aufträge, Alter des
ältesten in Stunden, abgebrochene Steckbriefe und solche, die noch einmal
versucht werden. Mit Application Insights ist der ganze Lauf außerdem ein Trace
`finder_run` mit den Phasen (`collect_sources` mit je einem Schritt `source`,
`prefilter`, `enrich_details`, `evaluate`, `availability_checks`, `publish`,
`notifications`) und darunter `agent_run`.

Die Arbeitsmappe „Job Finder – Betrieb“ (Azure-Portal, Application Insights
`appi-jobfinder` oder Log Analytics, „Arbeitsmappen“; Terraform:
`infrastructure/workbooks/operations.json`) zeigt auf einer Seite die Laufdauer,
diese Kennzahlen, Status und Treffer je Quelle, neue Stellen je Quelle,
Agentenkosten je Tag und die Ladezeiten der Review samt Schritten, für einen
wählbaren Zeitraum. Arbeitsmappen kosten nichts; sie lesen nur die vorhandenen
Logs.

## Kosten

Listenpreise in Frankreich Mitte, ohne Steuern, laut Azure Retail Prices API am
30.09.2026:

| Posten | Art | Etwa pro Monat |
| --- | --- | --- |
| PostgreSQL Flexible Server B1ms mit 32 GiB | fix | 15,55 € (11,90 € Rechenleistung, 3,65 € Speicher) |
| Container Registry Basic | fix | 4,35 € |
| Container Apps (Finder-Job, Review) | nach Nutzung | blieb bisher im kostenlosen Monatskontingent |
| Log Analytics, Application Insights, Blob Storage, Metrik-Alarme | nach Nutzung | Cent-Beträge |
| Sprachmodell und Websuche des Agenten | nach Nutzung | wenige Cent je Steckbrief, höchstens 1 € am Tag und 20 € im Monat (Standardgrenzen) |

Der Server läuft derzeit über ein kostenloses Kontingent der Subscription;
danach kommen die 15,55 € hinzu. Das Monatsbudget von 25 € in
`infrastructure/monitoring.tf` deckt Registry und die Grenze des Agenten und
muss dann auf gut 40 € steigen, sonst meldet es jeden Monat eine
Überschreitung. Ein Budget warnt nur, es stoppt nichts; die harten Grenzen
setzt der Kostenwächter des Agenten.

## Zugriffswege

Die Container-Apps-Umgebung läuft im Consumption-Profil ohne VNet-Integration:
Worker und Review haben keine feste ausgehende IP, und alle Dienste sind über
ihren öffentlichen Endpunkt erreichbar. Der Schutz liegt deshalb bei
Anmeldung, RBAC und TLS, nicht an der Netzwerkgrenze.

| Ressource | Eigentlicher Zugriffsschutz |
| --- | --- |
| Review-Container-App | Easy Auth (Entra ID), nur das eigene Konto; öffentlich erst nach der Anmeldekonfiguration, jeder Deploy prüft den Zugriff ohne Login (`review.tf`) |
| PostgreSQL | TLS mit `sslmode=verify-full`, eingeschränkte DB-Rollen und deren Passwort oder nach gesonderter Entra-Abnahme Token; Firewall: Azure-Dienste (`0.0.0.0`, jede Subscription) und `local-review` |
| Blob Storage | RBAC, Kontoschlüssel abgeschaltet; Schreibrechte nur auf `application-documents` |
| Key Vault | RBAC: in `split` hat jede Laufzeit nur `Key Vault Secrets User` auf ihren benötigten Secrets; `Secrets Officer` nur für das eigene Konto |
| Azure OpenAI | RBAC ohne API-Schlüssel: Worker, Hybrid-Lauf und das eigene Konto mit `Cognitive Services OpenAI User` |
| Container Registry | RBAC, `admin_enabled = false` |
| Worker (Container Apps Job) | kein Ingress, nur ausgehend |

GitHub Actions melden sich per OIDC ohne gespeichertes Azure-Geheimnis an, mit
drei getrennten Identitäten (`infrastructure/cicd.tf`): Apply mit
`Contributor` und `Role Based Access Control Administrator` auf `rg-jobfinder`
plus Schreibzugriff auf den `tfstate`-Container, Build nur mit `AcrPush` und
`Reader` auf der Registry, Plan für Pull Requests nur lesend.

**Warum kein VNet und keine Private Endpoints:** Technisch ginge es, denn die
Workload-Profile-Umgebung unterstützt VNet-Integration auch im
Consumption-Profil. Der Netzwerktyp lässt sich aber nur beim Anlegen einer
Umgebung festlegen, der Umstieg wäre also ein Umzug in eine neue Umgebung. Ein
Private Endpoint (etwa 6,30 € im Monat) ließe die Container dann privat auf die
Datenbank zugreifen, und die Regel für alle Azure-Dienste könnte entfallen; der
Zugriff vom eigenen Rechner bliebe über `local-review` möglich. Ein vollständig
privater Server schlösse dagegen Hybrid-Lauf und Admin-Zugriff ohne VPN aus. Ein
NAT Gateway (etwa 31 € im Monat samt öffentlicher IP) wäre nur für eine feste
ausgehende Adresse nötig, etwa um die Firewall auf die Container zu begrenzen.
Für einen Nutzer ohne Daten Dritter und ohne Compliance-Vorgabe tragen TLS, RBAC
und das Passwort der App-Rolle die Absicherung. Kämen mehrere Nutzer oder
Bewerberdaten Dritter hinzu, wäre das der erste Punkt, der sich ändern sollte.
