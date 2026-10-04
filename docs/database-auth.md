# Datenbankanmeldung mit Microsoft Entra (F10)

## Nutzen und aktueller Umfang

Worker und Review verwenden nach F09 eigene Datenbankrollen mit festgelegten
Rechten. F10 ergänzt die Anmeldung über ihre bereits vorhandenen Azure-Identitäten:
Ein zeitlich begrenztes Entra-Token ersetzt beim Verbindungsaufbau das gespeicherte
DB-Passwort. Die Rechte auf Tabellen und Datensätze ändern sich dadurch nicht.
Für den Hybridlauf kann dessen vorhandener Service Principal ein DB-Token beziehen;
sein Azure-Client-Secret bleibt dabei zunächst erforderlich.

Der lokale erste Baustein ist die explizite Token-Anmeldung im zentralen
Verbindungsaufbau. Standard bleibt die bisherige Passwortanmeldung. Azure-Server,
PostgreSQL-Principals, Terraform-Container-Konfiguration und Hybrid-Umschaltmarker
werden damit noch nicht verändert. Ein Merge allein aktiviert keine Entra-Anmeldung.

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

## Noch vorzubereitende Cloud-Umstellung

1. Nach vollständiger F09-Betriebsabnahme den Azure-Server zusätzlich für Entra
   vorbereiten. Passwortauth bleibt zunächst verfügbar. Einen gesonderten
   Entra-Administrator für Einrichtung/Notzugang vorsehen, niemals eine Laufzeit-MI
   als DB-Administrator verwenden. Die Aktivierung startet den PostgreSQL-Server
   neu; das Betriebsfenster muss vor Anwendung bekannt sein.
2. Eigene Entra-Principals anlegen und anhand der unveränderlichen Object-IDs der
   Worker-/Review-MI und des Hybrid-Service-Principals zuordnen. Vorgesehene neue
   Rollennamen `jobfinder_worker_entra`, `jobfinder_review_entra`,
   `jobfinder_hybrid_entra`; keine bestehenden Passwortrollen überschreiben.
   Die `pgaadauth`-Einrichtungsfunktionen müssen in der Datenbank `postgres`
   ausgeführt werden. Nicht-Admin-Rollen mit den bestehenden F09-NOLOGIN-Gruppen
   verbinden und ihre effektiven Rechte in `jobfinder` prüfen. Die Review bleibt
   an dieselbe Datensatz-Policy gebunden.
3. Image-Pull, Token-Anmeldung, neue Verbindung nach Tokenablauf, alle drei
   Laufzeiten, negative Rechte und den administrativen Rückkehrweg separat in
   Azure abnehmen. Lokale Attrappen bestätigen keine Azure-Token-Anmeldung.
4. Erst danach die passwortfreien DSNs und expliziten Modi freigegeben aktivieren.
   Die nicht mehr benötigten Worker-/Review-DB-Secret-Verweise und deren Vault-Rollen
   in einem geprüften Plan entfernen. Lokale Hybrid-Konfiguration gesondert
   aktivieren; ihren bisherigen Zugang als Rückkehrweg erhalten. Alte DB-Secrets
   erst nach erfolgreicher Betriebsabnahme aufräumen.

Eine spätere Abschaltung der serverweiten Passwortauthentifizierung betrifft
auch den administrativen Notzugang und benötigt eine eigene Entscheidung.
Entra verändert die vorhandene Netzwerk-Firewall nicht.

## Quellen

- [Microsoft: Anmeldung mit Managed Identity](https://learn.microsoft.com/en-us/azure/postgresql/security/security-connect-with-managed-identity)
- [Microsoft: Entra-Principals verwalten](https://learn.microsoft.com/en-us/azure/postgresql/security/security-manage-entra-users)
- [Microsoft: Authentifizierungsmodi, Neustart und Tokenlebensdauer](https://learn.microsoft.com/en-us/azure/postgresql/security/security-entra-concepts)
