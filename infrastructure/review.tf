# Die Review läuft als eigene, dauerhaft erreichbare Container App
# (anders als der Worker-Job, der nur bei Bedarf startet und wieder endet).
resource "azurerm_container_app" "review" {
  name                         = "jobfinder-review"
  resource_group_name          = azurerm_resource_group.jobfinder.name
  container_app_environment_id = azurerm_container_app_environment.jobfinder.id
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.jobfinder.id]
  }

  registry {
    server   = azurerm_container_registry.jobfinder.login_server
    identity = azurerm_user_assigned_identity.jobfinder.id
  }

  # Verweise auf Key-Vault-Adressen, nie auf die Werte selbst.
  secret {
    name                = "jobfinder-database-url"
    key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/JobfinderDatabaseUrl"
    identity            = azurerm_user_assigned_identity.jobfinder.id
  }
  secret {
    name                = "aad-client-secret"
    key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/ReviewAadClientSecret"
    identity            = azurerm_user_assigned_identity.jobfinder.id
  }
  # Persönliche Sucheinstellungen (Inhalt von user_settings.local.yaml); sie
  # gehören weder ins öffentliche Repo noch ins Image.
  secret {
    name                = "jobfinder-user-settings"
    key_vault_secret_id = "${azurerm_key_vault.jobfinder.vault_uri}secrets/JobfinderUserSettings"
    identity            = azurerm_user_assigned_identity.jobfinder.id
  }

  # Beim Anlegen nur innerhalb der Umgebung erreichbar. Öffentlich (HTTPS,
  # von Azure bereitgestellt) schaltet sie erst azapi_resource_action.review_public,
  # nachdem die Anmeldung (azapi_resource.review_auth) steht.
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

      # 0.0.0.0 statt des lokalen Standards 127.0.0.1, sonst erreicht der
      # Container-Ingress den Prozess nicht; --no-browser passt für einen
      # Container ohne Anzeige.
      command = ["python", "-m", "job_finder.review", "--host", "0.0.0.0", "--no-browser"]

      env {
        name  = "PYTHONUNBUFFERED"
        value = "1"
      }
      env {
        name        = "JOBFINDER_DATABASE_URL"
        secret_name = "jobfinder-database-url"
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
        value = azurerm_user_assigned_identity.jobfinder.client_id
      }
    }
  }

  tags = azurerm_resource_group.jobfinder.tags

  # Wie beim Worker: Die App-Version setzt die CI/CD-Pipeline, nicht Terraform.
  # Den öffentlichen Zugang setzt review_public; Terraform dreht ihn hier nicht
  # zurück. Ein Löschen oder Neuanlegen verweigert Terraform: Ohne die
  # Anmeldung wären Bewerbungsdaten kurz öffentlich. Einen bewussten Neuaufbau
  # beschreibt docs/operations.md.
  lifecycle {
    ignore_changes  = [template[0].container[0].image, ingress[0].external_enabled]
    prevent_destroy = true
  }

  depends_on = [
    azurerm_role_assignment.acr_pull,
    azurerm_role_assignment.keyvault_secrets_user_worker,
  ]
}

# Easy Auth (Microsoft-Entra-ID-Anmeldung). azurerm bildet diese Ressource
# noch nicht ab; azapi spricht dafür direkt die Azure-Resource-Manager-API an.
# Terraform legt sie nach der App an; öffentlich wird die App erst danach
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
          # Beschränkt den Zugriff explizit auf diese eine Person statt auf
          # "irgendwer aus dem Tenant" - relevant, falls dem Tenant später
          # weitere Konten (Gäste, Mitglieder) hinzugefügt werden.
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

  # Wie bei der App: Ohne diese Konfiguration wäre die Review offen.
  lifecycle {
    prevent_destroy = true
  }
}

# Schaltet die Review erst öffentlich, wenn die Anmeldung konfiguriert ist; so
# gibt es beim Neuaufbau keinen Moment ohne Login. Bei der bestehenden,
# schon öffentlichen App ändert dieser PATCH nichts. Läuft die Aktion nicht,
# bleibt die App intern: Das Deploy scheitert dann an der Zugangsprüfung,
# statt die Daten offenzulegen.
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
