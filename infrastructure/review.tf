# The review runs as its own, permanently reachable container app
# (unlike the worker job, which starts only when needed and ends again).
resource "azurerm_container_app" "review" {
  name                         = "jobfinder-review"
  resource_group_name          = azurerm_resource_group.jobfinder.name
  container_app_environment_id = azurerm_container_app_environment.jobfinder.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"

  identity {
    type         = "UserAssigned"
    identity_ids = local.review_identity_ids
  }

  registry {
    server   = azurerm_container_registry.jobfinder.login_server
    identity = local.review_identity
  }

  # References to Key Vault addresses, never to the values themselves.
  dynamic "secret" {
    for_each = local.database_entra_active ? [] : [1]
    content {
      name                = "jobfinder-database-url"
      key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/${local.review_database_secret}"
      identity            = local.review_identity
    }
  }
  secret {
    name                = "aad-client-secret"
    key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/ReviewAadClientSecret"
    identity            = local.review_identity
  }
  # Personal search settings (content of user_settings.local.yaml); they
  # belong neither in the public repo nor in the image.
  secret {
    name                = "jobfinder-user-settings"
    key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/JobfinderUserSettings"
    identity            = local.review_identity
  }

  # On creation reachable only within the environment. azapi_resource_action.review_public
  # makes it public (HTTPS, provided by Azure) only after the sign-in
  # (azapi_resource.review_auth) is in place.
  ingress {
    external_enabled = false
    target_port      = 8765
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 1

    container {
      name   = "jobfinder-review"
      image  = "${azurerm_container_registry.jobfinder.login_server}/jobfinder:${var.image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"

      # 0.0.0.0 instead of the local default 127.0.0.1, otherwise the container
      # ingress cannot reach the process; --no-browser fits a container
      # without a display.
      command = ["python", "-m", "job_finder.review", "--host", "0.0.0.0", "--no-browser"]

      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }
      env {
        name        = "JOBFINDER_DATABASE_URL"
        secret_name = local.database_entra_active ? null : "jobfinder-database-url"
        value       = local.database_entra_active ? local.entra_database_urls.review : null
      }
      dynamic "env" {
        for_each = local.database_entra_active ? [1] : []
        content {
          name  = "JOBFINDER_DATABASE_AUTH"
          value = "managed_identity"
        }
      }
      env {
        name        = "JOBFINDER_USER_SETTINGS"
        secret_name = "jobfinder-user-settings"
      }
      env {
        name  = "JOBFINDER_DOCUMENTS_BACKEND"
        value = "blob"
      }
      env {
        name  = "JOBFINDER_STORAGE_ACCOUNT"
        value = azurerm_storage_account.jobfinder.name
      }
      env {
        name  = "JOBFINDER_STORAGE_CONTAINER"
        value = azurerm_storage_container.application_documents.name
      }
      env {
        name  = "JOBFINDER_REVIEW_HOST"
        value = var.review_fqdn
      }
      env {
        name  = "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID"
        value = local.review_client_id
      }
      # Timings per API request (job_finder/telemetry.py), only route, status and durations.
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = azurerm_application_insights.jobfinder.connection_string
      }
    }
  }

  tags = azurerm_resource_group.jobfinder.tags

  # As for the worker: the CI/CD pipeline sets the app version, not Terraform.
  # review_public sets the public access; Terraform does not turn it back
  # here. Terraform refuses a deletion or recreation: without the sign-in
  # application data would be public for a moment. docs/operations.md
  # describes a deliberate rebuild.
  lifecycle {
    ignore_changes  = [template[0].container[0].image, ingress[0].external_enabled]
    prevent_destroy = true
  }

  depends_on = [
    azurerm_role_assignment.acr_pull,
    azurerm_role_assignment.keyvault_secrets_user_worker,
    azurerm_role_assignment.acr_pull_review,
    azurerm_role_assignment.keyvault_review_secret,
    azurerm_role_assignment.storage_blob_data_contributor_review,
  ]
}

# Easy Auth (Microsoft Entra ID sign-in). azurerm does not cover this
# resource yet; azapi talks to the Azure Resource Manager API directly for it.
# Terraform creates it after the app; the app becomes public only afterwards
# (review_public).
resource "azapi_resource" "review_auth" {
  type      = "Microsoft.App/containerApps/authConfigs@2024-03-01"
  name      = "current"
  parent_id = azurerm_container_app.review.id

  body = {
    properties = {
      platform = {
        enabled = true
      }
      globalValidation = {
        unauthenticatedClientAction = "RedirectToLoginPage"
        redirectToProvider          = "azureactivedirectory"
      }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            clientId                = var.review_aad_client_id
            clientSecretSettingName = "aad-client-secret"
            openIdIssuer            = "https://sts.windows.net/${data.azurerm_client_config.current.tenant_id}/"
          }
          # Restricts access explicitly to this one person instead of
          # "anyone in the tenant" - relevant if further accounts (guests,
          # members) are added to the tenant later.
          validation = {
            defaultAuthorizationPolicy = {
              allowedPrincipals = {
                identities = [var.owner_object_id]
              }
            }
          }
        }
      }
    }
  }

  # As for the app: without this configuration the review would be open.
  lifecycle {
    prevent_destroy = true
  }
}

# Makes the review public only once the sign-in is configured; so a
# rebuild has no moment without sign-in. For the existing, already
# public app this PATCH changes nothing. If the action does not run,
# the app stays internal: the deploy then fails at the access check
# instead of exposing the data.
resource "azapi_resource_action" "review_public" {
  type        = "Microsoft.App/containerApps@2024-03-01"
  resource_id = azurerm_container_app.review.id
  method      = "PATCH"

  body = {
    properties = {
      configuration = {
        ingress = {
          external = true
        }
      }
    }
  }

  depends_on = [azapi_resource.review_auth]
}
