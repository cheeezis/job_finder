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

  tags = azurerm_resource_group.jobfinder.tags
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
resource "azurerm_role_assignment" "storage_blob_data_contributor" {
  scope                = azurerm_storage_account.jobfinder.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.jobfinder.principal_id
  principal_type       = "ServicePrincipal"
}

# Der aktuell angemeldete Benutzer (az login), z.B. für lokale Entwicklung und
# manuelle Prüfungen ohne Kontoschlüssel - dieselbe Identitätsbasis wie die App.
data "azurerm_client_config" "current" {}

resource "azurerm_role_assignment" "storage_blob_data_contributor_dev" {
  scope                = azurerm_storage_account.jobfinder.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}
