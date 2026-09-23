# Phase 7: Dauerhafte Ablage für Bewerbungsdokumente, getrennt von der Datenbank.
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

# Enthält die Bewerbungsdokumente. "private" heißt: kein anonymer Lesezugriff,
# nur über eine authentifizierte Identität oder einen Kontoschlüssel.
resource "azurerm_storage_container" "application_documents" {
  name                  = "application-documents"
  storage_account_id    = azurerm_storage_account.jobfinder.id
  container_access_type = "private"
}

# Dieselbe Identität, die der Worker schon fürs ACR-Image-Pull nutzt, bekommt
# zusätzlich Lese-/Schreibzugriff auf die Dokumente. Eine Identität kann mehrere
# Rollen auf unterschiedlichen Ressourcen halten; eine zweite ist nicht nötig.
# Bewusst nur auf den Dokumente-Container: Im selben Account liegt auch der
# Terraform-State, den die App weder lesen noch ändern darf.
resource "azurerm_role_assignment" "storage_blob_data_contributor" {
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
  scope                = azurerm_storage_container.application_documents.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.local_docker_sp_object_id
  principal_type       = "ServicePrincipal"
}
