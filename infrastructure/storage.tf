# Dauerhafte Ablage für Bewerbungsdokumente, getrennt von der Datenbank.
# Der Name muss global eindeutig sein; gleiches Hash-Muster wie beim Postgres-Server.
resource "azurerm_storage_account" "jobfinder" {
  name                = "stjobfinder${substr(sha256(var.subscription_id), 0, 8)}"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location

  # Kleinste Redundanzstufe für den Einstieg; ausreichend für dieses Lernprojekt.
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"

  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  # Kein Zugriff über die Account-Schlüssel: Wer sie abfragen kann (etwa mit
  # Contributor), bekommt damit trotzdem keinen Datenzugriff. Alle Zugriffe
  # laufen über Entra ID und die eng gefassten Rollen unten.
  shared_access_key_enabled = false

  # Der Account hält die einzigen Cloud-Kopien der Bewerbungsdokumente und den
  # Terraform-State. Überschriebene Blobs bleiben als Version erhalten,
  # gelöschte Blobs und Container lassen sich 14 Tage lang wiederherstellen.
  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 14
    }

    container_delete_retention_policy {
      days = 14
    }
  }

  tags = azurerm_resource_group.jobfinder.tags

  # Zusätzlich zur Löschsperre unten: Einen Plan, der den Account löschen oder
  # neu anlegen würde, bricht Terraform schon vor dem Apply ab.
  lifecycle {
    prevent_destroy = true
  }
}

# Ohne Aufräumen sammelt sich bei jedem Terraform-Apply eine State-Version an.
resource "azurerm_storage_management_policy" "jobfinder" {
  storage_account_id = azurerm_storage_account.jobfinder.id

  rule {
    name    = "delete-old-versions"
    enabled = true

    filters {
      blob_types = ["blockBlob"]
    }

    actions {
      version {
        delete_after_days_since_creation = 30
      }
    }
  }
}

# Löschsperre für den ganzen Account: Soft Delete rettet nur einzelne Blobs und
# Container, nicht einen gelöschten Account samt Dokumenten und Terraform-State.
# Ändern bleibt erlaubt, Löschen nicht - auch nicht für Container und
# Rollenzuweisungen darunter. Sperren darf nur ein Owner anlegen oder entfernen,
# die Pipeline nicht: Fehlt die Sperre, scheitert deshalb der nächste CI-Apply,
# bis sie lokal wieder angelegt ist.
resource "azurerm_management_lock" "storage" {
  name       = "no-delete"
  scope      = azurerm_storage_account.jobfinder.id
  lock_level = "CanNotDelete"
  notes      = "Bewerbungsdokumente und Terraform-State. Entfernen nur bewusst und lokal."
}

# Enthält die Bewerbungsdokumente. "private" heißt: kein anonymer Lesezugriff,
# nur über eine authentifizierte Entra-ID-Identität (Kontoschlüssel sind aus).
resource "azurerm_storage_container" "application_documents" {
  name                  = "application-documents"
  storage_account_id    = azurerm_storage_account.jobfinder.id
  container_access_type = "private"

  lifecycle {
    prevent_destroy = true
  }
}

# Übergangsrecht der bisherigen gemeinsamen Identität. In split übernimmt die
# Review den Dokumentzugriff. Beide geplanten Worker überspringen das ZIP-Backup
# und benötigen deshalb keine Dokument-Bytes; Metadaten bleiben in PostgreSQL.
resource "azurerm_role_assignment" "storage_blob_data_contributor" {
  count                = local.runtime_split ? 0 : 1
  scope                = azurerm_storage_container.application_documents.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.jobfinder.principal_id
  principal_type       = "ServicePrincipal"
}

# Liefert nur noch die Tenant-ID (keyvault.tf, review.tf); object_id wird
# bewusst NICHT mehr von hier gelesen, siehe var.owner_object_id.
data "azurerm_client_config" "current" {}

# Eigener Zugriff für lokale Entwicklung und manuelle Prüfungen ohne
# Kontoschlüssel. Bleibt bewusst auf dem ganzen Account: Das lokale
# terraform braucht darüber Zugriff auf den tfstate-Container.
resource "azurerm_role_assignment" "storage_blob_data_contributor_dev" {
  scope                = azurerm_storage_account.jobfinder.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.owner_object_id
}

# Dedicated app registration for the local Docker-based hybrid worker run
# (StepStone/Remotely), which has no Managed Identity and no interactive
# az-CLI session available inside the container. Least privilege: only this
# one role on the documents container - not the tfstate container in the same
# account, since this principal's secret lives on a local disk.
resource "azurerm_role_assignment" "storage_blob_data_contributor_local_docker" {
  count                = local.runtime_split ? 0 : 1
  scope                = azurerm_storage_container.application_documents.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.local_docker_sp_object_id
  principal_type       = "ServicePrincipal"
}
