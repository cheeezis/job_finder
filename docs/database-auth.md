# Datenbankanmeldung mit Microsoft Entra (F10)

## Nutzen und aktueller Umfang

Worker und Review verwenden nach F09 eigene Datenbankrollen mit festgelegten
Rechten. F10 ergänzt die Anmeldung über ihre bereits vorhandenen Azure-Identitäten:
Ein zeitlich begrenztes Entra-Token ersetzt beim Verbindungsaufbau das gespeicherte
DB-Passwort. Die Rechte auf Tabellen und Datensätze ändern sich dadurch nicht.
Für den Hybridlauf kann dessen vorhandener Service Principal ein DB-Token beziehen;
sein Azure-Client-Secret bleibt dabei zunächst erforderlich.

Die Implementierung umfasst Token-Anmeldung, einen separaten Einrichtungshelfer,
Terraform-Phasen und eine gesonderte Hybrid-Umschaltung. Standard bleibt die
bisherige Passwortanmeldung. Ein Merge allein aktiviert keine Entra-Anmeldung:
Servervorbereitung und Laufzeitumschaltung müssen ausdrücklich freigegeben werden.

## Verbindungskonfiguration

`JOBFINDER_DATABASE_AUTH` bestimmt ausschließlich die Laufzeitanmeldung:

| Wert | Anmeldung | Identität |
| --- | --- | --- |
| `password` (Standard) | bisheriger DSN | bestehende lokale oder Azure-DB-Rolle |
| `managed_identity` | PostgreSQL-Token | explizite `JOBFINDER_MANAGED_IDENTITY_CLIENT_ID` |
| `service_principal` | PostgreSQL-Token | explizite `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` |

Für Entra muss `JOBFINDER_DATABASE_URL` einen DSN ohne Passwort, mit explizitem
Host, Datenbank, Rolle und `sslmode=verify-full` enthalten. Das konfigurierte
CA-Bundle bleibt erhalten. Beispiel mit erfundenen Angaben:

```text
JOBFINDER_DATABASE_AUTH=managed_identity
JOBFINDER_MANAGED_IDENTITY_CLIENT_ID=<Client-ID der eigenen Laufzeitidentität>
JOBFINDER_DATABASE_URL=postgresql://jobfinder_worker_entra@example.postgres.database.azure.com:5432/jobfinder?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt
```

Das Token wird nur im Speicher als Treiber-Passwort übergeben. Kein Token in
DSN, Runtime-Datei, Terraform-State, Kommandozeile oder Log hinterlegen. Für jede
neue Verbindung wird `get_token` aufgerufen; das Azure-SDK verwaltet gültige Tokens
und deren Erneuerung. Bestehende/nestende Transaktionen und Sitzungssperren behalten
ihre Verbindung. Es gibt derzeit keinen Verbindungspool; ein späterer Pool muss
denselben Token-Aufruf beim Erzeugen neuer Verbindungen verwenden.

Die Credential-Auswahl verwendet ausdrücklich ManagedIdentityCredential oder
ClientSecretCredential, ohne Ausweichen auf die Azure-CLI-Anmeldung des Entwicklers.
Fehlende Identität, unbekannter Modus, Passwort-DSN, fehlende TLS-Prüfung oder
Tokenfehler führen zum Abbruch. Keine Passwort-Ausweichanmeldung und keine
Wiederholung möglicherweise bereits ausgeführter Schreibtransaktionen.
Entra-Treiber-/Tokenfehler werden ohne sensible Ausnahmeinhalte weitergegeben.

Administrative Wartung bleibt getrennt: `transaction(admin=True)` und Alembic
nutzen ausschließlich `JOBFINDER_ADMIN_DATABASE_URL`. Ein Laufzeitmodus macht
die Anwendung nicht zum Entra-Administrator. Lokale Compose-Tests verwenden
weiterhin Testpasswörter.

## Terraform-Phasen

| `database_auth_phase` | Server und Administrator | Laufzeiten |
| --- | --- | --- |
| `password` (Standard) | bestehende Konfiguration | bisherige getrennte Passwortrollen und Vault-Verweise |
| `prepare` | Entra zusätzlich aktiv; eigener persönlicher Entra-Admin | unverändert, einschließlich DB-Secret-Zugriff |
| `entra` | Entra und administrativer Passwortzugang bleiben verfügbar | eigene passwortfreie DSNs, explizite MI; nur Worker-/Review-DB-Secret-Verweise und zugehörige Vault-Rollen entfallen |

`prepare` und `entra` verlangen `runtime_identity_phase=split` sowie
`runtime_access_verified=true`. `postgres_entra_admin_name` muss den Anmeldenamen
des durch `owner_object_id` bestimmten persönlichen Entra-Administrators enthalten.
Eine Worker-/Review-MI oder der Hybrid-Service-Principal darf nicht Administrator sein.
`entra` verlangt zusätzlich `database_entra_verified=true` nach der tatsächlichen
Azure-Abnahme. Die GitHub-Pipeline übergibt entsprechend `DATABASE_AUTH_PHASE`
(Standard `password`), `DATABASE_ENTRA_VERIFIED` (Standard `false`) und
`POSTGRES_ENTRA_ADMIN_NAME`. Lokale Terraform-Werte und GitHub-Werte müssen vor
einem Apply zusammenpassen. Das Image mit Token-Unterstützung zuerst deployen.

Entra-Aktivierung startet den PostgreSQL-Server neu. Ein passendes Betriebsfenster
ist vor dem ersten `prepare`-Apply erforderlich. Server, Datenbank und Administrator
bleiben durch `prevent_destroy` geschützt. Rückkehr nach einer Aktivierung immer
zu `prepare`, nicht zu `password`: damit wird Entra weder abgebaut noch der Admin
gelöscht. Server-Passwortauth bleibt in allen Phasen an.

Die Outputs `entra_principals` und `entra_database_urls` enthalten die geplanten
Object-IDs beziehungsweise passwortfreie DSNs, keine Tokens oder Passwörter.
Sie werden im getrennten F09-Modus auch vor `prepare` bereitgestellt; ihre Existenz
bestätigt keine eingerichteten SQL-Principals oder Azure-Anmeldung.

## Entra-Principals einrichten

`scripts/create_entra_roles.py` verwendet zwei getrennte administrative Zugänge
zum selben Server:

- `JOBFINDER_ENTRA_ADMIN_DATABASE_URL` aus `.env.entra-azure` oder der Umgebung:
  passwortfreier DSN des persönlichen Entra-Admins zur Datenbank `postgres`, mit
  `verify-full` und passendem lokalem CA-Bundle. Nur dieser Wartungshelfer bezieht
  das Token aus einer expliziten `AzureCliCredential` im vorgegebenen Tenant.
- `JOBFINDER_ADMIN_DATABASE_URL` aus `.env.postgres-azure` oder der Umgebung:
  bestehender Passwort-Admin zur Anwendungsdatenbank. Er ordnet die von ihm
  verwalteten F09-Zugriffsgruppen zu. So muss der Entra-Admin weder diese Gruppen
  übernehmen noch zusätzliche Admin-Rechte auf sie erhalten.

Die lokal ignorierten Dateien bleiben getrennt von der aktiven Runtime-Datei.
Token, Passwort oder DSN nie als Kommandozeilenargument übergeben. Nach dem
freigegebenen `prepare`-Apply den nicht geheimen Principal-Output exportieren:

```powershell
terraform -chdir=infrastructure output -json entra_principals > tmp/entra-principals.json
uv run python scripts/create_entra_roles.py --principals-file tmp/entra-principals.json
# Erst nach Prüfung und gesonderter Produktionsfreigabe:
uv run python scripts/create_entra_roles.py --principals-file tmp/entra-principals.json --apply
```

Standard ist eine technisch schreibgeschützte Prüfung. `--apply` legt nur die
drei neuen Nicht-Admin-Logins `jobfinder_worker_entra`, `jobfinder_review_entra`,
`jobfinder_hybrid_entra` über `pgaadauth_create_principal_with_oid` an und ordnet sie
den bestehenden F09-NOLOGIN-Gruppen zu. Die Zuordnung verwendet die Object-IDs der
MIs und des Hybrid-Service-Principals; Client-IDs oder Anzeigenamen sind hierfür
ungeeignet. Der Helfer liest Tenant, Object-ID, Principal-Typ und Admin-Markierung
zurück und prüft die Rollenattribute, Mitgliedschaften, vollständige effektive
Tabellenrechte und die bestehende Review-Datensatz-Policy. Bestehende Passwortrollen,
Passwörter, Tabellenrechte, Schema und aktive Konfiguration werden nicht verändert.

Fehlende F09-Grenzen, PUBLIC-Tabellenrechte, fremde Mitgliedschaften, eine bereits
anderweitig gebundene Object-ID oder eine gleichnamige Rolle ohne richtige
Entra-Zuordnung führen zum Abbruch. Der Helfer repariert solche Zustände nicht
automatisch und setzt keinen Abnahmemarker.

Die Identitäten werden in `postgres`, die Mitgliedschaften in der Anwendungsdatenbank
eingerichtet. Diese zwei Transaktionen können nicht gemeinsam atomar committen.
Scheitert die zweite Phase, können neue Logins ohne Zugriffsgruppen verbleiben;
die aktiven Zugänge bleiben erhalten. Erneutes Prüfen und Ausführen setzt sicher
fort, ohne Passwörter zu rotieren. Vor der Umschaltung alle effektiven Rechte mit
den tatsächlichen Token-Verbindungen separat abnehmen.

## Hybrid separat umschalten

Der Runner liest zusätzliche Marker aus der vorhandenen, ignorierten
`.env.runtime-azure`. Zum Vorbereiten ergänzen, die bisherigen Werte behalten:

```text
JOBFINDER_HYBRID_DATABASE_AUTH=password
JOBFINDER_HYBRID_ENTRA_VERIFIED=0
JOBFINDER_HYBRID_ENTRA_DATABASE_URL=postgresql://jobfinder_hybrid_entra@example.postgres.database.azure.com:5432/jobfinder?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt
```

Erst nach Azure-Abnahme `JOBFINDER_HYBRID_ENTRA_VERIFIED=1` und
`JOBFINDER_HYBRID_DATABASE_AUTH=entra` setzen. Der Runner verlangt zusätzlich den
bestehenden geprüften F09-Modus (`JOBFINDER_RUNTIME_ACCESS=split`,
`JOBFINDER_RUNTIME_CREDENTIALS_READY=1`). Er prüft Rolle, Host, Port und Datenbank
gegen den bisherigen Hybridzugang, verweigert statische Passwörter und verlangt
`verify-full`. Im Container verwendet er das Linux-CA-Bundle und übergibt
`JOBFINDER_DATABASE_AUTH=service_principal`. Die vorhandenen expliziten SP-Angaben
aus `.env.docker-local` bleiben erforderlich; sein Client-Secret entfällt durch
diese DB-Umstellung nicht. Rückkehr: nur `JOBFINDER_HYBRID_DATABASE_AUTH=password`
setzen. Fehlender Marker entspricht ebenfalls dem bisherigen Passwortmodus.

## Cloud-Abnahme und Aktivierung

1. Nach vollständiger F09-Betriebsabnahme Backup/Rückkehrweg prüfen, den konkreten
   `prepare`-Plan auf Änderungen und Ressourcenersetzungen prüfen und das
   Neustartfenster freigeben. Erst danach Server und eigenen Entra-Admin vorbereiten.
2. Principal-Output und Einrichtungszugänge prüfen; Rollen mit dem Helfer nach
   gesonderter Freigabe anlegen. Alte Zugänge zunächst aktiv halten.
3. Mit den tatsächlichen eigenen Identitäten von Worker, Review und Hybrid
   Image-Pull, Token-Anmeldung und neue Verbindung nach Tokenablauf abnehmen.
   Rechte inklusive negativer Schreib-/DDL-/Datensatzprüfungen bestätigen.
   Für Proben keine komplette Finder-Ausführung, Evals, Modellgeneration oder
   Discord-Sends starten; Schreiben in einer abschließend zurückgerollten
   Transaktion prüfen. Administrativen Passwort-Notzugang separat bestätigen.
4. Erst dann `database_entra_verified=true`, den konkreten `entra`-Plan und die
   Hybrid-Umschaltung freigeben. Persönlichen Review-Zugriff samt Dokumenten sowie
   die folgenden regulären Cloud-/Hybrid-Läufe abnehmen. Alte DB-Secrets und Rollen
   als Rückkehrweg erhalten; ihre Löschung erst später gesondert entscheiden.

Eine spätere Abschaltung der serverweiten Passwortauthentifizierung betrifft
auch den administrativen Notzugang und benötigt eine eigene Entscheidung.
Entra verändert die vorhandene Netzwerk-Firewall nicht.

## Lokale Prüfungen und ihre Grenze

Die Auth-Tests simulieren Credential-Auswahl, Tokenwechsel und Fehlerfälle ohne
Azure-Anmeldung. Die Entra-Rechtetests simulieren nur die Azure-spezifische
Principal-API; echte lokale PostgreSQL-Rollen prüfen F09-Gruppen, Review-RLS,
Tabellen-/DDL-Grenzen, Wiederholung und Fehler zwischen den Einrichtungsphasen.
Hybridtests verwenden erfundene Konfiguration und ersetzen Docker/CLI/Benachrichtigungen.
Terraform-Tests simulieren alle Provider und prüfen Standard, Vorbereitung,
Aktivierung und fehlende Freigaben. Diese Prüfungen ersetzen keine Azure-Abnahme.

## Quellen

- [Microsoft: Anmeldung mit Managed Identity](https://learn.microsoft.com/en-us/azure/postgresql/security/security-connect-with-managed-identity)
- [Microsoft: Entra-Principals verwalten](https://learn.microsoft.com/en-us/azure/postgresql/security/security-manage-entra-users)
- [Microsoft: Authentifizierungsmodi, Neustart und Tokenlebensdauer](https://learn.microsoft.com/en-us/azure/postgresql/security/security-entra-concepts)
