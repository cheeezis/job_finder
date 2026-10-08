# F09: the first stage adds logins; only a separate acceptance allows split.
variable "runtime_identity_phase" {
  description = "F09 stage: legacy (unchanged), prepare (additional rights), split (separate runtimes)."
  type        = string
  default     = "legacy"
  validation {
    condition     = contains(["legacy", "prepare", "split"], var.runtime_identity_phase)
    error_message = "runtime_identity_phase must be legacy, prepare or split."
  }
}

variable "runtime_access_verified" {
  description = "Set to true only after documented image pull, secret, database and negative checks."
  type        = bool
  default     = false
  validation {
    condition     = var.runtime_identity_phase != "split" || var.runtime_access_verified
    error_message = "Before split, the effective access in prepare must be accepted."
  }
}

locals {
  runtime_prepared = var.runtime_identity_phase != "legacy"
  runtime_split    = var.runtime_identity_phase == "split" && var.runtime_access_verified
  review_identity  = local.runtime_split ? azurerm_user_assigned_identity.review[0].id : azurerm_user_assigned_identity.jobfinder.id
  review_client_id = local.runtime_split ? azurerm_user_assigned_identity.review[0].client_id : azurerm_user_assigned_identity.jobfinder.client_id
  review_identity_ids = local.runtime_split ? [azurerm_user_assigned_identity.review[0].id] : concat(
    [azurerm_user_assigned_identity.jobfinder.id],
    local.runtime_prepared ? [azurerm_user_assigned_identity.review[0].id] : [],
  )
  worker_database_secret = local.runtime_split ? "JobfinderWorkerDatabaseUrl" : "JobfinderDatabaseUrl"
  review_database_secret = local.runtime_split ? "JobfinderReviewDatabaseUrl" : "JobfinderDatabaseUrl"
  worker_secret_names = toset(local.runtime_prepared ? [for name in [
    "DiscordWebhookUrl", "StartupJobsApiKey", "JobfinderWorkerDatabaseUrl", "JobfinderUserSettings", "JobfinderProfile",
  ] : name if !local.database_entra_active || name != "JobfinderWorkerDatabaseUrl"] : [])
  review_secret_names = toset(local.runtime_prepared ? [for name in [
    "JobfinderReviewDatabaseUrl", "ReviewAadClientSecret", "JobfinderUserSettings",
  ] : name if !local.database_entra_active || name != "JobfinderReviewDatabaseUrl"] : [])
}

# The earlier identity stays with the worker; no swap of a running principal.
resource "azurerm_user_assigned_identity" "review" {
  count               = local.runtime_prepared ? 1 : 0
  name                = "id-jobfinder-review"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  tags                = azurerm_resource_group.jobfinder.tags
}

resource "azurerm_role_assignment" "acr_pull_review" {
  count                = local.runtime_prepared ? 1 : 0
  scope                = azurerm_container_registry.jobfinder.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.review[0].principal_id
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
  principal_id         = azurerm_user_assigned_identity.review[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "storage_blob_data_contributor_review" {
  count                = local.runtime_prepared ? 1 : 0
  scope                = azurerm_storage_container.application_documents.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.review[0].principal_id
  principal_type       = "ServicePrincipal"
}

# Address changes keep existing assignments in legacy/prepare.
moved {
  from = azurerm_role_assignment.keyvault_secrets_user_worker
  to   = azurerm_role_assignment.keyvault_secrets_user_worker[0]
}
moved {
  from = azurerm_role_assignment.storage_blob_data_contributor
  to   = azurerm_role_assignment.storage_blob_data_contributor[0]
}
moved {
  from = azurerm_role_assignment.storage_blob_data_contributor_local_docker
  to   = azurerm_role_assignment.storage_blob_data_contributor_local_docker[0]
}
