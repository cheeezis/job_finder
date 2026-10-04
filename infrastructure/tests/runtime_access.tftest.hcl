# Alle Provider sind simuliert: kein Azure-Zugriff, keine Ressourcen oder Modellkosten.
mock_provider "azurerm" {
  override_during = plan
  mock_data "azurerm_client_config" {
    defaults = { tenant_id = "00000000-0000-0000-0000-000000000001" }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/example/providers/Microsoft.ManagedIdentity/userAssignedIdentities/review"
      principal_id = "00000000-0000-0000-0000-000000000002"
      client_id    = "00000000-0000-0000-0000-000000000003"
    }
  }
  mock_resource "azurerm_key_vault" {
    defaults = {
      id        = "/subscriptions/00000000-0000-0000-000000000001/resourceGroups/example/providers/Microsoft.KeyVault/vaults/example"
      vault_uri = "https://example.vault.azure.net/"
    }
  }
  mock_resource "azurerm_storage_container" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/example/providers/Microsoft.Storage/storageAccounts/example/blobServices/default/containers/application-documents" }
  }
  override_resource {
    target = azurerm_user_assigned_identity.jobfinder
    values = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/example/providers/Microsoft.ManagedIdentity/userAssignedIdentities/worker"
      principal_id = "00000000-0000-0000-0000-000000000004"
      client_id    = "00000000-0000-0000-0000-000000000005"
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

run "legacy_retains_current_access" {
  command = plan
  assert {
    condition = (
      length(azurerm_user_assigned_identity.review) == 0 &&
      length(azurerm_role_assignment.keyvault_worker_secret) == 0 &&
      length(azurerm_role_assignment.keyvault_review_secret) == 0 &&
      length(azurerm_role_assignment.keyvault_secrets_user_worker) == 1 &&
      length(azurerm_role_assignment.storage_blob_data_contributor) == 1 &&
      length(azurerm_role_assignment.storage_blob_data_contributor_local_docker) == 1 &&
      local.review_identity == azurerm_user_assigned_identity.jobfinder.id &&
      local.review_database_secret == "JobfinderDatabaseUrl" && local.worker_database_secret == "JobfinderDatabaseUrl"
    )
    error_message = "legacy muss die laufenden Zugänge erhalten."
  }
}

run "prepare_adds_without_switching" {
  command = plan
  variables { runtime_identity_phase = "prepare" }
  assert {
    condition = (
      length(azurerm_user_assigned_identity.review) == 1 &&
      length(azurerm_role_assignment.keyvault_worker_secret) == 5 &&
      length(azurerm_role_assignment.keyvault_review_secret) == 3 &&
      length(azurerm_role_assignment.keyvault_secrets_user_worker) == 1 &&
      length(local.review_identity_ids) == 2 &&
      local.review_identity == azurerm_user_assigned_identity.jobfinder.id &&
      local.review_database_secret == "JobfinderDatabaseUrl" && local.worker_database_secret == "JobfinderDatabaseUrl"
    )
    error_message = "prepare muss die zusätzliche Review-Identität ohne Zugangswechsel ergänzen."
  }
  assert {
    condition = (
      alltrue([for name, role in azurerm_role_assignment.keyvault_review_secret : role.principal_id == azurerm_user_assigned_identity.review[0].principal_id && endswith(role.scope, "/secrets/${name}")]) &&
      !contains(keys(azurerm_role_assignment.keyvault_review_secret), "DiscordWebhookUrl") &&
      !contains(keys(azurerm_role_assignment.keyvault_review_secret), "JobfinderProfile")
    )
    error_message = "Review darf nur ihre drei eigenen Secrets erhalten."
  }
}

run "split_requires_acceptance" {
  command = plan
  variables { runtime_identity_phase = "split" }
  expect_failures = [var.runtime_access_verified]
}

run "split_removes_shared_capabilities" {
  command = plan
  variables {
    runtime_identity_phase  = "split"
    runtime_access_verified = true
  }
  assert {
    condition = (
      local.review_identity == azurerm_user_assigned_identity.review[0].id &&
      local.review_client_id == azurerm_user_assigned_identity.review[0].client_id &&
      toset(local.review_identity_ids) == toset([azurerm_user_assigned_identity.review[0].id]) &&
      local.review_database_secret == "JobfinderReviewDatabaseUrl" && local.worker_database_secret == "JobfinderWorkerDatabaseUrl" &&
      length(azurerm_role_assignment.keyvault_secrets_user_worker) == 0 &&
      length(azurerm_role_assignment.storage_blob_data_contributor) == 0 &&
      length(azurerm_role_assignment.storage_blob_data_contributor_local_docker) == 0 &&
      azurerm_role_assignment.storage_blob_data_contributor_review[0].scope == azurerm_storage_container.application_documents.id &&
      azurerm_role_assignment.openai_user_worker.principal_id != azurerm_user_assigned_identity.review[0].principal_id
    )
    error_message = "split muss Review und Worker trennen und überflüssige Dokument-/Vault-Rechte entfernen."
  }
  assert {
    condition = (
      azurerm_container_app.review.identity[0].identity_ids == toset([azurerm_user_assigned_identity.review[0].id]) &&
      azurerm_container_app.review.registry[0].identity == azurerm_user_assigned_identity.review[0].id &&
      alltrue([for secret in azurerm_container_app.review.secret : secret.identity == azurerm_user_assigned_identity.review[0].id]) &&
      one([for secret in azurerm_container_app.review.secret : secret.key_vault_secret_id if secret.name == "jobfinder-database-url"]) == "https://example.vault.azure.net/secrets/JobfinderReviewDatabaseUrl" &&
      one([for secret in azurerm_container_app_job.finder.secret : secret.key_vault_secret_id if secret.name == "jobfinder-database-url"]) == "https://example.vault.azure.net/secrets/JobfinderWorkerDatabaseUrl" &&
      one([for env in azurerm_container_app.review.template[0].container[0].env : env.value if env.name == "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID"]) == azurerm_user_assigned_identity.review[0].client_id
    )
    error_message = "Die echten Container-Bindungen müssen zur getrennten Identität und DB-Rolle zeigen."
  }
}
