# Shared resource group for the job finder infrastructure managed by Terraform.
# References to other resources make their values and dependencies usable.
resource "azurerm_resource_group" "jobfinder" {
  name     = "rg-jobfinder"
  location = var.location

  # The other resources take over these tags.
  tags = {
    project    = "jobfinder"
    managed_by = "terraform"
  }
}

# Stores the Docker images the CI pipeline builds and pushes.
resource "azurerm_container_registry" "jobfinder" {
  name                = "acrjobfinder"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  sku                 = "Basic"
  # The finder gets access through a managed identity instead of the admin account.
  admin_enabled = false

  tags = azurerm_resource_group.jobfinder.tags
}

# Stores the containers' operational logs; job data lives in PostgreSQL and Blob Storage.
resource "azurerm_log_analytics_workspace" "jobfinder" {
  name                = "law-jobfinder"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location
  # Billing tier for ingested logs; no reserved storage size.
  sku = "PerGB2018"
  # Log retention in days.
  retention_in_days = 30

  tags = azurerm_resource_group.jobfinder.tags
}

# Execution environment for the container jobs; the finder is defined separately.
resource "azurerm_container_app_environment" "jobfinder" {
  name                = "cae-jobfinder"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location

  logs_destination           = "log-analytics"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.jobfinder.id

  # Consumption profile inside a workload profiles environment.
  # This avoids the Express environment, which was unsuitable for jobs before.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }

  tags = azurerm_resource_group.jobfinder.tags
}

# Identity the finder uses to pull its image without a registry password.
resource "azurerm_user_assigned_identity" "jobfinder" {
  name                = "id-jobfinder-pull"
  resource_group_name = azurerm_resource_group.jobfinder.name
  location            = azurerm_resource_group.jobfinder.location

  tags = azurerm_resource_group.jobfinder.tags
}

# Allows the identity to pull images from exactly this registry.
# Pushing images is not part of this role.
resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.jobfinder.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.jobfinder.principal_id
  principal_type       = "ServicePrincipal"
}

# Defines the finder job; it starts on a schedule (see schedule_trigger_config).
resource "azurerm_container_app_job" "finder" {
  name                         = "jobfinder-worker"
  resource_group_name          = azurerm_resource_group.jobfinder.name
  location                     = azurerm_resource_group.jobfinder.location
  container_app_environment_id = azurerm_container_app_environment.jobfinder.id
  workload_profile_name        = "Consumption"

  # Limit the run time to one hour and do not retry failed runs automatically.
  replica_timeout_in_seconds = 3600
  replica_retry_limit        = 0

  # 08:00 and 18:00 CEST = 06:00 and 16:00 UTC. Runs all year on a fixed
  # UTC time; the actual local time shifts by one hour when switching
  # between CEST and CET.
  schedule_trigger_config {
    cron_expression = "0 6,16 * * *"
    # One replica per execution; its successful completion ends the execution.
    parallelism              = 1
    replica_completion_count = 1
  }

  # Assigns the identity created above to this job.
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.jobfinder.id]
  }

  # Uses this identity to sign in when pulling the private image.
  registry {
    server   = azurerm_container_registry.jobfinder.login_server
    identity = azurerm_user_assigned_identity.jobfinder.id
  }

  # Refers only to the Key Vault address, never to the secret value itself;
  # Azure resolves it on every start through the assigned identity.
  secret {
    name                = "discord-webhook-url"
    key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/DiscordWebhookUrl"
    identity            = azurerm_user_assigned_identity.jobfinder.id
  }
  secret {
    name                = "startup-jobs-api-key"
    key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/StartupJobsApiKey"
    identity            = azurerm_user_assigned_identity.jobfinder.id
  }
  dynamic "secret" {
    for_each = local.database_entra_active ? [] : [1]
    content {
      name                = "jobfinder-database-url"
      key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/${local.worker_database_secret}"
      identity            = azurerm_user_assigned_identity.jobfinder.id
    }
  }
  # Personal search settings (content of user_settings.local.yaml); they
  # belong neither in the public repo nor in the image.
  secret {
    name                = "jobfinder-user-settings"
    key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/JobfinderUserSettings"
    identity            = azurerm_user_assigned_identity.jobfinder.id
  }
  # Personal profile for the AI agent (content of profile.local.yaml).
  # Only the worker writes fact sheets; the review does not need it. The
  # secret must exist before the first apply with this reference.
  secret {
    name                = "jobfinder-profile"
    key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/JobfinderProfile"
    identity            = azurerm_user_assigned_identity.jobfinder.id
  }

  # Hybrid split: StepStone and Remotely return no matches to Azure
  # (bot protection blocks known cloud IP ranges); these two run instead
  # once a day from the local computer against the same database.
  # Without this command block the image's start command would apply:
  # python run_finder.py.
  template {
    container {
      name    = "jobfinder-worker"
      command = ["python", "run_finder.py", "--exclude-sources", "stepstone,remotely"]
      # Only for the initial setup; afterwards the CI/CD pipeline sets the image
      # (see lifecycle below).
      image  = "${azurerm_container_registry.jobfinder.login_server}/jobfinder:${var.image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      # Print Python output straight away, so progress appears promptly in the Azure logs.
      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }

      # Account name/container are no secrets; access runs through the managed
      # identity assigned above, no stored password needed.
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
        name  = "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID"
        value = azurerm_user_assigned_identity.jobfinder.client_id
      }
      env {
        name  = "JOBFINDER_REVIEW_HOST"
        value = var.review_fqdn
      }
      # The ZIP backup before every run would vanish with the container; here
      # point-in-time restore (Postgres) and blob versioning protect the data.
      env {
        name  = "JOBFINDER_SKIP_RUN_BACKUP"
        value = "1"
      }
      # Names this run as the cloud one in the runs table and the review.
      env {
        name  = "JOBFINDER_RUNNER"
        value = "cloud"
      }

      env {
        name        = "JOBFINDER_DATABASE_URL"
        secret_name = local.database_entra_active ? null : "jobfinder-database-url"
        value       = local.database_entra_active ? local.entra_database_urls.worker : null
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
        name        = "JOBFINDER_PROFILE"
        secret_name = "jobfinder-profile"
      }
      # Address of the language model for the AI agent; without it a run skips
      # the agent, like the local hybrid run that does not set it.
      env {
        name  = "JOBFINDER_OPENAI_ENDPOINT"
        value = azurerm_cognitive_account.openai.endpoint
      }
      # Target of the agent traces; without this value the agent writes none.
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = azurerm_application_insights.jobfinder.connection_string
      }

      env {
        name        = "DISCORD_WEBHOOK_URL"
        secret_name = "discord-webhook-url"
      }
      env {
        name        = "STARTUP_JOBS_API_KEY"
        secret_name = "startup-jobs-api-key"
      }
    }
  }

  tags = azurerm_resource_group.jobfinder.tags

  # The app version belongs to the CI/CD pipeline (az containerapp job update).
  # If Terraform managed it as well, every local apply would reset it to the
  # default of var.image_tag.
  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }

  # The pull and Key Vault permissions must be created before the job.
  # The reference to the identity alone does not ensure this order.
  depends_on = [
    azurerm_role_assignment.acr_pull,
    azurerm_role_assignment.keyvault_secrets_user_worker,
    azurerm_role_assignment.keyvault_worker_secret,
  ]
}
