# Netzwerkpfade

Übersicht, wie die einzelnen Komponenten sich erreichen, und worüber der
jeweilige Zugriff tatsächlich abgesichert ist. Phase 11 der Roadmap.

## Aktuelle Topologie

Die `azurerm_container_app_environment` (`cae-jobfinder`) läuft im
Consumption-Profil **ohne VNet-Integration**. Das heißt: Worker und Review
haben keine feste, vorhersagbare ausgehende IP-Adresse - sie teilen sich
einen von Microsoft verwalteten, dynamischen IP-Pool. Postgres, Storage und
Key Vault sind alle über ihren **öffentlichen Endpunkt** erreichbar; keiner
davon liegt in einem eigenen virtuellen Netzwerk.

## Pfade

**Browser → Review-App**
HTTPS zur öffentlichen Ingress-Adresse der Review-Container-App. Easy Auth
(Entra ID) fängt jede nicht angemeldete Anfrage ab und leitet zum Login um;
nur `var.owner_object_id` (Jannis) ist als Principal zugelassen
(`review.tf`).

**Review-App / Worker → PostgreSQL**
Öffentlicher Endpunkt des Flexible Servers, Firewallregel
`allow-azure-services` (Azure-Sonderwert `0.0.0.0`, erlaubt jeden
Azure-Dienst in jeder Subscription - nicht nur diese). Der eigentliche
Zugriffsschutz liegt nicht auf Netzwerkebene, sondern bei TLS
(`sslmode=verify-full`) und dem Passwort der eingeschränkten Rolle
`jobfinder_app`. Worker und Review lesen es per Managed Identity aus dem Key
Vault; für den lokalen Hybrid-Lauf liegt es zusätzlich in der von Git
ausgeschlossenen `.env.postgres-azure`.

**Review-App / Worker → Blob Storage (Dokumente)**
Öffentlicher Endpunkt, keine IP-Einschränkung. Zugriffsschutz ausschließlich
über Azure-AD-RBAC via Managed Identity (`Storage Blob Data Contributor` nur
auf dem Container `application-documents`); Kontoschlüssel sind abgeschaltet.

**Review-App / Worker → Key Vault**
Öffentlicher Endpunkt, keine IP-Einschränkung. Zugriffsschutz über RBAC:
`Key Vault Secrets User` für die gemeinsame Managed Identity von Worker und
Review, `Key Vault Secrets Officer` nur für Jannis selbst.

**Lokaler Rechner (Jannis) → PostgreSQL**
Für `scripts/check_azure_postgres.py`, die lokale Review gegen die
Cloud-Datenbank oder Terraform-Admin-Zugriffe. Firewallregel `local-review`,
exakt auf `var.postgres_client_ipv4` beschränkt. Muss bei IP-Wechsel
manuell nachgezogen werden (siehe `docs/postgresql.md`).

**Lokaler Hybrid-Lauf (Docker, StepStone/Remotely) → PostgreSQL + Storage**
Läuft auf demselben Rechner, dieselbe öffentliche IP wie oben - nutzt
also ebenfalls `local-review` für Postgres. Storage-Zugriff über den
eigens angelegten, eng begrenzten Service Principal
(`jobfinder-local-docker`, nur `Storage Blob Data Contributor` auf dem
Container `application-documents`).

**GitHub Actions (CI/CD) → Azure Resource Manager, ACR, tfstate-Container**
OIDC-Föderation ohne gespeichertes Azure-Anmeldegeheimnis, mit drei
getrennten Identitäten (`infrastructure/cicd.tf`). Apply: `Contributor` +
`Role Based Access Control Administrator` auf `rg-jobfinder`, plus
`Storage Blob Data Contributor` auf dem `tfstate`-Container. Build: nur
`AcrPush` und `Reader` auf der Registry. Plan für Pull Requests: nur `Reader`
auf `rg-jobfinder` und `Storage Blob Data Reader` auf `tfstate`.

## Was ist öffentlich erreichbar, und warum

| Ressource | Öffentlich? | Eigentlicher Zugriffsschutz |
|---|---|---|
| Review-Container-App | Ja, absichtlich | Easy Auth (Entra ID), nur Jannis |
| PostgreSQL Flexible Server | Ja | TLS + Passwort der eingeschränkten `jobfinder_app`-Rolle |
| Storage Account | Ja | RBAC (Azure AD), Kontoschlüssel abgeschaltet |
| Key Vault | Ja | RBAC (Azure AD) |
| Container Registry (ACR) | Ja | RBAC (Azure AD), `admin_enabled = false` |
| Worker (Container Apps Job) | Nein - hat keine Ingress, läuft nur aus- gehend | - |

## Warum (noch) keine VNet-Integration / Private Endpoints

Eine echte Netzwerkisolation (Container-Apps-Umgebung ins VNet integrieren,
Postgres/Storage/Key-Vault auf Private Endpoints umstellen, öffentlichen
Zugriff abschalten) ist die "richtige" produktionsnahe Lösung, hat für
dieses Projekt aber einen konkreten Preis:

- Consumption-Profil + VNet-Integration erzwingt eine feste ausgehende
  Adresse (NAT Gateway o. ä.), zusätzliche laufende Kosten ab ca. 20+
  EUR/Monat.
- Private Endpoints kosten selbst noch einmal pro Endpunkt (Postgres,
  Storage, Key Vault wären mindestens drei).
- Azure veröffentlicht für das Consumption-Profil keine stabilen
  IP-Bereiche (kein Service-Tag), die sich stattdessen in eine engere
  Firewallregel hätten gießen lassen - geprüft, existiert nicht.

Für den aktuellen Umfang (ein Nutzer, keine fremden/sensiblen Daten Dritter,
keine Compliance-Vorgabe) trägt TLS + RBAC/Passwort die eigentliche Last der
Absicherung, nicht die Netzwerkgrenze. Sollte das Projekt wachsen (mehrere
Nutzer, echte Bewerberdaten Dritter, o. ä.), ist das der erste Punkt, der
sich ändern sollte - dieser Abschnitt dient als bewusste, dokumentierte
Entscheidung, kein Versehen.
