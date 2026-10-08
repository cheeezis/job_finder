# All providers are simulated: no Azure access or production changes.
mock_provider "azurerm" {
  override_during = plan
  mock_data "azurerm_client_config" {
    defaults = { tenant_id = "00000000-0000-0000-0000-000000000001" }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/example/providers/Microsoft.ManagedIdentity/userAssignedIdentities/worker"
      principal_id = "00000000-0000-0000-0000-000000000002"
      client_id    = "00000000-0000-0000-0000-000000000003"
    }
  }
  mock_resource "azurerm_key_vault" {
    defaults = {
      id        = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/example/providers/Microsoft.KeyVault/vaults/example"
      vault_uri = "https://example.vault.azure.net/"
    }
  }
  mock_resource "azurerm_storage_container" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/example/providers/Microsoft.Storage/storageAccounts/example/blobServices/default/containers/application-documents" }
  }
  mock_resource "azurerm_storage_account" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/example/providers/Microsoft.Storage/storageAccounts/example" }
  }
  mock_resource "azurerm_postgresql_flexible_server" {
    defaults = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/example/providers/Microsoft.DBforPostgreSQL/flexibleServers/example"
      fqdn = "example.postgres.database.azure.com"
    }
  }
}
mock_provider "azapi" {
  override_during = plan
}

variables {
  subscription_id         = "00000000-0000-0000-0000-000000000001"
  postgres_admin_password = "mock-password-never-used"
  postgres_client_ipv4    = "192.0.2.1"
  alert_email             = "example@example.test"
  review_fqdn             = "review.example.test"
}

run "database_and_document_recovery_windows_match" {
  command = plan
  assert {
    condition = (
      azurerm_postgresql_flexible_server.jobfinder.backup_retention_days == 14 &&
      azurerm_storage_account.jobfinder.blob_properties[0].delete_retention_policy[0].days >= azurerm_postgresql_flexible_server.jobfinder.backup_retention_days &&
      azurerm_storage_account.jobfinder.blob_properties[0].container_delete_retention_policy[0].days >= azurerm_postgresql_flexible_server.jobfinder.backup_retention_days
    )
    error_message = "Deleted documents and containers must cover at least the chosen 14-day database window."
  }
}

run "lifecycle_deletion_is_limited_to_state_versions" {
  command = plan
  assert {
    condition = alltrue([
      for rule in azurerm_storage_management_policy.jobfinder.rule : !rule.enabled || (
        length(rule.filters[0].prefix_match) > 0 &&
        alltrue([for prefix in rule.filters[0].prefix_match : startswith(prefix, "tfstate/")]) &&
        length(rule.actions[0].base_blob) == 0 &&
        rule.actions[0].version[0].delete_after_days_since_creation == 30
      )
    ])
    error_message = "Deletion by age may only hit old versions in the real tfstate container; application documents and current blobs are kept."
  }
}

run "document_history_and_delete_locks_remain_enabled" {
  command = plan
  assert {
    condition = (
      azurerm_storage_account.jobfinder.blob_properties[0].versioning_enabled &&
      azurerm_storage_container.application_documents.container_access_type == "private" &&
      azurerm_management_lock.storage.lock_level == "CanNotDelete" &&
      azurerm_management_lock.storage.scope == azurerm_storage_account.jobfinder.id &&
      azurerm_management_lock.postgres.lock_level == "CanNotDelete" &&
      azurerm_management_lock.postgres.scope == azurerm_postgresql_flexible_server.jobfinder.id
    )
    error_message = "Document versions, private access and the existing delete locks for database and storage must be kept."
  }
}
