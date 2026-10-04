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
  mock_resource "azurerm_postgresql_flexible_server" {
    defaults = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/example/providers/Microsoft.DBforPostgreSQL/flexibleServers/example"
      fqdn = "example.postgres.database.azure.com"
    }
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

run "password_auth_keeps_existing_container_access" {
  command = plan
  variables {
    runtime_identity_phase  = "split"
    runtime_access_verified = true
  }
  assert {
    condition = (
      azurerm_postgresql_flexible_server.jobfinder.authentication[0].password_auth_enabled &&
      !azurerm_postgresql_flexible_server.jobfinder.authentication[0].active_directory_auth_enabled &&
      length(azurerm_postgresql_flexible_server_active_directory_administrator.owner) == 0 &&
      length(azurerm_role_assignment.keyvault_worker_secret) == 5 &&
      length(azurerm_role_assignment.keyvault_review_secret) == 3 &&
      one([for env in azurerm_container_app_job.finder.template[0].container[0].env : env.secret_name if env.name == "JOBFINDER_DATABASE_URL"]) == "jobfinder-database-url" &&
      !contains([for env in azurerm_container_app_job.finder.template[0].container[0].env : env.name], "JOBFINDER_DATABASE_AUTH") &&
      !contains([for env in azurerm_container_app.review.template[0].container[0].env : env.name], "JOBFINDER_DATABASE_AUTH")
    )
    error_message = "Ein Merge darf ohne F10-Freigabe keine Anmeldung oder Secret-Verweise umschalten."
  }
}

run "entra_prepare_preserves_password_runtimes" {
  command = plan
  variables {
    runtime_identity_phase    = "split"
    runtime_access_verified   = true
    database_auth_phase       = "prepare"
    postgres_entra_admin_name = "owner@example.test"
  }
  assert {
    condition = (
      azurerm_postgresql_flexible_server.jobfinder.authentication[0].password_auth_enabled &&
      azurerm_postgresql_flexible_server.jobfinder.authentication[0].active_directory_auth_enabled &&
      azurerm_postgresql_flexible_server.jobfinder.authentication[0].tenant_id == "00000000-0000-0000-0000-000000000001" &&
      length(azurerm_postgresql_flexible_server_active_directory_administrator.owner) == 1 &&
      azurerm_postgresql_flexible_server_active_directory_administrator.owner[0].principal_type == "User" &&
      azurerm_postgresql_flexible_server_active_directory_administrator.owner[0].object_id == var.owner_object_id &&
      length(azurerm_role_assignment.keyvault_worker_secret) == 5 &&
      length(azurerm_role_assignment.keyvault_review_secret) == 3 &&
      one([for secret in azurerm_container_app_job.finder.secret : secret.key_vault_secret_id if secret.name == "jobfinder-database-url"]) == "https://example.vault.azure.net/secrets/JobfinderWorkerDatabaseUrl" &&
      one([for secret in azurerm_container_app.review.secret : secret.key_vault_secret_id if secret.name == "jobfinder-database-url"]) == "https://example.vault.azure.net/secrets/JobfinderReviewDatabaseUrl" &&
      !contains([for env in azurerm_container_app.review.template[0].container[0].env : env.name], "JOBFINDER_DATABASE_AUTH")
    )
    error_message = "prepare muss Entra ergänzen und beide bisherigen Laufzeitzugänge erhalten."
  }
  assert {
    condition = (
      output.entra_principals.worker == azurerm_user_assigned_identity.jobfinder.principal_id &&
      output.entra_principals.review == azurerm_user_assigned_identity.review[0].principal_id &&
      output.entra_principals.hybrid == var.local_docker_sp_object_id &&
      output.entra_database_urls.hybrid == "postgresql://jobfinder_hybrid_entra@example.postgres.database.azure.com:5432/jobfinder?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt"
    )
    error_message = "Einrichtungsoutputs müssen die eigenen Object-IDs und passwortfreien Rollen liefern."
  }
}

run "entra_requires_f09_split" {
  command = plan
  variables {
    database_auth_phase       = "prepare"
    postgres_entra_admin_name = "owner@example.test"
  }
  expect_failures = [var.database_auth_phase]
}

run "entra_requires_explicit_admin" {
  command = plan
  variables {
    runtime_identity_phase  = "split"
    runtime_access_verified = true
    database_auth_phase     = "prepare"
  }
  expect_failures = [var.postgres_entra_admin_name]
}

run "entra_requires_azure_acceptance" {
  command = plan
  variables {
    runtime_identity_phase    = "split"
    runtime_access_verified   = true
    database_auth_phase       = "entra"
    postgres_entra_admin_name = "owner@example.test"
  }
  expect_failures = [var.database_entra_verified]
}

run "runtime_identity_cannot_be_database_admin" {
  command = plan
  variables {
    runtime_identity_phase    = "split"
    runtime_access_verified   = true
    database_auth_phase       = "prepare"
    postgres_entra_admin_name = "owner@example.test"
    owner_object_id           = "00000000-0000-0000-0000-000000000004"
  }
  expect_failures = [azurerm_postgresql_flexible_server_active_directory_administrator.owner[0]]
}

run "accepted_entra_switches_only_database_credentials" {
  command = plan
  variables {
    runtime_identity_phase    = "split"
    runtime_access_verified   = true
    database_auth_phase       = "entra"
    database_entra_verified   = true
    postgres_entra_admin_name = "owner@example.test"
  }
  assert {
    condition = (
      azurerm_postgresql_flexible_server.jobfinder.authentication[0].password_auth_enabled &&
      length(azurerm_role_assignment.keyvault_worker_secret) == 4 &&
      length(azurerm_role_assignment.keyvault_review_secret) == 2 &&
      !contains(keys(azurerm_role_assignment.keyvault_worker_secret), "JobfinderWorkerDatabaseUrl") &&
      !contains(keys(azurerm_role_assignment.keyvault_review_secret), "JobfinderReviewDatabaseUrl") &&
      !contains([for secret in azurerm_container_app_job.finder.secret : secret.name], "jobfinder-database-url") &&
      !contains([for secret in azurerm_container_app.review.secret : secret.name], "jobfinder-database-url") &&
      one([for env in azurerm_container_app_job.finder.template[0].container[0].env : env.value if env.name == "JOBFINDER_DATABASE_URL"]) == output.entra_database_urls.worker &&
      one([for env in azurerm_container_app.review.template[0].container[0].env : env.value if env.name == "JOBFINDER_DATABASE_URL"]) == output.entra_database_urls.review &&
      one([for env in azurerm_container_app_job.finder.template[0].container[0].env : env.value if env.name == "JOBFINDER_DATABASE_AUTH"]) == "managed_identity" &&
      one([for env in azurerm_container_app.review.template[0].container[0].env : env.value if env.name == "JOBFINDER_DATABASE_AUTH"]) == "managed_identity" &&
      one([for env in azurerm_container_app_job.finder.template[0].container[0].env : env.value if env.name == "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID"]) == azurerm_user_assigned_identity.jobfinder.client_id &&
      one([for env in azurerm_container_app.review.template[0].container[0].env : env.value if env.name == "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID"]) == azurerm_user_assigned_identity.review[0].client_id &&
      length(azurerm_role_assignment.storage_blob_data_contributor) == 0 &&
      length(azurerm_role_assignment.storage_blob_data_contributor_review) == 1
    )
    error_message = "entra muss nur DB-Zugänge umschalten, eigene MIs verwenden und F09-Rechte erhalten."
  }
}

run "runtime_admin_guard_ignores_uuid_case" {
  command = plan
  variables {
    runtime_identity_phase    = "split"
    runtime_access_verified   = true
    database_auth_phase       = "prepare"
    postgres_entra_admin_name = "owner@example.test"
    local_docker_sp_object_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    owner_object_id           = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
  }
  expect_failures = [azurerm_postgresql_flexible_server_active_directory_administrator.owner[0]]
}
