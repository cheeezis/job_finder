# F09: worker and review run as separate identities, each reading only its own secrets.
locals {
  worker_secret_names = toset(["DiscordWebhookUrl", "StartupJobsApiKey", "JobfinderUserSettings", "JobfinderProfile"])
  review_secret_names = toset(["ReviewAadClientSecret", "JobfinderUserSettings"])
}

# The earlier identity stays with the worker; no swap of a running principal.
resource "azurerm_user_assigned_identity" "review" {
  name                = "id-jobfinder-review"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  tags                = azurerm_resource_group.jobfinder.tags
}

resource "azurerm_role_assignment" "acr_pull_review" {
  scope                = azurerm_container_registry.jobfinder.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.review.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "keyvault_worker_secret" {
  for_each             = local.worker_secret_names
  scope                = "${azurerm_key_vault.jobfinder.id}/secrets/${each.value}"
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.jobfinder.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "keyvault_review_secret" {
  for_each             = local.review_secret_names
  scope                = "${azurerm_key_vault.jobfinder.id}/secrets/${each.value}"
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.review.principal_id
  principal_type       = "ServicePrincipal"
}

# Only the review reads and writes application documents.
resource "azurerm_role_assignment" "storage_blob_data_contributor_review" {
  scope                = azurerm_storage_container.application_documents.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.review.principal_id
  principal_type       = "ServicePrincipal"
}

# Addresses from the switchable migration stages; the objects stay unchanged.
moved {
  from = azurerm_user_assigned_identity.review[0]
  to   = azurerm_user_assigned_identity.review
}
moved {
  from = azurerm_role_assignment.acr_pull_review[0]
  to   = azurerm_role_assignment.acr_pull_review
}
moved {
  from = azurerm_role_assignment.storage_blob_data_contributor_review[0]
  to   = azurerm_role_assignment.storage_blob_data_contributor_review
}
moved {
  from = azurerm_role_assignment.monitoring_publisher_review[0]
  to   = azurerm_role_assignment.monitoring_publisher_review
}
