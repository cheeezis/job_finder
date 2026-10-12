# All providers are simulated: no Azure access, no resources or model costs.
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
  subscription_id           = "00000000-0000-0000-0000-000000000001"
  postgres_admin_password   = "mock-password-never-used"
  postgres_client_ipv4      = "192.0.2.1"
  alert_email               = "example@example.test"
  review_fqdn               = "review.example.test"
  postgres_entra_admin_name = "owner@example.test"
}

run "runtimes_use_separate_identities" {
  command = plan
  assert {
    condition = (
      length(azurerm_role_assignment.keyvault_worker_secret) == 4 &&
      length(azurerm_role_assignment.keyvault_review_secret) == 2 &&
      alltrue([for name, role in azurerm_role_assignment.keyvault_review_secret : role.principal_id == azurerm_user_assigned_identity.review.principal_id && endswith(role.scope, "/secrets/${name}")]) &&
      !contains(keys(azurerm_role_assignment.keyvault_review_secret), "DiscordWebhookUrl") &&
      !contains(keys(azurerm_role_assignment.keyvault_review_secret), "JobfinderProfile") &&
      azurerm_role_assignment.storage_blob_data_contributor_review.scope == azurerm_storage_container.application_documents.id &&
      azurerm_role_assignment.openai_user_worker.principal_id != azurerm_user_assigned_identity.review.principal_id
    )
    error_message = "Worker and review must only get their own secrets; only the review reaches the documents."
  }
  assert {
    condition = (
      azurerm_container_app.review.identity[0].identity_ids == toset([azurerm_user_assigned_identity.review.id]) &&
      azurerm_container_app.review.registry[0].identity == azurerm_user_assigned_identity.review.id &&
      alltrue([for secret in azurerm_container_app.review.secret : secret.identity == azurerm_user_assigned_identity.review.id]) &&
      one([for env in azurerm_container_app_job.finder.template[0].container[0].env : env.value if env.name == "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID"]) == azurerm_user_assigned_identity.jobfinder.client_id &&
      one([for env in azurerm_container_app.review.template[0].container[0].env : env.value if env.name == "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID"]) == azurerm_user_assigned_identity.review.client_id
    )
    error_message = "The container bindings must point to the separate identities."
  }
}

run "runtimes_sign_in_with_entra" {
  command = plan
  assert {
    condition = (
      azurerm_postgresql_flexible_server.jobfinder.authentication[0].password_auth_enabled &&
      azurerm_postgresql_flexible_server.jobfinder.authentication[0].active_directory_auth_enabled &&
      azurerm_postgresql_flexible_server.jobfinder.authentication[0].tenant_id == "00000000-0000-0000-0000-000000000001" &&
      azurerm_postgresql_flexible_server_active_directory_administrator.owner.principal_type == "User" &&
      azurerm_postgresql_flexible_server_active_directory_administrator.owner.object_id == var.owner_object_id &&
      !contains([for secret in azurerm_container_app_job.finder.secret : secret.name], "jobfinder-database-url") &&
      !contains([for secret in azurerm_container_app.review.secret : secret.name], "jobfinder-database-url") &&
      one([for env in azurerm_container_app_job.finder.template[0].container[0].env : env.value if env.name == "JOBFINDER_DATABASE_URL"]) == output.entra_database_urls.worker &&
      one([for env in azurerm_container_app.review.template[0].container[0].env : env.value if env.name == "JOBFINDER_DATABASE_URL"]) == output.entra_database_urls.review &&
      one([for env in azurerm_container_app_job.finder.template[0].container[0].env : env.value if env.name == "JOBFINDER_DATABASE_AUTH"]) == "managed_identity" &&
      one([for env in azurerm_container_app.review.template[0].container[0].env : env.value if env.name == "JOBFINDER_DATABASE_AUTH"]) == "managed_identity"
    )
    error_message = "Worker and review must sign in with their managed identities, without a database password."
  }
  assert {
    condition = (
      output.entra_principals.worker == azurerm_user_assigned_identity.jobfinder.principal_id &&
      output.entra_principals.review == azurerm_user_assigned_identity.review.principal_id &&
      output.entra_principals.hybrid == var.local_docker_sp_object_id &&
      output.entra_database_urls.hybrid == "postgresql://jobfinder_hybrid_entra@example.postgres.database.azure.com:5432/jobfinder?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt"
    )
    error_message = "Setup outputs must provide the own object IDs and password-free roles."
  }
}

run "entra_requires_explicit_admin" {
  command = plan
  variables { postgres_entra_admin_name = " " }
  expect_failures = [var.postgres_entra_admin_name]
}

run "entra_admin_name_fits_postgres_role_names" {
  command = plan
  variables { postgres_entra_admin_name = "${join("", [for i in range(60) : "x"])}@example.test" }
  expect_failures = [var.postgres_entra_admin_name]
}

run "runtime_identity_cannot_be_database_admin" {
  command = plan
  variables { owner_object_id = "00000000-0000-0000-0000-000000000004" }
  expect_failures = [azurerm_postgresql_flexible_server_active_directory_administrator.owner]
}

run "runtime_admin_guard_ignores_uuid_case" {
  command = plan
  variables {
    local_docker_sp_object_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    owner_object_id           = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
  }
  expect_failures = [azurerm_postgresql_flexible_server_active_directory_administrator.owner]
}
