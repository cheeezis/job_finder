# Getrennte Laufzeitrechte (F09)

F09 wird in zwei Etappen eingeführt. Standard bleibt `legacy`; ein Merge allein
schaltet keine Identität und keinen Datenbankzugang um. `prepare` ergänzt Zugänge,
die vorhandenen Anwendungen laufen weiter mit ihren bisherigen Zugängen. Erst
nach dokumentierter Abnahme folgt `split`. Während `prepare` besitzt die Review
noch die bisherigen breiten Rechte; die Trennung ist dann noch nicht abgeschlossen.

## Rechte nach der Umschaltung

| Bereich | Cloud-Worker | Review | Lokaler Hybrid-Worker |
| --- | --- | --- | --- |
| Identität | bisherige Worker-MI | eigene Review-MI | bisheriger eigener Service Principal |
| ACR | AcrPull | AcrPull | Pull über Host-Anmeldung |
| Key Vault | fünf einzelne Worker-Secrets | drei einzelne Review-Secrets | keine Vault-Rolle; lokale ignorierte Konfiguration |
| Modell | bestehende Aufrufrolle | keine Modellrolle | bestehende optionale Aufrufrolle |
| Blob-Dokumente | kein Datenzugriff | Contributor nur auf Dokumentcontainer | kein Datenzugriff |
| Terraform-State | kein Zugriff | kein Zugriff | kein Zugriff |
| DB-Login | `jobfinder_worker` | `jobfinder_review` | `jobfinder_hybrid` |
| DB-Gruppe | `jobfinder_worker_access` | `jobfinder_review_access` | `jobfinder_worker_access` |

Worker-Secrets: `DiscordWebhookUrl`, `StartupJobsApiKey`,
`JobfinderWorkerDatabaseUrl`, `JobfinderUserSettings`, `JobfinderProfile`.
Review-Secrets: `JobfinderReviewDatabaseUrl`, `ReviewAadClientSecret`,
`JobfinderUserSettings`. Insbesondere erhalten Worker und Review nach `split`
keinen Zugriff auf die alte gemeinsame `JobfinderDatabaseUrl`; die Review erhält
weder Discord-Webhook noch Profil oder Quellen-API-Key. Die Datenbankwerte werden
außerhalb Terraform vorbereitet, damit keine Passwörter im State landen.
Secret-Rollen liegen auf einzelnen Secret-Ressourcen des bestehenden Vaults.
[Microsoft: Key-Vault-RBAC](https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide)

Beide geplanten Worker setzen `JOBFINDER_SKIP_RUN_BACKUP=1`. Sie schreiben
Dokumentmetadaten mit dem bestehenden Memory-Adapter, greifen aber nicht auf
Dokument-Bytes zu. Deshalb entfallen ihre bisherigen Blob-Rollen. Manuelle
Dokument-Backups und Restore laufen separat als Administrator/Owner. Wird das
Laufzeit-Backup später wieder aktiviert, muss der tatsächliche Dokumentzugriff
vorher neu bewertet werden. Entwickler- und CI-Berechtigungen bleiben getrennt.

## PostgreSQL-Vertrag

| Tabellen | Worker/Hybrid | Review |
| --- | --- | --- |
| `job_state`, `workflow_history`, `application_documents` | SELECT/INSERT/UPDATE/DELETE | SELECT/INSERT/UPDATE/DELETE |
| `datasets`, `jobs`, `recommendations`, `manual_sources` | SELECT/INSERT/UPDATE/DELETE | SELECT/INSERT/UPDATE/DELETE; Datensatzgrenze siehe unten |
| `agent_usage`, `agent_fact_sheets` | SELECT/INSERT/UPDATE/DELETE | nur SELECT für Kostenübersicht und vorhandene Steckbriefe |
| `notifications`, `source_cache` | SELECT/INSERT/UPDATE/DELETE | keine Rechte |
| Versionsmarker, Importjournal und neue Tabellen | keine Rechte | keine Rechte |
| DDL, TRUNCATE, Rollenverwaltung, Objektbesitz | keine Rechte | keine Rechte |

Die Listen sind ausdrücklich festgelegt. Neue Tabellen erhalten keine impliziten
Laufzeitrechte. Die Vorbereitung verweigert privilegierte bestehende Rollen,
Objektbesitz, unerwartete Mitgliedschaften und wirksame zusätzliche Rechte durch
`PUBLIC`. Sie repariert keine fremden Rollen oder öffentlichen Rechte automatisch.
Passwörter vorhandener Rollen werden nicht automatisch ersetzt.

Revision `0002_runtime_boundaries` aktiviert Row Level Security auf `datasets`.
Mitglieder der Review-Gruppe dürfen ausschließlich `internal/jobs.json`,
`output/recommendations.json` und `internal/manual_jobs_cache.json` sehen/ändern.
So können sie keinen Worker-Datensatz löschen und darüber dessen `notifications`
oder `source_cache` per Fremdschlüssel-Kaskade entfernen. Die Mitgliedschaft wird
auch bei `SET ROLE` auf die Capability-Gruppe erkannt. Tabellenbesitzer und
BYPASSRLS-Principals dürfen deshalb niemals Laufzeitrollen sein.
[PostgreSQL: Row Security](https://www.postgresql.org/docs/18/ddl-rowsecurity.html)

Die Migration ändert keine Nutzdaten, legt keine clusterweiten Rollen an und
erhält den bisherigen gemeinsamen Zugang. `schema-status` prüft die neue Policy
technisch lesend; eine entfernte oder veränderte Policy wird nicht automatisch
repariert. Die ursprüngliche Baseline bleibt unverändert. Ein Image-Rollback
entfernt die Policy nicht; deren Downgrade wird verweigert. Administratoren
verwenden nach der Migration die aktuelle Wartungsversion für Migration/Restore.

Die bestehenden Adapter teilen weiterhin Bestands- und Bewerbungszeilen. F09
trennt Komponentenrechte, aber noch nicht einzelne Spalten oder fachliche
Repository-Verträge. Die weitere Aufteilung folgt in F17. Die NOLOGIN-Gruppen
können in F10 auch an getrennte Entra-DB-Principals vergeben werden.

## Etappe 1: Vorbereiten und prüfen

1. Sicherung und administratives `schema-status` vor der freigegebenen
   Produktionsmigration. `job_finder.db migrate` übernimmt `0002_runtime_boundaries`
   in einer Transaktion; Worker und Review führen keine Migrationen aus.
2. `python scripts/create_runtime_roles.py --target azure` prüft zunächst nur,
   welche Rollen existieren. Erst `--apply` erstellt drei Logins, die beiden
   Gruppen und die festen Rechte. Das setzt die intakte Migration voraus.
   Lokal entsprechend `--target local`; lokal prüfen heißt keine Produktion ändern.
3. Der Helfer legt `.env.runtime-azure` beziehungsweise `.env.runtime-local` an.
   Die Dateien sind ignoriert und enthalten Geheimnisse; Zugriffsrechte wie bei
   den vorhandenen lokalen `.env`-Dateien begrenzen. Ein vorbereitetes File bleibt
   auf `JOBFINDER_RUNTIME_ACCESS=legacy`. Der Ready-Marker wird erst nach erfolgreichen
   eigenen Verbindungen und Prüfung der effektiven Rechte auf `1` gesetzt.
   Ein abgebrochener Lauf mit Marker `0` darf keine Umschaltung auslösen.
4. Die zwei Cloud-DSNs aus der Datei als neue Worker-/Review-Secrets im Vault
   hinterlegen, ohne Werte im Terminal, in Shell-Argumenten oder im Terraform-State
   auszugeben. Der Helfer schreibt weder Key Vault noch bestehende Runtime-Konfiguration.
5. Terraform mit `runtime_identity_phase=prepare` planen und freigeben.
   CI verwendet dafür die Repository-Variable `RUNTIME_IDENTITY_PHASE`;
   `RUNTIME_ACCESS_VERIFIED` bleibt `false`. Die neue Review-MI wird zusätzlich
   angehängt; Registry, aktive Secret-Verweise und Client-ID bleiben auf dem
   bisherigen Zugang. Die neuen Rollen können vor der Umschaltung propagieren.
6. Mit separat freigegebenen, kurzlebigen Probestarts und den neuen Identitäten
   Image-Pull, Auflösung der eigenen Secret-Verweise und DB-Verbindung bestätigen.
   Keine Finder-Vollausführung, Discord-Nachricht oder Modellgeneration zur Probe.
   Review: Laden, Notiz/Entscheidung, manueller Import und Dokumentzugriff mit
   synthetischen Testdaten; Worker: DB-/Quellkonfiguration ohne Modell-/Webhook-Aufruf.
   Dokumentproben anschließend entfernen. Ergebnisse privat dokumentieren.
7. Erlaubte und verbotene Cloud-Zugriffe getrennt prüfen: Review erhält kein
   Modellrecht, keinen Webhook-/Profil-/Worker-DSN-Zugriff und keinen State-Zugriff;
   Worker hat keine Dokumentrechte nach Entfernen des Übergangsrechts. Bestehende
   Rollenzuweisungen einschließlich übergeordneter Scopes und Mitgliedschaften
   prüfen. Secret-Metadaten und RBAC-/Modell-Konfiguration genügen für die
   Negativprüfung, ohne fremde Secret-Werte zu lesen oder Modellaufrufe abzurechnen.
   Die funktionale Auflösung eigener Secrets wird beim Probestart geprüft.

Lokale DB-Tests und simulierte Terraform-Pläne belegen den vorbereiteten Vertrag.
Sie belegen weder Azure-Rollenpropagation noch einen echten Image-Pull. Darum ist
`runtime_access_verified=true` eine separat dokumentierte menschliche Abnahme,
kein Ergebnis von `terraform validate` oder `depends_on`.

## Etappe 2: Umschalten und abnehmen

1. Erst nach Etappe 1 `runtime_identity_phase=split` und
   `runtime_access_verified=true` freigeben. In CI entsprechen dem die beiden
   Repository-Variablen. Terraform verweigert einen Split ohne Abnahmemarker.
2. Plan muss die Review vollständig von der Worker-MI trennen, alle drei
   Review-Secret-Verweise und ihre Client-ID auf die neue MI umstellen und beide
   Cloud-DSNs wechseln. Die bisherigen breiten Vault-/Worker-/Hybrid-Blob-Rollen
   werden entfernt; Review bleibt auf ihren Dokumentcontainer beschränkt.
   Easy Auth, Eigentümerbeschränkung, Ingress-Reihenfolge und Löschschutz bleiben
   bestehen. Bei unerwarteten Ersatz-/Löschaktionen keinen Apply ausführen.
3. Vor dem Entzug der Blob-Rollen die bestehende Storage-Löschsperre prüfen:
   sie kann auch untergeordnete Verwaltungsaktionen blockieren. Falls nötig,
   die exakt betroffenen Rollenzuweisungen in einem separat freigegebenen
   Owner-Wartungsfenster entziehen und die Sperre sofort wiederherstellen;
   anschließend State/Plan abgleichen. Kein automatisches Entfernen der Sperre.
   [Microsoft: Resource Locks](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/lock-resources)
4. Lokal erst nach DB-Abnahme `JOBFINDER_RUNTIME_ACCESS=split` im ignorierten
   Runtime-File setzen. Der Hybrid-Runner nutzt dann den eigenen Hybrid-DSN;
   ein fehlender Ready-Marker führt zum Abbruch. Die TLS-Zertifikatsprüfung wird
   auch im Container erhalten. Die alte `.env.postgres-azure` bleibt als Rückkehrweg.
5. Nach Umschaltung Review-Start/Anmeldung/Dokumentzugriff, ersten normalen
   Worker-/Hybrid-Lauf und erneut die negativen Rechte prüfen. Erst danach F09
   als produktiv abgeschlossen markieren. Nachträgliche Wartung eines bereits
   aktivierten Runtime-Files wird vom Vorbereitungsskript verweigert.

## Rückkehr

Bei Fehlern zuerst die betroffene Komponente anhalten. Der alte gemeinsame
DB-Login und dessen Secret bleiben erhalten. `prepare` kann die Übergangsrechte
wiederherstellen; erst nach deren Propagation und Prüfung zurück auf alte
Client-ID, Registry-Identität und DSNs wechseln. Die lokale Aktivierung kann
kontrolliert auf `legacy` zurückgestellt werden. Das stellt vorübergehend auch
die alten breiteren Rechte wieder her und ist als Rückkehr gesondert abzunehmen.
Keine Datenbank-Downgrades, keine Passwortrotation und keine Entfernung der neuen
Rollen sind dafür nötig. Probestarts und Produktionsumschaltung gehören zu einer
separaten Freigabe nach dem lokalen PR, nicht zur lokalen Vorbereitung.
