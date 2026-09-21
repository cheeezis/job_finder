# Phase 7: PostgreSQL in Azure

Dieser Schritt ergänzt die Terraform-Infrastruktur um den Datenbankserver.
Die laufende Anwendung bleibt zunächst mit der lokalen Docker-Datenbank verbunden.
Cloud-Datenübernahme, Anwendungsbenutzer und Worker-/Review-Anbindung folgen separat.

Stand 19. September 2026: Die vier Ressourcen wurden erfolgreich angelegt.
Die Verbindung meldet PostgreSQL 18.6 und TLS 1.3 mit `sslmode=verify-full`.
Der abschließende Terraform-Plan meldet **No changes**. Punkt 4 ist damit
abgeschlossen; Punkt 5 ist für die Datenbank umgesetzt, die Dateiablage folgt.

## Was Terraform anlegt

Die Datei `infrastructure/postgresql.tf` enthält vier Ressourcen:

| Ressource | Zweck |
| --- | --- |
| Flexible Server | PostgreSQL 18 als von Azure verwalteter Dienst in France Central |
| Datenbank `jobfinder` | Leere Anwendungsdatenbank innerhalb dieses Servers |
| Firewallregel `local-review` | Zugriff ausschließlich von der eingetragenen öffentlichen IPv4 |
| Einstellung `require_secure_transport` | TLS für Datenbankverbindungen erzwingen |

Der Servername wird reproduzierbar aus `psql-jobfinder-` und einem kurzen Hash
der Subscription-ID gebildet. Die bestehende Ressourcengruppe wird verwendet.
Die kleine Größe `B_Standard_B1ms` hat eine vCPU und 2 GiB RAM. Speicher:
32 GiB, Leistungsstufe P4, keine automatische Vergrößerung. Azure bewahrt
Serverbackups sieben Tage auf; Hochverfügbarkeit und georedundante Backups sind
für diesen Einstieg nicht eingeschaltet.
Azure hat Verfügbarkeitszone 3 gewählt. `ignore_changes = [zone]` übernimmt
diese automatische Wahl und verhindert eine unnötige Folgeänderung.

## Lokale Eingaben und Secrets

`infrastructure/postgres.auto.tfvars.json` enthält das generierte Admin-Passwort
und `postgres_client_ipv4`. Terraform liest die Datei automatisch ein.
Sie ist von Git ausgeschlossen; das gesamte Verzeichnis `infrastructure` ist
vom Docker-Build ausgeschlossen. Diese Datei sicher aufbewahren und nicht posten.

Das Passwort wird durch `ephemeral = true` und `administrator_password_wo`
nicht im Terraform-State oder gespeicherten Plan abgelegt. Deshalb muss es auch
beim Anwenden eines gespeicherten Plans wieder verfügbar sein. Terraform ab 1.11
und AzureRM ab 4.81 sind in der Konfiguration vorausgesetzt.
`administrator_password_wo_version` wird bei einer beabsichtigten Passwortrotation
erhöht; nicht bei jedem normalen Plan.

`jobfinder_admin` ist für Einrichtung und Verwaltung vorgesehen. Die Anwendung
soll vor der Cloud-Umstellung einen eigenen Datenbankbenutzer erhalten.

## Plan und Apply

Im Repository-Stamm:

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

Der erste geprüfte Plan vom 19. September 2026 enthält vier Ergänzungen,
keine Änderungen und keine Löschungen. Spätere Pläne immer erneut prüfen.

## Zugriff prüfen

```powershell
.\.venv\Scripts\python.exe scripts/check_azure_postgres.py
```

Die Prüfung liest die Terraform-Outputs und das lokale Admin-Secret, verbindet
sich ausschließlich lesend und prüft Zertifikat und Hostname mit
`sslmode=verify-full`. Dafür exportiert sie die vertrauenswürdigen CA-Zertifikate
dieses Rechners nach `tmp/azure-postgres-trusted-roots.pem`. Die Datei enthält
öffentliche CA-Zertifikate und wird bei der Prüfung neu erzeugt. Lokale
Anwendungskonfiguration und Datenbestände werden dadurch nicht geändert.

Die Firewall-IP muss bei einem Wechsel der Internetverbindung aktualisiert
werden: `postgres_client_ipv4` in der lokalen Eingabedatei ändern, dann neuen
Plan prüfen und anwenden. Eine IP-Freigabe ersetzt weder Passwort noch TLS.
Die Worker-Freigabe ist noch nicht eingerichtet. Seine derzeitigen ausgehenden
IPs sind zahlreich und nicht dauerhaft garantiert; die passende Anbindung wird
vor dem Cloud-Betrieb behandelt. Es gibt keine pauschale Freigabe für alle
Azure-Dienste oder das gesamte Internet.

## Laufende Kosten und Pausen

Abfrage der öffentlichen Azure-Retail-API am 19. September 2026 für France Central:
B1MS-Rechenleistung 0,0163 EUR/Stunde und Standardspeicher 0,1142 EUR/GB/Monat.
Bei 730 Stunden und 32 GB ergibt das etwa **15,55 EUR/Monat** für diese beiden
Positionen, vor möglichen Steuern und zusätzlichen Kosten. Studentenangebote
oder Guthaben sind nicht als Preisnachlass vorausgesetzt. Andere Projektressourcen
wie Registry und Logs kommen separat hinzu.

Der Server läuft auch außerhalb von Finder-Läufen. Zum Pausieren:

```powershell
$jobfinderPostgresServer = terraform -chdir=infrastructure output -raw postgres_server_name
az postgres flexible-server stop --resource-group rg-jobfinder --name $jobfinderPostgresServer
# Für die Weiterarbeit:
az postgres flexible-server start --resource-group rg-jobfinder --name $jobfinderPostgresServer
```

Ein gestoppter Server wird nach sieben Tagen automatisch wieder gestartet.
Speicher bleibt auch während des Stopps kostenpflichtig. Stoppen ist daher keine
dauerhafte Kostenabschaltung. Ein späteres Löschen benötigt vorher eine geprüfte
Datensicherung; `terraform destroy` im Ordner betrifft die gesamte dort verwaltete
Infrastruktur, nicht nur PostgreSQL.

Quellen: [Azure-Retail-API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices),
[Azure PostgreSQL Preise](https://azure.microsoft.com/en-us/pricing/details/postgresql/flexible-server/),
[Server stoppen](https://learn.microsoft.com/en-us/azure/postgresql/configure-maintain/how-to-stop-server),
[PostgreSQL Zertifikatsprüfung](https://www.postgresql.org/docs/current/libpq-ssl.html).
